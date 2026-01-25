@echo off
chcp 1251 >nul
color 0A
title Discord Token Manager + Ngrok

echo ================================
echo Discord Token Manager + Ngrok
echo ================================
echo.

:: Проверка наличия ngrok
where ngrok >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Ngrok не найден!
    echo.
    echo Пожалуйста, установите Ngrok:
    echo 1. Скачайте: https://ngrok.com/download
    echo 2. Распакуйте ngrok.exe в папку с проектом
    echo 3. Запустите: ngrok config add-authtoken YOUR_TOKEN
    echo.
    pause
    exit /b 1
)

echo [OK] Ngrok найден
echo.

:: Запуск основного скрипта в отдельном окне
echo [INFO] Запуск Token Manager...
start "Discord Token Manager" cmd /k "py main.py --dashboard"

:: Ждем 5 секунд пока Flask запустится
echo [INFO] Ожидание запуска Flask (5 секунд)...
timeout /t 5 /nobreak >nul

:: Запуск ngrok
echo.
echo [INFO] Запуск Ngrok туннеля...
echo.
echo ===============================================
echo   ИНСТРУКЦИЯ:
echo   1. Дождитесь строки "Forwarding"
echo   2. Скопируйте URL (например: https://abc123.ngrok.io)
echo   3. Откройте этот URL на телефоне
echo ===============================================
echo.

ngrok http 5000

:: Если ngrok закрылся - спрашиваем что делать
echo.
echo Ngrok остановлен.
pause