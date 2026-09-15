@echo off
rem ---------------------------------------------------------------------
rem  Build de l'exécutable Windows (icône barre système, sans console).
rem  À lancer depuis un venv dédié au build (voir README, section
rem  "Application Windows") : le même interpréteur que le reste du projet.
rem ---------------------------------------------------------------------
setlocal
cd /d "%~dp0\.."

if not exist ".venv-build\Scripts\python.exe" (
  echo Creation du venv de build...
  python -m venv .venv-build
)

".venv-build\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv-build\Scripts\python.exe" -m pip install -r requirements-app.txt
".venv-build\Scripts\python.exe" -m pip install pyinstaller

".venv-build\Scripts\python.exe" -m PyInstaller packaging\transcription.spec --noconfirm

echo.
echo Build termine : dist\Transcription\Transcription.exe
endlocal
