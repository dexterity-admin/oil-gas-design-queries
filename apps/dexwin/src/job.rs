//! Windows job objects: the processes a DexMentor app starts (the model runner, the Library engine) are
//! put in a job so that
//!   * they die with the app, even when the app is killed (no orphan eating RAM after a crash);
//!   * the kernel holds them to a hard share of the whole PC's CPU, whatever the work;
//!   * a runaway cannot commit more memory than the given limit.
//! The app's own window process is not in the job, so drawing the page is never starved.
use std::os::windows::io::AsRawHandle;
use std::process::Child;
use std::sync::Mutex;

use windows_sys::Win32::Foundation::HANDLE;
use windows_sys::Win32::System::JobObjects::*;

pub struct Job(HANDLE);

// A job handle is a kernel handle: usable from any thread.
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

impl Job {
    /// A job whose processes are killed when the app exits. `memory_limit`: bytes of committed memory
    /// for all its processes together (None = no limit).
    pub fn new(memory_limit: Option<usize>) -> Option<Job> {
        unsafe {
            let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if h.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if let Some(bytes) = memory_limit {
                info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_MEMORY;
                info.JobMemoryLimit = bytes;
            }
            SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            Some(Job(h))
        }
    }

    /// Hard CPU ceiling in percent of the whole PC; 0 (or 100 and over) lifts it. True when applied.
    pub fn cap_cpu(&self, percent: f64) -> bool {
        let mut info = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION::default();
        if percent > 0.0 && percent < 100.0 {
            info.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP;
            info.Anonymous.CpuRate = (percent * 100.0).round() as u32; // in 1/100 of a percent
        }
        unsafe {
            SetInformationJobObject(
                self.0,
                JobObjectCpuRateControlInformation,
                &info as *const _ as *const _,
                std::mem::size_of::<JOBOBJECT_CPU_RATE_CONTROL_INFORMATION>() as u32,
            ) != 0
        }
    }

    pub fn add(&self, child: &Child) -> bool {
        unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) != 0 }
    }
}

/// Raises a job's CPU ceiling while at least one piece of heavy work runs (an answer being written), and
/// puts it back when the last one ends. `let _g = boost.hold();` for the length of the work.
pub struct Boost {
    job: Option<&'static Job>,
    idle: f64,
    busy: f64,
    n: Mutex<usize>,
}

impl Boost {
    pub fn new(job: Option<&'static Job>, idle: f64, busy: f64) -> Boost {
        if let Some(j) = job {
            j.cap_cpu(idle);
        }
        Boost { job, idle, busy, n: Mutex::new(0) }
    }

    pub fn hold(&'static self) -> BoostGuard {
        let mut n = self.n.lock().unwrap();
        *n += 1;
        if *n == 1 {
            if let Some(j) = self.job {
                j.cap_cpu(self.busy);
            }
        }
        BoostGuard(self)
    }
}

pub struct BoostGuard(&'static Boost);

impl Drop for BoostGuard {
    fn drop(&mut self) {
        let mut n = self.0.n.lock().unwrap();
        *n -= 1;
        if *n == 0 {
            if let Some(j) = self.0.job {
                j.cap_cpu(self.0.idle);
            }
        }
    }
}

/// Free physical memory in bytes (0 if unknown).
pub fn free_memory() -> u64 {
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
    unsafe {
        let mut m: MEMORYSTATUSEX = std::mem::zeroed();
        m.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
        if GlobalMemoryStatusEx(&mut m) != 0 {
            m.ullAvailPhys
        } else {
            0
        }
    }
}

/// A cap in percent from an environment variable (0 turns it off), else the default.
pub fn limit_from_env(name: &str, default: f64) -> f64 {
    std::env::var(name).ok().and_then(|v| v.trim().parse().ok()).unwrap_or(default)
}

/// Process creation flags: no console window, below-normal priority (the engineer's own programs come first).
pub const QUIET: u32 = 0x0800_0000 | 0x0000_4000; // CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
