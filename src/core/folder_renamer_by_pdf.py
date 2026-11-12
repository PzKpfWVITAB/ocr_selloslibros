import re
import os
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from src.parsers.folder_parser import FolderParser
from src.database.mysql_manager import MySQLManager


class FolderRenamerByFirstPdf:
    """
    Renombra carpetas registrales tomando como base el nombre del primer archivo PDF (alfabéticamente)
    hasta la posición del Año, resolviendo colisiones con _{numero} y recuperando el año original
    de la carpeta si el PDF contiene '0000'.
    Todos los movimientos se auditan y persisten en MySQL.
    """

    def __init__(self, mysql_manager: Optional[MySQLManager] = None):
        self.mysql = mysql_manager

    @staticmethod
    def extract_year_from_folder_name(folder_name: str) -> Optional[str]:
        """
        Extrae un año de 4 dígitos válido (1800-2035) del nombre de la carpeta.
        Soporta carpetas como '1981_CYA_02_00_00_00_0176-0237' o '01_01_01_03_00_00_1981'.
        """
        # 1. Si comienza con 4 dígitos al inicio (ej. 1981_...)
        m_start = re.match(r"^(\d{4})_", folder_name)
        if m_start:
            y = int(m_start.group(1))
            if 1800 <= y <= 2035:
                return m_start.group(1)

        # 2. Si tiene año al final o entre guiones bajos o delimitadores
        matches = re.findall(r"(?:^|_|-|\s)(18\d{2}|19\d{2}|20\d{2})(?:_|-|\s|$)", folder_name)
        if matches:
            return matches[-1]

        return None

    @staticmethod
    def is_duplicate_pdf_name(filename: str) -> bool:
        """
        Detecta si el archivo PDF tiene indicación de duplicado como _DUP, _DUP1, _DUP2, etc.
        Estos archivos se salen del formato oficial y no deben seleccionarse como el primer PDF
        para nombrar la carpeta, debiendo tomarse el siguiente archivo en formato estándar.
        """
        stem = Path(filename).stem
        return bool(re.search(r"[_+-]DUP\d*(\.pdf)?$", stem, re.IGNORECASE)) or "_DUP" in stem.upper()

    @classmethod
    def get_first_pdf_code_up_to_year(
        cls,
        first_pdf_name: str,
        folder_original_name: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        A partir del nombre del primer PDF y la carpeta original, extrae el código base hasta el año.
        Si el año del PDF es '0000', se recupera el año de la carpeta original.
        Limpia automáticamente cualquier sufijo de duplicado (_DUP, _DUP1, etc.) si estuviera presente.

        Retorna:
            (codigo_sugerido, razon_o_detalle)
        """
        name_no_ext = Path(first_pdf_name).stem

        # Quitar sufijos de duplicado (_DUP, _DUP1, etc.) si vinieran en el nombre
        clean_name = re.sub(r"[_+-]DUP\d*.*$", "", name_no_ext, flags=re.IGNORECASE)

        # Quitar sufijos de copia o carátula si los tuviera
        tokens = clean_name.split("_")
        if len(tokens) < 4:
            return None, "El archivo PDF no tiene suficientes segmentos separados por guion bajo."

        # Buscar la posición del año (4 dígitos o 0000)
        # En el estándar: [del]_[sec]_[lib]_[tom/materia]_[vol]_[fracc]_[año]_[hoja]_[copia]
        year_idx = -1
        year_val = ""

        # Recorrer desde atrás para buscar el año (normalmente antes de la hoja y la copia)
        for i in range(len(tokens) - 1, -1, -1):
            tok = tokens[i]
            if (tok.isdigit() and len(tok) == 4 and 1800 <= int(tok) <= 2035) or tok == "0000":
                year_idx = i
                year_val = tok
                break

        if year_idx == -1:
            # Fallback con regex: buscar patrón _YYYY_ o _0000_
            m = re.search(r"_(18\d{2}|19\d{2}|20\d{2}|0000)_", name_no_ext)
            if m:
                end_pos = m.end() - 1
                prefix = name_no_ext[:end_pos]
                year_val = m.group(1)
            else:
                return None, f"No se encontró posición de año de 4 dígitos ni '0000' en '{first_pdf_name}'."
        else:
            prefix = "_".join(tokens[:year_idx + 1])

        # REGLA DEL AÑO 0000: Mantener el año de la carpeta
        detail_msg = "OK"
        if year_val == "0000":
            folder_year = cls.extract_year_from_folder_name(folder_original_name)
            if folder_year:
                # Reemplazar el 0000 final del prefijo con el año real de la carpeta
                prefix = re.sub(r"_0000$", f"_{folder_year}", prefix)
                detail_msg = f"Año 0000 en PDF sustituido por año {folder_year} de la carpeta original"
            else:
                detail_msg = "Año 0000 en PDF conservado (la carpeta no tenía año de 4 dígitos detectable)"

        return prefix, detail_msg

    @classmethod
    def resolve_destination_collision(cls, parent_dir: Path, target_base_name: str, current_folder_path: Path) -> Tuple[Path, str, int]:
        """
        Si la carpeta destino ya existe y NO es la misma carpeta actual:
        Genera {target_base_name}_2, {target_base_name}_3, etc.
        """
        if current_folder_path.name == target_base_name:
            return current_folder_path, target_base_name, 0

        target_path = parent_dir / target_base_name
        if not target_path.exists():
            return target_path, target_base_name, 0

        # Si ya existe, buscar el siguiente índice disponible
        collision_idx = 2
        while True:
            candidate_name = f"{target_base_name}_{collision_idx}"
            candidate_path = parent_dir / candidate_name
            if candidate_path == current_folder_path:
                return candidate_path, candidate_name, collision_idx
            if not candidate_path.exists():
                return candidate_path, candidate_name, collision_idx
            collision_idx += 1

    def preview_folder_rename(self, folder_path: Path) -> Dict[str, Any]:
        """Analiza la carpeta y genera la vista previa del renombrado sin aplicar cambios."""
        folder_path = Path(folder_path)
        if not folder_path.is_dir():
            return {
                "folder_path": str(folder_path),
                "original_name": folder_path.name,
                "status": "ERROR_NO_EXISTE",
                "message": "La ruta no es un directorio válido."
            }

        # Obtener primer PDF ordenado alfabéticamente
        pdfs = sorted([f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"])
        if not pdfs:
            return {
                "folder_path": str(folder_path),
                "original_name": folder_path.name,
                "status": "SIN_PDFS",
                "message": "La carpeta no contiene archivos PDF."
            }

        # Buscar el primer PDF que cumpla con la estructura de libro válida con año,
        # OMITIENDO:
        # 1. Archivos duplicados (_DUP, _DUP1, etc. que se salen del formato oficial)
        # 2. Carátulas (CARATULA)
        # 3. Archivos irregulares fuera de estándar
        # Se toma el siguiente archivo que sí cumpla con el formato estándar.
        target_pdf = None
        base_code = None
        detail = ""

        # Prioridad 1: PDFs regulares limpios (sin _DUP y sin CARATULA)
        for p in pdfs:
            if self.is_duplicate_pdf_name(p.name):
                continue
            if "CARATULA" in p.name.upper():
                continue
            code_cand, det_cand = self.get_first_pdf_code_up_to_year(p.name, folder_path.name)
            if code_cand:
                target_pdf = p
                base_code = code_cand
                detail = det_cand
                break

        # Prioridad 2: Si no hubo PDFs regulares limpios, buscar PDFs sin _DUP (incluso con CARATULA)
        if not target_pdf:
            for p in pdfs:
                if self.is_duplicate_pdf_name(p.name):
                    continue
                code_cand, det_cand = self.get_first_pdf_code_up_to_year(p.name, folder_path.name)
                if code_cand:
                    target_pdf = p
                    base_code = code_cand
                    detail = det_cand
                    break

        # Prioridad 3: Fallback de seguridad (si todos los PDFs tuvieran _DUP)
        if not target_pdf:
            for p in pdfs:
                code_cand, det_cand = self.get_first_pdf_code_up_to_year(p.name, folder_path.name)
                if code_cand:
                    target_pdf = p
                    base_code = code_cand
                    detail = f"{det_cand} (Tomado de {p.name} omitiendo sufijo DUP)"
                    break

        # Fallback si ninguno cumplió estructura con año
        if not target_pdf:
            target_pdf = pdfs[0]
            base_code, detail = self.get_first_pdf_code_up_to_year(target_pdf.name, folder_path.name)

        first_pdf = target_pdf

        if not base_code:
            return {
                "folder_path": str(folder_path),
                "original_name": folder_path.name,
                "first_pdf": first_pdf.name,
                "status": "ERROR_PARSEO",
                "message": detail
            }

        dest_path, final_name, col_idx = self.resolve_destination_collision(
            folder_path.parent, base_code, folder_path
        )

        needs_rename = (folder_path.name != final_name)

        dup_files = [p.name for p in pdfs if self.is_duplicate_pdf_name(p.name)]
        if dup_files:
            dup_msg = f"{len(dup_files)} archivo(s) DUP detectados (se mantendrán con _DUP para revisión manual)"
            detail = f"{detail} | {dup_msg}" if detail != "OK" else dup_msg

        return {
            "folder_path": str(folder_path),
            "original_name": folder_path.name,
            "parent_dir": str(folder_path.parent),
            "first_pdf": first_pdf.name,
            "total_pdfs": len(pdfs),
            "base_code": base_code,
            "suggested_name": final_name,
            "destination_path": str(dest_path),
            "collision_index": col_idx,
            "needs_rename": needs_rename,
            "detail": detail,
            "dup_files_count": len(dup_files),
            "dup_files": dup_files,
            "status": "OK" if needs_rename else "YA_TIENE_NOMBRE_CORRECTO"
        }

    def execute_folder_rename(self, folder_path: Path) -> Dict[str, Any]:
        """Aplica el renombrado físico de la carpeta y registra la operación en MySQL."""
        prev = self.preview_folder_rename(folder_path)
        if prev["status"] not in ["OK", "YA_TIENE_NOMBRE_CORRECTO"]:
            return prev

        if not prev["needs_rename"]:
            prev["message"] = "La carpeta ya coincide exactamente con el código sugerido."
            return prev

        orig_path = Path(prev["folder_path"])
        dest_path = Path(prev["destination_path"])

        try:
            os.rename(orig_path, dest_path)
            prev["applied"] = True
            prev["message"] = f"Renombrado exitoso a '{dest_path.name}'"

            # Registro en MySQL
            if self.mysql and self.mysql.enabled:
                self.mysql.registrar_movimiento_carpeta(
                    tipo_movimiento="RENOMBRADO_PRIMER_PDF",
                    nombre_origen=orig_path.name,
                    nombre_destino=dest_path.name,
                    ruta_origen=str(orig_path),
                    ruta_destino=str(dest_path),
                    primer_pdf_referencia=prev.get("first_pdf"),
                    motivo=prev.get("detail"),
                    indice_colision=prev.get("collision_index", 0)
                )

        except Exception as e:
            prev["applied"] = False
            prev["status"] = "ERROR_RENOMBRADO"
            prev["message"] = f"Error al renombrar: {str(e)}"

        return prev
