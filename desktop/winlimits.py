"""Hard limits on how much of an office PC a DexMentor program may use (Windows, standard library only).

The program and every process it starts (the model runner) are put in a Windows job object with a
hard CPU cap: the kernel itself stops them at the given share of the whole PC, whatever the work.
The cap can be changed while running (the engineer app raises it only while an answer is written).
Priority is lowered too, so the engineer's own programs always come first.
"""
import ctypes
import os
import threading

_job = None  # kept open for the life of the program; closing it would lift the cap
# Popen creationflags for the app window (Edge/Chrome): page drawing must not starve behind the model.
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


class _CpuRate(ctypes.Structure):  # JOBOBJECT_CPU_RATE_CONTROL_INFORMATION
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]


class _BasicLimit(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


def _kernel32():
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    return k


def _set(k, job, percent):
    info = _CpuRate(0x1 | 0x4, int(round(percent * 100)))  # JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | HARD_CAP
    return bool(k.SetInformationJobObject(job, 15, ctypes.byref(info), ctypes.sizeof(info)))


def cap(percent):
    """Caps this process and its children at `percent` of the whole PC's CPU. Returns True when applied."""
    global _job
    if os.name != "nt" or not 0 < percent < 100:
        return False
    k = _kernel32()
    k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS, inherited by children
    job = k.CreateJobObjectW(None, None)
    if not job or not _set(k, job, percent):
        return False  # JobObjectCpuRateControlInformation (15) needs Windows 8 or later
    basic = _BasicLimit(LimitFlags=0x800)  # JOB_OBJECT_LIMIT_BREAKAWAY_OK: only for processes started with BREAKAWAY
    k.SetInformationJobObject(job, 2, ctypes.byref(basic), ctypes.sizeof(basic))
    if not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
        return False
    _job = job
    return True


class Boost:
    """Raises the cap while at least one piece of heavy work runs, and puts it back when the last one ends.

        boost = Boost(idle=6, busy=20)
        with boost:
            ...  # the model writes an answer
    """

    def __init__(self, idle, busy):
        self.idle, self.busy, self.n, self.lock = idle, busy, 0, threading.Lock()

    def __enter__(self):
        with self.lock:
            self.n += 1
            if self.n == 1 and _job and self.busy > 0:
                _set(_kernel32(), _job, self.busy)

    def __exit__(self, *exc):
        with self.lock:
            self.n -= 1
            if self.n == 0 and _job:
                _set(_kernel32(), _job, self.idle)


def popen_outside(cmd, flags=0):
    """Starts a program outside the cap (the app window). If an outer job forbids that, starts it inside."""
    import subprocess
    try:
        return subprocess.Popen(cmd, creationflags=flags | (BREAKAWAY if _job else 0))
    except OSError:
        return subprocess.Popen(cmd, creationflags=flags)


def limit_from_env(name, default):
    """The cap in percent from an environment variable (0 turns it off), else the default."""
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)
