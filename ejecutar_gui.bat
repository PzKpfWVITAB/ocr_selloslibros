@echo off
chcp 65001 >nul
title OCR Sellos y Libros Registrales
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app_gui.py %*
) else (
    python app_gui.py %*
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [AVISO] Ocurrió un error al iniciar la aplicación.
    echo Si es una PC nueva o faltan dependencias, ejecuta primero 'instalar_entorno.bat'.
    echo.
    pause
)
