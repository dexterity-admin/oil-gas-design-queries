# main_window_sqa.py
import sys
import os
import time
import json
import traceback
from datetime import timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QStatusBar,
    QPushButton, QFileDialog, QSplitter, QListWidget, QListWidgetItem, QMessageBox,
    QApplication, QSizePolicy, QDialog, QMenu, QMenuBar, QScrollArea,
    QDialogButtonBox, QTextBrowser
)
# QTimer is still needed for scheduling GUI updates from the worker thread callback
from PySide6.QtCore import Qt, Slot, QTimer, QMetaObject, Q_ARG, QSize, QUrl, QPoint
from PySide6.QtGui import QPixmap, QAction, QIcon, QTextCursor, QDesktopServices, QImage

from loguru import logger
from sentence_transformers import SentenceTransformer
from PIL import Image, UnidentifiedImageError, ImageOps
import requests # For checking LLM server status

import config_sqa
from settings_dialog_sqa import SettingsDialog
# --- >> Import the new RAGWorker instead of RAGThread << ---
from rag_worker import RAGWorker
# --- >> END IMPORT << ---

QApplication.setApplicationName("SQA-O&G")
QApplication.setOrganizationName("Dexterity Design Services")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = config_sqa.load_settings()
        self.embedding_model = None # Loaded in deferred_init
        # --- >> Remove RAGThread reference, add RAGWorker reference << ---
        # self.rag_thread = None
        self.rag_worker_thread: RAGWorker | None = None
        # --- >> END CHANGE << ---
        self.current_image_paths = []; self.current_image_index = -1
        self.setWindowTitle("SQA-O&G: Standard Query Assistant"); self.resize(1000, 750)
        self.init_ui()
        self.status_label.setText("Initializing..."); self.ask_button.setEnabled(False)
        QTimer.singleShot(100, self.deferred_init)

    def init_ui(self):
        # --- UI Setup remains the same ---
        central_widget = QWidget(); self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget); main_layout.setContentsMargins(5, 5, 5, 5)
        top_widget = QWidget(); top_layout = QVBoxLayout(top_widget); top_layout.setContentsMargins(0,0,0,5)
        question_label = QLabel("Enter your question about O&G standards:")
        self.question_input = QTextEdit(); self.question_input.setPlaceholderText("e.g., What are the hydrostatic testing requirements for API 650 tanks?")
        self.question_input.setMinimumHeight(60); self.question_input.setMaximumHeight(150)
        button_layout = QHBoxLayout()
        self.ask_button = QPushButton("Ask SQA"); self.ask_button.setIcon(QIcon.fromTheme("system-search")); self.ask_button.setMinimumWidth(100); self.ask_button.clicked.connect(self.ask_sqa)
        self.clear_button = QPushButton("Clear"); self.clear_button.setIcon(QIcon.fromTheme("edit-clear")); self.clear_button.setMinimumWidth(100); self.clear_button.clicked.connect(self.clear_all)
        button_layout.addStretch(); button_layout.addWidget(self.ask_button); button_layout.addWidget(self.clear_button); button_layout.addStretch()
        top_layout.addWidget(question_label); top_layout.addWidget(self.question_input); top_layout.addLayout(button_layout)
        top_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal); self.left_pane_splitter = QSplitter(Qt.Orientation.Vertical)
        answer_widget = QWidget(); answer_layout = QVBoxLayout(answer_widget); answer_layout.setContentsMargins(0,0,0,0)
        answer_layout.addWidget(QLabel("<b>Answer:</b>")); self.answer_browser = QTextBrowser(); self.answer_browser.setReadOnly(True); self.answer_browser.setOpenExternalLinks(True)
        self.answer_browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding); answer_layout.addWidget(self.answer_browser); self.left_pane_splitter.addWidget(answer_widget)
        context_widget = QWidget(); context_layout = QVBoxLayout(context_widget); context_layout.setContentsMargins(0,5,0,0)
        context_layout.addWidget(QLabel("<b>Source Context:</b>")); self.context_list = QListWidget(); self.context_list.setWordWrap(True)
        self.context_list.itemDoubleClicked.connect(self.open_source_document); context_layout.addWidget(self.context_list); self.left_pane_splitter.addWidget(context_widget)
        self.main_splitter.addWidget(self.left_pane_splitter)
        image_widget = QWidget(); image_layout = QVBoxLayout(image_widget); image_layout.setContentsMargins(5,0,0,0)
        image_layout.addWidget(QLabel("<b>Relevant Images:</b>")); self.image_scroll_area = QScrollArea(); self.image_scroll_area.setWidgetResizable(True)
        self.image_scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_display_label = QLabel("No relevant images found."); self.image_display_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_display_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored); self.image_display_label.setScaledContents(False)
        self.image_scroll_area.setWidget(self.image_display_label); image_layout.addWidget(self.image_scroll_area, 1)
        image_nav_layout = QHBoxLayout()
        self.prev_image_button = QPushButton("< Prev"); self.prev_image_button.setEnabled(False); self.prev_image_button.clicked.connect(self.show_prev_image)
        self.image_nav_label = QLabel("Image 0 of 0"); self.image_nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_image_button = QPushButton("Next >"); self.next_image_button.setEnabled(False); self.next_image_button.clicked.connect(self.show_next_image)
        image_nav_layout.addWidget(self.prev_image_button); image_nav_layout.addStretch(); image_nav_layout.addWidget(self.image_nav_label); image_nav_layout.addStretch(); image_nav_layout.addWidget(self.next_image_button)
        image_layout.addLayout(image_nav_layout); self.main_splitter.addWidget(image_widget)
        main_layout.addWidget(top_widget); main_layout.addWidget(self.main_splitter, 1)
        self.main_splitter.setSizes([650, 350]); self.left_pane_splitter.setSizes([400, 300])
        self.create_menus()
        self.status_bar = QStatusBar(); self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready."); self.llm_status_label = QLabel("LLM Server: Unknown")
        self.db_count_label = QLabel("| Indexed Chunks: N/A"); self.db_count_label.setToolTip("Total text chunks in the DB (updates after query).")
        self.dev_by_label = QLabel("| Developed By: Vikram"); self.dev_for_label = QLabel("| Developed for: Dexterity Design Services"); self.copyright_label = QLabel("| © 2024")
        self.status_bar.addWidget(self.status_label, 1); self.status_bar.addPermanentWidget(self.llm_status_label); self.status_bar.addPermanentWidget(self.db_count_label)
        self.status_bar.addPermanentWidget(self.dev_by_label); self.status_bar.addPermanentWidget(self.dev_for_label); self.status_bar.addPermanentWidget(self.copyright_label)

    def deferred_init(self):
        # --- Deferred init remains the same, loads embedding model, checks server ---
        logger.info("Starting deferred initialization (SQA App)..."); self.status_label.setText("Loading embedding model..."); QApplication.processEvents()
        try:
            emb_model_name = self.settings.get('embedding_model', config_sqa.DEFAULT_EMBEDDING_MODEL)
            logger.info(f"[SQA Main] Loading embedding model: {emb_model_name}"); self.embedding_model = SentenceTransformer(emb_model_name)
            logger.info("[SQA Main] Embedding model loaded."); self.status_label.setText("Ready.")
        except Exception as e:
            logger.error(f"[SQA Main] Failed to load embedding model: {e}"); QMessageBox.critical(self, "Model Error", f"Failed to load embedding model '{self.settings.get('embedding_model')}':\n{e}")
            self.embedding_model = None; self.status_label.setText("Error: Embedding Model Failed.")
        self.check_llm_server_status(); self.db_count_label.setText("| Indexed Chunks: N/A (Query to update)"); logger.info("Deferred initialization complete (SQA App).")

    def create_menus(self):
        # --- Menu creation remains the same ---
        menu_bar = self.menuBar(); file_menu = menu_bar.addMenu("&File"); settings_action = QAction("&Settings...", self); settings_action.triggered.connect(self.open_settings_dialog)
        exit_action = QAction("E&xit", self); exit_action.triggered.connect(self.close); file_menu.addAction(settings_action); file_menu.addSeparator(); file_menu.addAction(exit_action)
        help_menu = menu_bar.addMenu("&Help"); about_action = QAction("&About SQA-O&G", self); about_action.triggered.connect(self.show_about_dialog); help_menu.addAction(about_action)

    def check_llm_server_status(self):
        # --- Server status check remains the same ---
        llm_server_url_setting = self.settings.get('llm_server_url', config_sqa.DEFAULT_LLM_SERVER_URL)
        status_text = "LLM Server: Checking..."; llm_ok = False; self.llm_status_label.setText(status_text); QApplication.processEvents()
        if not llm_server_url_setting: status_text = "LLM Server: Not Configured"; logger.warning("LLM Server URL not configured.")
        else:
            base_url = llm_server_url_setting
            if "/generate" in base_url: base_url = base_url.rsplit('/generate', 1)[0]
            base_url = base_url.rstrip('/'); ping_url = f"{base_url}/docs" # Check /docs endpoint
            logger.debug(f"Pinging LLM server documentation at: {ping_url}")
            try:
                response = requests.get(ping_url, timeout=3.0); response.raise_for_status()
                status_text = f"LLM Server: Connected"; logger.info(f"LLM Server check to {ping_url} OK."); llm_ok = True
            except requests.exceptions.RequestException as e: status_text = "LLM Server: Disconnected"; logger.warning(f"LLM Server check to {ping_url} failed: {e}")
            except Exception as e_unknown: status_text = "LLM Server: Check Error"; logger.error(f"Unknown error checking {ping_url}: {e_unknown}")
        self.llm_status_label.setText(status_text)
        self.ask_button.setEnabled(self.embedding_model is not None and llm_ok)

    def clear_all(self):
        # --- Clear remains the same ---
        self.question_input.clear(); self.answer_browser.clear(); self.context_list.clear()
        self.image_display_label.setText("Image area cleared."); self.image_display_label.setPixmap(QPixmap())
        self.current_image_paths = []; self.current_image_index = -1; self.update_image_navigation()
        self.status_label.setText("Ready."); self.check_llm_server_status()

    # --- >> MODIFIED ask_sqa to use RAGWorker (threading.Thread) << ---
    def ask_sqa(self):
        """Handles the 'Ask SQA' button click. Validates inputs and starts RAGWorker."""
        question = self.question_input.toPlainText().strip()
        db_path_to_use = self.settings.get('chroma_db_path')
        collection_name_to_use = config_sqa.COLLECTION_NAME
        llm_server_url_to_use = self.settings.get('llm_server_url')

        # Validations
        if not question: QMessageBox.warning(self, "Input Error", "Please enter a question."); return
        if not self.embedding_model: QMessageBox.critical(self, "Error", "Embedding model not loaded."); return
        if not Path(db_path_to_use).is_dir(): QMessageBox.critical(self, "Error", f"ChromaDB path invalid:\n{db_path_to_use}"); return
        if not llm_server_url_to_use: QMessageBox.critical(self, "Error", "LLM Server URL not configured."); self.check_llm_server_status(); return

        # Prevent starting another worker if one is already running
        if self.rag_worker_thread and self.rag_worker_thread.is_alive():
            logger.warning("ask_sqa called while previous worker is still running.")
            return

        self.ask_button.setEnabled(False); self.clear_button.setEnabled(False)
        self.answer_browser.clear(); self.context_list.clear()
        self.image_display_label.setText("Loading images..."); self.image_display_label.setPixmap(QPixmap())
        self.current_image_paths = []; self.current_image_index = -1; self.update_image_navigation()
        self.status_label.setText("Processing query..."); self.db_count_label.setText("| Indexed Chunks: Querying...")

        # Create and start the RAGWorker thread
        logger.info("Starting RAGWorker thread...")
        self.rag_worker_thread = RAGWorker(
            question=question,
            db_path_str=db_path_to_use,
            collection_name_str=collection_name_to_use,
            embedding_model=self.embedding_model,
            llm_server_url=llm_server_url_to_use, # Full endpoint URL needed by worker
            top_k=self.settings.get('top_k', config_sqa.DEFAULT_TOP_K_RESULTS),
            callback_fn=self.handle_rag_worker_result, # Pass the callback method
            parent_pid=os.getpid()
        )
        self.rag_worker_thread.start() # Start the Python thread
    # --- >> END MODIFICATION << ---

    # --- >> NEW: Callback function to handle results from RAGWorker << ---
    # This method will be called by RAGWorker when it finishes.
    # IMPORTANT: It runs in the RAGWorker's thread context!
    def handle_rag_worker_result(self, success, result_data, error_string):
        """Callback executed by RAGWorker thread upon completion."""
        logger.info(f"handle_rag_worker_result received. Success: {success}")
        # Use QTimer.singleShot to schedule GUI updates on the main thread
        QTimer.singleShot(0, lambda: self._update_gui_from_worker(success, result_data, error_string))

    # --- >> NEW: Slot executed by QTimer in the GUI thread << ---
    @Slot(bool, object, str) # Use 'object' for the flexible result_data dictionary
    def _update_gui_from_worker(self, success, result_data, error_string):
        """Updates the GUI based on results from RAGWorker. Runs in GUI thread."""
        logger.debug(f"Updating GUI from worker result. Success: {success}")
        self.is_rag_processing = False # Mark processing as done conceptually

        if success and result_data:
            # Update GUI elements with successful results
            self.answer_browser.setPlainText(result_data.get("answer", "(No answer generated)"))
            self.display_context(result_data.get("context", [])) # Use existing display method
            self.display_images(result_data.get("images", []))   # Use existing display method
            self.update_db_info_label(result_data.get("db_count", -1)) # Update count
            self.status_label.setText("Ready.")
        else:
            # Handle errors reported by the worker
            self.status_label.setText(f"Error: {error_string[:100] if error_string else 'Processing Failed'}")
            if error_string: # Show detailed error if provided
                 QMessageBox.warning(self, "Processing Error", error_string)
            else: # Generic failure message
                 QMessageBox.warning(self, "Processing Error", "The query could not be completed successfully.")
            # Clear results areas on error
            self.answer_browser.clear()
            self.context_list.clear()
            self.image_display_label.setText("Error during processing.")
            self.image_display_label.setPixmap(QPixmap())
            self.current_image_paths = []; self.current_image_index = -1
            self.update_image_navigation()
            self.db_count_label.setText("| Indexed Chunks: Error")


        # Re-enable buttons and re-check LLM status
        self.ask_button.setEnabled(True); self.clear_button.setEnabled(True)
        self.check_llm_server_status()
        self.rag_worker_thread = None # Clear reference after processing result
    # --- >> END NEW SLOTS << ---

    # --- REMOVED Slots previously connected to RAGThread QThread signals ---
    # @Slot(str) def update_status(self, message): ...
    # @Slot(list) def display_context(self, source_chunks): ... (Now called by _update_gui_from_worker)
    # @Slot(list) def display_images(self, image_paths): ... (Now called by _update_gui_from_worker)
    # @Slot(str) def append_text_chunk(self, text_piece): ... (No streaming with this RAGWorker)
    # @Slot() def on_stream_finished(self): ... (No streaming with this RAGWorker)
    # @Slot(bool) def on_rag_processing_complete(self, success): ... (Replaced by callback)
    # @Slot() def on_thread_truly_finished(self): ... (Replaced by callback)
    # @Slot(str) def handle_rag_error(self, error_message): ... (Replaced by callback)
    # @Slot(int) def update_db_info_label(self, count): ... (Now called by _update_gui_from_worker)
    # --- END REMOVALS ---

    # Keep methods that are called directly by UI elements or other parts
    def load_image(self, index):
        if not (0 <= index < len(self.current_image_paths)): self.image_display_label.setText("No image selected."); self.image_display_label.setPixmap(QPixmap()); self.update_image_navigation(); return
        path_str = self.current_image_paths[index]; path = Path(path_str)
        if not path.is_file(): logger.error(f"Image file not found: {path_str}"); self.image_display_label.setText(f"Image not found:\n{path.name}"); self.image_display_label.setPixmap(QPixmap()); self.update_image_navigation(); return
        try:
            img = Image.open(path); img = ImageOps.exif_transpose(img)
            if img.mode == "RGBA": qimage = QImage(img.tobytes("raw", "BGRA"), img.width, img.height, QImage.Format.Format_ARGB32)
            elif img.mode == "RGB": qimage = QImage(img.tobytes("raw", "BGR"), img.width, img.height, QImage.Format.Format_RGB888)
            elif img.mode == "L": qimage = QImage(img.tobytes("raw", "L"), img.width, img.height, QImage.Format.Format_Grayscale8)
            else: logger.warning(f"Converting image mode '{img.mode}'"); img = img.convert("RGBA"); qimage = QImage(img.tobytes("raw", "BGRA"), img.width, img.height, QImage.Format.Format_ARGB32)
            pixmap = QPixmap.fromImage(qimage); self.image_display_label.setPixmap(pixmap); self.image_display_label.resize(pixmap.size())
            self.image_display_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self.image_display_label.setToolTip(path_str)
        except UnidentifiedImageError: logger.error(f"Cannot identify image: {path_str}"); self.image_display_label.setText(f"Error: Cannot display\n{path.name}"); self.image_display_label.setPixmap(QPixmap())
        except Exception as e: logger.error(f"Error loading image {path_str}: {e}"); self.image_display_label.setText(f"Error loading\n{path.name}"); self.image_display_label.setPixmap(QPixmap())
        self.update_image_navigation()

    def update_image_navigation(self):
        total = len(self.current_image_paths); current = self.current_image_index + 1 if total > 0 else 0
        self.image_nav_label.setText(f"Image {current} of {total}")
        self.prev_image_button.setEnabled(self.current_image_index > 0)
        self.next_image_button.setEnabled(self.current_image_index < total - 1)

    def show_prev_image(self):
        if self.current_image_index > 0: self.current_image_index -= 1; self.load_image(self.current_image_index)
    def show_next_image(self):
        if self.current_image_index < len(self.current_image_paths) - 1: self.current_image_index += 1; self.load_image(self.current_image_index)

    def open_source_document(self, item):
        item_data = item.data(Qt.ItemDataRole.UserRole);
        if not item_data or 'metadata' not in item_data: return
        metadata = item_data['metadata']; source_file_name = metadata.get('source')
        if not source_file_name: return
        try:
            db_dir = Path(self.settings.get('chroma_db_path')); potential_source_folder = db_dir.parent / "pdfs_to_process"
            file_path_to_open = potential_source_folder / source_file_name
            if file_path_to_open.is_file(): logger.info(f"Attempting to open source file: {file_path_to_open}"); QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path_to_open)))
            else: logger.warning(f"Original file '{source_file_name}' not found at: {file_path_to_open}"); QMessageBox.warning(self, "File Not Found", f"Could not find source file:\n{source_file_name}")
        except Exception as e: logger.error(f"Error opening source file {source_file_name}: {e}"); QMessageBox.warning(self, "Error Opening File", f"Could not open:\n{source_file_name}\n\nError: {e}")

    def open_settings_dialog(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            logger.info("Settings updated by user.")
            self.settings = dialog.get_settings()
            config_sqa.save_settings(self.settings)
            self.check_llm_server_status()
            self.db_count_label.setText("| Indexed Chunks: N/A (Query to update)")
            self.status_label.setText("Settings updated. Ready.")

    def show_about_dialog(self):
        llm_info = f"LLM Server at {self.settings.get('llm_server_url', 'Not Configured')}"
        about_text = f"""<b>SQA-O&G: Standard Query Assistant</b><br><br>
Developed for: Dexterity Design Services<br>
Developed By: Vikram<br><br>
© 2024<br><br>
This tool uses ChromaDB and a separate LLM inference server ({llm_info}) to answer questions based on indexed documents."""
        QMessageBox.about(self, "About SQA-O&G", about_text)

    def closeEvent(self, event):
        # Stop the worker thread if it's running
        if self.rag_worker_thread and self.rag_worker_thread.is_alive():
            logger.warning("Window close requested during RAG processing. Attempting to stop worker thread...")
            self.rag_worker_thread.stop() # Signal the thread to stop
            # Give it a short time to finish naturally after the stop signal
            self.rag_worker_thread.join(timeout=1.0) # Wait 1 second
            if self.rag_worker_thread.is_alive():
                 logger.warning("RAG worker thread did not exit cleanly after stop request.")
                 # Since it's a daemon thread, it should exit when main app exits anyway
        event.accept() # Allow window to close