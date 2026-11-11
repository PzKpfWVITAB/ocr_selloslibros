"""
Script generador del paquete 100% Portable (Carpeta con .exe + dependencias + settings.json + archivo .zip).
Empaqueta Python, Tkinter, Claude Sonnet 5 Vision Engine, Smart Cache, OpenCV y PyMuPDF.
"""
import sys
import json
import shutil
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DIST_DIR = PROJECT_DIR / "dist"
BUILD_DIR = PROJECT_DIR / "build"
APP_NAME = "OCR_SellosLibros_Portable"
EXE_NAME = "OCR_SellosLibros"

from src.core.settings_manager import SettingsManager

def build():
    print("=" * 72)
    print(" GENERANDO VERSIÓN PORTABLE (CLAUDE SONNET 5 + CACHÉ INTELIGENTE)")
    print("=" * 72)

    # 1. Cerrar instancia previa si estuviera abierta y respaldar caché/DB antes de limpiar
    subprocess.run(["taskkill", "/F", "/IM", f"{EXE_NAME}.exe"], capture_output=True)
    preserved_dir = PROJECT_DIR / "_preserved_portable_state"
    if preserved_dir.exists():
        shutil.rmtree(preserved_dir, ignore_errors=True)
    old_portable = DIST_DIR / APP_NAME
    if old_portable.exists():
        for item_name in ("cache", "auditoria_libros.db", "rollback_backups", "settings.json"):
            src_item = old_portable / item_name
            if src_item.exists():
                preserved_dir.mkdir(parents=True, exist_ok=True)
                dst_item = preserved_dir / item_name
                if src_item.is_dir():
                    shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                else:
                    shutil.copy2(src_item, dst_item)

    for d in [BUILD_DIR, DIST_DIR]:
        if d.exists():
            print(f"[1/4] Limpiando directorio previo: {d.name}...")
            shutil.rmtree(d, ignore_errors=True)

    # 2. Construir comando de PyInstaller (mucho más ligero y rápido sin motores pesados locales)
    print("[2/4] Empaquetando ejecutable (.exe) y dependencias...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        f"--name={EXE_NAME}",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
        f"--specpath={BUILD_DIR}",
        f"--paths={PROJECT_DIR}",
        "--collect-all=pymupdf",
        "--hidden-import=cv2",
        "--hidden-import=numpy",
        "--hidden-import=sqlite3",
        "--hidden-import=config",
        "--hidden-import=src",
        "--hidden-import=src.service",
        "--hidden-import=src.database",
        "--hidden-import=src.database.db_manager",
        "--hidden-import=src.core.pdf_selector",
        "--hidden-import=src.core.image_extractor",
        "--hidden-import=src.core.preprocessor",
        "--hidden-import=src.core.settings_manager",
        "--hidden-import=src.core.cache_manager",
        "--hidden-import=src.core.claude_engine",
        "--hidden-import=src.core.caratula_inspector",
        "--hidden-import=src.core.file_operations",
        "--hidden-import=src.parsers.folder_parser",
        "--hidden-import=src.parsers.text_normalizer",
        "--hidden-import=src.parsers.stamp_parser",
        "--hidden-import=src.matcher.comparator",
        str(PROJECT_DIR / "app_gui.py")
    ]

    subprocess.run(cmd, check=True, cwd=str(PROJECT_DIR))

    # 3. Preparar carpeta portable con settings.json preconfigurado y carpeta cache/
    raw_folder = DIST_DIR / EXE_NAME
    portable_folder = DIST_DIR / APP_NAME
    if raw_folder.exists() and raw_folder != portable_folder:
        if portable_folder.exists():
            shutil.rmtree(portable_folder, ignore_errors=True)
        shutil.copytree(raw_folder, portable_folder, dirs_exist_ok=True)
        shutil.rmtree(raw_folder, ignore_errors=True)

    # Copiar/crear settings.json con la API Key y caché activada, y restaurar estado previo si existía
    current_settings = SettingsManager.load()
    (portable_folder / "settings.json").write_text(
        json.dumps(current_settings, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    (portable_folder / "cache").mkdir(parents=True, exist_ok=True)
    if preserved_dir.exists():
        for item_p in preserved_dir.iterdir():
            dst_p = portable_folder / item_p.name
            if item_p.is_dir():
                shutil.copytree(item_p, dst_p, dirs_exist_ok=True)
            else:
                shutil.copy2(item_p, dst_p)
        shutil.rmtree(preserved_dir, ignore_errors=True)

    readme_path = portable_folder / "LEEME_INSTRUCCIONES.txt"
    readme_path.write_text(
        "======================================================================\n"
        " AUDITORIA DE SELLOS Y LIBROS REGISTRALES (CLAUDE SONNET 5 + CACHE)\n"
        "======================================================================\n\n"
        "1. EJECUCION INMEDIATA:\n"
        "   - Haz doble clic en 'OCR_SellosLibros.exe'.\n"
        "   - No requiere instalar Python ni ninguna dependencia externa.\n"
        "   - Conserva siempre 'OCR_SellosLibros.exe' junto a su carpeta '_internal'.\n\n"
        "2. CONFIGURACION DE API KEY Y CACHE:\n"
        "   - Haz clic en el boton 'Configurar API Key / Cache' arriba a la derecha\n"
        "     (o en el menu superior 'Configuracion API y Cache').\n"
        "   - Ahi puedes cambiar la API Key, probar la conexion y gestionar la cache.\n\n"
        "3. REGLA DE CARPETA RAIZ:\n"
        "   - Selecciona la carpeta contenedora (ej. ...\\Tuxtla Gutierrez\\KERUBIN).\n"
        "   - El sistema revisara UNICAMENTE las carpetas con '_' en el primer nivel\n"
        "     de la raiz seleccionada, ignorando subcarpetas y BUENOS / MALOS.\n",
        encoding="utf-8"
    )

    # 4. Generar archivo .zip listo para compartir
    print("[3/4] Comprimiendo carpeta portable en archivo .zip...")
    zip_base = DIST_DIR / APP_NAME
    zip_file = shutil.make_archive(
        base_name=str(zip_base),
        format="zip",
        root_dir=str(DIST_DIR),
        base_dir=APP_NAME
    )

    exe_path = portable_folder / f"{EXE_NAME}.exe"
    zip_size_mb = Path(zip_file).stat().st_size / (1024 * 1024)

    print("\n" + "=" * 72)
    print(" [4/4] ¡PAQUETE PORTABLE GENERADO EXITOSAMENTE!")
    print("=" * 72)
    print(f"  • Carpeta Portable : {portable_folder}")
    print(f"  • Ejecutable (.exe): {exe_path}")
    print(f"  • Configuración    : {portable_folder / 'settings.json'}")
    print(f"  • Archivo ZIP listo: {zip_file} ({zip_size_mb:.1f} MB)")
    print("=" * 72)

if __name__ == "__main__":
    build()
