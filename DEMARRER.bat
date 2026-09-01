@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Prototype - Planification des gardes psychiatriques
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo.
echo ============================================================
echo   PLANIFICATION DES GARDES PSYCHIATRIQUES
echo   Prototype de demonstration - donnees entierement fictives
echo ============================================================
echo.
echo Cette fenetre va preparer puis lancer le prototype.
echo La premiere fois, comptez quelques minutes.
echo.

REM ------------------------------------------------------------
REM 1. Trouver Python
REM ------------------------------------------------------------
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY goto :pas_de_python

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto :python_trop_ancien

REM ------------------------------------------------------------
REM 2. Preparer l'environnement isole
REM ------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Preparation de l'environnement...
    %PY% -m venv .venv
    if errorlevel 1 goto :erreur_venv
) else (
    echo [1/4] Environnement deja pret.
)

set "VPY=.venv\Scripts\python.exe"

REM ------------------------------------------------------------
REM 3. Installer les composants
REM ------------------------------------------------------------
"%VPY%" -c "import fastapi, uvicorn, sqlalchemy, jinja2" >nul 2>&1
if errorlevel 1 (
    echo [2/4] Installation des composants ^(connexion Internet requise^)...
    "%VPY%" -m pip install --upgrade pip --quiet
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 goto :erreur_installation
) else (
    echo [2/4] Composants deja installes.
)

REM ------------------------------------------------------------
REM 4. Creer le jeu de demonstration
REM ------------------------------------------------------------
if not exist "gardes.db" (
    echo [3/4] Creation du jeu de demonstration...
    echo.
    "%VPY%" -X utf8 scripts\seed_demo.py
    if errorlevel 1 goto :erreur_demo
    echo.
) else (
    echo [3/4] Jeu de demonstration deja present.
)

REM ------------------------------------------------------------
REM 5. Lancer
REM ------------------------------------------------------------
echo [4/4] Demarrage de l'application...
echo.
echo ------------------------------------------------------------
echo   Le navigateur va s'ouvrir sur :  http://127.0.0.1:8000
echo.
echo   Connexion :  admin@demo.invalid
echo   Mot de passe :  demo
echo.
echo   Pour arreter : fermez simplement cette fenetre noire.
echo ------------------------------------------------------------
echo.

start "" /min cmd /c "timeout /t 5 >nul & start "" http://127.0.0.1:8000"
"%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo L'application s'est arretee.
pause
exit /b 0

REM ============================================================
REM Messages d'erreur
REM ============================================================

:pas_de_python
echo.
echo ------------------------------------------------------------
echo   PYTHON N'EST PAS INSTALLE SUR CET ORDINATEUR
echo ------------------------------------------------------------
echo.
echo   Le prototype a besoin de Python pour fonctionner.
echo   C'est gratuit et l'installation prend deux minutes.
echo.
echo   1. Ouvrez :  https://www.python.org/downloads/
echo   2. Cliquez sur le gros bouton jaune "Download Python".
echo   3. Lancez le fichier telecharge.
echo   4. IMPORTANT : cochez "Add python.exe to PATH" en bas
echo      de la premiere fenetre, AVANT de cliquer sur "Install Now".
echo   5. Une fois termine, double-cliquez a nouveau sur DEMARRER.bat
echo.
pause
exit /b 1

:python_trop_ancien
echo.
echo ------------------------------------------------------------
echo   LA VERSION DE PYTHON EST TROP ANCIENNE
echo ------------------------------------------------------------
echo.
echo   Il faut Python 3.11 ou plus recent.
echo   Installez la derniere version depuis :
echo      https://www.python.org/downloads/
echo   en cochant "Add python.exe to PATH".
echo.
pause
exit /b 1

:erreur_venv
echo.
echo ------------------------------------------------------------
echo   LA PREPARATION A ECHOUE
echo ------------------------------------------------------------
echo.
echo   L'environnement isole n'a pas pu etre cree.
echo   Cause la plus frequente : le dossier se trouve dans un
echo   emplacement protege ^(Program Files, lecteur reseau...^).
echo.
echo   Essayez de deplacer tout le dossier sur votre Bureau,
echo   puis relancez DEMARRER.bat
echo.
pause
exit /b 1

:erreur_installation
echo.
echo ------------------------------------------------------------
echo   L'INSTALLATION DES COMPOSANTS A ECHOUE
echo ------------------------------------------------------------
echo.
echo   Causes les plus frequentes :
echo     - pas de connexion Internet ;
echo     - un pare-feu ou un proxy d'entreprise bloque le
echo       telechargement des composants.
echo.
echo   Si vous etes sur un poste professionnel, essayez depuis
echo   un ordinateur personnel, ou demandez de l'aide au service
echo   informatique en montrant les lignes rouges ci-dessus.
echo.
pause
exit /b 1

:erreur_demo
echo.
echo ------------------------------------------------------------
echo   LA CREATION DU JEU DE DEMONSTRATION A ECHOUE
echo ------------------------------------------------------------
echo.
echo   Supprimez le fichier "gardes.db" s'il existe,
echo   puis relancez DEMARRER.bat
echo.
pause
exit /b 1
