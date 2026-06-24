@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM   INSTALLATION — Démarrage automatique au boot Windows
REM   À exécuter UNE SEULE FOIS (en administrateur)
REM ═══════════════════════════════════════════════════════════════════════

echo.
echo ============================================
echo   Installation Demarrage Automatique
echo   TontineBot Pro v9.18
echo ============================================
echo.

set "TONTINEBOT_DIR=%~dp0"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

REM Créer un raccourci .lnk vers DEMARRAGE.bat dans le dossier Startup
echo Creation du raccourci de demarrage automatique...
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%TEMP%\install_startup.vbs"
echo sLinkFile = "%STARTUP_DIR%\TontineBot_Pro.lnk" >> "%TEMP%\install_startup.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%TEMP%\install_startup.vbs"
echo oLink.TargetPath = "%TONTINEBOT_DIR%DEMARRAGE.bat" >> "%TEMP%\install_startup.vbs"
echo oLink.WorkingDirectory = "%TONTINEBOT_DIR%" >> "%TEMP%\install_startup.vbs"
echo oLink.Description = "TontineBot Pro v9.18 - BADF Ltd" >> "%TEMP%\install_startup.vbs"
echo oLink.Save >> "%TEMP%\install_startup.vbs"
cscript //nologo "%TEMP%\install_startup.vbs"
del "%TEMP%\install_startup.vbs"

if exist "%STARTUP_DIR%\TontineBot_Pro.lnk" (
    echo.
    echo ============================================
    echo   INSTALLATION REUSSIE
    echo ============================================
    echo.
    echo Le bot demarrera automatiquement a chaque
    echo allumage du PC.
    echo.
    echo Pour desinstaller, supprimer :
    echo %STARTUP_DIR%\TontineBot_Pro.lnk
    echo.
) else (
    echo.
    echo ============================================
    echo   ECHEC INSTALLATION
    echo ============================================
    echo.
    echo Verifier les permissions du dossier :
    echo %STARTUP_DIR%
    echo.
)

pause
