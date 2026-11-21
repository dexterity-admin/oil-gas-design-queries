# settings_dialog_sqa.py
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QDialogButtonBox,
    QFileDialog, QSpinBox, QLabel, QHBoxLayout, QMessageBox
)
from PySide6.QtCore import Slot
from pathlib import Path

import config_sqa # Import your config handling module

class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SQA-O&G Settings")
        self.current_settings = current_settings.copy()
        self.setMinimumWidth(550)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # --- ChromaDB Path Setting ---
        self.chroma_path_edit = QLineEdit(self.current_settings.get('chroma_db_path', ''))
        browse_chroma_button = QPushButton("Browse...")
        browse_chroma_button.clicked.connect(self.browse_chroma_path)
        chroma_layout = QHBoxLayout()
        chroma_layout.addWidget(self.chroma_path_edit)
        chroma_layout.addWidget(browse_chroma_button)
        form_layout.addRow("ChromaDB Path:", chroma_layout)

        # --- >> CHANGED: LLM Server URL Input << ---
        self.llm_server_url_edit = QLineEdit(self.current_settings.get('llm_server_url', config_sqa.DEFAULT_LLM_SERVER_URL))
        self.llm_server_url_edit.setPlaceholderText("e.g., http://127.0.0.1:8000 or http://your-server-ip:8000")
        self.llm_server_url_edit.setToolTip("URL of the running LLM Inference Server (FastAPI).")
        form_layout.addRow("LLM Inference Server URL:", self.llm_server_url_edit)
        # --- >> END CHANGE << ---

        # --- Embedding Model Display (Read-Only) ---
        emb_model_name_display = self.current_settings.get('embedding_model', config_sqa.DEFAULT_EMBEDDING_MODEL)
        self.embedding_model_label = QLabel(f"<i>{emb_model_name_display}</i>")
        self.embedding_model_label.setToolTip("This embedding model was used for indexing and must match.")
        form_layout.addRow("Embedding Model (Indexer):", self.embedding_model_label)

        # --- Top K Results Setting ---
        self.top_k_spinbox = QSpinBox()
        self.top_k_spinbox.setMinimum(1); self.top_k_spinbox.setMaximum(20)
        self.top_k_spinbox.setToolTip("Number of relevant text chunks to retrieve for LLM context.")
        try: top_k_value = int(self.current_settings.get('top_k', config_sqa.DEFAULT_TOP_K_RESULTS))
        except (ValueError, TypeError): top_k_value = config_sqa.DEFAULT_TOP_K_RESULTS
        self.top_k_spinbox.setValue(top_k_value); form_layout.addRow("Context Chunks (Top K):", self.top_k_spinbox)

        layout.addLayout(form_layout)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept_settings); button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    @Slot()
    def browse_chroma_path(self):
        start_path = self.chroma_path_edit.text()
        if not Path(start_path).is_dir(): start_path = str(Path.home())
        directory = QFileDialog.getExistingDirectory(self,"Select ChromaDB Directory",start_path)
        if directory: self.chroma_path_edit.setText(directory)

    # Removed browse_local_model_path

    @Slot()
    def accept_settings(self):
        chroma_path = self.chroma_path_edit.text().strip()
        llm_server_url = self.llm_server_url_edit.text().strip().rstrip('/') # Ensure no trailing slash
        top_k = self.top_k_spinbox.value()

        if not Path(chroma_path).is_dir(): QMessageBox.warning(self, "Invalid Path", "ChromaDB path invalid."); return
        if not llm_server_url: QMessageBox.warning(self, "Missing URL", "Please specify the LLM Server URL."); return
        if not (llm_server_url.startswith('http://') or llm_server_url.startswith('https://')):
            QMessageBox.warning(self, "Invalid URL", "LLM Server URL must start with http:// or https://."); return

        self.current_settings['chroma_db_path'] = chroma_path
        self.current_settings['llm_server_url'] = llm_server_url # Store new URL
        self.current_settings['top_k'] = top_k

        if config_sqa.save_settings(self.current_settings): super().accept()
        else: QMessageBox.critical(self, "Save Error", "Failed to save settings.")

    def get_settings(self): return self.current_settings