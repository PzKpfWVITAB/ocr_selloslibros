@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Instalacion Automatica - OCR Sellos Libros Registrales
cd /d "%~dp0"

echo ======================================================================
echo   INSTALADOR AUTOMATICO DE ENTORNO Y DEPENDENCIAS - PC NUEVA
echo   OCR Sellos y Libros Registrales - Claude Sonnet 5
echo ======================================================================
echo.

:: 1. Verificar si Python esta instalado
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_CMD=python"
    goto :python_found
)

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PY_CMD=py"
    goto :python_found
)

echo [ERROR] No se encontro Python instalado en este equipo.
echo.
echo Por favor instala Python 3.10 o superior desde:
echo https://www.python.org/downloads/
echo.
echo IMPORTANTE: Durante la instalacion, asegurate de marcar la casilla:
echo   [X] Add Python to PATH
echo.
pause
exit /b 1

:python_found
echo [1/5] Verificando version de Python en el sistema...
%PY_CMD% --version
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Fallo la verificacion de Python.
    pause
    exit /b 1
)
echo.

:: 2. Crear entorno virtual (.venv) si no existe
echo [2/5] Configurando entorno virtual .venv...
if exist ".venv\Scripts\python.exe" goto :venv_exists
echo Creando entorno virtual aislado en .venv...
%PY_CMD% -m venv .venv
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] No se pudo crear el entorno virtual.
    pause
    exit /b 1
)
echo Entorno virtual creado exitosamente.
goto :venv_ready

:venv_exists
echo El entorno virtual .venv ya existe.

:venv_ready
echo.

:: 3. Actualizar pip e instalar dependencias de requirements.txt
echo [3/5] Instalando dependencias necesarias...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Ocurrio un error al instalar las dependencias de requirements.txt.
    pause
    exit /b 1
)
echo Dependencias instaladas correctamente.
echo.

:: 4. Inicializar .env y settings.json si no existen
echo [4/5] Verificando archivos de configuracion .env y settings.json...
if not exist ".env" if exist ".env.example" copy /y ".env.example" ".env" >nul
if exist ".env" echo Archivo .env verificado.

if exist "settings.json" goto :settings_ok
if exist "settings.example.json" (
    copy /y "settings.example.json" "settings.json" >nul
    echo Archivo settings.json creado desde settings.example.json.
    goto :settings_ok
)
echo Creando settings.json con valores predeterminados...
python -c "import json; open('settings.json','w',encoding='utf-8').write('{}')"

:settings_ok
echo Archivos de configuracion verificados.
echo.

:: 5. Ejecutar suite de pruebas unitarias para validar
echo [5/5] Ejecutando pruebas unitarias de verificacion...
python run_tests.py --unit
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ADVERTENCIA] Algunas pruebas reportaron avisos. Revisa la salida anterior.
    goto :fin_pruebas
)
echo.
echo ======================================================================
echo   INSTALACION COMPLETADA EXITOSAMENTE EN ESTA PC
echo ======================================================================
echo.
echo Todo esta listo para usar:
echo   * Para abrir el sistema: Ejecuta 'ejecutar_gui.bat' o 'python app_gui.py'.
echo   * Para generar el paquete portable: Ejecuta 'generar_portable.bat'.
echo.

:fin_pruebas
set "RESP="
set /p "RESP=Deseas iniciar la aplicacion ahora mismo? [S/N]: "
if /i "%RESP%"=="S" start "" "ejecutar_gui.bat"

exit /b 0
