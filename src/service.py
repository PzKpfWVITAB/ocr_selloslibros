import os
import re
import functools
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Tuple
import time

from config import PDF_RENDER_DPI, CLAHE_CLIP_LIMIT, CLAHE_TILE_GRID_SIZE
from src.core.pdf_selector import PDFSelector
from src.core.image_extractor import ImageExtractor
from src.core.preprocessor import ImagePreprocessor
from src.core.settings_manager import SettingsManager
from src.core.cache_manager import CacheManager
from src.core.claude_engine import ClaudeVisionEngine
from src.core.caratula_inspector import CaratulaInspector
from src.core.file_operations import FileOperations
from src.core.folder_renamer_by_pdf import FolderRenamerByFirstPdf
from src.parsers.folder_parser import FolderParser
from src.matcher.comparator import FolderComparator
from src.database.mysql_manager import MySQLManager


class OperationBusyError(RuntimeError):
    """Se intentó iniciar una operación que modifica disco/BD mientras otra sigue en curso."""


def exclusive_op(label: str):
    """Exclusión mutua: solo UNA operación que modifique disco/BD a la vez (reentrante en el mismo hilo)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            if not self._op_lock.acquire(blocking=False):
                raise OperationBusyError(
                    f"Hay otra operación en curso ({self.current_operation or 'desconocida'}). "
                    "Espera a que termine antes de iniciar '" + label + "'."
                )
            prev = self.current_operation
            self.current_operation = label
            try:
                return fn(self, *args, **kwargs)
            finally:
                self.current_operation = prev
                self._op_lock.release()
        return wrapper
    return deco


class FolderOCRService:
    """
    Servicio integral de Trazabilidad MySQL, Chequeo de Estructura, Diagnóstico con IA y
    Traslados por Lotes (Invariante 950 carpetas = 950 carpetas):
    - Paso 1: Indexación inicial en MySQL (carpetas_iniciales + archivos_iniciales) y
      renombrado por 1er PDF hasta el año.
    - Paso 2: Chequeo estricto de estructura de 7 posiciones (pre-IA), validación rápida
      de carpetas homogéneas sin carátula, y análisis con Claude Vision (2 páginas) para
      carpetas con carátula independiente.
    - Paso 3: Traslado por lotes a Correctos o Apartados y Rollback estricto por lote.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.preprocessor = ImagePreprocessor(
            clip_limit=CLAHE_CLIP_LIMIT,
            tile_grid_size=CLAHE_TILE_GRID_SIZE
        )
        self.cache = CacheManager()
        self.settings = SettingsManager.load()
        self.mysql = MySQLManager(self.settings)
        self._op_lock = threading.RLock()
        self.current_operation: Optional[str] = None

    def reload_settings(self) -> Dict[str, Any]:
        self.settings = SettingsManager.load()
        self.mysql.update_config(
            host=str(self.settings.get("mysql_host", "127.0.0.1")),
            port=int(self.settings.get("mysql_port", 3306)),
            user=str(self.settings.get("mysql_user", "root")),
            password=str(self.settings.get("mysql_password", "")),
            database=str(self.settings.get("mysql_database", "trazabilidad_selloslibros")),
            enabled=bool(self.settings.get("mysql_enabled", True))
        )
        return self.settings

    @staticmethod
    def get_valid_subfolders(root_path: str) -> List[Path]:
        """
        Retorna únicamente las carpetas con '_' en su nombre que se encuentran
        directamente en la raíz seleccionada (nivel 1). Ignora BUENOS, MALOS y subcarpetas.
        """
        return PDFSelector.get_valid_subfolders(root_path)

    @staticmethod
    def preview_folders_caratulas(subfolders: List[Path]) -> List[Dict[str, Any]]:
        """
        Pre-escanea rápidamente la lista de carpetas para auto-detectar el archivo PDF
        que contiene la palabra 'CARATULA' en cada una antes de correr el diagnóstico.
        """
        items: List[Dict[str, Any]] = []
        for idx, folder in enumerate(subfolders, start=1):
            caratula_pdf = CaratulaInspector.auto_detect_caratula(folder)
            all_pdfs = CaratulaInspector.list_folder_pdfs(folder)
            items.append({
                "index": idx,
                "folder_path": str(folder),
                "folder_name": folder.name,
                "folder_data": FolderParser.parse(folder.name),
                "caratula_path": str(caratula_pdf) if caratula_pdf else None,
                "caratula_filename": caratula_pdf.name if caratula_pdf else "NO DETECTADA",
                "total_pdfs": len(all_pdfs),
            })
        return items

    def process_root_folder(
        self,
        root_path: str,
        custom_caratulas_map: Optional[Dict[str, str]] = None,
        on_folder_start: Optional[Callable[[int, int, Path], None]] = None,
        on_folder_complete: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta la Fase 1 (Diagnóstico General sin modificar archivos físicos) sobre todas las
        carpetas válidas con '_' en el nivel 1 de la carpeta raíz.
        """
        self.reload_settings()
        start_time = time.perf_counter()
        subfolders = self.get_valid_subfolders(root_path)
        total_folders = len(subfolders)
        custom_map = custom_caratulas_map or {}

        results: List[Dict[str, Any]] = []
        coinciden_count = 0
        discrepancias_count = 0
        ceros_count = 0
        apartar_count = 0
        reemplazar_hoja_count = 0

        for idx, folder_path in enumerate(subfolders, start=1):
            if should_stop and should_stop():
                break

            if on_folder_start:
                on_folder_start(idx, total_folders, folder_path)

            custom_c = custom_map.get(str(folder_path))
            try:
                folder_res = self.process_folder(
                    str(folder_path),
                    custom_caratula_path=custom_c,
                    save_to_db=True,
                )
            except Exception as exc:
                folder_res = self._build_error_result(folder_path, str(exc))

            v_global = folder_res.get("veredicto_global", "ERROR")
            if v_global == "COINCIDE_TOTAL":
                coinciden_count += 1
            elif v_global == "APARTAR_CARATULA_BLANCA":
                apartar_count += 1
            elif "CEROS" in v_global:
                ceros_count += 1
            else:
                discrepancias_count += 1

            if folder_res.get("needs_page_replacement", False):
                reemplazar_hoja_count += 1

            results.append(folder_res)

            if on_folder_complete:
                on_folder_complete(idx, total_folders, folder_res)

        total_elapsed = round(time.perf_counter() - start_time, 2)
        return {
            "root_path": str(root_path),
            "total_valid_folders": total_folders,
            "processed_folders_count": len(results),
            "coinciden_count": coinciden_count,
            "discrepancias_count": discrepancias_count,
            "ceros_count": ceros_count,
            "apartar_count": apartar_count,
            "reemplazar_hoja_count": reemplazar_hoja_count,
            "total_elapsed_seconds": total_elapsed,
            "cache_summary": self.cache.get_summary(),
            "folder_results": results,
        }

    @staticmethod
    def check_folder_structure(folder_path: Path) -> Dict[str, Any]:
        """
        Paso 2A (Chequeo estricto de estructura de 7 posiciones, 0 tokens):
        Compara el código de 7 posiciones (delegacion, seccion, libro, tomo, volumen,
        fraccionamiento, anio) de la carpeta contra TODOS los PDFs internos.
        Si algún PDF es irregular (ej. REG) o difiere en cualquiera de las 7 posiciones,
        la carpeta se marca como 'ESTRUCTURA_DISCREPANTE' para envío directo a Apartados.
        También detecta si existe archivo independiente de CARÁTULA.
        """
        folder_p = Path(folder_path)
        folder_data = FolderParser.parse(folder_p.name)
        f_keys = ("delegacion", "seccion_raw", "libro_raw", "tomo_raw", "volumen_raw", "fraccionamiento_raw", "anio_raw")
        folder_tuple = tuple(str(folder_data.get(k) or "").zfill(4 if k == "anio_raw" else 2) for k in f_keys)

        pdfs = sorted(
            [f for f in folder_p.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"],
            key=lambda x: x.name,
        )

        caratula_files: List[str] = []
        regular_pdfs: List[str] = []
        mismatched_pdfs: List[str] = []
        irregular_pdfs: List[str] = []
        dup_pdfs: List[str] = []

        for pdf in pdfs:
            name = pdf.name
            if CaratulaInspector.is_irregular_reg_pdf_name(name):
                irregular_pdfs.append(name)
                continue

            if CaratulaInspector.is_duplicate_pdf_name(name):
                dup_pdfs.append(name)

            is_car = "CARATULA" in name.upper()
            if is_car:
                caratula_files.append(name)
            else:
                regular_pdfs.append(name)

            # Validar patrón de 7 posiciones en todo PDF regular (y en carátula si tiene prefijo numérico)
            p_data = FolderParser.parse(name)
            p_valid = bool(p_data.get("anio_raw"))
            if not is_car:
                if not p_valid:
                    mismatched_pdfs.append(f"{name} (patrón inválido)")
                else:
                    p_tuple = tuple(str(p_data.get(k) or "").zfill(4 if k == "anio_raw" else 2) for k in f_keys)
                    if p_tuple != folder_tuple:
                        mismatched_pdfs.append(f"{name} ({'_'.join(p_tuple)} != {'_'.join(folder_tuple)})")
            else:
                if p_valid:
                    p_tuple = tuple(str(p_data.get(k) or "").zfill(4 if k == "anio_raw" else 2) for k in f_keys)
                    if p_tuple != folder_tuple:
                        mismatched_pdfs.append(f"{name} ({'_'.join(p_tuple)} != {'_'.join(folder_tuple)})")

        is_homogeneous = (len(mismatched_pdfs) == 0 and len(irregular_pdfs) == 0 and len(pdfs) > 0)
        reasons = []
        if len(pdfs) == 0:
            reasons.append("Carpeta sin archivos PDF.")
        if irregular_pdfs:
            reasons.append(f"Archivos irregulares REG: {', '.join(irregular_pdfs[:5])}")
        if mismatched_pdfs:
            reasons.append(f"PDFs con diferente patrón de 7 posiciones: {', '.join(mismatched_pdfs[:5])}")

        return {
            "is_homogeneous": is_homogeneous,
            "has_caratula_file": len(caratula_files) > 0,
            "caratula_files": caratula_files,
            "regular_pdfs": regular_pdfs,
            "mismatched_pdfs": mismatched_pdfs,
            "irregular_pdfs": irregular_pdfs,
            "dup_pdfs": dup_pdfs,
            "total_pdfs": len(pdfs),
            "reason": " | ".join(reasons) if reasons else "Estructura homogénea (7 posiciones coinciden).",
        }

    def process_folder(
        self,
        folder_path: str,
        custom_caratula_path: Optional[str] = None,
        save_to_db: bool = True,
        force_claude_on_missing_caratula: bool = False,
    ) -> Dict[str, Any]:
        """
        PASO 2 (Chequeo de Estructura + Diagnóstico General):
        - Paso 2A: Chequeo estricto de 7 posiciones (pre-IA). Si algún PDF difiere o es REG -> ESTRUCTURA_DISCREPANTE (Apartar, 0 tokens).
        - Paso 2B: Si todos los PDFs siguen el patrón y NO hay archivo de carátula independiente -> Carátula embebida correcta -> COINCIDE_TOTAL (0 tokens).
        - Paso 2C: Si todos los PDFs siguen el patrón y SÍ hay archivo de carátula independiente -> Analizar 2 páginas con Claude Vision y validar coincidencia + presencia en PDFs.
        """
        self.reload_settings()
        start_time = time.perf_counter()
        folder_p = Path(folder_path)
        folder_data = FolderParser.parse(folder_p.name)
        clean_current_code = FileOperations.strip_suffix_after_year(folder_p.name, folder_data)

        # Paso 2A: Chequeo estricto de estructura de 7 posiciones (0 tokens)
        struct = self.check_folder_structure(folder_p)
        if not struct["is_homogeneous"]:
            sub_rev = "ARCHIVOS IRREGULARES REG" if (struct["irregular_pdfs"] and not struct["mismatched_pdfs"]) else "ESTRUCTURA DISCREPANTE"
            ev_lines = [f"⛔ [0 TOKENS - ESTRUCTURA] {struct['reason']}"]
            return {
                "folder_path": str(folder_p),
                "folder_data": folder_data,
                "caratula_inspection": {
                    "status": "ESTRUCTURA_DISCREPANTE",
                    "total_regular_pdfs": len(struct["regular_pdfs"]),
                    "irregular_pdfs": struct["irregular_pdfs"],
                    "dup_pdfs": struct["dup_pdfs"],
                    "summary_message": struct["reason"],
                },
                "target_pdfs_analyzed": {},
                "processed_pages_count": 0,
                "total_elapsed_seconds": round(time.perf_counter() - start_time, 2),
                "documents_detail": {},
                "ocr_consolidado": {
                    "seccion": folder_data.get("seccion_raw") or "00",
                    "libro": folder_data.get("libro_raw") or "00",
                    "tomo": folder_data.get("tomo_raw") or "00",
                    "volumen": folder_data.get("volumen_raw") or "00",
                    "fraccionamiento": folder_data.get("fraccionamiento_raw") or "00",
                    "anio": folder_data.get("anio_raw") or "0000",
                    "es_auxiliar": False,
                    "evidencias_globales": ev_lines,
                },
                "comparacion": {
                    "veredicto": "ESTRUCTURA_DISCREPANTE",
                    "discrepancias": ev_lines,
                    "coincidencias": [],
                    "propuestas_ceros": [],
                    "detalles_campos": {},
                },
                "veredicto_global": "ESTRUCTURA_DISCREPANTE",
                "subcarpeta_revision": sub_rev,
                "codigo_base_sugerido": clean_current_code,
                "needs_page_replacement": False,
                "needs_rename": False,
                "needs_apartar": True,
                "has_discrepancy": True,
                "cache_summary": self.cache.get_summary(),
            }

        # Paso 2B: Carpeta homogénea SIN archivo de carátula independiente (carátula embebida) -> CORRECTA (0 tokens)
        if not struct["has_caratula_file"] and not custom_caratula_path:
            ev_lines = [
                f"✅ [0 TOKENS - SIN ARCHIVO CARÁTULA] Todos los {len(struct['regular_pdfs'])} PDFs siguen el patrón exacto de la carpeta (carátula embebida). Lista para Correctos."
            ]
            return {
                "folder_path": str(folder_p),
                "folder_data": folder_data,
                "caratula_inspection": {
                    "status": "CARATULA_EMBEBIDA_OK",
                    "total_regular_pdfs": len(struct["regular_pdfs"]),
                    "irregular_pdfs": [],
                    "dup_pdfs": struct["dup_pdfs"],
                    "summary_message": ev_lines[0],
                },
                "target_pdfs_analyzed": {},
                "processed_pages_count": 0,
                "total_elapsed_seconds": round(time.perf_counter() - start_time, 2),
                "documents_detail": {},
                "ocr_consolidado": {
                    "seccion": folder_data.get("seccion_raw") or "00",
                    "libro": folder_data.get("libro_raw") or "00",
                    "tomo": folder_data.get("tomo_raw") or "00",
                    "volumen": folder_data.get("volumen_raw") or "00",
                    "fraccionamiento": folder_data.get("fraccionamiento_raw") or "00",
                    "anio": folder_data.get("anio_raw") or "0000",
                    "es_auxiliar": False,
                    "evidencias_globales": ev_lines,
                },
                "comparacion": {
                    "veredicto": "COINCIDE_TOTAL",
                    "discrepancias": [],
                    "coincidencias": ev_lines,
                    "propuestas_ceros": [],
                    "detalles_campos": {},
                },
                "veredicto_global": "COINCIDE_TOTAL",
                "subcarpeta_revision": "",
                "codigo_base_sugerido": clean_current_code,
                "needs_page_replacement": False,
                "needs_rename": False,
                "needs_apartar": False,
                "has_discrepancy": False,
                "cache_summary": self.cache.get_summary(),
            }

        # Paso 2C: Carpeta homogénea CON archivo de carátula independiente -> Inspección y análisis con Claude Vision (2 páginas)
        insp = CaratulaInspector.inspect_folder(folder_p, custom_caratula_path=custom_caratula_path)
        c_status = insp["status"]
        caratula_path_str = insp.get("caratula_path")
        info_page_num = insp.get("info_page_num")

        delegacion_fija = folder_data.get("delegacion") or "01"
        clean_current_code = FileOperations.strip_suffix_after_year(folder_p.name, folder_data)

        doc_results: Dict[str, Dict[str, Any]] = {}
        processed_pages_count = 0
        analyzed_pdfs_map: Dict[str, str] = {}

        if caratula_path_str:
            analyzed_pdfs_map["CARATULA"] = Path(caratula_path_str).name

        # Caso A: Carátula totalmente en blanco (0 tokens Claude) -> Apartar carpeta
        if c_status == "TOTALMENTE_BLANCA":
            ocr_final = {
                "seccion": "00",
                "libro": "00",
                "tomo": "00",
                "volumen": "00",
                "fraccionamiento": "00",
                "anio": "0000",
                "es_auxiliar": False,
                "evidencias_globales": [
                    f"⚪ [0 TOKENS] {insp.get('summary_message', 'Carátula totalmente en blanco.')}"
                ],
            }
            doc_results["CARATULA"] = {
                "pdf_file": insp.get("caratula_filename", ""),
                "evidencias": ocr_final["evidencias_globales"],
            }
            comparison = {
                "veredicto": "APARTAR_BLANCO",
                "discrepancias": [insp.get("summary_message", "Carátula en blanco")],
                "coincidencias": [],
                "propuestas_ceros": [],
                "detalles_campos": {},
            }
            has_dup = len(insp.get("dup_pdfs", [])) > 0
            has_reg = len(insp.get("irregular_pdfs", [])) > 0
            if has_dup and has_reg:
                sub_rev = "ARCHIVOS CON DUP Y REG"
            elif has_reg:
                sub_rev = "ARCHIVOS IRREGULARES REG"
            elif has_dup:
                sub_rev = "ARCHIVOS CON DUP"
            else:
                sub_rev = "CARATULA EN BLANCO"

            result = {
                "folder_path": str(folder_p),
                "folder_data": folder_data,
                "caratula_inspection": insp,
                "target_pdfs_analyzed": analyzed_pdfs_map,
                "processed_pages_count": 0,
                "total_elapsed_seconds": round(time.perf_counter() - start_time, 2),
                "documents_detail": doc_results,
                "ocr_consolidado": ocr_final,
                "comparacion": comparison,
                "veredicto_global": "APARTAR_CARATULA_BLANCA",
                "subcarpeta_revision": sub_rev,
                "codigo_base_sugerido": clean_current_code,
                "needs_page_replacement": len(insp.get("replaceable_pdfs", [])) > 0,
                "needs_rename": folder_p.name != clean_current_code,
                "needs_apartar": True,
                "has_discrepancy": True,
                "cache_summary": self.cache.get_summary(),
            }
            if save_to_db:
                self._save_diagnosis_result(result)
            return result

        # Caso B: No hay archivo de carátula o existen PDFs sin página de carátula (y no se forzó Claude)
        if c_status in ("SIN_ARCHIVO_CARATULA", "PDFS_SIN_CARATULA") and not force_claude_on_missing_caratula:
            missing_list = insp.get("pdfs_missing_caratula", [])
            ev_lines = [
                f"⚠ [0 TOKENS] {insp.get('summary_message', '')}"
            ]
            if missing_list:
                ev_lines.append(f"PDFs sin carátula ({len(missing_list)}): {', '.join(missing_list[:15])}")
                if len(missing_list) > 15:
                    ev_lines.append(f"... y {len(missing_list) - 15} archivos adicionales.")

            ocr_final = {
                "seccion": folder_data.get("seccion_raw") or "00",
                "libro": folder_data.get("libro_raw") or "00",
                "tomo": folder_data.get("tomo_raw") or "00",
                "volumen": folder_data.get("volumen_raw") or "00",
                "fraccionamiento": folder_data.get("fraccionamiento_raw") or "00",
                "anio": folder_data.get("anio_raw") or "0000",
                "es_auxiliar": False,
                "evidencias_globales": ev_lines,
            }
            doc_results["CARATULA"] = {
                "pdf_file": insp.get("caratula_filename") or "SIN CARÁTULA",
                "evidencias": ev_lines,
            }
            has_dup = len(insp.get("dup_pdfs", [])) > 0
            has_reg = len(insp.get("irregular_pdfs", [])) > 0
            if has_dup and has_reg:
                veredicto_g = "REVISION_DUP_Y_REG"
                sub_rev = "ARCHIVOS CON DUP Y REG"
            elif has_reg:
                veredicto_g = "REVISION_ARCHIVOS_REG"
                sub_rev = "ARCHIVOS IRREGULARES REG"
            elif has_dup:
                veredicto_g = "REVISION_ARCHIVOS_DUP"
                sub_rev = "ARCHIVOS CON DUP"
            else:
                veredicto_g = "SIN_ARCHIVO_CARATULA" if c_status == "SIN_ARCHIVO_CARATULA" else "DISCREPANCIA_SIN_CARATULA"
                sub_rev = "SIN ARCHIVO CARATULA" if c_status == "SIN_ARCHIVO_CARATULA" else "PDFS SIN CARATULA"
            comparison = {
                "veredicto": veredicto_g,
                "discrepancias": ev_lines,
                "coincidencias": [],
                "propuestas_ceros": [],
                "detalles_campos": {},
            }
            result = {
                "folder_path": str(folder_p),
                "folder_data": folder_data,
                "caratula_inspection": insp,
                "target_pdfs_analyzed": analyzed_pdfs_map,
                "processed_pages_count": 0,
                "total_elapsed_seconds": round(time.perf_counter() - start_time, 2),
                "documents_detail": doc_results,
                "ocr_consolidado": ocr_final,
                "comparacion": comparison,
                "veredicto_global": veredicto_g,
                "subcarpeta_revision": sub_rev,
                "codigo_base_sugerido": clean_current_code,
                "needs_page_replacement": False,
                "needs_rename": folder_p.name != clean_current_code,
                "needs_apartar": True,
                "has_discrepancy": True,
                "cache_summary": self.cache.get_summary(),
            }
            if save_to_db:
                self._save_diagnosis_result(result)
            return result

        # Caso C: Todos los PDFs tienen una página de la Carátula ('OK_CON_INFO' o 'REEMPLAZABLE_HOJA_BLANCA')
        # -> Analizar en Claude la página o páginas relevantes de la CARÁTULA
        caratula_pdf_path = Path(caratula_path_str)
        c_total_pages = int(insp.get("caratula_total_pages") or 1)
        info_pages = list(insp.get("info_pages", []))
        if not info_pages and (c_pages := insp.get("caratula_pages")):
            info_pages = [p["page_num"] for p in c_pages if p.get("has_text")]

        # Determinar qué páginas de la carátula analizar:
        # - Si tiene 1 página: analizar pág. 1.
        # - Si tiene 2 páginas: analizar AMBAS páginas (pág. 1 suele ser la tapa/guarda comercial
        #   y pág. 2 suele ser el Acta de Apertura oficial con sellos, o ambas se complementan).
        # - Si tiene 3 o más páginas: analizar todas las páginas con texto detectado (hasta 3), o las primeras 2.
        if c_total_pages == 1:
            target_pages = [1]
        elif c_total_pages == 2:
            target_pages = [1, 2]
        else:
            if info_pages:
                target_pages = sorted([p for p in info_pages if 1 <= p <= 3])
            else:
                target_pages = [1, 2]

        api_key = self.settings.get("api_key", "")
        model = self.settings.get("model", "claude-sonnet-5")
        use_local_cache = bool(self.settings.get("use_local_cache", True))
        use_prompt_cache = bool(self.settings.get("use_prompt_cache", True))
        max_dim = int(self.settings.get("image_max_dim", 1400))
        jpeg_quality = int(self.settings.get("jpeg_quality", 82))

        pages_label = ", ".join(str(p) for p in target_pages)
        pages_suffix = "_".join(str(p) for p in target_pages)
        cache_key = f"{self.cache.compute_pdf_key(caratula_pdf_path, max_pages=max(target_pages))}_pages_{pages_suffix}"
        cached_doc = self.cache.get(cache_key) if use_local_cache else None

        # Si está en caché pero el documento en caché tenía todos ceros (sin datos)
        # y la carátula tiene más páginas que las cacheadas, invalidar para reanalizar con Claude
        if cached_doc is not None:
            repaired_doc, changed = ClaudeVisionEngine.sanitize_cached_doc(cached_doc)
            if changed:
                cached_doc = repaired_doc
                if use_local_cache:
                    self.cache.set(cache_key, repaired_doc)

            cached_has_info = any(
                str(cached_doc.get(k, "00")).strip() not in ("00", "0", "0000", "", "None")
                for k in ("seccion", "libro", "tomo", "volumen", "fraccionamiento", "anio")
            )
            if not cached_has_info and c_total_pages > 1 and len(target_pages) < c_total_pages:
                cached_doc = None

        if cached_doc is not None:
            doc_summary = dict(cached_doc)
            doc_summary["pdf_file"] = caratula_pdf_path.name
            doc_summary["from_cache"] = True
            ev_list = list(doc_summary.get("evidencias", []))
            ev_list.insert(0, f"⚡ [CACHÉ LOCAL: 0 tokens | Carátula Pág(s). {pages_label}]")
            ev_list.insert(1, f"📄 {insp.get('summary_message', '')}")
            doc_summary["evidencias"] = ev_list
            processed_pages_count = len(target_pages)
            doc_results["CARATULA"] = doc_summary
        else:
            pages = ImageExtractor.extract_pages_by_numbers(
                caratula_pdf_path,
                page_nums=target_pages,
                dpi=PDF_RENDER_DPI
            )
            processed_pages_count = len(pages)

            prepared_pages = []
            for p_num, img_bgr in pages:
                enhanced = self.preprocessor.enhance_faint_text(img_bgr, apply_unsharp=True)
                prepared_pages.append((p_num, enhanced))

            parsed_doc, usage = ClaudeVisionEngine.analyze_document_pages(
                pages_bgr=prepared_pages,
                api_key=api_key,
                model=model,
                use_prompt_cache=use_prompt_cache,
                max_dim=max_dim,
                jpeg_quality=jpeg_quality,
                doc_role="CARATULA"
            )
            self.cache.record_api_usage(usage)

            in_tok = usage.get("input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            model_used = usage.get("model_used", model)

            parsed_doc["pages_analyzed"] = len(prepared_pages)
            if use_local_cache:
                self.cache.set(cache_key, parsed_doc)

            doc_summary = dict(parsed_doc)
            doc_summary["pdf_file"] = caratula_pdf_path.name
            doc_summary["from_cache"] = False
            ev_list = list(doc_summary.get("evidencias", []))
            ev_list.insert(
                0,
                f"🤖 [Claude {model_used} | Carátula Pág(s). {pages_label} | Input: {in_tok} tok | CacheRead: {cache_read} tok]"
            )
            ev_list.insert(1, f"📄 {insp.get('summary_message', '')}")
            doc_summary["evidencias"] = ev_list
            doc_results["CARATULA"] = doc_summary

        ocr_final = FolderComparator.synthesize_detections(doc_results)
        comparison = FolderComparator.compare(folder_data, ocr_final)

        # Verificar si se encontró al menos un dato real en la carátula
        found_any_ocr_info = any(
            str(ocr_final.get(k, "00")).strip() not in ("00", "0", "0000", "", "None")
            for k in ("seccion", "libro", "tomo", "volumen", "fraccionamiento", "anio")
        )

        folder_libro_norm = FolderComparator._normalize_for_comparison(folder_data.get("libro_val"))
        has_valid_libro = str(ocr_final.get("libro", "00")).strip() not in ("00", "0", "0000", "", "None", "-", "null")
        is_folder_libro_zero = (folder_libro_norm == 0)
        sin_datos_estructurales = (is_folder_libro_zero and not has_valid_libro)

        # REINTENTO AUTOMÁTICO (FALLBACK): Si no se encontró información (o solo año sin datos de libro)
        # y la carátula tiene páginas adicionales que no se enviaron inicialmente (ej. si c_total_pages >= 3 y solo se enviaron [1, 2]):
        if (not found_any_ocr_info or sin_datos_estructurales) and c_total_pages > len(target_pages):
            fallback_pages = [p for p in range(1, min(c_total_pages + 1, 4)) if p not in target_pages]
            if fallback_pages:
                fb_pages = ImageExtractor.extract_pages_by_numbers(
                    caratula_pdf_path,
                    page_nums=fallback_pages,
                    dpi=PDF_RENDER_DPI
                )
                if fb_pages:
                    fb_prepared = []
                    for p_num, img_bgr in fb_pages:
                        enhanced = self.preprocessor.enhance_faint_text(img_bgr, apply_unsharp=True)
                        fb_prepared.append((p_num, enhanced))

                    fb_parsed, fb_usage = ClaudeVisionEngine.analyze_document_pages(
                        pages_bgr=fb_prepared,
                        api_key=api_key,
                        model=model,
                        use_prompt_cache=use_prompt_cache,
                        max_dim=max_dim,
                        jpeg_quality=jpeg_quality,
                        doc_role="CARATULA"
                    )
                    self.cache.record_api_usage(fb_usage)

                    fb_has_info = any(
                        str(fb_parsed.get(k, "00")).strip() not in ("00", "0", "0000", "", "None")
                        for k in ("seccion", "libro", "tomo", "volumen", "fraccionamiento", "anio")
                    )
                    if fb_has_info:
                        target_pages.extend(fallback_pages)
                        pages_label = ", ".join(str(p) for p in target_pages)
                        fb_parsed["pages_analyzed"] = len(prepared_pages) + len(fb_prepared)
                        doc_summary = dict(fb_parsed)
                        doc_summary["pdf_file"] = caratula_pdf_path.name
                        doc_summary["from_cache"] = False
                        ev_list = list(doc_summary.get("evidencias", []))
                        fb_in_tok = fb_usage.get("input_tokens", 0)
                        fb_model = fb_usage.get("model_used", model)
                        ev_list.insert(
                            0,
                            f"🤖 [Claude {fb_model} | Reintento Carátula Pág(s). {','.join(map(str, fallback_pages))} | Input: {fb_in_tok} tok]"
                        )
                        doc_summary["evidencias"] = ev_list
                        doc_results["CARATULA"] = doc_summary
                        ocr_final = FolderComparator.synthesize_detections(doc_results)
                        comparison = FolderComparator.compare(folder_data, ocr_final)
                        found_any_ocr_info = True
                        has_valid_libro = str(ocr_final.get("libro", "00")).strip() not in ("00", "0", "0000", "", "None", "-", "null")
                        sin_datos_estructurales = (is_folder_libro_zero and not has_valid_libro)

        # Criterio estricto para Carátula Embebida:
        # Solo se considera Carátula Válida si contiene datos objetivos de apertura de libro (Libro y Año válidos).
        is_embedded_candidate = (c_status == "CARATULA_EMBEBIDA_CANDIDATA")
        has_valid_anio = str(ocr_final.get("anio", "0000")).strip() not in ("0000", "0", "00", "", "None", "-", "null")
        is_valid_embedded_caratula = False
        if is_embedded_candidate:
            is_valid_embedded_caratula = found_any_ocr_info and has_valid_libro and has_valid_anio

        # Criterio de Carátula Sin Datos Estructurales de Libro:
        # Si la carpeta tiene el campo libro en ceros (00) y la carátula NO contiene un Libro válido,
        # la carátula carece de los metadatos estructurales de apertura de libro (ej. es un título de propiedad,
        # folio individual, hoja sin acta, etc.). En este caso, aunque se haya extraído un año aislado,
        # NO es información suficiente para validar la carpeta como correcta. Debe apartarse a 'CARATULA SIN DATOS'.
        folder_libro_norm = FolderComparator._normalize_for_comparison(folder_data.get("libro_val"))
        is_folder_libro_zero = (folder_libro_norm == 0)
        sin_datos_estructurales = (is_folder_libro_zero and not has_valid_libro)

        embedded_to_create = None

        if not found_any_ocr_info or sin_datos_estructurales or (is_embedded_candidate and not is_valid_embedded_caratula):
            if is_embedded_candidate:
                # Regla solicitada: Si se probó la página 1 compartida pero no resultó ser una carátula válida
                # (o es un documento particular/en blanco sin datos de libro), NO se pasa a correctos: se aparta.
                codigo_sugerido = clean_current_code
                needs_page_replacement = False
                needs_rename = False
                veredicto_global = "SIN_ARCHIVO_CARATULA"
                subcarpeta_revision = "SIN ARCHIVO CARATULA"
                needs_apartar = True
                insp["status"] = "SIN_ARCHIVO_CARATULA"
                ocr_final["evidencias_globales"].append(
                    f"⚠ [CARÁTULA NO VÁLIDA] La Página 1 compartida en los archivos no contiene datos registrales válidos de apertura de libro "
                    f"(Libro={ocr_final.get('libro')}, Año={ocr_final.get('anio')}). Se clasifica para revisión en '{subcarpeta_revision}'."
                )
            else:
                # Regla normal: Si no se encuentra información suficiente de libro en la carátula,
                # dejar el nombre de la carpeta tal cual, solo limpiándolo hasta el año.
                codigo_sugerido = clean_current_code
                needs_page_replacement = (c_status == "REEMPLAZABLE_HOJA_BLANCA" or len(insp.get("replaceable_pdfs", [])) > 0)
                needs_rename = (folder_p.name != clean_current_code)
                veredicto_global = "SIN_INFO_LIMPIAR_NOMBRE" if needs_rename else "SIN_INFO_MANTENER_NOMBRE"
                subcarpeta_revision = "CARATULA SIN DATOS"
                needs_apartar = True
                if sin_datos_estructurales and found_any_ocr_info:
                    ocr_final["evidencias_globales"].append(
                        f"⚠ [CARÁTULA SIN DATOS DE LIBRO] La carátula no contiene acta de apertura con datos de Libro válidos "
                        f"(Libro={ocr_final.get('libro')}, Año={ocr_final.get('anio')}). Al estar la carpeta en ceros ('{clean_current_code}'), "
                        f"se conserva el nombre actual y se clasifica para revisión en subcarpeta '{subcarpeta_revision}'."
                    )
                else:
                    ocr_final["evidencias_globales"].append(
                        f"ℹ Sin información extraíble en carátula (revisadas pág(s). {pages_label}): se conserva el nombre actual limpio ('{clean_current_code}') "
                        f"y se clasifica para revisión en subcarpeta '{subcarpeta_revision}'."
                    )
        else:
            # Construir el código base sugerido hasta el Año (conservando siempre posición 1 = Delegación)
            codigo_sugerido = FileOperations.build_clean_folder_code(
                delegacion=delegacion_fija,
                seccion=ocr_final.get("seccion"),
                libro=ocr_final.get("libro"),
                tomo=ocr_final.get("tomo"),
                volumen=ocr_final.get("volumen"),
                fraccionamiento=ocr_final.get("fraccionamiento"),
                anio=ocr_final.get("anio"),
            )

            # No se modifican páginas binarias de PDFs; verificación 100% de solo lectura
            needs_page_replacement = False
            comp_veredicto = comparison.get("veredicto", "INDETERMINADO")
            needs_rename = False
            needs_apartar = False
            embedded_to_create = None

            has_missing_or_blank_internal = (
                c_status == "REEMPLAZABLE_HOJA_BLANCA"
                or len(insp.get("pdfs_missing_caratula", [])) > 0
                or len(insp.get("replaceable_pdfs", [])) > 0
            )

            if comp_veredicto == "SIN_DATOS_CARATULA":
                needs_apartar = True
                veredicto_global = "CARATULA_SIN_DATOS"
                subcarpeta_revision = "CARATULA SIN DATOS"
            elif has_missing_or_blank_internal:
                needs_apartar = True
                veredicto_global = "DISCREPANCIA_CARATULA_INTERNA"
                subcarpeta_revision = "PDFS CON CARATULA BLANCA O FALTANTE"
            elif comp_veredicto != "COINCIDE_TOTAL":
                needs_apartar = True
                needs_rename = True
                veredicto_global = comp_veredicto
                subcarpeta_revision = "DISCREPANCIA CARATULA VS CARPETA"
            else:
                veredicto_global = "COINCIDE_TOTAL"
                subcarpeta_revision = ""

        # REGLA ESTRICTA DE DUP Y REG: Si la carpeta contiene archivos con _DUP o archivos REG/irregulares,
        # se clasifica directamente para Apartado / Revisión y NUNCA se da por COINCIDE_TOTAL.
        has_dup = len(insp.get("dup_pdfs", [])) > 0
        has_reg = len(insp.get("irregular_pdfs", [])) > 0

        if has_dup or has_reg:
            needs_apartar = True
            if has_dup and has_reg:
                veredicto_global = "REVISION_DUP_Y_REG"
                subcarpeta_revision = "ARCHIVOS CON DUP Y REG"
                msg_flag = f"⚠ [APARTADO DIRECTO] Contiene {len(insp.get('dup_pdfs', []))} archivo(s) DUP y {len(insp.get('irregular_pdfs', []))} archivo(s) REG."
            elif has_reg:
                veredicto_global = "REVISION_ARCHIVOS_REG"
                subcarpeta_revision = "ARCHIVOS IRREGULARES REG"
                msg_flag = f"⚠ [APARTADO DIRECTO] Contiene {len(insp.get('irregular_pdfs', []))} archivo(s) irregular(es) tipo REG ({', '.join(insp.get('irregular_pdfs', [])[:5])})."
            else:
                veredicto_global = "REVISION_ARCHIVOS_DUP"
                subcarpeta_revision = "ARCHIVOS CON DUP"
                msg_flag = f"⚠ [APARTADO DIRECTO] Contiene {len(insp.get('dup_pdfs', []))} archivo(s) con sufijo DUP ({', '.join(insp.get('dup_pdfs', [])[:5])})."

            ocr_final.setdefault("evidencias_globales", []).append(f"{msg_flag} Clasificada para revisión en '{subcarpeta_revision}'.")
            comparison.setdefault("discrepancias", []).append(msg_flag)

        has_discrepancy = (veredicto_global != "COINCIDE_TOTAL") or needs_apartar

        result = {
            "folder_path": str(folder_p),
            "folder_data": folder_data,
            "caratula_inspection": insp,
            "target_pdfs_analyzed": analyzed_pdfs_map,
            "processed_pages_count": processed_pages_count,
            "total_elapsed_seconds": round(time.perf_counter() - start_time, 2),
            "documents_detail": doc_results,
            "ocr_consolidado": ocr_final,
            "comparacion": comparison,
            "veredicto_global": veredicto_global,
            "subcarpeta_revision": subcarpeta_revision,
            "codigo_base_sugerido": codigo_sugerido,
            "needs_page_replacement": needs_page_replacement,
            "needs_rename": needs_rename,
            "needs_apartar": needs_apartar,
            "has_discrepancy": has_discrepancy,
            "embedded_caratula_to_create": embedded_to_create,
            "cache_summary": self.cache.get_summary(),
        }

        if save_to_db:
            self._save_diagnosis_result(result)

        return result

    def _save_diagnosis_result(self, result: Dict[str, Any]) -> None:
        """Método mantenido por compatibilidad; los diagnósticos ya no usan SQLite."""
        return None

    # =========================================================================
    # FASE 2: OPERACIONES FÍSICAS Y ROLLBACK TOTAL
    # =========================================================================

    def execute_page_replacement(self, diag_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Reemplaza la hoja en blanco adjunta en los PDFs de la carpeta por la página
        con información de la Carátula, respaldando el original para Rollback.
        """
        raise RuntimeError(
            "Operación deshabilitada: reemplazar hojas modifica el contenido binario del PDF y NO es reversible. "
            "El flujo actual es solo-lectura sobre el contenido de los PDFs."
        )
        insp = diag_result.get("caratula_inspection", {})
        folder_path = diag_result.get("folder_path")
        caratula_path = insp.get("caratula_path")
        info_page_num = insp.get("info_page_num")
        replaceable_pdfs = insp.get("replaceable_pdfs", [])

        if not folder_path or not caratula_path or not info_page_num:
            raise ValueError("La carpeta no cuenta con datos válidos de página con información para reemplazo.")

        if not diag_result.get("initial_folder_path"):
            diag_result["initial_folder_path"] = str(folder_path)
            diag_result["initial_folder_name"] = Path(str(folder_path)).name

        res = FileOperations.replace_blank_caratula_in_folder_pdfs(
            folder_path=folder_path,
            caratula_path=caratula_path,
            info_page_num=int(info_page_num),
            replaceable_pdfs=replaceable_pdfs,
            db_manager=None,
            mysql_manager=self.mysql,
        )

        diag_result["needs_page_replacement"] = False
        insp["status"] = "OK_CON_INFO"
        insp["pdfs_with_blank_page"] = 0
        insp["replaceable_pdfs"] = []
        insp["summary_message"] = (
            f"✔ Hoja de carátula aplicada exitosamente (Pág. {info_page_num}) en {res['replaced_count']} PDF(s) (archivos REG intactos)."
        )
        if not diag_result.get("needs_apartar"):
            if diag_result.get("veredicto_global") == "REEMPLAZAR_HOJA_BLANCA":
                diag_result["veredicto_global"] = "COINCIDE_TOTAL"
                diag_result["has_discrepancy"] = False
            elif diag_result.get("veredicto_global") == "REEMPLAZAR_HOJA_Y_RENOMBRAR":
                diag_result["veredicto_global"] = "DISCREPANCIA"

        actions = list(diag_result.get("applied_actions", []))
        actions.append(f"🛠 Hoja blanca reemplazada ({res['replaced_count']} PDFs)")
        diag_result["applied_actions"] = actions
        diag_result["last_action"] = f"🛠 Hoja Reemplazada ({res['replaced_count']} PDFs)"
        diag_result["files_modified_count"] = diag_result.get("files_modified_count", 0) + res["replaced_count"]
        return res

    @exclusive_op("Renombrar carpeta")
    def execute_folder_and_pdfs_rename(
        self,
        diag_result: Dict[str, Any],
        custom_clean_base_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Renombra los PDFs internos con el código base hasta el año (SIN _2, _3) y
        renombra la carpeta con el código base (agregando _2, _3 si hay colisión de directorios).
        Siempre garantiza que la posición 1 (Delegación) permanezca intacta.
        """
        folder_path = diag_result.get("folder_path", "")
        folder_data = diag_result.get("folder_data", {})
        delegacion_fija = folder_data.get("delegacion") or "01"

        if not diag_result.get("initial_folder_path"):
            diag_result["initial_folder_path"] = str(folder_path)
            diag_result["initial_folder_name"] = Path(str(folder_path)).name

        code = (custom_clean_base_code or diag_result.get("codigo_base_sugerido") or "").strip()
        if not code:
            raise ValueError("No se especificó un código base válido para renombrar.")

        # Forzar la regla de negocio: la primera posición (Delegación) NUNCA se toca
        parts = code.split("_")
        if len(parts) >= 1 and parts[0] != str(delegacion_fija):
            parts[0] = str(delegacion_fija)
            code = "_".join(parts)

        old_name = Path(str(folder_path)).name
        ejec_id = None
        if self.mysql and getattr(self.mysql, "enabled", False):
            ejec_id = self.mysql.crear_ejecucion_lote("RENOMBRADO_INDIVIDUAL", {"folder": old_name, "target": code})
        res = FileOperations.rename_folder_and_internal_pdfs(
            folder_path=folder_path,
            clean_base_code=code,
            db_manager=None,
            mysql_manager=self.mysql,
            ejecucion_id=ejec_id,
        )
        if ejec_id is not None:
            self.mysql.finalizar_ejecucion_lote(ejec_id, 1, res.get("files_renamed_count", 0))
        # Actualizar la ruta, el parser, la carátula y la comparativa en tiempo real
        new_folder_p = Path(res["new_folder_path"])
        diag_result["folder_path"] = str(new_folder_p)
        diag_result["folder_data"] = FolderParser.parse(res["new_folder_name"])
        diag_result["codigo_base_sugerido"] = res["clean_base_code"]
        diag_result["needs_rename"] = False

        insp = diag_result.get("caratula_inspection", {})
        old_c_name = insp.get("caratula_filename", "")
        if old_c_name and old_c_name not in ("SIN CARÁTULA", "NO DETECTADA", "ERROR") and not old_c_name.startswith("[EMBEBIDA]"):
            new_c_name = FileOperations.build_new_pdf_name(old_c_name, res["clean_base_code"], old_name)
            new_c_path = new_folder_p / new_c_name
            if new_c_path.exists():
                insp["caratula_filename"] = new_c_name
                insp["caratula_path"] = str(new_c_path)

        # Creación física de carátulas deshabilitada (no reversible)
        emb_to_create = None
        if emb_to_create:
            target_c_name = f"{res['clean_base_code']}_CARATULA_00.pdf"
            src_pdf = Path(emb_to_create["source_pdf"])
            if not src_pdf.exists():
                src_pdf = new_folder_p / src_pdf.name
            if not src_pdf.exists():
                candidates = [f for f in new_folder_p.glob("*.pdf") if "CARATULA" not in f.name]
                if candidates:
                    src_pdf = candidates[0]
            if src_pdf.exists():
                c_res = FileOperations.create_caratula_from_pdf_page(
                    folder_path=new_folder_p,
                    source_pdf_path=src_pdf,
                    page_num=emb_to_create.get("page_num", 1),
                    target_caratula_name=target_c_name,
                    db_manager=None,
                    mysql_manager=self.mysql,
                )
                if c_res.get("created"):
                    insp["caratula_filename"] = target_c_name
                    insp["caratula_path"] = str(new_folder_p / target_c_name)
                    actions = list(diag_result.get("applied_actions", []))
                    actions.append(f"📄 Carátula física extraída y creada: {target_c_name}")
                    diag_result["applied_actions"] = actions


        ocr_final = diag_result.get("ocr_consolidado", {})
        found_any_ocr_info = any(
            str(ocr_final.get(k, "00")).strip() not in ("00", "0", "0000", "", "None", "-")
            for k in ("seccion", "libro", "tomo", "volumen", "fraccionamiento", "anio")
        )
        if found_any_ocr_info:
            diag_result["comparacion"] = FolderComparator.compare(diag_result["folder_data"], ocr_final)

        if diag_result.get("veredicto_global") == "SIN_INFO_LIMPIAR_NOMBRE":
            diag_result["veredicto_global"] = "SIN_INFO_MANTENER_NOMBRE"
        elif not diag_result.get("needs_page_replacement") and not diag_result.get("needs_apartar"):
            diag_result["veredicto_global"] = "COINCIDE_TOTAL"
            diag_result["has_discrepancy"] = False

        actions = list(diag_result.get("applied_actions", []))
        actions.append(f"✏️ Renombrada: {old_name} -> {res['new_folder_name']}")
        diag_result["applied_actions"] = actions
        diag_result["last_action"] = f"✏️ Renombrada ({res['new_folder_name']})"
        diag_result["files_modified_count"] = res["files_renamed_count"]

        return res

    def execute_embedded_caratula_creation(self, diag_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrae y genera físicamente el archivo de carátula _CARATULA_00.pdf a partir de la
        página 1 compartida en los archivos de la carpeta, independientemente de si la carpeta
        necesita renombrarse o no.
        """
        return {"created": False, "error": "Operación deshabilitada: crear carátulas modifica archivos y no es reversible."}
        emb_to_create = diag_result.get("embedded_caratula_to_create")
        if not emb_to_create:
            return {"created": False, "message": "No hay carátula embebida pendiente de creación."}

        folder_path = Path(diag_result.get("folder_path", ""))
        if not folder_path.exists():
            return {"created": False, "error": f"La carpeta no existe: {folder_path}"}

        clean_base = diag_result.get("codigo_base_sugerido") or FileOperations.strip_suffix_after_year(folder_path.name)
        target_c_name = emb_to_create.get("target_name") or f"{clean_base}_CARATULA_00.pdf"

        target_file = folder_path / target_c_name
        insp = diag_result.setdefault("caratula_inspection", {})
        if target_file.exists():
            insp["caratula_filename"] = target_c_name
            insp["caratula_path"] = str(target_file)
            insp["status"] = "OK_CON_INFO"
            diag_result["embedded_caratula_to_create"] = None
            return {"created": True, "already_existed": True, "caratula_path": str(target_file)}

        src_pdf = Path(emb_to_create.get("source_pdf", ""))
        if not src_pdf.exists():
            src_pdf = folder_path / src_pdf.name
        if not src_pdf.exists():
            candidates = [f for f in folder_path.glob("*.pdf") if "CARATULA" not in f.name]
            if candidates:
                src_pdf = candidates[0]

        if not src_pdf.exists():
            return {"created": False, "error": "No se encontró PDF origen para extraer la carátula."}

        c_res = FileOperations.create_caratula_from_pdf_page(
            folder_path=folder_path,
            source_pdf_path=src_pdf,
            page_num=emb_to_create.get("page_num", 1),
            target_caratula_name=target_c_name,
            db_manager=None,
            mysql_manager=self.mysql,
        )
        if c_res.get("created"):
            insp["caratula_filename"] = target_c_name
            insp["caratula_path"] = str(folder_path / target_c_name)
            insp["status"] = "OK_CON_INFO"
            diag_result["embedded_caratula_to_create"] = None
            actions = list(diag_result.get("applied_actions", []))
            actions.append(f"📄 Carátula física extraída y creada: {target_c_name}")
            diag_result["applied_actions"] = actions
            diag_result["last_action"] = f"📄 Carátula Creada ({target_c_name})"
            diag_result["files_modified_count"] = diag_result.get("files_modified_count", 0) + 1

        return c_res

    def execute_all_pending_corrections(self, diag_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta en un solo paso todas las correcciones físicas pendientes en disco:
        1. Reemplazo o inserción de carátula en PDFs regulares y DUP (excluyendo REG).
        2. Extracción y creación física de carátula embebida si aplica (independiente de renombrado).
        3. Renombrado de carpeta y PDFs regulares y DUP (excluyendo REG).
        4. Re-evaluación del veredicto final.
        Todo queda respaldado en SQLite y backups_rollback/ para Rollback total.
        """
        applied_now = []
        modified_count = 0

        # Paso 1: Reemplazo o inserción de carátulas en blanco
        if diag_result.get("needs_page_replacement") and diag_result.get("caratula_inspection", {}).get("replaceable_pdfs"):
            try:
                rep_res = self.execute_page_replacement(diag_result)
                cnt = rep_res.get("replaced_count", 0)
                modified_count += cnt
                applied_now.append(f"🛠 Carátula corregida en {cnt} PDF(s)")
            except Exception as exc:
                applied_now.append(f"⚠ Error al corregir carátula: {exc}")

        # Paso 2: Extracción y creación física de carátula embebida (independiente de si necesita renombre)
        if diag_result.get("embedded_caratula_to_create"):
            try:
                emb_res = self.execute_embedded_caratula_creation(diag_result)
                if emb_res.get("created"):
                    modified_count += 1
                    applied_now.append("📄 Carátula física extraída y creada")
            except Exception as exc:
                applied_now.append(f"⚠ Error al crear carátula física: {exc}")

        # Paso 3: Renombrado de carpeta y PDFs (conservando DUP e ignorando REG)
        if diag_result.get("needs_rename"):
            try:
                ren_res = self.execute_folder_and_pdfs_rename(diag_result)
                cnt = ren_res.get("files_renamed_count", 0)
                modified_count += cnt
                applied_now.append(f"✏️ Renombrada carpeta y {cnt} PDF(s)")
            except Exception as exc:
                applied_now.append(f"⚠ Error al renombrar: {exc}")

        # Paso 4: Re-evaluación inmediata de estado
        insp = diag_result.get("caratula_inspection", {})
        has_dup = len(insp.get("dup_pdfs", [])) > 0
        has_reg = len(insp.get("irregular_pdfs", [])) > 0
        folder_p = Path(diag_result.get("folder_path", ""))
        clean_code = diag_result.get("codigo_base_sugerido") or FileOperations.strip_suffix_after_year(folder_p.name)
        caratula_file = folder_p / f"{clean_code}_CARATULA_00.pdf"
        caratula_exists = caratula_file.exists() or (insp.get("caratula_path") and Path(insp["caratula_path"]).exists())

        if caratula_exists and not has_dup and not has_reg and not diag_result.get("needs_page_replacement"):
            if folder_p.name == clean_code:
                diag_result["veredicto_global"] = "COINCIDE_TOTAL"
                diag_result["needs_rename"] = False
                diag_result["has_discrepancy"] = False
                insp["status"] = "OK_CON_INFO"

        return {
            "success": True,
            "modified_count": modified_count,
            "applied_actions": applied_now,
            "folder_path": diag_result.get("folder_path"),
        }

    def audit_folder_congruence(self, diag_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Audita la congruencia física y estructural entre la información en memoria/BD
        y el estado real del disco en este momento.
        """
        folder_path_str = diag_result.get("folder_path", "")
        folder_p = Path(folder_path_str)
        discrepancies: List[str] = []
        suggested_action = "OK"

        if not folder_p.exists():
            discrepancies.append(f"Carpeta no encontrada en ruta actual: {folder_p.name}")
            return {
                "folder_name": folder_p.name,
                "exists_on_disk": False,
                "is_congruent": False,
                "discrepancies": discrepancies,
                "suggested_action": "Buscar carpeta en destino o actualizar ruta",
                "physical_caratula": None,
                "physical_pdfs_count": 0,
            }

        pdfs = list(folder_p.glob("*.pdf"))
        caratulas = [f.name for f in pdfs if "CARATULA" in f.name.upper()]
        has_physical_caratula = len(caratulas) > 0
        physical_c_name = caratulas[0] if caratulas else None

        insp = diag_result.get("caratula_inspection", {})
        mem_c_name = insp.get("caratula_filename", "")
        is_mem_embedded = mem_c_name.startswith("[EMBEBIDA]") or (diag_result.get("embedded_caratula_to_create") is not None)

        if has_physical_caratula and is_mem_embedded:
            discrepancies.append(f"Carátula física '{physical_c_name}' YA existe en disco, pero en memoria figura como '{mem_c_name}'")
            suggested_action = "Reconocer Carátula Física OK_CON_INFO"

        clean_sugerido = diag_result.get("codigo_base_sugerido") or FileOperations.strip_suffix_after_year(folder_p.name)
        if diag_result.get("needs_rename") and folder_p.name == clean_sugerido:
            discrepancies.append(f"Marcada como 'needs_rename=True', pero el nombre en disco '{folder_p.name}' ya coincide con el sugerido")
            suggested_action = "Limpiar bandera de renombre y validar COINCIDE_TOTAL"

        v_global = diag_result.get("veredicto_global", "")
        has_dup = len(insp.get("dup_pdfs", [])) > 0
        has_reg = len(insp.get("irregular_pdfs", [])) > 0
        if has_physical_caratula and folder_p.name == clean_sugerido and not has_dup and not has_reg:
            if v_global not in ("COINCIDE_TOTAL", "MOVIDO_A_CORRECTOS"):
                discrepancies.append(f"Veredicto en memoria es '{v_global}', pero físicamente ya cumple COINCIDE_TOTAL")
                suggested_action = "Actualizar a COINCIDE_TOTAL"

        is_congruent = (len(discrepancies) == 0)
        return {
            "folder_name": folder_p.name,
            "exists_on_disk": True,
            "is_congruent": is_congruent,
            "discrepancies": discrepancies,
            "suggested_action": suggested_action,
            "physical_caratula": physical_c_name,
            "physical_pdfs_count": len(pdfs),
        }

    def resync_folder_with_physical_disk(self, diag_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resincroniza el objeto de diagnóstico en memoria y en la base de datos con el
        estado real físico del disco en este instante.
        """
        folder_p = Path(diag_result.get("folder_path", ""))
        if not folder_p.exists():
            return {"success": False, "error": f"La carpeta no existe en disco: {folder_p}"}

        pdfs = list(folder_p.glob("*.pdf"))
        caratulas = [f for f in pdfs if "CARATULA" in f.name.upper()]
        insp = diag_result.setdefault("caratula_inspection", {})
        applied_changes = []

        if caratulas:
            c_file = caratulas[0]
            if insp.get("caratula_filename") != c_file.name or insp.get("status") != "OK_CON_INFO":
                insp["caratula_filename"] = c_file.name
                insp["caratula_path"] = str(c_file)
                insp["status"] = "OK_CON_INFO"
                diag_result["embedded_caratula_to_create"] = None
                applied_changes.append(f"Reconocida carátula física: {c_file.name}")

        diag_result["folder_data"] = FolderParser.parse(folder_p.name)
        clean_code = diag_result.get("codigo_base_sugerido") or FileOperations.strip_suffix_after_year(folder_p.name)

        has_dup = len(insp.get("dup_pdfs", [])) > 0
        has_reg = len(insp.get("irregular_pdfs", [])) > 0

        if folder_p.name == clean_code:
            if diag_result.get("needs_rename"):
                diag_result["needs_rename"] = False
                applied_changes.append("Bandera de renombre retirada (nombre ya coincide)")

        if caratulas and folder_p.name == clean_code and not has_dup and not has_reg:
            if diag_result.get("veredicto_global") not in ("COINCIDE_TOTAL", "MOVIDO_A_CORRECTOS"):
                diag_result["veredicto_global"] = "COINCIDE_TOTAL"
                diag_result["has_discrepancy"] = False
                diag_result["needs_apartar"] = False
                applied_changes.append("Veredicto actualizado a COINCIDE_TOTAL")

        if self.mysql and getattr(self.mysql, "enabled", False):
            # Ya no se usa SQLite
            pass

        return {
            "success": True,
            "folder_name": folder_p.name,
            "applied_changes": applied_changes,
        }

    @exclusive_op("Mover a Apartados")
    def execute_move_to_apartados(
        self,
        diag_result: Dict[str, Any],
        apartados_dir: str,
        custom_error_subfolder: Optional[str] = None,
        ejecucion_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Envía una carpeta que requiere revisión al directorio de Apartados seleccionado,
        dentro de una subcarpeta con descripción rápida del error, registrando en MySQL.
        """
        folder_path = diag_result.get("folder_path", "")
        if not diag_result.get("initial_folder_path"):
            diag_result["initial_folder_path"] = str(folder_path)
            diag_result["initial_folder_name"] = Path(str(folder_path)).name

        insp = diag_result.get("caratula_inspection", {})
        error_sub = (
            custom_error_subfolder
            or diag_result.get("subcarpeta_revision")
            or FileOperations.determine_review_subfolder(
                caratula_status=insp.get("status", ""),
                veredicto_global=diag_result.get("veredicto_global", ""),
            )
        )
        own_batch = False
        if ejecucion_id is None and self.mysql and getattr(self.mysql, "enabled", False):
            ejecucion_id = self.mysql.crear_ejecucion_lote(
                "MOVER_APARTADO_INDIVIDUAL",
                {"folder": Path(str(folder_path)).name, "subcarpeta": error_sub},
            )
            own_batch = True

        res = FileOperations.move_folder_to_apartados(
            folder_path=folder_path,
            apartados_dir=apartados_dir,
            db_manager=None,
            tipo_movimiento=f"APARTADO: {error_sub}",
            error_subfolder=error_sub,
            mysql_manager=self.mysql,
            ejecucion_id=ejecucion_id,
        )
        if own_batch and ejecucion_id is not None:
            self.mysql.finalizar_ejecucion_lote(ejecucion_id, 1, res.get("files_moved_count", 0))

        new_p = Path(res["new_folder_path"])
        diag_result["folder_path"] = str(new_p)
        diag_result["folder_data"] = FolderParser.parse(new_p.name)
        diag_result["subcarpeta_revision"] = error_sub
        diag_result["needs_apartar"] = False
        diag_result["needs_rename"] = False
        diag_result["veredicto_global"] = "APARTADO_COMPLETADO"
        diag_result["has_discrepancy"] = False
        diag_result["moved_destination_type"] = "APARTADOS"

        actions = list(diag_result.get("applied_actions", []))
        actions.append(f"📦 Apartada en '{error_sub}' -> {new_p.name}")
        diag_result["applied_actions"] = actions
        diag_result["last_action"] = f"📦 Apartada ({error_sub})"
        diag_result["files_modified_count"] = res["files_moved_count"]
        return res

    @exclusive_op("Mover a Correctos")
    def execute_move_to_validados(
        self,
        diag_result: Dict[str, Any],
        validados_dir: str,
        ejecucion_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Mueve una carpeta que está totalmente bien (COINCIDE_TOTAL) al directorio de carpetas
        correctas/validadas especificado por el usuario y lo registra en MySQL con Rollback por lotes.
        """
        folder_path = diag_result.get("folder_path", "")
        if not diag_result.get("initial_folder_path"):
            diag_result["initial_folder_path"] = str(folder_path)
            diag_result["initial_folder_name"] = Path(str(folder_path)).name

        own_batch = False
        if ejecucion_id is None and self.mysql and getattr(self.mysql, "enabled", False):
            ejecucion_id = self.mysql.crear_ejecucion_lote(
                "MOVER_CORRECTO_INDIVIDUAL",
                {"folder": Path(str(folder_path)).name},
            )
            own_batch = True

        res = FileOperations.move_folder_to_validados(
            folder_path=folder_path,
            validados_dir=validados_dir,
            db_manager=None,
            mysql_manager=self.mysql,
            ejecucion_id=ejecucion_id,
        )
        if own_batch and ejecucion_id is not None:
            self.mysql.finalizar_ejecucion_lote(ejecucion_id, 1, res.get("files_moved_count", 0))

        new_p = Path(res["new_folder_path"])
        diag_result["folder_path"] = str(new_p)
        diag_result["folder_data"] = FolderParser.parse(new_p.name)
        diag_result["veredicto_global"] = "MOVIDO_A_CORRECTOS"
        diag_result["has_discrepancy"] = False
        diag_result["needs_rename"] = False
        diag_result["needs_apartar"] = False
        diag_result["needs_page_replacement"] = False
        diag_result["moved_destination_type"] = "CORRECTOS"

        actions = list(diag_result.get("applied_actions", []))
        actions.append(f"✅ Movida a Correctos -> {new_p.name}")
        diag_result["applied_actions"] = actions
        diag_result["last_action"] = "✅ Movida a Correctos"
        diag_result["files_modified_count"] = res["files_moved_count"]
        return res

    # =========================================================================
    # NUEVO FLUJO DE 3 PASOS + ROLLBACK POR LOTES (100% MYSQL)
    # =========================================================================

    def _destino_paso1(self, sf: Path) -> Tuple[Optional[str], str, str]:
        """
        REGLA DEL PASO 1 (única fuente de verdad, usada por la vista previa y por la ejecución):
        1. Se toma el PRIMER PDF en orden alfabético que no sea carátula (_CARATULA) ni irregular (REG).
           Si no hay ninguno, se usa el primer PDF de la carpeta.
        2. El nombre propuesto para la carpeta se toma DIRECTAMENTE del nombre del primer PDF hasta el año:
           - La Delegación (ej. '01') se toma DIRECTAMENTE del primer PDF (nunca de la carpeta, evitando destruir 01).
           - El año (ej. '1982' o '0000') se conserva tal cual al final (evitando el año en ambos extremos).
           - Quita sufijos de duplicado (_DUP, _DUP1).
        3. Si ese PDF no tiene año detectable -> la carpeta se OMITE (no se toca).
        4. Si ya existe otra carpeta con ese nombre se agrega _2, _3...
        Solo se renombra la CARPETA. Los PDFs internos NO se tocan.
        Retorna (nombre_destino | None, motivo, nombre_del_pdf_base).
        """
        pdfs = sorted(
            [f for f in sf.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"],
            key=lambda x: x.name,
        )
        if not pdfs:
            return None, "Sin PDFs: no se toca", ""
        regulars = [
            p for p in pdfs
            if "CARATULA" not in p.name.upper()
            and not CaratulaInspector.is_irregular_reg_pdf_name(p.name)
        ]
        first_pdf = regulars[0] if regulars else pdfs[0]

        code, detail = FolderRenamerByFirstPdf.get_first_pdf_code_up_to_year(first_pdf.name, sf.name)
        if not code:
            return None, detail or f"El 1er PDF '{first_pdf.name}' no tiene posición de año válida: no se toca", first_pdf.name

        return code, detail or "OK", first_pdf.name

    def planificar_paso1(self, root_path: str) -> Dict[str, Any]:
        """Vista previa (SOLO LECTURA) de lo que haría el Paso 1, simulando colisiones _2/_3."""
        subfolders = self.get_valid_subfolders(root_path)
        taken = {p.name.lower() for p in Path(root_path).iterdir() if p.is_dir()} if Path(root_path).is_dir() else set()
        items: List[Dict[str, Any]] = []
        for sf in subfolders:
            code, reason, base_pdf = self._destino_paso1(sf)
            if code is None:
                items.append({"carpeta": sf.name, "nuevo": "", "pdf_base": base_pdf, "accion": "OMITIR", "motivo": reason})
                continue
            if code.lower() == sf.name.lower():
                items.append({"carpeta": sf.name, "nuevo": sf.name, "pdf_base": base_pdf, "accion": "SIN_CAMBIO", "motivo": "Ya tiene el nombre correcto"})
                continue
            final = code
            n = 2
            while final.lower() in taken:
                final = f"{code}_{n}"
                n += 1
            taken.discard(sf.name.lower())
            taken.add(final.lower())
            items.append({
                "carpeta": sf.name, "nuevo": final, "pdf_base": base_pdf, "accion": "RENOMBRAR",
                "motivo": "Con sufijo por colisión" if final != code else "OK",
            })
        return {
            "total": len(items),
            "renombrar": sum(1 for i in items if i["accion"] == "RENOMBRAR"),
            "sin_cambio": sum(1 for i in items if i["accion"] == "SIN_CAMBIO"),
            "omitir": sum(1 for i in items if i["accion"] == "OMITIR"),
            "colisiones": sum(1 for i in items if i["accion"] == "RENOMBRAR" and i["motivo"] != "OK"),
            "items": items,
        }

    def planificar_paso2_estructura(self, root_path: str) -> Dict[str, Any]:
        """
        Vista previa (SOLO LECTURA, 0 TOKENS) del Chequeo de Estructura pre-IA:
        1. Carpetas que no siguen el patrón de 7 posiciones o con PDFs irregulares (REG) -> APARTADOS directo (Paso 2A).
        2. Carpetas homogéneas sin archivo de carátula externa -> CORRECTOS directo (carátula embebida, Paso 2B).
        3. Carpetas homogéneas con archivo de carátula externa -> REQUIERE IA (análisis visual de carátula, Paso 2C).
        """
        subfolders = self.get_valid_subfolders(root_path)
        items: List[Dict[str, Any]] = []
        for sf in subfolders:
            struct = self.check_folder_structure(sf)
            if not struct["is_homogeneous"]:
                items.append({
                    "carpeta": sf.name,
                    "ruta": str(sf),
                    "total_pdfs": struct["total_pdfs"],
                    "tiene_caratula": struct["has_caratula_file"],
                    "clasificacion": "APARTADOS",
                    "accion": "Directo a Apartados (0 tokens)",
                    "motivo": struct["reason"],
                })
            elif not struct["has_caratula_file"]:
                items.append({
                    "carpeta": sf.name,
                    "ruta": str(sf),
                    "total_pdfs": struct["total_pdfs"],
                    "tiene_caratula": False,
                    "clasificacion": "CORRECTOS",
                    "accion": "Directo a Correctos (Carátula embebida, 0 tokens)",
                    "motivo": "Estructura 100% homogénea sin archivo de carátula externa",
                })
            else:
                c_names = ", ".join(struct["caratula_files"][:2])
                items.append({
                    "carpeta": sf.name,
                    "ruta": str(sf),
                    "total_pdfs": struct["total_pdfs"],
                    "tiene_caratula": True,
                    "clasificacion": "REQUIERE_IA",
                    "accion": "Requiere cotejo visual con IA (Paso 2C)",
                    "motivo": f"Carátula externa detectada ({c_names})",
                })

        total = len(items)
        apartados = sum(1 for i in items if i["clasificacion"] == "APARTADOS")
        correctos = sum(1 for i in items if i["clasificacion"] == "CORRECTOS")
        requiere_ia = sum(1 for i in items if i["clasificacion"] == "REQUIERE_IA")
        return {
            "total": total,
            "apartados": apartados,
            "correctos": correctos,
            "requiere_ia": requiere_ia,
            "items": items,
        }

    @exclusive_op("Paso 1: Indexar y Renombrar")
    def ejecutar_paso1_indexar_y_renombrar(
        self,
        root_path: str,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        PASO 1 (ver `_destino_paso1` para la regla exacta):
        indexa el estado original en MySQL y renombra SOLO carpetas. Cada renombre se registra en el
        lote; si el registro falla, el renombre se deshace al instante (nada queda sin rollback).
        """
        self.reload_settings()
        ok_db, msg_db = self.mysql.init_database_and_tables()
        if not ok_db:
            raise RuntimeError(f"No se pudo conectar/inicializar MySQL: {msg_db}")

        subfolders = self.get_valid_subfolders(root_path)
        total = len(subfolders)
        if total == 0:
            return {
                "total_carpetas": 0, "nuevas_indexadas": 0, "archivos_indexados": 0,
                "renombradas": 0, "archivos_renombrados": 0, "errores": [], "omitidas": [], "ejecucion_id": None,
            }

        carpetas_info: List[Dict[str, Any]] = []
        for sf in subfolders:
            pdfs = sorted(
                [f for f in sf.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"],
                key=lambda x: x.name,
            )
            carpetas_info.append({
                "nombre_original": sf.name,
                "ruta_original": str(sf.resolve()),
                "pdf_names": [p.name for p in pdfs],
            })

        total_idx, nuevas_idx, files_idx, uuid_map = self.mysql.indexar_estado_inicial(carpetas_info)
        ejecucion_id = self.mysql.crear_ejecucion_lote(
            tipo_ejecucion="PASO_1_INDEXAR_Y_RENOMBRAR",
            detalles={"root_path": str(root_path), "total_carpetas": total, "nuevas_indexadas": nuevas_idx},
        )

        renamed_folders = 0
        errors: List[str] = []
        omitted: List[str] = []
        try:
            for idx, sf in enumerate(subfolders, start=1):
                if progress_cb:
                    progress_cb(idx, total, f"Paso 1 ({idx}/{total}): {sf.name}")
                try:
                    code, reason, _ = self._destino_paso1(sf)
                    if code is None:
                        omitted.append(f"{sf.name}: {reason}")
                        continue
                    c_uuid = uuid_map.get(str(sf.resolve()))
                    target = FileOperations.resolve_directory_collision(
                        parent_dir=sf.parent, base_folder_name=code, current_folder_path=sf,
                    ).resolve()
                    if target == sf.resolve():
                        continue
                    old_str, old_name = str(sf.resolve()), sf.name
                    n_pdfs = len([f for f in sf.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"])
                    os.replace(old_str, str(target))
                    try:
                        if not c_uuid:
                            raise RuntimeError("carpeta sin UUID en el índice")
                        self.mysql.registrar_movimiento_carpeta(
                            ejecucion_id=ejecucion_id, carpeta_uuid=c_uuid, tipo_accion="RENOMBRADO_CARPETA",
                            nombre_anterior=old_name, nombre_nuevo=target.name,
                            ruta_anterior=old_str, ruta_nueva=str(target), cantidad_archivos=n_pdfs,
                        )
                    except Exception as log_exc:
                        os.replace(str(target), old_str)  # sin registro no hay rollback: se deshace
                        raise RuntimeError(f"no se pudo registrar en MySQL, renombre deshecho: {log_exc}")
                    renamed_folders += 1
                except Exception as exc:
                    errors.append(f"{sf.name}: {exc}")
        finally:
            self.mysql.finalizar_ejecucion_lote(
                ejecucion_id=ejecucion_id,
                total_carpetas=renamed_folders,
                total_archivos=0,
                estado="COMPLETADO" if not errors else "COMPLETADO_CON_ERRORES",
                detalles={"errores": errors[:20], "omitidas": omitted[:50]},
            )

        return {
            "ejecucion_id": ejecucion_id,
            "total_carpetas": total,
            "nuevas_indexadas": nuevas_idx,
            "archivos_indexados": files_idx,
            "renombradas": renamed_folders,
            "archivos_renombrados": 0,
            "errores": errors,
            "omitidas": omitted,
        }

    @exclusive_op("Paso 3: Traslado por lote")
    def ejecutar_paso3_trasladar_lote(
        self,
        results_list: List[Dict[str, Any]],
        target_dir: str,
        mode: str,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        PASO 3:
        Traslada un lote de carpetas ('CORRECTOS' o 'APARTADOS') registrando la ejecución completa
        en `ejecuciones_lotes` para permitir Rollback exacto por lote.
        """
        self.reload_settings()
        is_correctos = str(mode).upper() == "CORRECTOS"
        candidates = [
            r for r in results_list
            if (
                (r.get("veredicto_global") == "COINCIDE_TOTAL" and not r.get("needs_apartar"))
                if is_correctos
                else r.get("needs_apartar")
            )
        ]
        if not candidates:
            return {"ejecucion_id": None, "movidas": 0, "archivos_movidos": 0, "errores": []}

        tipo_ejec = "PASO_3_MOVER_CORRECTOS" if is_correctos else "PASO_3_MOVER_APARTADOS"
        ejecucion_id = self.mysql.crear_ejecucion_lote(
            tipo_ejecucion=tipo_ejec,
            detalles={"target_dir": str(target_dir), "candidatos": len(candidates)},
        )

        movidas = 0
        archivos_movidos = 0
        errors: List[str] = []
        total = len(candidates)

        for idx, res in enumerate(candidates, start=1):
            fname = res.get("folder_data", {}).get("folder_name") or Path(res.get("folder_path", "")).name
            if progress_cb:
                progress_cb(idx, total, f"Paso 3 ({idx}/{total}): {fname}")
            try:
                if is_correctos:
                    mv = self.execute_move_to_validados(res, target_dir, ejecucion_id=ejecucion_id)
                else:
                    mv = self.execute_move_to_apartados(res, target_dir, ejecucion_id=ejecucion_id)
                movidas += 1
                archivos_movidos += int(mv.get("files_moved_count", 0))
            except Exception as exc:
                errors.append(f"{fname}: {exc}")

        self.mysql.finalizar_ejecucion_lote(
            ejecucion_id=ejecucion_id,
            total_carpetas=movidas,
            total_archivos=archivos_movidos,
            estado="COMPLETADO" if not errors else "COMPLETADO_CON_ERRORES",
            detalles={"errores": errors[:20]} if errors else None,
        )

        return {
            "ejecucion_id": ejecucion_id,
            "movidas": movidas,
            "archivos_movidos": archivos_movidos,
            "errores": errors,
        }

    def obtener_historial_ejecuciones(self) -> List[Dict[str, Any]]:
        return self.mysql.obtener_historial_ejecuciones()

    @exclusive_op("Rollback de lote")
    def ejecutar_rollback_ultima_ejecucion(self) -> Dict[str, Any]:
        return self.mysql.revertir_ultima_ejecucion()

    @exclusive_op("Reset maestro")
    def full_system_reset(self, clear_cache: bool = False) -> Dict[str, Any]:
        """
        Revierte todos los lotes en orden cronológico inverso y limpia MySQL.
        """
        reverted_batches = 0
        rb_errors: List[str] = []
        if self.mysql and getattr(self.mysql, "enabled", False):
            while self.mysql.obtener_ultima_ejecucion_revertible():
                rb = self.mysql.revertir_ultima_ejecucion()
                if rb.get("success"):
                    reverted_batches += 1
                else:
                    rb_errors.extend(rb.get("errors") or [rb.get("message", "Error en rollback")])
                    break

        if rb_errors:
            # NO se borra la BD si el rollback quedó incompleto: perderíamos la única forma de recuperar.
            mysql_ok, mysql_msg = False, "BD conservada: el rollback quedó incompleto (reintenta el rollback)"
        else:
            mysql_ok, mysql_msg = self.mysql.reset_database() if (self.mysql and getattr(self.mysql, "enabled", False)) else (True, "MySQL no activo")

        cache_cleared = False
        if clear_cache:
            try:
                self.cache.clear_disk_cache()
                cache_cleared = True
            except Exception:
                pass

        return {
            "reverted_batches": reverted_batches,
            "rollback_errors": rb_errors,
            "mysql_reset": {"ok": mysql_ok, "msg": mysql_msg},
            "cache_cleared": cache_cleared,
        }

    @staticmethod
    def _build_error_result(folder_path: Path, error_msg: str) -> Dict[str, Any]:
        folder_data = FolderParser.parse(folder_path.name)
        return {
            "folder_path": str(folder_path),
            "folder_data": folder_data,
            "caratula_inspection": {
                "status": "ERROR",
                "caratula_filename": "ERROR",
                "summary_message": error_msg,
            },
            "target_pdfs_analyzed": {},
            "processed_pages_count": 0,
            "total_elapsed_seconds": 0.0,
            "documents_detail": {
                "ERROR": {
                    "pdf_file": folder_path.name,
                    "evidencias": [error_msg],
                }
            },
            "ocr_consolidado": {
                "seccion": "00", "libro": "00", "tomo": "00",
                "volumen": "00", "fraccionamiento": "00", "anio": "0000",
                "es_auxiliar": False, "evidencias_globales": [error_msg],
            },
            "comparacion": {
                "veredicto": "ERROR",
                "coincidencias": [],
                "discrepancias": [error_msg],
                "propuestas_ceros": [],
                "detalles_campos": {},
            },
            "veredicto_global": "ERROR",
            "subcarpeta_revision": "ERROR EN ARCHIVOS",
            "codigo_base_sugerido": folder_path.name,
            "needs_page_replacement": False,
            "needs_rename": False,
            "needs_apartar": True,
            "has_discrepancy": True,
            "error": error_msg,
        }
