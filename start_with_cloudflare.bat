@echo off
chcp 65001 >nul
color 0A
title Discord Token Manager

echo ================================
echo Starting Discord Token Manager
echo ================================
echo.

:: Check for cloudflared
where cloudflared.exe >nul 2>nul
if errorlevel 1 (
    echo ERROR: cloudflared.exe not found!
    echo.
    echo Please download from:
    echo https://github.com/cloudflare/cloudflared/releases
    echo.
    pause
    exit /b 1
)

echo [+] Cloudflared found
echo [i] Starting Flask app...
start "Flask App" cmd /k "py main.py --dashboard"

timeout /t 5 /nobreak >nul

echo.
echo [i] Starting Cloudflare tunnel...
echo ====================================
echo   Tunnel will start in 3 seconds...
echo   Copy the URL when it appears
echo ====================================
echo.

timeout /t 3 /nobreak >nul
cloudflared.exe tunnel --url http://localhost:5000

echo.
pause
