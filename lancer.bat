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
if not exist ".venv\.dependances-ok" (
  echo   Installation des dependances ^(quelques minutes la premiere fois^)...
  "%VENV_PY%" -m pip install --upgrade pip --quiet
  "%VENV_PY%" -m pip install -r requirements-app.txt
  if errorlevel 1 (
    echo.
    echo   [X] Installation des dependances impossible ^(connexion Internet ?^).
    pause
    exit /b 1
  )
  echo ok> ".venv\.dependances-ok"
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
