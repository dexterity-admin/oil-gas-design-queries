"""Entry point for the packaged DexMentor Library program (server PC)."""
import multiprocessing

if __name__ == "__main__":
    # The scan reader runs in worker processes (server/ocr.py); in the packaged program a worker starts
    # this same .exe, and freeze_support() turns it into the worker before anything else runs.
    multiprocessing.freeze_support()
    import winlimits

    # Hard ceiling in percent of the whole PC (0 = no cap). At 10 the worst single second measured 12.2 %,
    # so Task Manager never shows the Library above 15 %, even while it reads a scanned book.
    winlimits.cap(winlimits.limit_from_env("DEXMENTOR_CPU_LIMIT", 10))

    from server.__main__ import main

    main()
