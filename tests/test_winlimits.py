"""The CPU cap is enforced by Windows: a program that keeps every core busy gets only its share, and no
single second goes over the ceiling Task Manager would show. Run with:  python -m pytest tests"""
import os, subprocess, sys, textwrap
import pytest

DESKTOP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop")

# Caps itself, keeps one process per core spinning (the children join the job), and every second reads the
# job's own kernel accounting: CPU time of the whole family against the CPU time the PC had in that second.
CHILD = textwrap.dedent("""
    import ctypes, os, sys, time, multiprocessing
    sys.path.insert(0, sys.argv[1])
    import winlimits

    def spin(until):
        while time.time() < until:
            pass

    def used():
        acct = (ctypes.c_longlong * 6)()  # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: user, kernel time first
        ctypes.windll.kernel32.QueryInformationJobObject(winlimits._job, 1, acct, ctypes.sizeof(acct), None)
        return (acct[0] + acct[1]) / 1e7

    if __name__ == "__main__":
        assert winlimits.cap(float(sys.argv[2]))
        procs = [multiprocessing.Process(target=spin, args=(time.time() + 14,)) for _ in range(os.cpu_count())]
        for p in procs: p.start()
        time.sleep(2)  # let them all start
        shares, last, t = [], used(), time.time()
        for _ in range(10):
            time.sleep(1)
            now, nt = used(), time.time()
            shares.append((now - last) / ((nt - t) * os.cpu_count())); last, t = now, nt
        for p in procs: p.join()
        print("%.4f %.4f" % (sum(shares) / len(shares), max(shares)))
""")


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows only; the programs ship for Windows")
@pytest.mark.parametrize("cap, ceiling", [(10, 0.15), (6, 0.10), (20, 0.25)])  # Library; engineer app idle, answering
def test_no_second_goes_over_the_ceiling(tmp_path, cap, ceiling):
    script = tmp_path / "spin.py"
    script.write_text(CHILD)
    out = subprocess.run([sys.executable, str(script), DESKTOP, str(cap)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    avg, worst = map(float, out.stdout.split())
    assert avg > 0.5 * cap / 100, "the work did not run (%.1f%%)" % (100 * avg)
    assert worst < ceiling, "cap %d%%: worst second %.1f%% of the PC" % (cap, 100 * worst)


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows only")
def test_boost_raises_the_cap_only_while_working(tmp_path):
    script = tmp_path / "boost.py"
    script.write_text(textwrap.dedent("""
        import sys
        sys.path.insert(0, sys.argv[1])
        import ctypes, winlimits
        def rate():
            info = winlimits._CpuRate()
            ctypes.windll.kernel32.QueryInformationJobObject(winlimits._job, 15, ctypes.byref(info), ctypes.sizeof(info), None)
            return info.CpuRate // 100
        assert winlimits.cap(6)
        b = winlimits.Boost(idle=6, busy=20)
        seen = [rate()]
        with b:
            seen.append(rate())
            with b:  # two answers at once: still raised until the last one ends
                seen.append(rate())
            seen.append(rate())
        seen.append(rate())
        print(seen)
    """))
    out = subprocess.run([sys.executable, str(script), DESKTOP], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[6, 20, 20, 20, 6]"
