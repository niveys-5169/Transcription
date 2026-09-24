@echo off
rem ---------------------------------------------------------------------
rem  Transcription de cours - lanceur Windows
rem  Premier lancement : cree un environnement Python isole et installe
rem  les dependances. Ensuite, demarre simplement l'application.
rem ---------------------------------------------------------------------
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo   Transcription de cours
echo   ----------------------
echo.

rem --- Trouver un interpreteur Python REELLEMENT installe ---------------
rem  "python.exe" peut etre l'alias du Microsoft Store : il ne fait rien et
rem  n'affiche rien. On ne retient donc un interpreteur que s'il repond
rem  vraiment a "--version". Le lanceur "py" est essaye en premier.
set "PYTHON="
for /f "delims=" %%v in ('py -3 --version 2^>nul') do set "PYTHON=py -3"
if not defined PYTHON (
  for /f "delims=" %%v in ('python --version 2^>nul') do set "PYTHON=python"
)
if not defined PYTHON (
  echo   [X] Python n'a pas ete trouve sur cette machine.
  echo.
  echo   Installez Python 3.10 ou plus recent depuis https://www.python.org/downloads/
  echo   en cochant "Add python.exe to PATH" pendant l'installation.
  echo.
  pause
  exit /b 1
)
echo   Python detecte : !PYTHON!

rem --- Mise a jour du code -----------------------------------------------
rem  Si ce dossier est un clone git, on recupere la derniere version avant
rem  de demarrer : plus besoin de recompiler l'exe a chaque changement.
rem  Un echec (hors ligne, modifications locales en conflit) n'empeche pas
rem  le lancement : on demarre simplement la version deja presente.
if exist ".git" (
  where git >nul 2>&1
  if errorlevel 1 (
    echo   [i] git introuvable : mise a jour automatique ignoree.
  ) else (
    echo   Recherche de mises a jour...
    git pull --ff-only
    if errorlevel 1 (
      echo   [~] Mise a jour impossible, lancement de la version actuelle.
    )
  )
)

rem --- Dossier de donnees -------------------------------------------------
rem  L'exe range ses donnees dans %LOCALAPPDATA%\Transcription, le mode
rem  source dans .\data. Si seul l'exe a deja servi sur cette machine, on
rem  reutilise ses donnees pour retrouver la meme bibliotheque.
if not defined TRANSCRIPTION_DATA_DIR (
  if not exist "data\transcription.db" (
    if exist "%LOCALAPPDATA%\Transcription\transcription.db" (
      set "TRANSCRIPTION_DATA_DIR=%LOCALAPPDATA%\Transcription"
      echo   Donnees de l'application Windows : %LOCALAPPDATA%\Transcription
    )
  )
)

rem --- Environnement isole ----------------------------------------------
set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo   Creation de l'environnement Python ^(une seule fois^)...
  !PYTHON! -m venv .venv
  if errorlevel 1 (
    echo   [X] Creation de l'environnement impossible.
    pause
    exit /b 1
  )
)

rem --- Dependances -------------------------------------------------------
rem  On garde une copie du requirements-app.txt installe : si une mise a
rem  jour le modifie, les dependances sont reinstallees automatiquement.
set "DEPS_MARKER=.venv\.requirements-installees.txt"
set "DEPS_TO_INSTALL="
if not exist "%DEPS_MARKER%" (
  set "DEPS_TO_INSTALL=1"
) else (
  fc /b requirements-app.txt "%DEPS_MARKER%" >nul 2>&1
  if errorlevel 1 set "DEPS_TO_INSTALL=1"
)
if defined DEPS_TO_INSTALL (
  echo   Installation des dependances ^(quelques minutes la premiere fois^)...
  "%VENV_PY%" -m pip install --upgrade pip --quiet
  "%VENV_PY%" -m pip install -r requirements-app.txt
  if errorlevel 1 (
    echo.
    echo   [X] Installation des dependances impossible ^(connexion Internet ?^).
    pause
    exit /b 1
  )
  copy /y requirements-app.txt "%DEPS_MARKER%" >nul
)

rem --- Lancement ---------------------------------------------------------
echo   Demarrage du serveur...
echo.
"%VENV_PY%" run.py %*
if errorlevel 1 (
  echo.
  echo   [X] L'application s'est arretee sur une erreur.
  pause
  exit /b 1
)

endlocal
