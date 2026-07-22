@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM   TontineBot Pro v9.18 — DEMARRAGE v2 — Green API
REM   BADF Ltd — Cameroun
REM
REM   Architecture : Python Flask + Green API WhatsApp (compte perso, scan QR)
REM
REM   Ce script :
REM     1. Vérifie que PostgreSQL tourne (redémarre si arrêté)
REM     2. Vérifie que le port 5000 est libre
REM     3. Lance ngrok en arrière-plan (tunnel public pour webhook Green API)
REM     4. Lance le watchdog Node.js qui gère le bot Python
REM     5. Auto-restart en boucle infinie si le watchdog crash
REM
REM   Usage : double-clic, OU placer dans dossier Démarrage Windows
REM ═══════════════════════════════════════════════════════════════════════

cd /d "%~dp0"
title TontineBot Pro v9.18 — BADF Ltd

REM ── Créer le dossier logs si absent ───────────────────────────────────
if not exist "logs" mkdir logs

REM ── En-tête log ────────────────────────────────────────────────────────
echo. >> logs\autostart.log
echo ============================================ >> logs\autostart.log
echo  DEMARRAGE %date% %time% >> logs\autostart.log
echo ============================================ >> logs\autostart.log

echo.
echo ============================================
echo   TontineBot Pro v9.18 — BADF Ltd
echo   Stack : Python + Green API WhatsApp
echo ============================================
echo.

REM ── 1. Vérifier PostgreSQL ────────────────────────────────────────────
echo [1/4] Verification PostgreSQL...
sc query postgresql-x64-18 | findstr /C:"RUNNING" >nul
if errorlevel 1 (
    echo    PostgreSQL arrete, demarrage en cours...
    net start postgresql-x64-18 >> logs\autostart.log 2>&1
    timeout /t 3 /nobreak >nul
) else (
    echo    PostgreSQL actif.
)

REM ── 2. Libérer le port 5000 (au cas où un ancien process traine) ──────
echo [2/4] Verification port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do (
    echo    Port 5000 occupe par PID %%a, kill en cours...
    taskkill /F /PID %%a >nul 2>&1
)
echo    Port 5000 libre.

REM ── 3. Lancer ngrok en arrière-plan ────────────────────────────────────
echo [3/4] Lancement ngrok...
tasklist | findstr /I "ngrok.exe" >nul
if errorlevel 1 (
    start "ngrok" /MIN cmd /c "ngrok http --domain=lennox-unbiographical-jasmin.ngrok-free.dev 5000 >> logs\ngrok.log 2>&1"
    timeout /t 3 /nobreak >nul
    echo    ngrok lance.
) else (
    echo    ngrok deja actif.
)

REM ── Charger les variables d'environnement depuis ENV ─────────────────
echo [ENV] Chargement variables d'environnement...
if exist "ENV" (
    for /f "usebackq tokens=1,* delims== eol=#" %%A in ("ENV") do (
        if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
    )
    echo    Variables chargees depuis ENV.
) else (
    echo    ATTENTION : fichier ENV introuvable ! Green API ne fonctionnera pas.
)

REM ── 4. Lancer le watchdog Node.js (qui lance Python à son tour) ───────
echo [4/4] Lancement watchdog...
echo.

:LOOP_WATCHDOG
echo ============================================
echo  WATCHDOG ACTIF %date% %time%
echo ============================================
echo.

REM Lancer le watchdog. Si crash, retry après 5s.
node watchdog.js
echo.
echo [%date% %time%] Watchdog arrete, redemarrage dans 5s...
echo [%date% %time%] Watchdog arrete >> logs\autostart.log
timeout /t 5 /nobreak >nul
goto LOOP_WATCHDOG
