import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from src.core.settings_manager import SettingsManager


class DatabaseManager:
    """
    Administrador de Base de Datos SQLite ('auditoria_libros.db') con dos tablas:
    1. 'detecciones': Registra el diagnóstico general de cada carpeta (estado de carátula,
       páginas con texto/blancas, metadatos de carpeta vs detectados por Claude y código sugerido).
    2. 'movimientos_archivos': Registra todo movimiento físico (reemplazo de hoja en blanco,
       renombrado de carpeta/PDFs o envío a apartados) con ruta anterior, ruta nueva,
       nombre anterior, nombre nuevo, hash SHA-256, número de páginas y trazabilidad
       completa hacia el punto inicial (antes del primer cambio) para permitir Rollback exacto.
    """
    _lock = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            self.db_path = SettingsManager.get_base_dir() / "auditoria_libros.db"
        else:
            self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS detecciones (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fecha_registro TEXT NOT NULL,
                        ruta_carpeta TEXT NOT NULL,
                        nombre_carpeta TEXT NOT NULL,
                        pdf_caratula TEXT,
                        estado_caratula TEXT NOT NULL,
                        pagina_info_caratula INTEGER,
                        todos_pdfs_con_caratula INTEGER DEFAULT 0,
                        total_pdfs INTEGER DEFAULT 0,
                        pdfs_con_info INTEGER DEFAULT 0,
                        pdfs_con_hoja_blanca INTEGER DEFAULT 0,
                        pdfs_sin_caratula_json TEXT,
                        carpeta_delegacion TEXT,
                        carpeta_seccion TEXT,
                        carpeta_libro TEXT,
                        carpeta_tomo TEXT,
                        carpeta_volumen TEXT,
                        carpeta_fraccionamiento TEXT,
                        carpeta_anio TEXT,
                        detectado_seccion TEXT,
                        detectado_libro TEXT,
                        detectado_tomo TEXT,
                        detectado_volumen TEXT,
                        detectado_fraccionamiento TEXT,
                        detectado_anio TEXT,
                        veredicto TEXT NOT NULL,
                        codigo_base_sugerido TEXT,
                        evidencias_json TEXT,
                        total_imagenes INTEGER DEFAULT 0
                    )
                    """
                )

                # Migración suave de columna total_imagenes si ya existía la tabla
                try:
                    cur.execute("ALTER TABLE detecciones ADD COLUMN total_imagenes INTEGER DEFAULT 0")
                except Exception:
                    pass

                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS movimientos_archivos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fecha_movimiento TEXT NOT NULL,
                        tipo_movimiento TEXT NOT NULL,
                        ruta_anterior TEXT NOT NULL,
                        ruta_nueva TEXT NOT NULL,
                        nombre_anterior TEXT NOT NULL,
                        nombre_nuevo TEXT NOT NULL,
                        hash TEXT NOT NULL,
                        numero_paginas INTEGER NOT NULL,
                        ruta_origen_inicial TEXT NOT NULL,
                        nombre_origen_inicial TEXT NOT NULL,
                        hash_origen_inicial TEXT NOT NULL,
                        backup_contenido_path TEXT,
                        revertido INTEGER DEFAULT 0
                    )
                    """
                )

                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mov_ruta_nueva ON movimientos_archivos(ruta_nueva, revertido)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mov_origen_inicial ON movimientos_archivos(ruta_origen_inicial, revertido)"
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _norm_path(p: Any) -> str:
        return str(Path(str(p)).resolve())

    def save_detection(self, diag: Dict[str, Any]) -> int:
        """
        Registra en la tabla 'detecciones' el resultado del diagnóstico de una carpeta.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        folder_data = diag.get("folder_data", {})
        ocr = diag.get("ocr_consolidado", {})
        insp = diag.get("caratula_inspection", {})
        comp = diag.get("comparacion", {})

        pdfs_sin_caratula = insp.get("pdfs_missing_caratula", [])
        evidencias = ocr.get("evidencias_globales", [])

        tot_pdfs = int(insp.get("total_regular_pdfs", 0))
        tot_img = int(insp.get("total_pages_in_folder", 0))
        if tot_img <= 0:
            tot_img = tot_pdfs + (1 if insp.get("caratula_path") else 0)

        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO detecciones (
                        fecha_registro, ruta_carpeta, nombre_carpeta, pdf_caratula,
                        estado_caratula, pagina_info_caratula, todos_pdfs_con_caratula,
                        total_pdfs, pdfs_con_info, pdfs_con_hoja_blanca, pdfs_sin_caratula_json,
                        carpeta_delegacion, carpeta_seccion, carpeta_libro, carpeta_tomo,
                        carpeta_volumen, carpeta_fraccionamiento, carpeta_anio,
                        detectado_seccion, detectado_libro, detectado_tomo,
                        detectado_volumen, detectado_fraccionamiento, detectado_anio,
                        veredicto, codigo_base_sugerido, evidencias_json, total_imagenes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_str,
                        str(diag.get("folder_path", "")),
                        str(folder_data.get("folder_name", Path(diag.get("folder_path", "")).name)),
                        str(insp.get("caratula_filename") or ""),
                        str(insp.get("status", "DESCONOCIDO")),
                        insp.get("info_page_num"),
                        1 if insp.get("all_pdfs_have_caratula", False) else 0,
                        tot_pdfs,
                        int(insp.get("pdfs_with_info_page", 0)),
                        int(insp.get("pdfs_with_blank_page", 0)),
                        json.dumps(pdfs_sin_caratula, ensure_ascii=False),
                        str(folder_data.get("delegacion") or "01"),
                        str(folder_data.get("seccion_raw") or "00"),
                        str(folder_data.get("libro_raw") or "00"),
                        str(folder_data.get("tomo_raw") or "00"),
                        str(folder_data.get("volumen_raw") or "00"),
                        str(folder_data.get("fraccionamiento_raw") or "00"),
                        str(folder_data.get("anio_raw") or "0000"),
                        str(ocr.get("seccion") or "00"),
                        str(ocr.get("libro") or "00"),
                        str(ocr.get("tomo") or "00"),
                        str(ocr.get("volumen") or "00"),
                        str(ocr.get("fraccionamiento") or "00"),
                        str(ocr.get("anio") or "0000"),
                        str(diag.get("veredicto_global") or comp.get("veredicto") or "INDETERMINADO"),
                        str(diag.get("codigo_base_sugerido") or ""),
                        json.dumps(evidencias, ensure_ascii=False),
                        tot_img,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def get_prior_active_origin(self, ruta_anterior: Any) -> Optional[Dict[str, Any]]:
        """
        Busca si 'ruta_anterior' ya fue resultado ('ruta_nueva') de un movimiento previo no revertido.
        Si existe, retorna sus datos de origen inicial para encadenar la trazabilidad hasta antes del 1er cambio.
        """
        norm_prev = self._norm_path(ruta_anterior)
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM movimientos_archivos
                    WHERE ruta_nueva = ? AND revertido = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (norm_prev,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def log_file_movement(
        self,
        tipo_movimiento: str,
        ruta_anterior: Any,
        ruta_nueva: Any,
        nombre_anterior: str,
        nombre_nuevo: str,
        hash_actual: str,
        numero_paginas: int,
        hash_origen_inicial: Optional[str] = None,
        backup_contenido_path: Optional[str] = None,
    ) -> int:
        """
        Registra un movimiento de archivo en 'movimientos_archivos'.
        Si el archivo ya había sido modificado previamente (y no ha sido revertido),
        hereda automáticamente su 'ruta_origen_inicial', 'nombre_origen_inicial',
        'hash_origen_inicial' y 'backup_contenido_path' del primer cambio.
        """
        norm_ant = self._norm_path(ruta_anterior)
        norm_nva = self._norm_path(ruta_nueva)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        prior = self.get_prior_active_origin(norm_ant)
        if prior is not None:
            origen_ruta = prior["ruta_origen_inicial"]
            origen_nombre = prior["nombre_origen_inicial"]
            origen_hash = prior["hash_origen_inicial"]
            origen_backup = prior["backup_contenido_path"] or backup_contenido_path
        else:
            origen_ruta = norm_ant
            origen_nombre = nombre_anterior
            origen_hash = hash_origen_inicial or hash_actual
            origen_backup = backup_contenido_path

        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO movimientos_archivos (
                        fecha_movimiento, tipo_movimiento,
                        ruta_anterior, ruta_nueva,
                        nombre_anterior, nombre_nuevo,
                        hash, numero_paginas,
                        ruta_origen_inicial, nombre_origen_inicial,
                        hash_origen_inicial, backup_contenido_path,
                        revertido
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        now_str,
                        tipo_movimiento,
                        norm_ant,
                        norm_nva,
                        nombre_anterior,
                        nombre_nuevo,
                        hash_actual,
                        int(numero_paginas),
                        origen_ruta,
                        origen_nombre,
                        origen_hash,
                        origen_backup,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def get_rollback_candidates(self, filter_initial_folder: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Devuelve el estado consolidado de todos los archivos que tienen cambios activos (revertido = 0),
        vinculando su ubicación actual ('ruta_actual') con su punto exacto antes del primer cambio
        ('ruta_origen_inicial', 'nombre_origen_inicial', 'hash_origen_inicial', 'backup_contenido_path').
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM movimientos_archivos
                    WHERE revertido = 0
                    ORDER BY id ASC
                    """
                )
                rows = [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

        grouped: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            init_path = r["ruta_origen_inicial"]
            init_folder = str(Path(init_path).parent)
            if filter_initial_folder and self._norm_path(filter_initial_folder) != self._norm_path(init_folder):
                continue

            if init_path not in grouped:
                grouped[init_path] = {
                    "ruta_origen_inicial": init_path,
                    "nombre_origen_inicial": r["nombre_origen_inicial"],
                    "carpeta_origen_inicial": init_folder,
                    "nombre_carpeta_inicial": Path(init_folder).name,
                    "hash_origen_inicial": r["hash_origen_inicial"],
                    "backup_contenido_path": r["backup_contenido_path"],
                    "ruta_actual": r["ruta_nueva"],
                    "nombre_actual": r["nombre_nuevo"],
                    "carpeta_actual": str(Path(r["ruta_nueva"]).parent),
                    "hash_actual": r["hash"],
                    "numero_paginas": r["numero_paginas"],
                    "tipos_movimiento": [r["tipo_movimiento"]],
                    "cantidad_cambios": 1,
                    "ultimo_id": r["id"],
                    "ultima_fecha": r["fecha_movimiento"],
                }
            else:
                item = grouped[init_path]
                item["ruta_actual"] = r["ruta_nueva"]
                item["nombre_actual"] = r["nombre_nuevo"]
                item["carpeta_actual"] = str(Path(r["ruta_nueva"]).parent)
                item["hash_actual"] = r["hash"]
                item["numero_paginas"] = r["numero_paginas"]
                if not item["backup_contenido_path"] and r["backup_contenido_path"]:
                    item["backup_contenido_path"] = r["backup_contenido_path"]
                if r["tipo_movimiento"] not in item["tipos_movimiento"]:
                    item["tipos_movimiento"].append(r["tipo_movimiento"])
                item["cantidad_cambios"] += 1
                item["ultimo_id"] = r["id"]
                item["ultima_fecha"] = r["fecha_movimiento"]

        result = list(grouped.values())
        for item in result:
            item["tipos_movimiento_str"] = " -> ".join(item["tipos_movimiento"])
        result.sort(key=lambda x: x["ultimo_id"], reverse=True)
        return result

    def mark_file_reverted(self, ruta_origen_inicial: Any) -> int:
        """
        Marca como revertidos (revertido = 1) todos los movimientos activos asociados
        a 'ruta_origen_inicial' una vez que el archivo volvió intacto a su origen.
        """
        norm_init = self._norm_path(ruta_origen_inicial)
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE movimientos_archivos
                    SET revertido = 1
                    WHERE ruta_origen_inicial = ? AND revertido = 0
                    """,
                    (norm_init,),
                )
                conn.commit()
                return int(cur.rowcount)
            finally:
                conn.close()

    def get_detections(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM detecciones ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                )
                return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

    def get_movements(self, include_reverted: bool = True, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                if include_reverted:
                    cur.execute(
                        "SELECT * FROM movimientos_archivos ORDER BY id DESC LIMIT ?",
                        (int(limit),),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM movimientos_archivos WHERE revertido = 0 ORDER BY id DESC LIMIT ?",
                        (int(limit),),
                    )
                return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

    def get_executive_statistics(self) -> Dict[str, Any]:
        """
        Calcula indicadores estadísticos cruzando detecciones y movimientos_archivos:
        - Dimensionamiento de Carpetas: Total, Bien (Coincide/Corregidas), Mal (Discrepancia/Apartadas), % Efectividad.
        - Dimensionamiento de Archivos PDF: Total PDFs registrados, PDFs en carpetas bien vs mal, PDFs modificados físicamente.
        - Dimensionamiento de Imágenes (Páginas): Total imágenes, imágenes validadas, imágenes en revisión, promedio por carpeta.
        - Desglose por veredicto y estado de carátula.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                # 1. Obtener última detección por carpeta
                cur.execute("""
                    SELECT d.*
                    FROM detecciones d
                    INNER JOIN (
                        SELECT nombre_carpeta, MAX(id) as max_id
                        FROM detecciones
                        GROUP BY nombre_carpeta
                    ) latest ON d.id = latest.max_id
                """)
                latest_dets = [dict(r) for r in cur.fetchall()]

                # 2. Métricas de movimientos
                cur.execute("""
                    SELECT 
                        COUNT(DISTINCT ruta_origen_inicial) as total_archivos_modificados,
                        SUM(numero_paginas) as total_paginas_modificadas,
                        COUNT(*) as total_movimientos
                    FROM movimientos_archivos
                    WHERE revertido = 0
                """)
                mov_row = cur.fetchone()
                mov_stats = dict(mov_row) if mov_row else {}

                # Desglose de movimientos por tipo
                cur.execute("""
                    SELECT tipo_movimiento, COUNT(*) as cantidad, SUM(numero_paginas) as paginas
                    FROM movimientos_archivos
                    WHERE revertido = 0
                    GROUP BY tipo_movimiento
                """)
                mov_by_type = [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

        total_carpetas = len(latest_dets)
        carpetas_bien = 0
        carpetas_mal = 0
        total_pdfs = 0
        pdfs_bien = 0
        pdfs_mal = 0
        total_imagenes = 0
        imagenes_bien = 0
        imagenes_mal = 0

        veredicto_counts: Dict[str, Dict[str, int]] = {}
        caratula_counts: Dict[str, Dict[str, int]] = {}

        for d in latest_dets:
            v = str(d.get("veredicto") or "PENDIENTE").upper()
            c_stat = str(d.get("estado_caratula") or "DESCONOCIDO").upper()
            p_cnt = int(d.get("total_pdfs") or 0)
            img_cnt = int(d.get("total_imagenes") or p_cnt)
            if img_cnt <= 0:
                img_cnt = p_cnt

            total_pdfs += p_cnt
            total_imagenes += img_cnt

            es_bien = ("COINCIDE_TOTAL" in v or "CORRECTOS" in v or "REEMPLAZAR_HOJA_BLANCA" in v) and "DISCREPANCIA" not in v and "APARTAR" not in v

            if es_bien:
                carpetas_bien += 1
                pdfs_bien += p_cnt
                imagenes_bien += img_cnt
            else:
                carpetas_mal += 1
                pdfs_mal += p_cnt
                imagenes_mal += img_cnt

            # Veredictos agrupados
            if v not in veredicto_counts:
                veredicto_counts[v] = {"carpetas": 0, "pdfs": 0, "imagenes": 0}
            veredicto_counts[v]["carpetas"] += 1
            veredicto_counts[v]["pdfs"] += p_cnt
            veredicto_counts[v]["imagenes"] += img_cnt

            # Estado carátula agrupado
            if c_stat not in caratula_counts:
                caratula_counts[c_stat] = {"carpetas": 0, "pdfs": 0, "imagenes": 0}
            caratula_counts[c_stat]["carpetas"] += 1
            caratula_counts[c_stat]["pdfs"] += p_cnt
            caratula_counts[c_stat]["imagenes"] += img_cnt

        pct_carpetas_bien = round((carpetas_bien / total_carpetas * 100), 1) if total_carpetas > 0 else 0.0
        pct_imagenes_bien = round((imagenes_bien / total_imagenes * 100), 1) if total_imagenes > 0 else 0.0
        promedio_img_carpeta = round(total_imagenes / total_carpetas, 1) if total_carpetas > 0 else 0.0

        return {
            "total_carpetas": total_carpetas,
            "carpetas_bien": carpetas_bien,
            "carpetas_mal": carpetas_mal,
            "pct_carpetas_bien": pct_carpetas_bien,
            "total_pdfs": total_pdfs,
            "pdfs_bien": pdfs_bien,
            "pdfs_mal": pdfs_mal,
            "total_imagenes": total_imagenes,
            "imagenes_bien": imagenes_bien,
            "imagenes_mal": imagenes_mal,
            "pct_imagenes_bien": pct_imagenes_bien,
            "promedio_img_carpeta": promedio_img_carpeta,
            "archivos_modificados": mov_stats.get("total_archivos_modificados") or 0,
            "paginas_modificadas": mov_stats.get("total_paginas_modificadas") or 0,
            "desglose_veredicto": veredicto_counts,
            "desglose_caratula": caratula_counts,
            "desglose_movimientos": mov_by_type,
        }

    def get_traceability_comparison(
        self,
        filter_text: str = "",
        only_changed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Retorna la matriz de trazabilidad comparando el estado inicial contra el actual:
        [Archivo Original | Ubicación Original | Archivo Actual | Ubicación Actual | Págs | Operación | Hashes]
        """
        candidates = self.get_rollback_candidates()
        results: List[Dict[str, Any]] = []
        q = (filter_text or "").strip().lower()

        for c in candidates:
            orig_name = c["nombre_origen_inicial"]
            orig_folder = c["nombre_carpeta_inicial"]
            curr_name = c["nombre_actual"]
            curr_folder = Path(c["carpeta_actual"]).name
            changed = (orig_name != curr_name or c["carpeta_origen_inicial"] != c["carpeta_actual"])

            if only_changed and not changed:
                continue

            if q:
                searchable = f"{orig_name} {orig_folder} {curr_name} {curr_folder} {c['tipos_movimiento_str']}".lower()
                if q not in searchable:
                    continue

            results.append({
                "nombre_origen": orig_name,
                "carpeta_origen": orig_folder,
                "ruta_origen": c["ruta_origen_inicial"],
                "nombre_actual": curr_name,
                "carpeta_actual": curr_folder,
                "ruta_actual": c["ruta_actual"],
                "numero_paginas": c["numero_paginas"],
                "tipos_movimiento": c["tipos_movimiento_str"],
                "cantidad_cambios": c["cantidad_cambios"],
                "hash_inicial": c["hash_origen_inicial"],
                "hash_actual": c["hash_actual"],
                "fecha_ultimo_movimiento": c["ultima_fecha"],
                "ha_cambiado": changed,
                "estado_str": "MODIFICADO" if changed else "INTACTO",
            })

        return results

    def reset_database(self) -> Tuple[bool, str]:
        """
        Limpia completamente las tablas de 'movimientos_archivos' y 'detecciones',
        reiniciando los contadores autoincrementables y compactando la base de datos con VACUUM.
        Deja la base de datos SQLite en cero absoluto (estado inicial).
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM movimientos_archivos")
                cur.execute("DELETE FROM detecciones")
                try:
                    cur.execute("DELETE FROM sqlite_sequence WHERE name IN ('movimientos_archivos', 'detecciones')")
                except Exception:
                    pass
                conn.commit()
                conn.execute("VACUUM")
                return True, "Base de datos SQLite restablecida a estado inicial (0 registros)."
            except Exception as e:
                return False, f"Error al reiniciar base de datos SQLite: {e}"
            finally:
                conn.close()
