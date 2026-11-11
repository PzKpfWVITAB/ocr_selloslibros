import os
import re
from pathlib import Path
from typing import Dict, List, Optional

def natural_sort_key(s: str):
    """Ordenamiento natural para cadenas con números (e.g. 0001, 0002, 0010)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]

class PDFSelector:
    """
    Selecciona inteligentemente las subcarpetas válidas dentro de una carpeta raíz
    y los PDFs clave a analizar dentro de cada carpeta de libro:
    - Carátula (si existe un archivo con 'CARATULA' en el nombre)
    - El primer PDF
    - El PDF de en medio
    - El penúltimo PDF
    """

    @staticmethod
    def get_valid_subfolders(root_path: str) -> List[Path]:
        """
        Obtiene únicamente las carpetas con '_' ubicadas directamente en el primer nivel
        de la carpeta raíz seleccionada (sin entrar a subcarpetas ni analizar carpetas
        como BUENOS o MALOS).
        """
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"La ruta especificada no existe: {root_path}")

        valid_folders: List[Path] = []
        with os.scandir(str(root)) as entries:
            for entry in entries:
                if entry.is_dir() and "_" in entry.name:
                    valid_folders.append(Path(entry.path))

        valid_folders.sort(key=lambda p: natural_sort_key(p.name))
        return valid_folders

    @staticmethod
    def get_target_pdfs(folder_path: str) -> Dict[str, Path]:
        """
        Retorna un diccionario etiquetado con los archivos seleccionados:
        {'CARATULA': Path, 'PRIMERO': Path, 'ENMEDIO': Path, 'PENULTIMO': Path}
        """
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"La carpeta especificada no existe: {folder_path}")

        all_pdfs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
        if not all_pdfs:
            return {}

        caratula_pdf: Optional[Path] = None
        regular_pdfs: List[Path] = []

        for p in all_pdfs:
            if "CARATULA" in p.name.upper():
                caratula_pdf = p
            else:
                regular_pdfs.append(p)

        # Ordenar naturalmente los PDFs regulares
        regular_pdfs.sort(key=lambda p: natural_sort_key(p.name))

        targets: Dict[str, Path] = {}

        if caratula_pdf:
            targets["CARATULA"] = caratula_pdf

        n = len(regular_pdfs)
        if n == 1:
            targets["PRIMERO"] = regular_pdfs[0]
        elif n == 2:
            targets["PRIMERO"] = regular_pdfs[0]
            targets["PENULTIMO"] = regular_pdfs[1]
        elif n >= 3:
            targets["PRIMERO"] = regular_pdfs[0]
            targets["ENMEDIO"] = regular_pdfs[n // 2]
            targets["PENULTIMO"] = regular_pdfs[-2]

        return targets
