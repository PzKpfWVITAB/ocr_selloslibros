@echo off
chcp 65001 >nul
title Instalación Automática - OCR Sellos Libros Registrales
cd /d "%~dp0"

echo ======================================================================
echo   INSTALADOR AUTOMÁTICO DE ENTORNO Y DEPENDENCIAS (PC NUEVA)
echo   OCR Sellos y Libros Registrales - Claude Sonnet 5
echo ======================================================================
echo.

:: 1. Verificar si Python está instalado
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    where py >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] No se encontró Python instalado en este equipo.
        echo.
        echo Por favor instala Python 3.10 o superior desde:
        echo https://www.python.org/downloads/
        echo.
        echo IMPORTANTE: Durante la instalación, asegúrate de marcar la casilla:
        echo   "[X] Add Python to PATH" (Agregar Python al PATH)
        echo.
        pause
        exit /b 1
    )
    set PY_CMD=py
) else (
    set PY_CMD=python
)

echo [1/5] Verificando versión de Python en el sistema...
%PY_CMD% --version
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Falló la verificación de Python.
    pause
    exit /b 1
)
echo.

:: 2. Crear entorno virtual (.venv) si no existe
echo [2/5] Configurando entorno virtual (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual aislado en .venv...
    %PY_CMD% -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo Entorno virtual creado exitosamente.
) else (
    echo El entorno virtual .venv ya existe.
)
echo.

:: 3. Actualizar pip e instalar dependencias de requirements.txt
echo [3/5] Instalando dependencias necesarias (PyMuPDF, OpenCV, RapidOCR, NumPy, PyInstaller)...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Ocurrió un error al instalar las dependencias de requirements.txt.
    pause
    exit /b 1
)
echo Dependencias instaladas correctamente.
echo.

:: 4. Inicializar .env y settings.json si no existen
echo [4/5] Verificando archivos de configuración (.env y settings.json)...
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo Se creó el archivo '.env' desde '.env.example' (ingresa tu ANTHROPIC_API_KEY ahí).
    )
) else (
    echo '.env' ya existe y fue conservado.
)

if not exist "settings.json" (
    if exist "settings.example.json" (
        copy /y "settings.example.json" "settings.json" >nul
        echo Se creó 'settings.json' preconfigurado.
    ) else (
        echo Creando settings.json con valores predeterminados...
        python -c "from src.core.settings_manager import SettingsManager; SettingsManager.load()"
    )
) else (
    echo 'settings.json' ya existe y fue conservado.
)
echo.

:: 5. Ejecutar suite de pruebas unitarias para validar
echo [5/5] Ejecutando pruebas unitarias de verificación...
python run_tests.py --unit
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ADVERTENCIA] Algunas pruebas reportaron avisos. Revisa la salida anterior.
) else (
    echo.
    echo ======================================================================
    echo   ¡INSTALACIÓN COMPLETADA EXITOSAMENTE EN ESTA PC!
    echo ======================================================================
    echo.
    echo Todo está listo para usar:
    echo   • Para abrir el sistema: Ejecuta 'ejecutar_gui.bat' o 'python app_gui.py'.
    echo   • Para generar el paquete portable: Ejecuta 'generar_portable.bat'.
    echo.
)

set /p RESP="¿Deseas iniciar la aplicación ahora mismo? (S/N): "
if /i "%RESP%"=="S" (
    start "" "ejecutar_gui.bat"
)

exit /b 0
