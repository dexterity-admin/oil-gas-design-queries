# main.py
import sys
from PySide6.QtWidgets import QApplication
from main_window import MainWindow
from loguru import logger
import multiprocessing # <<--- IMPORT MULTIPROCESSING

if __name__ == "__main__":
    # --- >> ADD THIS LINE - Essential for frozen executables using multiprocessing << ---
    multiprocessing.freeze_support()
    # --- >> END ADD << ---

    # Configure Loguru basic console logging (optional here, as main_window also configures it)
    # You might want to remove this one if main_window handles all needed logging sinks.
    # logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <7} | {process.name}:{process.id} | {message}")

    # Create and run the QApplication
    app = QApplication(sys.argv)
    window = MainWindow()  # Creates the main GUI window
    window.show()
    sys.exit(app.exec())  # Start the Qt event loop