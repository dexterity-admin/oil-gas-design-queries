# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# --- Determine Model Paths ---
model_src_path = r'D:\Py\Oil & Gas Design Queries\models\models--sentence-transformers--all-MiniLM-L6-v2\snapshots\c9745ed1d9f207416be6d2e6f8de32d1f16199bf'
model_dest_basename_in_bundle = 'all-MiniLM-L6-v2'
model_dest_path = os.path.join('models', model_dest_basename_in_bundle)
if not os.path.exists(model_src_path): print(f"FATAL ERROR: Model source path not found: '{model_src_path}'"); sys.exit(1)
else: print(f"Model source path: '{model_src_path}' -> '{model_dest_path}'")

block_cipher = None

# --- Data files to bundle ---
datas = [
    (model_src_path, model_dest_path),
    # ('test.pdf', '.') # Removed for Step 6
]

# --- Binaries ---
binaries = []
# from PyInstaller.utils.hooks import collect_dynamic_libs
# binaries += collect_dynamic_libs('fitz') # Add only if fitz DLLs cause issues

# --- Hidden Imports ---
hiddenimports = [
    # GUI
    'PySide6.QtCore','PySide6.QtGui','PySide6.QtWidgets','PySide6.QtSvg',
    'pkg_resources.py2_warn',
    # ML Stack
    'sentence_transformers','transformers','transformers.models','transformers.models.auto',
    'torch','torch.nn','huggingface_hub','safetensors','numpy',
    # DB Stack
    'chromadb','chromadb.api','chromadb.api.models','chromadb.config','hnswlib',
    'sqlite3','uuid','chromadb.telemetry','chromadb.telemetry.product',
    'chromadb.telemetry.product.posthog','posthog','chromadb.api.rust',
    # OCR Stack
    'pytesseract','PIL',
    # PDF Stack
    'fitz','fitz.fitz',
    # Standard libs sometimes missed
    'time', 're', 'io', 'uuid', 'traceback',
]

a = Analysis(
    ['run_indexing_gui.py'], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter','matplotlib','IPython','notebook','jupyter_client','pytest'],
    win_no_prefer_redirects=False, win_private_assemblies=False, cipher=block_cipher, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [], [], [], name='OG_Indexing_Tool', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=True, runtime_tmpdir=None,
    console=False, # KEEP TRUE for Dry Run Test
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=True, upx_exclude=[],
    name='OG_Indexing_Tool_Bundle'
)