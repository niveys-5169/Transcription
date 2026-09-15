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
  if errorlevel 1 (
    echo.
    echo [X] Creation du venv de build impossible.
    pause
    exit /b 1
  )
)

echo Installation des dependances...
".venv-build\Scripts\python.exe" -m pip install --upgrade pip --quiet
if errorlevel 1 (
  echo.
  echo [X] Mise a jour de pip impossible.
  pause
  exit /b 1
)

".venv-build\Scripts\python.exe" -m pip install -r requirements-app.txt
if errorlevel 1 (
  echo.
  echo [X] Installation des dependances de l'application impossible.
  pause
  exit /b 1
)

".venv-build\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 (
  echo.
  echo [X] Installation de PyInstaller impossible.
  pause
  exit /b 1
)

echo.
echo Construction de l'executable...
".venv-build\Scripts\python.exe" -m PyInstaller packaging\transcription.spec --noconfirm
if errorlevel 1 (
  echo.
  echo [X] PyInstaller a echoue - voir le message d'erreur ci-dessus.
  pause
  exit /b 1
)

echo.
echo Build termine : dist\Transcription\Transcription.exe
pause
endlocal
