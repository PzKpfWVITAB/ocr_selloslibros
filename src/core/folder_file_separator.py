import re
import os
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict
import pymupdf as fitz

from src.parsers.folder_parser import FolderParser
from src.database.mysql_manager import MySQLManager
from src.core.file_operations import FileOperations


class FolderFileSeparator:
    """
    Detecta si una carpeta contiene PDFs mezclados pertenecientes a diferentes
    Libros, Secciones, Tomos o Años. Agrupa los archivos homogéneos y los separa
    en nuevas carpetas limpias, manteniendo juntos los registros correspondientes
    e incluyendo su respectiva carátula.
    Todos los movimientos físicos y hashes se registran en MySQL.
    """

    def __init__(self, mysql_manager: Optional[MySQLManager] = None):
        self.mysql = mysql_manager

    @classmethod
    def parse_pdf_group_key(cls, pdf_name: str) -> Dict[str, Any]:
        """
        Analiza el nombre de un PDF y extrae su clave de grupo hasta el año.
        Estructura típica: [del]_[sec]_[lib]_[tom]_[vol]_[fracc]_[anio]_[hoja]_[copia].pdf
        """
        stem = Path(pdf_name).stem
        is_caratula = bool(re.search(r"CARATULA", stem, re.IGNORECASE))

        # Quitar sufijo de carátula para obtener la base limpia
        clean_stem = re.sub(r"_CARATULA.*$", "", stem, flags=re.IGNORECASE)

        tokens = clean_stem.split("_")

        # Buscar año de 4 dígitos o 0000
        year_idx = -1
        year_val = ""
        for i in range(len(tokens) - 1, -1, -1):
            tok = tokens[i]
            if (tok.isdigit() and len(tok) == 4 and 1800 <= int(tok) <= 2035) or tok == "0000":
                year_idx = i
                year_val = tok
                break

        if year_idx != -1 and year_idx >= 3:
            # Clave hasta el año
            prefix = "_".join(tokens[:year_idx + 1])
        else:
            # Fallback regex
            m = re.search(r"^(.*?_(?:18\d{2}|19\d{2}|20\d{2}|0000))", clean_stem)
            if m:
                prefix = m.group(1)
            else:
                prefix = clean_stem

        # Intentar extraer campos para metadatos
        parsed_fields = FolderParser.parse(prefix)

        return {
            "pdf_name": pdf_name,
            "group_key": prefix,
            "is_caratula": is_caratula,
            "seccion": parsed_fields.get("seccion"),
            "libro": parsed_fields.get("libro"),
            "tomo": parsed_fields.get("tomo"),
            "volumen": parsed_fields.get("volumen"),
            "fraccionamiento": parsed_fields.get("fraccionamiento"),
            "anio": parsed_fields.get("anio")
        }

    def inspect_folder_groups(self, folder_path: Path) -> Dict[str, Any]:
        """
        Inspecciona los PDFs de la carpeta y determina si hay mezcla de libros.
        """
        folder_path = Path(folder_path)
        if not folder_path.is_dir():
            return {
                "folder_path": str(folder_path),
                "is_mixed": False,
                "status": "ERROR_NO_DIR",
                "message": "No es un directorio válido."
            }

        pdfs = sorted([f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"])
        if not pdfs:
            return {
                "folder_path": str(folder_path),
                "is_mixed": False,
                "status": "SIN_PDFS",
                "message": "No contiene archivos PDF."
            }

        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for pdf_file in pdfs:
            info = self.parse_pdf_group_key(pdf_file.name)
            info["full_path"] = str(pdf_file)
            groups[info["group_key"]].append(info)

        # Si hay más de un grupo, está mezclada
        is_mixed = len(groups) > 1

        # Identificar grupo primario (el que tiene más PDFs)
        sorted_groups = sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)
        primary_group_key = sorted_groups[0][0] if sorted_groups else ""

        groups_summary = {}
        for g_key, items in sorted_groups:
            caratulas = [it["pdf_name"] for it in items if it["is_caratula"]]
            groups_summary[g_key] = {
                "count": len(items),
                "caratulas": caratulas,
                "sample_files": [it["pdf_name"] for it in items[:3]],
                "is_primary": (g_key == primary_group_key)
            }

        return {
            "folder_path": str(folder_path),
            "folder_name": folder_path.name,
            "total_pdfs": len(pdfs),
            "total_groups": len(groups),
            "is_mixed": is_mixed,
            "primary_group": primary_group_key,
            "groups_summary": groups_summary,
            "groups_detail": groups,
            "status": "MEZCLADA" if is_mixed else "HOMOGENEA"
        }

    def preview_folder_split(self, folder_path: Path) -> Dict[str, Any]:
        """Genera vista previa de la separación de archivos sin moverlos físicamente."""
        insp = self.inspect_folder_groups(folder_path)
        if not insp.get("is_mixed"):
            return insp

        parent_dir = Path(insp["folder_path"]).parent
        actions: List[Dict[str, Any]] = []

        for g_key, items in insp["groups_detail"].items():
            if g_key == insp["primary_group"]:
                continue  # El grupo primario permanece en la carpeta origen

            # Determinar carpeta destino para este grupo secundario
            target_base_name = g_key
            cand_path = parent_dir / target_base_name
            col_idx = 0
            if cand_path.exists():
                col_idx = 2
                while True:
                    cand_name = f"{target_base_name}_{col_idx}"
                    cand_path = parent_dir / cand_name
                    if not cand_path.exists():
                        break
                    col_idx += 1

            files_to_move = [it["pdf_name"] for it in items]
            has_caratula = any(it["is_caratula"] for it in items)

            actions.append({
                "group_key": g_key,
                "target_folder_name": cand_path.name,
                "target_folder_path": str(cand_path),
                "files_count": len(files_to_move),
                "files": files_to_move,
                "has_own_caratula": has_caratula,
                "collision_index": col_idx
            })

        insp["split_actions"] = actions
        return insp

    def execute_folder_split(self, folder_path: Path) -> Dict[str, Any]:
        """
        Ejecuta la separación física de archivos mezclados en nuevas carpetas
        y registra cada movimiento en MySQL con hash SHA-256 y metadatos completos.
        """
        prev = self.preview_folder_split(folder_path)
        if not prev.get("is_mixed"):
            prev["applied"] = False
            prev["message"] = "La carpeta no está mezclada o no requiere separación."
            return prev

        orig_folder = Path(prev["folder_path"])
        executed_actions = []

        for act in prev.get("split_actions", []):
            dest_folder = Path(act["target_folder_path"])
            dest_folder.mkdir(parents=True, exist_ok=True)

            files_moved_count = 0
            moved_files_info = []

            for pdf_name in act["files"]:
                src_file = orig_folder / pdf_name
                dst_file = dest_folder / pdf_name

                if not src_file.exists():
                    continue

                # Calcular hash SHA-256 y tamaño antes de mover
                sha256 = MySQLManager.compute_sha256(src_file)
                size_bytes = src_file.stat().st_size if src_file.exists() else 0
                pages = 0
                try:
                    with fitz.open(str(src_file)) as doc:
                        pages = len(doc)
                except Exception:
                    pass

                # Mover archivo
                shutil.move(str(src_file), str(dst_file))
                files_moved_count += 1

                # Extraer metadatos
                parsed_meta = self.parse_pdf_group_key(pdf_name)

                # Registrar en MySQL
                if self.mysql and self.mysql.enabled:
                    self.mysql.registrar_trazabilidad_archivo(
                        tipo_operacion="MOVER_POR_SEPARACION",
                        archivo_nombre_origen=pdf_name,
                        archivo_nombre_destino=dst_file.name,
                        carpeta_origen=orig_folder.name,
                        carpeta_destino=dest_folder.name,
                        ruta_completa_origen=str(src_file),
                        ruta_completa_destino=str(dst_file),
                        hash_sha256=sha256,
                        tamanio_bytes=size_bytes,
                        numero_paginas=pages,
                        es_caratula=parsed_meta["is_caratula"],
                        seccion=str(parsed_meta.get("seccion") or ""),
                        libro=str(parsed_meta.get("libro") or ""),
                        tomo=str(parsed_meta.get("tomo") or ""),
                        volumen=str(parsed_meta.get("volumen") or ""),
                        anio=parsed_meta.get("anio"),
                        fraccionamiento=parsed_meta.get("fraccionamiento")
                    )

                moved_files_info.append(pdf_name)

            # Si este grupo no tenía carátula propia pero la carpeta origen tenía una carátula general
            # y se requiere preservarla, verificar si se copia
            own_caratula = [f for f in act["files"] if "CARATULA" in f.upper()]
            caratula_asoc = own_caratula[0] if own_caratula else None

            # Registrar en tabla separaciones_carpetas
            if self.mysql and self.mysql.enabled:
                self.mysql.registrar_separacion_carpeta(
                    carpeta_origen=str(orig_folder),
                    nombre_carpeta_origen=orig_folder.name,
                    clave_grupo=act["group_key"],
                    carpeta_destino_creada=str(dest_folder),
                    nombre_carpeta_destino=dest_folder.name,
                    total_archivos_movidos=files_moved_count,
                    caratula_asociada=caratula_asoc
                )

                self.mysql.registrar_movimiento_carpeta(
                    tipo_movimiento="CREACION_CARPETA_SEPARADA",
                    nombre_origen=orig_folder.name,
                    nombre_destino=dest_folder.name,
                    ruta_origen=str(orig_folder),
                    ruta_destino=str(dest_folder),
                    motivo=f"Separación de grupo {act['group_key']} ({files_moved_count} archivos)",
                    indice_colision=act.get("collision_index", 0)
                )

            act["files_moved_count"] = files_moved_count
            act["status"] = "SEPARADO_EXITOSAMENTE"
            executed_actions.append(act)

        prev["applied"] = True
        prev["executed_actions"] = executed_actions
        prev["message"] = f"Se separaron {len(executed_actions)} grupos en nuevas carpetas con trazabilidad MySQL."
        return prev
