@echo off
rem ---------------------------------------------------------------------
rem  Build de l'exécutable Windows (icône barre système, sans console).
rem  À lancer depuis un venv dédié au build (voir README, section
rem  "Application Windows") : le même interpréteur que le reste du projet.
rem ---------------------------------------------------------------------
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

if not exist ".venv-build\Scripts\python.exe" (
  rem ctranslate2 (dependance de faster-whisper) n'a pas forcement de build
  rem pour les toutes dernieres versions de Python : on cherche 3.12/3.11
  rem explicitement plutot que d'utiliser "python" (qui peut pointer sur une
  rem version plus recente, ex. 3.14, non supportee).
  set "BUILD_PYTHON="
  for %%v in (3.12 3.11 3.13) do (
    if not defined BUILD_PYTHON (
      for /f "delims=" %%p in ('py -%%v --version 2^>nul') do set "BUILD_PYTHON=py -%%v"
    )
  )
  if not defined BUILD_PYTHON (
    echo.
    echo [X] Aucun Python 3.11/3.12/3.13 trouve ^(py -3.12, py -3.11...^).
    echo     Installez-en un depuis https://www.python.org/downloads/ et
    echo     relancez ce script : ctranslate2 ^(dependance de faster-whisper^)
    echo     n'a souvent pas encore de build pour la toute derniere version
    echo     de Python ^(ex. 3.14^).
    pause
    exit /b 1
  )
  echo Python de build detecte : !BUILD_PYTHON!
  echo Creation du venv de build...
  !BUILD_PYTHON! -m venv .venv-build
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
