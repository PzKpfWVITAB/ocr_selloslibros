import pymysql
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple


class MySQLManager:
    """
    Gestor de base de datos MySQL para Trazabilidad Total de movimientos físicos,
    renombrados y separación de libros del Registro Público de la Propiedad.
    """

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or {}
        self.host = self.settings.get("mysql_host", "127.0.0.1")
        self.port = int(self.settings.get("mysql_port", 3306))
        self.user = self.settings.get("mysql_user", "root")
        self.password = self.settings.get("mysql_password", "")
        self.database = self.settings.get("mysql_database", "trazabilidad_selloslibros")
        self.enabled = bool(self.settings.get("mysql_enabled", False))

    def update_config(self, host: str, port: int, user: str, password: str, database: str, enabled: bool = True):
        self.host = host.strip()
        self.port = int(port)
        self.user = user.strip()
        self.password = password
        self.database = database.strip()
        self.enabled = enabled

    def get_connection(self, include_db: bool = True):
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database if include_db else None,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            autocommit=True
        )

    @classmethod
    def test_connection_params(cls, host: str, port: int, user: str, password: str) -> Tuple[bool, str]:
        """Prueba si el servidor MySQL responde con las credenciales indicadas."""
        try:
            conn = pymysql.connect(
                host=host.strip(),
                port=int(port),
                user=user.strip(),
                password=password,
                charset="utf8mb4",
                connect_timeout=6
            )
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION() AS ver, DATABASE() AS cur_db")
                row = cur.fetchone()
                ver = row[0] if row else "Desconocida"
            conn.close()
            return True, f"Conexión exitosa a MySQL Server (Versión: {ver})"
        except pymysql.err.OperationalError as op_err:
            return False, f"Error de conexión MySQL: {op_err.args[1] if len(op_err.args) > 1 else str(op_err)}"
        except Exception as e:
            return False, f"Error al conectar con MySQL: {str(e)}"

    def init_database_and_tables(self) -> Tuple[bool, str]:
        """
        Crea la base de datos si no existe y todas las tablas necesarias
        para el registro y auditoría de movimientos físicos.
        """
        try:
            # 1. Crear base de datos
            conn_root = self.get_connection(include_db=False)
            with conn_root.cursor() as cur:
                cur.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
            conn_root.close()

            # 2. Conectar a la base de datos y crear tablas
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                # Tabla 1: Movimientos y renombrados de carpetas
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `movimientos_carpetas` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `fecha_movimiento` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `tipo_movimiento` VARCHAR(100) NOT NULL,
                        `nombre_origen` VARCHAR(255) NOT NULL,
                        `nombre_destino` VARCHAR(255) NOT NULL,
                        `ruta_origen` TEXT NOT NULL,
                        `ruta_destino` TEXT NOT NULL,
                        `primer_pdf_referencia` VARCHAR(255) NULL,
                        `motivo` VARCHAR(255) NULL,
                        `indice_colision` INT NOT NULL DEFAULT 0,
                        `usuario_maquina` VARCHAR(100) NULL,
                        `detalles_json` LONGTEXT NULL,
                        INDEX `idx_nombre_origen` (`nombre_origen`),
                        INDEX `idx_nombre_destino` (`nombre_destino`),
                        INDEX `idx_tipo_mov` (`tipo_movimiento`),
                        INDEX `idx_fecha` (`fecha_movimiento`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # Tabla 2: Trazabilidad física de archivos PDF individuales
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `trazabilidad_archivos` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `fecha_movimiento` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `tipo_operacion` VARCHAR(100) NOT NULL,
                        `archivo_nombre_origen` VARCHAR(255) NOT NULL,
                        `archivo_nombre_destino` VARCHAR(255) NOT NULL,
                        `carpeta_origen` TEXT NOT NULL,
                        `carpeta_destino` TEXT NOT NULL,
                        `ruta_completa_origen` TEXT NOT NULL,
                        `ruta_completa_destino` TEXT NOT NULL,
                        `hash_sha256` VARCHAR(64) NULL,
                        `tamanio_bytes` BIGINT NULL,
                        `numero_paginas` INT NOT NULL DEFAULT 0,
                        `es_caratula` TINYINT(1) NOT NULL DEFAULT 0,
                        `seccion` VARCHAR(50) NULL,
                        `libro` VARCHAR(50) NULL,
                        `tomo` VARCHAR(100) NULL,
                        `volumen` VARCHAR(50) NULL,
                        `anio` INT NULL,
                        `fraccionamiento` VARCHAR(255) NULL,
                        `estado_operacion` VARCHAR(50) NOT NULL DEFAULT 'EXITOSO',
                        `detalles_json` LONGTEXT NULL,
                        INDEX `idx_hash` (`hash_sha256`),
                        INDEX `idx_archivo_orig` (`archivo_nombre_origen`),
                        INDEX `idx_archivo_dest` (`archivo_nombre_destino`),
                        INDEX `idx_tipo_op` (`tipo_operacion`),
                        INDEX `idx_fecha_arch` (`fecha_movimiento`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # Tabla 3: Separación de archivos mezclados en carpetas
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `separaciones_carpetas` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `fecha_separacion` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `carpeta_origen` TEXT NOT NULL,
                        `nombre_carpeta_origen` VARCHAR(255) NOT NULL,
                        `clave_grupo` VARCHAR(255) NOT NULL,
                        `carpeta_destino_creada` TEXT NOT NULL,
                        `nombre_carpeta_destino` VARCHAR(255) NOT NULL,
                        `total_archivos_movidos` INT NOT NULL DEFAULT 0,
                        `caratula_asociada` VARCHAR(255) NULL,
                        `detalles_json` LONGTEXT NULL,
                        INDEX `idx_clave_grupo` (`clave_grupo`),
                        INDEX `idx_carpeta_orig` (`nombre_carpeta_origen`),
                        INDEX `idx_fecha_sep` (`fecha_separacion`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # Tabla 4: Registro de auditoría y diagnóstico general de carpetas
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `auditoria_diagnosticos` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `fecha_registro` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `ruta_carpeta` TEXT NOT NULL,
                        `nombre_carpeta` VARCHAR(255) NOT NULL,
                        `pdf_caratula` VARCHAR(255) NULL,
                        `estado_caratula` VARCHAR(100) NULL,
                        `total_pdfs` INT NOT NULL DEFAULT 0,
                        `codigo_esperado` VARCHAR(255) NULL,
                        `codigo_detectado` VARCHAR(255) NULL,
                        `codigo_sugerido` VARCHAR(255) NULL,
                        `veredicto` VARCHAR(100) NOT NULL,
                        `subcarpeta_revision` VARCHAR(100) NULL,
                        `evidencias_json` LONGTEXT NULL,
                        `total_imagenes` INT NOT NULL DEFAULT 0,
                        INDEX `idx_veredicto` (`veredicto`),
                        INDEX `idx_nom_carp` (`nombre_carpeta`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                try:
                    cur.execute("ALTER TABLE `auditoria_diagnosticos` ADD COLUMN `total_imagenes` INT NOT NULL DEFAULT 0;")
                except Exception:
                    pass

            conn.close()
            return True, f"Base de datos '{self.database}' y tablas creadas exitosamente."
        except Exception as e:
            return False, f"Error al inicializar tablas en MySQL: {str(e)}"

    @staticmethod
    def compute_sha256(file_path: Path, block_size: int = 65536) -> str:
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for block in iter(lambda: f.read(block_size), b""):
                    sha256.update(block)
            return sha256.hexdigest()
        except Exception:
            return ""

    def registrar_movimiento_carpeta(
        self,
        tipo_movimiento: str,
        nombre_origen: str,
        nombre_destino: str,
        ruta_origen: str,
        ruta_destino: str,
        primer_pdf_referencia: Optional[str] = None,
        motivo: Optional[str] = None,
        indice_colision: int = 0,
        usuario_maquina: Optional[str] = None,
        detalles_json: Optional[str] = None
    ) -> Optional[int]:
        if not self.enabled:
            return None
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `movimientos_carpetas` (
                        `tipo_movimiento`, `nombre_origen`, `nombre_destino`,
                        `ruta_origen`, `ruta_destino`, `primer_pdf_referencia`,
                        `motivo`, `indice_colision`, `usuario_maquina`, `detalles_json`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tipo_movimiento, nombre_origen, nombre_destino,
                    ruta_origen, ruta_destino, primer_pdf_referencia,
                    motivo, indice_colision, usuario_maquina, detalles_json
                ))
                inserted_id = cur.lastrowid
            conn.close()
            return inserted_id
        except Exception as err:
            print(f"[MySQLManager ERROR] registrar_movimiento_carpeta: {err}")
            return None

    def registrar_trazabilidad_archivo(
        self,
        tipo_operacion: str,
        archivo_nombre_origen: str,
        archivo_nombre_destino: str,
        carpeta_origen: str,
        carpeta_destino: str,
        ruta_completa_origen: str,
        ruta_completa_destino: str,
        hash_sha256: Optional[str] = None,
        tamanio_bytes: Optional[int] = None,
        numero_paginas: int = 0,
        es_caratula: bool = False,
        seccion: Optional[str] = None,
        libro: Optional[str] = None,
        tomo: Optional[str] = None,
        volumen: Optional[str] = None,
        anio: Optional[int] = None,
        fraccionamiento: Optional[str] = None,
        estado_operacion: str = "EXITOSO",
        detalles_json: Optional[str] = None
    ) -> Optional[int]:
        if not self.enabled:
            return None
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `trazabilidad_archivos` (
                        `tipo_operacion`, `archivo_nombre_origen`, `archivo_nombre_destino`,
                        `carpeta_origen`, `carpeta_destino`, `ruta_completa_origen`, `ruta_completa_destino`,
                        `hash_sha256`, `tamanio_bytes`, `numero_paginas`, `es_caratula`,
                        `seccion`, `libro`, `tomo`, `volumen`, `anio`, `fraccionamiento`,
                        `estado_operacion`, `detalles_json`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tipo_operacion, archivo_nombre_origen, archivo_nombre_destino,
                    carpeta_origen, carpeta_destino, ruta_completa_origen, ruta_completa_destino,
                    hash_sha256, tamanio_bytes, numero_paginas, 1 if es_caratula else 0,
                    seccion, libro, tomo, volumen, anio, fraccionamiento,
                    estado_operacion, detalles_json
                ))
                inserted_id = cur.lastrowid
            conn.close()
            return inserted_id
        except Exception as err:
            print(f"[MySQLManager ERROR] registrar_trazabilidad_archivo: {err}")
            return None

    def registrar_separacion_carpeta(
        self,
        carpeta_origen: str,
        nombre_carpeta_origen: str,
        clave_grupo: str,
        carpeta_destino_creada: str,
        nombre_carpeta_destino: str,
        total_archivos_movidos: int,
        caratula_asociada: Optional[str] = None,
        detalles_json: Optional[str] = None
    ) -> Optional[int]:
        if not self.enabled:
            return None
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `separaciones_carpetas` (
                        `carpeta_origen`, `nombre_carpeta_origen`, `clave_grupo`,
                        `carpeta_destino_creada`, `nombre_carpeta_destino`,
                        `total_archivos_movidos`, `caratula_asociada`, `detalles_json`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    carpeta_origen, nombre_carpeta_origen, clave_grupo,
                    carpeta_destino_creada, nombre_carpeta_destino,
                    total_archivos_movidos, caratula_asociada, detalles_json
                ))
                inserted_id = cur.lastrowid
            conn.close()
            return inserted_id
        except Exception as err:
            print(f"[MySQLManager ERROR] registrar_separacion_carpeta: {err}")
            return None

    def registrar_diagnostico_carpeta(
        self,
        ruta_carpeta: str,
        nombre_carpeta: str,
        pdf_caratula: Optional[str],
        estado_caratula: str,
        total_pdfs: int,
        codigo_esperado: str,
        codigo_detectado: str,
        codigo_sugerido: str,
        veredicto: str,
        subcarpeta_revision: str,
        evidencias_json: Optional[str] = None,
        total_imagenes: int = 0
    ) -> Optional[int]:
        if not self.enabled:
            return None
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `auditoria_diagnosticos` (
                        `ruta_carpeta`, `nombre_carpeta`, `pdf_caratula`, `estado_caratula`,
                        `total_pdfs`, `codigo_esperado`, `codigo_detectado`, `codigo_sugerido`,
                        `veredicto`, `subcarpeta_revision`, `evidencias_json`, `total_imagenes`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ruta_carpeta, nombre_carpeta, pdf_caratula, estado_caratula,
                    total_pdfs, codigo_esperado, codigo_detectado, codigo_sugerido,
                    veredicto, subcarpeta_revision, evidencias_json, total_imagenes
                ))
                inserted_id = cur.lastrowid
            conn.close()
            return inserted_id
        except Exception as err:
            print(f"[MySQLManager ERROR] registrar_diagnostico_carpeta: {err}")
            return None

    def get_executive_statistics(self) -> Dict[str, Any]:
        """
        Calcula indicadores estadísticos en el servidor MySQL:
        - Dimensionamiento de Carpetas (Total, Bien, Mal, % Efectividad).
        - Dimensionamiento de PDFs (Total, Bien, Mal, Modificados).
        - Dimensionamiento de Imágenes / Páginas (Total Imágenes, Imágenes Bien, Imágenes Mal).
        """
        if not self.enabled:
            return {}
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                # 1. Diagnósticos más recientes por carpeta
                cur.execute("""
                    SELECT a.*
                    FROM auditoria_diagnosticos a
                    INNER JOIN (
                        SELECT nombre_carpeta, MAX(id) as max_id
                        FROM auditoria_diagnosticos
                        GROUP BY nombre_carpeta
                    ) latest ON a.id = latest.max_id
                """)
                latest_dets = cur.fetchall()

                # 2. Métricas de trazabilidad de archivos
                cur.execute("""
                    SELECT 
                        COUNT(DISTINCT ruta_completa_origen) as total_archivos_modificados,
                        SUM(numero_paginas) as total_paginas_modificadas,
                        COUNT(*) as total_movimientos
                    FROM trazabilidad_archivos
                """)
                mov_stats = cur.fetchone() or {}

                # Desglose de movimientos por tipo
                cur.execute("""
                    SELECT tipo_operacion as tipo_movimiento, COUNT(*) as cantidad, SUM(numero_paginas) as paginas
                    FROM trazabilidad_archivos
                    GROUP BY tipo_operacion
                """)
                mov_by_type = cur.fetchall()
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

                if v not in veredicto_counts:
                    veredicto_counts[v] = {"carpetas": 0, "pdfs": 0, "imagenes": 0}
                veredicto_counts[v]["carpetas"] += 1
                veredicto_counts[v]["pdfs"] += p_cnt
                veredicto_counts[v]["imagenes"] += img_cnt

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
        except Exception as e:
            print(f"[MySQLManager ERROR] get_executive_statistics: {e}")
            return {}

    def get_traceability_comparison(
        self,
        filter_text: str = "",
        only_changed: bool = False,
        limit: int = 5000
    ) -> List[Dict[str, Any]]:
        """
        Retorna la matriz de trazabilidad desde MySQL:
        [Archivo Original | Ubicación Original | Archivo Destino | Ubicación Destino | Págs | Operación | Hashes]
        """
        if not self.enabled:
            return []
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM `trazabilidad_archivos` ORDER BY `id` DESC LIMIT {int(limit)}")
                rows = cur.fetchall()
            conn.close()

            results: List[Dict[str, Any]] = []
            q = (filter_text or "").strip().lower()

            for r in rows:
                orig_name = r.get("archivo_nombre_origen") or ""
                orig_folder = Path(r.get("carpeta_origen") or "").name
                curr_name = r.get("archivo_nombre_destino") or ""
                curr_folder = Path(r.get("carpeta_destino") or "").name
                changed = (orig_name != curr_name or r.get("carpeta_origen") != r.get("carpeta_destino"))

                if only_changed and not changed:
                    continue

                if q:
                    searchable = f"{orig_name} {orig_folder} {curr_name} {curr_folder} {r.get('tipo_operacion')}".lower()
                    if q not in searchable:
                        continue

                results.append({
                    "nombre_origen": orig_name,
                    "carpeta_origen": orig_folder,
                    "ruta_origen": r.get("ruta_completa_origen") or "",
                    "nombre_actual": curr_name,
                    "carpeta_actual": curr_folder,
                    "ruta_actual": r.get("ruta_completa_destino") or "",
                    "numero_paginas": r.get("numero_paginas") or 0,
                    "tipos_movimiento": r.get("tipo_operacion") or "",
                    "cantidad_cambios": 1,
                    "hash_inicial": r.get("hash_sha256") or "",
                    "hash_actual": r.get("hash_sha256") or "",
                    "fecha_ultimo_movimiento": str(r.get("fecha_movimiento") or ""),
                    "ha_cambiado": changed,
                    "estado_str": "MODIFICADO" if changed else "INTACTO",
                })

            return results
        except Exception as e:
            print(f"[MySQLManager ERROR] get_traceability_comparison: {e}")
            return []

    def registrar_rollback_archivo(
        self,
        archivo_nombre_revertido: str,
        ruta_origen_restaurada: str,
        hash_sha256: str = "",
        numero_paginas: int = 0
    ) -> Optional[int]:
        """Registra un evento de reversión / rollback en la trazabilidad de MySQL."""
        if not self.enabled:
            return None
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `trazabilidad_archivos` (
                        `tipo_operacion`, `archivo_nombre_origen`, `archivo_nombre_destino`,
                        `carpeta_origen`, `carpeta_destino`, `ruta_completa_origen`, `ruta_completa_destino`,
                        `hash_sha256`, `numero_paginas`, `estado_operacion`, `detalles_json`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    "ROLLBACK_REVERTIDO",
                    archivo_nombre_revertido,
                    archivo_nombre_revertido,
                    str(Path(ruta_origen_restaurada).parent),
                    str(Path(ruta_origen_restaurada).parent),
                    ruta_origen_restaurada,
                    ruta_origen_restaurada,
                    hash_sha256,
                    numero_paginas,
                    "REVERTIDO_A_ORIGEN",
                    "Restaurado a estado inicial previo al primer cambio"
                ))
                inserted_id = cur.lastrowid
            conn.close()
            return inserted_id
        except Exception as err:
            print(f"[MySQLManager ERROR] registrar_rollback_archivo: {err}")
            return None

    def reset_database(self) -> Tuple[bool, str]:
        """
        Limpia / trunca todas las tablas de trazabilidad en el servidor MySQL:
        'trazabilidad_archivos', 'movimientos_carpetas', 'separaciones_carpetas', 'auditoria_diagnosticos'.
        Deja el esquema MySQL en cero absoluto.
        """
        if not self.enabled:
            return True, "MySQL no está habilitado."
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE `trazabilidad_archivos`;")
                cur.execute("TRUNCATE TABLE `movimientos_carpetas`;")
                cur.execute("TRUNCATE TABLE `separaciones_carpetas`;")
                cur.execute("TRUNCATE TABLE `auditoria_diagnosticos`;")
            conn.close()
            return True, "Tablas de MySQL truncadas y restablecidas a estado inicial (0 registros)."
        except Exception as e:
            return False, f"Error al reiniciar tablas de MySQL: {e}"
