"""
Script ejecutable para analizar una carpeta raíz (contenedora de expedientes con '_')
o una carpeta individual:
- Si la carpeta seleccionada tiene subcarpetas con '_' en su primer nivel, analiza únicamente
  esas carpetas con '_' de la raíz (ignorando subcarpetas anidadas y carpetas sin '_' como BUENOS o MALOS).
- Si la carpeta seleccionada es directamente un expediente con '_' y archivos PDF, analiza ese expediente.
"""
import sys
import os
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_DIR))

from src.service import FolderOCRService

def select_folder_dialog() -> str:
    """Abre un diálogo visual de Windows para elegir carpeta si no se pasa por CLI."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(title="Selecciona la carpeta raíz (o carpeta individual) a analizar con OCR")
        root.destroy()
        return selected
    except Exception:
        return ""

def print_single_folder_report(res: dict):
    ocr_final = res["ocr_consolidado"]
    comp = res["comparacion"]

    print("=" * 70)
    print(f" CARPETA: {res['folder_data'].get('folder_name', res['folder_path'])}")
    print("=" * 70)
    for role, filename in res["target_pdfs_analyzed"].items():
        print(f"  • [{role:<10}] : {filename}")
    print(f"  Total páginas procesadas: {res['processed_pages_count']} (en {res['total_elapsed_seconds']} s)\n")

    print(f" {'CAMPO':<16} | {'CARPETA':<14} | {'OCR DETECTADO':<16} | {'ESTADO':<15}")
    print("-" * 70)

    for campo, det in comp["detalles_campos"].items():
        val_folder = str(det["carpeta"]) if det["carpeta"] is not None else "-"
        val_ocr = str(det["ocr"]) if det["ocr"] is not None else "-"
        estado = det["estado"]

        if estado == "COINCIDE":
            icono = "✔ COINCIDE"
        elif estado == "DISCREPANCIA":
            icono = "✖ DISCREPANCIA"
        elif estado == "CARPETA_EN_CEROS":
            icono = "★ PROPUESTA (CEROS)"
        else:
            icono = f"• {estado}"

        print(f" {campo.upper():<16} | {val_folder:<14} | {val_ocr:<16} | {icono:<15}")

    print("-" * 70)
    if ocr_final.get("fraccionamiento"):
        print(f" Fracc./Predio   : {ocr_final['fraccionamiento']}")
    if ocr_final.get("es_auxiliar"):
        print(f" Clasificación   : LIBRO AUXILIAR DETECTADO")
    print(f" Veredicto Final : {comp['veredicto']}")
    print("=" * 70)

    print("\nEVIDENCIAS EXTRAÍDAS:")
    for role, doc_info in res["documents_detail"].items():
        print(f"  [{role}] -> {doc_info['pdf_file']}")
        for ev in doc_info.get("evidencias", []):
            print(f"    - {ev}")
    print("\n")

def main():
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
    else:
        print("\nAbriendo explorador de archivos para seleccionar carpeta raíz...")
        folder_path = select_folder_dialog()

    if not folder_path or not os.path.exists(folder_path):
        print("No se seleccionó ninguna ruta válida.")
        sys.exit(1)

    service = FolderOCRService()
    subfolders = service.get_valid_subfolders(folder_path)

    if subfolders:
        print("\n" + "=" * 75)
        print(" AUDITORÍA POR LOTES DE CARPETA RAÍZ (SOLO CARPETAS CON '_' EN EL NIVEL 1)")
        print("=" * 75)
        print(f"Raíz seleccionada : {folder_path}")
        print(f"Carpetas válidas  : {len(subfolders)} (ignorando BUENOS, MALOS y subcarpetas)\n")

        def on_complete(idx, total, res):
            f_name = res["folder_data"].get("folder_name", Path(res["folder_path"]).name)
            veredicto = res["comparacion"]["veredicto"]
            t_sec = res["total_elapsed_seconds"]
            ocr = res["ocr_consolidado"]
            print(
                f"[{idx:>3}/{total}] {f_name:<45} -> {veredicto:<16} "
                f"(Sec:{ocr['seccion']} Lib:{ocr['libro']} Tom:{ocr['tomo']} Vol:{ocr['volumen']} Año:{ocr['anio']}) [{t_sec}s]"
            )

        batch_res = service.process_root_folder(folder_path, on_folder_complete=on_complete)
        print("\n" + "=" * 75)
        print(" RESUMEN GLOBAL DE LA CARPETA RAÍZ:")
        print(f"  • Total carpetas procesadas : {batch_res['processed_folders_count']} / {batch_res['total_valid_folders']}")
        print(f"  • Coincidencias totales     : {batch_res['coinciden_count']}")
        print(f"  • Discrepancias             : {batch_res['discrepancias_count']}")
        print(f"  • Carpetas en ceros         : {batch_res['ceros_count']}")
        print(f"  • Tiempo total              : {batch_res['total_elapsed_seconds']} s")
        print("=" * 75)
    else:
        p = Path(folder_path)
        if "_" in p.name and any(f.suffix.lower() == ".pdf" for f in p.iterdir() if f.is_file()):
            res = service.process_folder(folder_path)
            print_single_folder_report(res)
        else:
            print(
                f"\nNo se encontraron carpetas con '_' en el primer nivel de:\n  {folder_path}\n"
                "(Recuerda que carpetas sin '_' como BUENOS y MALOS o subcarpetas anidadas se ignoran)."
            )
            sys.exit(1)

if __name__ == "__main__":
    main()
