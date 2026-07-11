@echo off
setlocal
title Hyperliquid Agent - Arret
cd /d "%~dp0"
echo Arret de l'application Hyperliquid Agent...
docker compose down
if errorlevel 1 (
  echo [ERREUR] Impossible d'arreter l'application.
  pause
  exit /b 1
)
echo Application arretee. Les donnees PostgreSQL sont conservees.
timeout /t 3 /nobreak >nul
endlocal
