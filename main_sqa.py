# main_sqa.py
import sys
import multiprocessing # Import multiprocessing at the top

# Configure Loguru very early for any startup messages
from loguru import logger
logger.remove() # Remove default handler to avoid duplicates if MainWindow also configures
logger.add(sys.stderr, level="INFO",
           format="{time:HH:mm:ss} | {level: <7} | {process.name}:{process.id} | {message}")

def run_application():
    """Initializes and runs the PySide6 application."""
    # Imports specific to the Qt application are done inside this function
    from PySide6.QtWidgets import QApplication
    from main_window_sqa import MainWindow # Your main window class

    logger.info("SQA-O&G Application run_application() starting...")
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion") # Optional: for a consistent look

        window = MainWindow()
        window.show()

        logger.info("Application event loop starting.")
        exit_code = app.exec()
        logger.info(f"Application event loop finished with exit code: {exit_code}")
        sys.exit(exit_code)

    except Exception as e:
        logger.critical("Fatal error during SQA-O&G application execution:")
        logger.exception(e)
        try: # Attempt to show a GUI error message if possible
            from PySide6.QtWidgets import QMessageBox # Local import
            QMessageBox.critical(None, "Fatal Application Error",
                                 f"The SQA-O&G application encountered a fatal error and will now close.\n\nDetails:\n{e}\n\nPlease check log files.")
        except: pass # If GUI can't even show a message box
        sys.exit(1)


# This guard is CRUCIAL for multiprocessing when packaged/frozen,
# especially on Windows or when using the 'spawn' start method.
if __name__ == "__main__":
    # These MUST be called as early as possible and BEFORE most other imports or actions.
    try:
        # Explicitly set the start method to 'spawn'.
        # 'spawn' creates a fresh process, generally safer for GUI/frozen apps.
        # 'force=True' is used in case a library might have tried to set it already.
        multiprocessing.set_start_method('spawn', force=True)
        print("[Main Entry __main__] Multiprocessing start method set to 'spawn'.")
    except RuntimeError as e:
        # This can happen if the start method has already been set and force=False,
        # or if trying to set it after the context has already been used.
        print(f"[Main Entry __main__] Warning: Could not set multiprocessing start method (may be already set or context used): {e}")
        pass # Attempt to continue

    multiprocessing.freeze_support()
    print("[Main Entry __main__] multiprocessing.freeze_support() called.")

    # Call the main application function
    run_application()