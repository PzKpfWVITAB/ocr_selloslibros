from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
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
from src.database.db_manager import DatabaseManager
from src.parsers.folder_parser import FolderParser
from src.matcher.comparator import FolderComparator
from src.database.mysql_manager import MySQLManager


class FolderOCRService:
    """
    Servicio integral de Diagnóstico General (Fase 1), Ejecución Física (Fase 2),
    Auditoría en SQLite ('auditoria_libros.db'), Trazabilidad MySQL y Rollback Total:
    - Filtro de raíz: Solo revisa carpetas con '_' en el primer nivel de la raíz seleccionada
      (ignorando subcarpetas anidadas y carpetas sin '_' como BUENOS y MALOS).
    - Ahorro máximo de tokens en Claude Sonnet 5:
      1. Inspección local de la CARÁTULA y todos los PDFs de la carpeta (0 tokens).
      2. Si la carátula está totalmente en blanco -> APARTAR_CARATULA_BLANCA (0 tokens).
      3. Si hay PDFs que no tienen ninguna página de la carátula -> DISCREPANCIA_SIN_CARATULA (0 tokens).
      4. Si todos los PDFs tienen la carátula (ya sea la página con info o la página en blanco
         reemplazable), envía a Claude ÚNICAMENTE la página con información de la CARÁTULA (1 sola imagen
         por carpeta + Caché Local SHA-256 + Prompt Caching de Anthropic).
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.preprocessor = ImagePreprocessor(
            clip_limit=CLAHE_CLIP_LIMIT,
            tile_grid_size=CLAHE_TILE_GRID_SIZE
        )
        self.cache = CacheManager()
        self.settings = SettingsManager.load()
        self.db = DatabaseManager(db_path=db_path)
        self.mysql = MySQLManager(self.settings)

    def reload_settings(self) -> Dict[str, Any]:
        self.settings = SettingsManager.load()
        self.mysql.update_config(
            host=str(self.settings.get("mysql_host", "127.0.0.1")),
            port=int(self.settings.get("mysql_port", 3306)),
            user=str(self.settings.get("mysql_user", "root")),
            password=str(self.settings.get("mysql_password", "")),
            database=str(self.settings.get("mysql_database", "trazabilidad_selloslibros")),
            enabled=bool(self.settings.get("mysql_enabled", False))
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

    def process_folder(
        self,
        folder_path: str,
        custom_caratula_path: Optional[str] = None,
        save_to_db: bool = True,
        force_claude_on_missing_caratula: bool = False,
    ) -> Dict[str, Any]:
        """
        FASE 1 (Diagnóstico General 100% Lectura de una carpeta):
        1. Inspecciona localmente el PDF de la Carátula (páginas con texto vs en blanco) y
           comprueba si todos los PDFs de la carpeta tienen adjunta una página de la Carátula.
        2. Si la Carátula está totalmente en blanco -> 'APARTAR_CARATULA_BLANCA' (0 tokens).
        3. Si hay PDFs que no tienen ninguna página de la Carátula -> 'DISCREPANCIA_SIN_CARATULA' (0 tokens).
        4. Si todos los PDFs tienen la Carátula (con info o con hoja en blanco reemplazable por otra
           hoja de la Carátula que sí tiene info), consulta en Claude ÚNICAMENTE la página con info
           de la Carátula.
        5. Calcula el código base limpio hasta el año conservando intacta la posición 1 (Delegación)
           y guarda el diagnóstico en la tabla 'detecciones' de SQLite.
        """
        self.reload_settings()
        start_time = time.perf_counter()
        folder_p = Path(folder_path)
        folder_data = FolderParser.parse(folder_p.name)

        # 1. Inspección local de Carátula y hojas adjuntas en todos los PDFs (0 tokens)
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

        # REINTENTO AUTOMÁTICO (FALLBACK): Si no se encontró información y la carátula tiene páginas adicionales
        # que no se enviaron inicialmente (ej. si c_total_pages >= 3 y solo se enviaron [1, 2]):
        if not found_any_ocr_info and c_total_pages > len(target_pages):
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

        # Criterio estricto para Carátula Embebida:
        # Solo se considera Carátula Válida si contiene datos objetivos de apertura de libro (Libro y Año válidos).
        is_embedded_candidate = (c_status == "CARATULA_EMBEBIDA_CANDIDATA")
        is_valid_embedded_caratula = False
        if is_embedded_candidate:
            has_valid_libro = str(ocr_final.get("libro", "00")).strip() not in ("00", "0", "0000", "", "None", "-", "null")
            has_valid_anio = str(ocr_final.get("anio", "0000")).strip() not in ("0000", "0", "00", "", "None", "-", "null")
            is_valid_embedded_caratula = found_any_ocr_info and has_valid_libro and has_valid_anio

        embedded_to_create = None

        if not found_any_ocr_info or (is_embedded_candidate and not is_valid_embedded_caratula):
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
                # Regla normal: Si no se encuentra información alguna en la carátula,
                # dejar el nombre de la carpeta tal cual, solo limpiándolo hasta el año
                # (y adaptándolo con _2, _3 si hay colisiones al renombrar o apartar).
                codigo_sugerido = clean_current_code
                needs_page_replacement = (c_status == "REEMPLAZABLE_HOJA_BLANCA" or len(insp.get("replaceable_pdfs", [])) > 0)
                needs_rename = (folder_p.name != clean_current_code)
                veredicto_global = "SIN_INFO_LIMPIAR_NOMBRE" if needs_rename else "SIN_INFO_MANTENER_NOMBRE"
                subcarpeta_revision = "CARATULA SIN DATOS"
                needs_apartar = True
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

            needs_page_replacement = (c_status == "REEMPLAZABLE_HOJA_BLANCA")
            comp_veredicto = comparison.get("veredicto", "INDETERMINADO")
            needs_rename = (comp_veredicto != "COINCIDE_TOTAL") or (folder_p.name != codigo_sugerido)
            needs_apartar = False

            if is_embedded_candidate:
                insp["status"] = "CARATULA_EMBEBIDA_VALIDA"
                ocr_final["evidencias_globales"].append(
                    f"✔ [CARÁTULA EMBEBIDA VÁLIDA] Se detectó y validó el Acta de Apertura compartida en la Página 1 de los archivos. "
                    f"Se adopta como carátula oficial del libro y se clasifica para pasar a Correctos."
                )
                embedded_to_create = {
                    "source_pdf": str(caratula_pdf_path),
                    "page_num": 1,
                    "target_name": f"{codigo_sugerido}_CARATULA_00.pdf",
                }
                if not needs_rename:
                    veredicto_global = "COINCIDE_TOTAL"
                elif comp_veredicto == "RELLENO_DE_CEROS_PROPUESTO":
                    veredicto_global = "RELLENO_DE_CEROS_PROPUESTO"
                else:
                    veredicto_global = "RENOMBRAR_CARPETA"
            elif needs_page_replacement and comp_veredicto == "COINCIDE_TOTAL" and not needs_rename:
                veredicto_global = "REEMPLAZAR_HOJA_BLANCA"
            elif needs_page_replacement and needs_rename:
                veredicto_global = "REEMPLAZAR_HOJA_Y_RENOMBRAR"
            elif comp_veredicto == "COINCIDE_TOTAL" and needs_rename:
                # Coinciden los números pero la carpeta tenía un sufijo como _REVISAR_SEPARADO
                veredicto_global = "LIMPIAR_SUFIJO_CARPETA"
            else:
                veredicto_global = comp_veredicto

            subcarpeta_revision = FileOperations.determine_review_subfolder(
                caratula_status=c_status,
                veredicto_global=veredicto_global,
                found_any_ocr_info=True,
            )

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
        """Guarda el diagnóstico en SQLite local y, si está activo, en MySQL."""
        self.db.save_detection(result)
        if self.mysql and getattr(self.mysql, "enabled", False):
            try:
                insp = result.get("caratula_inspection", {})
                f_data = result.get("folder_data", {})
                ocr = result.get("ocr_consolidado", {})
                self.mysql.registrar_diagnostico_carpeta(
                    ruta_carpeta=result.get("folder_path", ""),
                    nombre_carpeta=f_data.get("folder_name", Path(result.get("folder_path", "")).name),
                    pdf_caratula=insp.get("caratula_filename"),
                    estado_caratula=insp.get("status", "DESCONOCIDO"),
                    total_pdfs=int(insp.get("total_regular_pdfs", 0)),
                    codigo_esperado=f_data.get("codigo_normalizado", ""),
                    codigo_detectado=result.get("codigo_base_sugerido", ""),
                    codigo_sugerido=result.get("codigo_base_sugerido", ""),
                    veredicto=result.get("veredicto_global", "INDETERMINADO"),
                    subcarpeta_revision=result.get("subcarpeta_revision", ""),
                    evidencias_json=json.dumps(ocr.get("evidencias_globales", []), ensure_ascii=False) if isinstance(ocr, dict) else None,
                    total_imagenes=int(insp.get("total_pages_in_folder", 0)),
                )
            except Exception as e:
                print(f"[Service Warning] No se pudo guardar diagnóstico en MySQL: {e}")

    # =========================================================================
    # FASE 2: OPERACIONES FÍSICAS Y ROLLBACK TOTAL
    # =========================================================================

    def execute_page_replacement(self, diag_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Reemplaza la hoja en blanco adjunta en los PDFs de la carpeta por la página
        con información de la Carátula, respaldando el original para Rollback.
        """
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
            db_manager=self.db,
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
        res = FileOperations.rename_folder_and_internal_pdfs(
            folder_path=folder_path,
            clean_base_code=code,
            db_manager=self.db,
            mysql_manager=self.mysql,
        )
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

        # Si había una carátula embebida pendiente de creación física en Fase 2:
        emb_to_create = diag_result.get("embedded_caratula_to_create")
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
                    db_manager=self.db,
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

        if self.mysql and self.mysql.enabled:
            self.mysql.registrar_movimiento_carpeta(
                tipo_movimiento="RENOMBRADO_AUDITORIA",
                nombre_origen=old_name,
                nombre_destino=res["new_folder_name"],
                ruta_origen=str(folder_path),
                ruta_destino=res["new_folder_path"],
                primer_pdf_referencia=res.get("new_folder_name"),
                motivo=f"Renombrado por auditoría ({res['files_renamed_count']} PDFs)",
                indice_colision=res.get("collision_index", 0)
            )

        return res

    def execute_all_pending_corrections(self, diag_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta en un solo paso todas las correcciones físicas pendientes en disco:
        1. Reemplazo o inserción de carátula en PDFs regulares y DUP (excluyendo REG).
        2. Extracción y creación física de carátula embebida si aplica.
        3. Renombrado de carpeta y PDFs regulares y DUP (excluyendo REG).
        Todo queda respaldado en SQLite y backups_rollback/ para Rollback total.
        """
        applied_now = []
        modified_count = 0

        # Paso 1: Reemplazo o inserción de carátulas si aplica
        if diag_result.get("needs_page_replacement") and diag_result.get("caratula_inspection", {}).get("replaceable_pdfs"):
            try:
                rep_res = self.execute_page_replacement(diag_result)
                cnt = rep_res.get("replaced_count", 0)
                modified_count += cnt
                applied_now.append(f"🛠 Carátula corregida en {cnt} PDF(s)")
            except Exception as exc:
                applied_now.append(f"⚠ Error al corregir carátula: {exc}")

        # Paso 2: Renombrado de carpeta y PDFs (conservando DUP e ignorando REG)
        if diag_result.get("needs_rename"):
            try:
                ren_res = self.execute_folder_and_pdfs_rename(diag_result)
                cnt = ren_res.get("files_renamed_count", 0)
                modified_count += cnt
                applied_now.append(f"✏️ Renombrada carpeta y {cnt} PDF(s)")
            except Exception as exc:
                applied_now.append(f"⚠ Error al renombrar: {exc}")

        return {
            "success": True,
            "modified_count": modified_count,
            "applied_actions": applied_now,
            "folder_path": diag_result.get("folder_path"),
        }

    def execute_move_to_apartados(
        self,
        diag_result: Dict[str, Any],
        apartados_dir: str,
        custom_error_subfolder: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Envía una carpeta que requiere revisión al directorio de Apartados seleccionado,
        dentro de una subcarpeta con descripción rápida del error (ej. 'CARATULA EN BLANCO',
        'PDFS SIN CARATULA', 'CARATULA SIN DATOS', 'SIN ARCHIVO CARATULA', etc.).
        """
        # Si la carpeta tiene correcciones físicas pendientes (hojas o renombre), aplicarlas antes de archivarla
        if diag_result.get("needs_page_replacement") or diag_result.get("needs_rename"):
            self.execute_all_pending_corrections(diag_result)

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
        res = FileOperations.move_folder_to_apartados(
            folder_path=folder_path,
            apartados_dir=apartados_dir,
            db_manager=self.db,
            tipo_movimiento=f"APARTADO: {error_sub}",
            error_subfolder=error_sub,
            mysql_manager=self.mysql,
        )
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

        if self.mysql and self.mysql.enabled:
            self.mysql.registrar_movimiento_carpeta(
                tipo_movimiento="APARTADO_REVISION",
                nombre_origen=Path(str(folder_path)).name,
                nombre_destino=new_p.name,
                ruta_origen=str(folder_path),
                ruta_destino=str(new_p),
                motivo=f"Apartado por {error_sub}",
                indice_colision=res.get("collision_index", 0)
            )

        return res

    def execute_move_to_validados(self, diag_result: Dict[str, Any], validados_dir: str) -> Dict[str, Any]:
        """
        Mueve una carpeta que está totalmente bien (COINCIDE_TOTAL) al directorio de carpetas
        correctas/validadas especificado por el usuario y lo registra en SQLite con Rollback.
        """
        folder_path = diag_result.get("folder_path", "")
        if not diag_result.get("initial_folder_path"):
            diag_result["initial_folder_path"] = str(folder_path)
            diag_result["initial_folder_name"] = Path(str(folder_path)).name

        # Si tenía carátula embebida pendiente de creación física y no fue renombrada previamente:
        emb_to_create = diag_result.get("embedded_caratula_to_create")
        if emb_to_create:
            folder_p = Path(folder_path)
            target_c_name = emb_to_create.get("target_name") or f"{folder_p.name}_CARATULA_00.pdf"
            src_pdf = Path(emb_to_create["source_pdf"])
            if not src_pdf.exists():
                src_pdf = folder_p / src_pdf.name
            if not src_pdf.exists():
                candidates = [f for f in folder_p.glob("*.pdf") if "CARATULA" not in f.name]
                if candidates:
                    src_pdf = candidates[0]
            if src_pdf.exists():
                c_file = folder_p / target_c_name
                if not c_file.exists():
                    c_res = FileOperations.create_caratula_from_pdf_page(
                        folder_path=folder_p,
                        source_pdf_path=src_pdf,
                        page_num=emb_to_create.get("page_num", 1),
                        target_caratula_name=target_c_name,
                        db_manager=self.db,
                        mysql_manager=self.mysql,
                    )
                    if c_res.get("created"):
                        insp = diag_result.get("caratula_inspection", {})
                        insp["caratula_filename"] = target_c_name
                        insp["caratula_path"] = str(folder_p / target_c_name)
                        actions = list(diag_result.get("applied_actions", []))
                        actions.append(f"📄 Carátula física extraída y creada: {target_c_name}")
                        diag_result["applied_actions"] = actions

        res = FileOperations.move_folder_to_validados(
            folder_path=folder_path,
            validados_dir=validados_dir,
            db_manager=self.db,
        )
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

        if self.mysql and self.mysql.enabled:
            self.mysql.registrar_movimiento_carpeta(
                tipo_movimiento="MOVIDO_A_CORRECTOS",
                nombre_origen=Path(str(folder_path)).name,
                nombre_destino=new_p.name,
                ruta_origen=str(folder_path),
                ruta_destino=str(new_p),
                motivo="Carpeta validada con COINCIDE_TOTAL",
                indice_colision=res.get("collision_index", 0)
            )

        return res

    def rollback_single_file(self, candidate: Dict[str, Any]):
        return FileOperations.rollback_single_candidate(candidate, self.db, self.mysql)

    def rollback_folder(self, initial_folder_path: str) -> Dict[str, Any]:
        return FileOperations.rollback_folder_to_initial_state(initial_folder_path, self.db, self.mysql)

    def rollback_all(self) -> Dict[str, Any]:
        return FileOperations.rollback_all_to_initial_state(self.db, self.mysql)

    def full_system_reset(self, clear_cache: bool = False) -> Dict[str, Any]:
        """
        Ejecuta el Rollback Físico Completo y posteriormente limpia todas las bases de datos:
        1. Revierte todos los archivos físicos a su ruta, nombre y contenido original.
        2. Limpia las tablas de SQLite (auditoria_libros.db).
        3. Limpia las tablas de MySQL (trazabilidad_selloslibros) si está habilitado.
        4. Opcionalmente limpia la caché local de Claude.
        """
        # Paso 1: Rollback Físico de todos los archivos modificados
        rb_res = self.rollback_all()

        # Paso 2: Limpieza de SQLite local
        sqlite_ok, sqlite_msg = self.db.reset_database()

        # Paso 3: Limpieza de MySQL si activo
        mysql_ok, mysql_msg = self.mysql.reset_database() if (self.mysql and getattr(self.mysql, "enabled", False)) else (True, "MySQL no activo")

        # Paso 4: Limpieza de caché si solicitada
        cache_cleared = False
        if clear_cache:
            try:
                self.cache.clear_disk_cache()
                cache_cleared = True
            except Exception:
                pass

        return {
            "rollback_result": rb_res,
            "sqlite_reset": {"ok": sqlite_ok, "msg": sqlite_msg},
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
