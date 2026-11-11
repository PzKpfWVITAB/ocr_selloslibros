@echo off
chcp 65001 >nul
title Generador de Paquete Portable (.exe + .zip) - OCR Sellos Libros
cd /d "%~dp0"

echo ======================================================================
echo  CONSTRUYENDO PAQUETE PORTABLE (.EXE + DEPENDENCIAS + .ZIP)
echo ======================================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" build_portable.py
) else (
    python build_portable.py
)

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Abriendo la carpeta 'dist' con el ejecutable y el archivo .zip...
    explorer "%~dp0dist"
) else (
    echo.
    echo [ERROR] Hubo un problema durante el empaquetado.
    echo Si faltan dependencias, ejecuta primero 'instalar_entorno.bat'.
)

pause
