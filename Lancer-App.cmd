@echo off
setlocal EnableDelayedExpansion
title Hyperliquid Agent - Demarrage
cd /d "%~dp0"

echo.
echo  Hyperliquid Agent
echo  =================

if not exist ".env" (
  echo [ERREUR] Le fichier .env est introuvable.
  echo Creez-le et ajoutez vos cles avant de relancer.
  pause
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo [1/4] Demarrage de Docker Desktop...
  if not exist "C:\Program Files\Docker\Docker\Docker Desktop.exe" (
    echo [ERREUR] Docker Desktop n'est pas installe.
    pause
    exit /b 1
  )
  start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  set /a attempts=0
  :wait_docker
  timeout /t 3 /nobreak >nul
  docker info >nul 2>&1
  if not errorlevel 1 goto docker_ready
  set /a attempts+=1
  if !attempts! GEQ 40 (
    echo [ERREUR] Docker Desktop n'a pas demarre apres 2 minutes.
    pause
    exit /b 1
  )
  goto wait_docker
)

:docker_ready
echo [2/4] Construction et lancement de l'application...
docker compose up -d --build
if errorlevel 1 (
  echo [ERREUR] Le lancement Docker a echoue.
  docker compose logs --tail 40
  pause
  exit /b 1
)

echo [3/4] Attente de l'API...
set /a health_attempts=0
:wait_api
for /f "delims=" %%S in ('docker inspect -f "{{.State.Status}}" hyperliquidagents-api-1 2^>nul') do set "api_status=%%S"
if "!api_status!"=="exited" (
  echo [ERREUR] L'API s'est arretee au demarrage. Derniers journaux :
  docker compose logs api --tail 60
  pause
  exit /b 1
)
powershell -NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing http://localhost:8000/api/health -TimeoutSec 2).StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 goto api_ready
timeout /t 2 /nobreak >nul
set /a health_attempts+=1
if !health_attempts! GEQ 45 (
  echo [ERREUR] L'API ne repond pas. Derniers journaux :
  docker compose logs api --tail 60
  pause
  exit /b 1
)
goto wait_api

:api_ready
echo [4/4] Ouverture du dashboard...
start "" "http://localhost:4173"
echo.
echo Application lancee : http://localhost:4173
echo Cette fenetre peut etre fermee.
timeout /t 5 /nobreak >nul
endlocal
