# main_window.py
import sys # Needed for logger stderr configuration
import os
import time
from datetime import timedelta # Import timedelta for formatting ETA
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QSpinBox, QProgressBar, QTextEdit,
    QMessageBox, QApplication, QSizePolicy
)
# Import necessary Qt Core components
from PySide6.QtCore import Qt, Slot, QTimer, QMetaObject, Q_ARG

from loguru import logger # Import logger

# Import project modules
import config
from manager_thread import ProcessingManagerThread

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Knowledge Base Indexer")
        self.setGeometry(100, 100, 800, 680)

        # --- Central Widget and Layout ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Folder Selection ---
        folder_layout = QHBoxLayout()
        self.pdf_folder_input = QLineEdit()
        self.pdf_folder_input.setPlaceholderText("Select folder containing PDF files...")
        pdf_button = QPushButton("Browse PDF Folder")
        pdf_button.clicked.connect(self.browse_pdf_folder)
        folder_layout.addWidget(QLabel("PDF Folder:"))
        folder_layout.addWidget(self.pdf_folder_input)
        folder_layout.addWidget(pdf_button)
        main_layout.addLayout(folder_layout)

        # --- ChromaDB Path ---
        db_folder_layout = QHBoxLayout()
        self.db_path_input = QLineEdit()
        self.db_path_input.setText(config.DEFAULT_CHROMA_PATH) # Default path
        self.db_path_input.setPlaceholderText("Select or enter path for ChromaDB data...")
        db_button = QPushButton("Browse DB Path")
        db_button.clicked.connect(self.browse_db_path)
        db_folder_layout.addWidget(QLabel("ChromaDB Path:"))
        db_folder_layout.addWidget(self.db_path_input)
        db_folder_layout.addWidget(db_button)
        main_layout.addLayout(db_folder_layout)

        # --- Tesseract Configuration Inputs ---
        tesseract_exe_layout = QHBoxLayout()
        self.tesseract_exe_input = QLineEdit()
        self.tesseract_exe_input.setPlaceholderText("(Optional) Path to tesseract.exe")
        tesseract_exe_button = QPushButton("Browse Tesseract Exe")
        tesseract_exe_button.clicked.connect(self.browse_tesseract_exe)
        tesseract_exe_layout.addWidget(QLabel("Tesseract Executable:"))
        tesseract_exe_layout.addWidget(self.tesseract_exe_input)
        tesseract_exe_layout.addWidget(tesseract_exe_button)
        main_layout.addLayout(tesseract_exe_layout)

        tessdata_layout = QHBoxLayout()
        self.tessdata_input = QLineEdit()
        self.tessdata_input.setPlaceholderText("(Optional) Path to 'tessdata' folder (TESSDATA_PREFIX)")
        tessdata_button = QPushButton("Browse Tessdata Folder")
        tessdata_button.clicked.connect(self.browse_tessdata_path)
        tessdata_layout.addWidget(QLabel("Tesseract Data Path:"))
        tessdata_layout.addWidget(self.tessdata_input)
        tessdata_layout.addWidget(tessdata_button)
        main_layout.addLayout(tessdata_layout)

        # --- Worker Count ---
        worker_layout = QHBoxLayout()
        self.worker_spinbox = QSpinBox()
        self.worker_spinbox.setMinimum(1)
        try:
            cpu_cores = os.cpu_count()
            default_workers = max(1, cpu_cores // 2) if cpu_cores else 2
            max_workers = max(1, cpu_cores * 2) if cpu_cores else 8
        except NotImplementedError:
            default_workers = config.DEFAULT_WORKER_COUNT
            max_workers = 8

        self.worker_spinbox.setMaximum(max_workers)
        self.worker_spinbox.setValue(default_workers)
        worker_layout.addWidget(QLabel("Number of Parallel Workers:"))
        worker_layout.addWidget(self.worker_spinbox)
        worker_layout.addStretch()
        main_layout.addLayout(worker_layout)

        # --- Start/Stop Buttons ---
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Processing")
        self.start_button.clicked.connect(self.start_processing)
        self.stop_button = QPushButton("Stop Processing")
        self.stop_button.clicked.connect(self.stop_processing)
        self.stop_button.setEnabled(False)
        button_layout.addStretch()
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        # --- Progress Bar & ETA ---
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p% - %v/%m Files")
        self.eta_label = QLabel("ETA: --:--:--")
        self.eta_label.setMinimumWidth(120)
        self.eta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.eta_label)
        main_layout.addLayout(progress_layout)

        # --- Status Label ---
        self.status_label = QLabel("Ready.")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.status_label)

        # --- Log Area ---
        main_layout.addWidget(QLabel("Processing Log:"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(self.log_area)

        # --- Member Variables ---
        self.manager_thread = None
        self.processing_start_time = None

        # --- Configure Loguru ---
        logger.remove()
        logger.add(
            sys.stderr,
            level="INFO",
            format="{time:HH:mm:ss} | {level: <7} | {process.name}:{process.id} | {message}"
        )
        logger.add(
            config.LOG_FILE,
            rotation="10 MB",
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {process.name}:{process.id} | {message}"
        )
        # GUI Sink Setup - Format string just shows the message as process info is included by worker
        logger.add(
            self.add_log_message_gui_sink,
            level="DEBUG",
            format="{message}"
        )

        logger.info("Application started. Ready.")


    def add_log_message_gui_sink(self, message):
        """Sink target function called by Loguru; uses invokeMethod for thread safety."""
        try:
            QMetaObject.invokeMethod(
                self,
                "update_log_area",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, message.strip())
            )
        except RuntimeError as e:
             print(f"RuntimeError invoking GUI log update (maybe closing?): {e}")
        except Exception as e:
            print(f"Unexpected error invoking GUI log update: {e}")

    @Slot(str)
    def update_log_area(self, message):
        """This SLOT runs safely in the GUI thread to append messages to the QTextEdit."""
        self.log_area.append(message)
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


    def browse_pdf_folder(self):
        """Opens a dialog to select the folder containing PDF files."""
        folder = QFileDialog.getExistingDirectory(self, "Select PDF Folder")
        if folder:
            self.pdf_folder_input.setText(folder)


    def browse_db_path(self):
        """Opens a dialog to select the folder for ChromaDB data."""
        folder = QFileDialog.getExistingDirectory(self, "Select ChromaDB Folder Location (Recommend Empty/Dedicated)")
        if folder:
            self.db_path_input.setText(folder)


    def browse_tesseract_exe(self):
        """Opens a dialog to select the Tesseract executable file."""
        filter_str = "Executable files (*.exe)" if os.name == 'nt' else "All files (*)"
        filename, _ = QFileDialog.getOpenFileName(self, "Select Tesseract Executable", filter=filter_str)
        if filename:
            self.tesseract_exe_input.setText(filename)


    def browse_tessdata_path(self):
        """Opens a dialog to select the Tesseract 'tessdata' folder."""
        folder = QFileDialog.getExistingDirectory(self, "Select Tessdata Folder (contains eng.traineddata etc.)")
        if folder:
            self.tessdata_input.setText(folder)


    def start_processing(self):
        """Validates inputs and starts the background processing thread."""
        pdf_folder = self.pdf_folder_input.text()
        db_path = self.db_path_input.text()
        worker_count = self.worker_spinbox.value()  # Get value from spinbox
        tesseract_cmd = self.tesseract_exe_input.text().strip()
        tessdata_prefix = self.tessdata_input.text().strip()

        # --- >> ADD THIS LOG LINE << ---
        logger.info(f"Attempting to start processing with worker_count = {worker_count}")
        # --- >> END ADD << ---

        # --- Input Validation ---
        if not pdf_folder or not Path(pdf_folder).is_dir():
            QMessageBox.warning(self, "Input Error", "Please select a valid PDF folder.")
            return
        if not db_path:
             QMessageBox.warning(self, "Input Error", "Please select or enter a valid path for ChromaDB data.")
             return
        if tesseract_cmd and not Path(tesseract_cmd).is_file():
             QMessageBox.warning(self, "Input Error", f"Tesseract executable path not found:\n{tesseract_cmd}")
             return
        if tessdata_prefix and not Path(tessdata_prefix).is_dir():
              QMessageBox.warning(self, "Input Error", f"Tessdata folder path not found:\n{tessdata_prefix}")
              return
        # --- End Validation ---

        # --- Disable UI during processing ---
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pdf_folder_input.setEnabled(False)
        self.db_path_input.setEnabled(False)
        self.tesseract_exe_input.setEnabled(False)
        self.tessdata_input.setEnabled(False)
        self.worker_spinbox.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p% - %v/%m Files")
        self.eta_label.setText("ETA: Calculating...")
        self.status_label.setText("Starting...")
        # --- End Disable UI ---

        # Record start time for ETA
        self.processing_start_time = time.time()

        # Create and start the manager thread
        self.manager_thread = ProcessingManagerThread(
            pdf_folder, db_path, worker_count, tesseract_cmd, tessdata_prefix
        )

        # Connect signals from the thread to slots in this GUI class
        self.manager_thread.progress_updated.connect(self.update_progress)
        self.manager_thread.status_update.connect(self.update_status)
        self.manager_thread.processing_finished.connect(self.on_processing_finished)

        self.manager_thread.start()


    def stop_processing(self):
        """Signals the manager thread to stop processing."""
        if self.manager_thread and self.manager_thread.isRunning():
            logger.warning("Stop button clicked. Requesting processing stop...")
            self.status_label.setText("Stopping...")
            self.stop_button.setEnabled(False)
            self.manager_thread.stop()


    @Slot(int, int)
    def update_progress(self, current_value, max_value):
        """Updates progress bar value and calculates/displays ETA."""
        safe_max_value = max(max_value, 1) # Ensure max value is at least 1 for display/calcs
        self.progress_bar.setMaximum(safe_max_value)
        # Ensure value doesn't exceed max if updates arrive slightly out of order
        self.progress_bar.setValue(min(current_value, safe_max_value))

        if self.processing_start_time and current_value > 0 and max_value > 0:
            elapsed_time = time.time() - self.processing_start_time
            time_per_item = elapsed_time / current_value
            remaining_items = max_value - current_value
            if remaining_items > 0:
                 estimated_remaining_time = time_per_item * remaining_items
                 eta_td = timedelta(seconds=int(estimated_remaining_time))
                 self.eta_label.setText(f"ETA: {str(eta_td)}")
            else:
                 self.eta_label.setText("ETA: Finishing...")
        elif current_value == 0 and max_value > 0 :
             self.eta_label.setText("ETA: Calculating...")
        elif max_value == 0:
            self.eta_label.setText("ETA: N/A")
            self.progress_bar.setFormat("No new files")
        else:
            self.eta_label.setText("ETA: --:--:--")


    @Slot(str)
    def update_status(self, message):
        """Updates the main status label text."""
        self.status_label.setText(message)


    @Slot(bool)
    def on_processing_finished(self, success):
        """Called when the manager thread signals it has finished."""
        logger.info(f"Processing finished signal received. Success: {success}")
        # --- Re-enable UI elements ---
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.pdf_folder_input.setEnabled(True)
        self.db_path_input.setEnabled(True)
        self.tesseract_exe_input.setEnabled(True)
        self.tessdata_input.setEnabled(True)
        self.worker_spinbox.setEnabled(True)
        # --- End Re-enable UI ---

        # Update final ETA/Status (check if stopped)
        stopped_early = self.manager_thread is not None and self.manager_thread._is_stopping

        if stopped_early:
            self.eta_label.setText("ETA: Stopped")
            # Keep the status message from the manager unless it's a generic one
            if "Stopping..." not in self.status_label.text() and "terminating" not in self.status_label.text():
                self.status_label.setText("Processing stopped by user.")
        elif self.progress_bar.value() == self.progress_bar.maximum() and success:
            self.eta_label.setText("ETA: Done")
        elif not success:
            self.eta_label.setText("ETA: Error")
        else: # Should be done if not stopped and no errors
            self.eta_label.setText("ETA: Done")

        # Show message box only if not stopped manually
        if not stopped_early:
            if success:
                QMessageBox.information(self, "Success", "Processing completed successfully!")
            else:
                 QMessageBox.warning(self, "Finished with Errors", "Processing finished, but errors occurred. Check the log file (processing_log.log) and GUI log for details.")

        self.manager_thread = None # Clean up thread reference


    def closeEvent(self, event):
        """Handles the window close event to ensure the background thread is stopped."""
        if self.manager_thread and self.manager_thread.isRunning():
            logger.warning("Window close requested during processing. Attempting to stop...")
            reply = QMessageBox.question(self, 'Confirm Exit',
                                         "Processing is ongoing. Are you sure you want to stop and exit?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)

            if reply == QMessageBox.StandardButton.Yes:
                self.stop_processing()
                # Give thread a moment to react
                if not self.manager_thread.wait(3000):
                     logger.error("Manager thread did not stop gracefully after 3s on close.")
                event.accept() # Allow window to close
            else:
                event.ignore() # Prevent window from closing
        else:
            event.accept() # Allow closing if no thread running