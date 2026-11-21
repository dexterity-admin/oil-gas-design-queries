# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('models\\qwen2.5-0.5b-instruct-q5_k_m.gguf', 'models')]
datas += collect_data_files('sentence_transformers')


a = Analysis(
    ['main_sqa.py'],
    pathex=[],
    binaries=[('D:\\Py\\Oil & Gas Design Queries\\.venv\\Lib\\site-packages\\llama_cpp\\lib\\ggml.dll', 'llama_cpp\\lib'), ('D:\\Py\\Oil & Gas Design Queries\\.venv\\Lib\\site-packages\\llama_cpp\\lib\\ggml-base.dll', 'llama_cpp\\lib'), ('D:\\Py\\Oil & Gas Design Queries\\.venv\\Lib\\site-packages\\llama_cpp\\lib\\ggml-cpu.dll', 'llama_cpp\\lib'), ('D:\\Py\\Oil & Gas Design Queries\\.venv\\Lib\\site-packages\\llama_cpp\\lib\\llama.dll', 'llama_cpp\\lib'), ('D:\\Py\\Oil & Gas Design Queries\\.venv\\Lib\\site-packages\\llama_cpp\\lib\\llava.dll', 'llama_cpp\\lib')],
    datas=datas,
    hiddenimports=['chromadb.telemetry.product.posthog', 'chromadb.api.rust'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SQA-O&G',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SQA-O&G',
)
