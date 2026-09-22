# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller : build --onedir, sans console, icône barre système.

À construire depuis le même interpréteur que le reste du projet (Python
3.11, dans un venv dédié au build — voir build.bat) : faster-whisper /
ctranslate2 n'ont pas forcément de wheels pour des versions de Python plus
récentes, et un mélange d'environnements est la première source d'imports
manquants au premier essai.

Onedir plutôt que onefile : faster-whisper/ctranslate2 sont volumineux, un
onefile décompresserait tout à chaque lancement.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

# PyInstaller exécute ce fichier avec exec() : __file__ n'existe pas ici.
# SPECPATH est la variable qu'il injecte lui-même (dossier du .spec).
ROOT = Path(SPECPATH).resolve().parent  # noqa: F821

hiddenimports = (
    collect_submodules("ctranslate2")
    + collect_submodules("tokenizers")
    + collect_submodules("huggingface_hub")
    + collect_submodules("docx")
    + ["anthropic", "googleapiclient"]
)

binaries = collect_dynamic_libs("ctranslate2")

datas = [
    (str(ROOT / "app" / "static"), "app/static"),
    (str(ROOT / "app" / "lexicon" / "mjpm.json"), "app/lexicon"),
] + collect_data_files("imageio_ffmpeg") + collect_data_files("docx")

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Transcription",
    console=False,
    icon=str(ROOT / "app" / "static" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Transcription",
)
