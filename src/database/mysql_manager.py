import pymysql
import json
import uuid
import shutil
import os
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from src.core.logger import get_logger

logger = get_logger("mysql_manager")


class MySQLManager:
    """
    Gestor 100% MySQL para Trazabilidad Total, Indexación Inicial de las 950 carpetas,
    Auditoría por Lotes de Ejecución y Rollback Estricto en orden cronológico inverso.
    """

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or {}
        self.host = self.settings.get("mysql_host", "127.0.0.1")
        self.port = int(self.settings.get("mysql_port", 3306))
        self.user = self.settings.get("mysql_user", "root")
        self.password = self.settings.get("mysql_password", "")
        self.database = self.settings.get("mysql_database", "trazabilidad_selloslibros")
        self.enabled = bool(self.settings.get("mysql_enabled", True))

    def update_config(self, host: str, port: int, user: str, password: str, database: str, enabled: bool = True):
        self.host = host.strip()
        self.port = int(port)
        self.user = user.strip()
        self.password = password
        self.database = database.strip()
        self.enabled = enabled

    def get_connection(self, include_db: bool = True, max_retries: int = 3, retry_delay: float = 0.5):
        """
        Obtiene una conexión pymysql con reintentos automáticos y backoff ante caídas transitorias.
        """
        last_exc = None
        for attempt in range(max_retries):
            try:
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
            except (pymysql.err.OperationalError, pymysql.err.DatabaseError) as err:
                last_exc = err
                if attempt < max_retries - 1:
                    logger.warning(f"Reintentando conexión a MySQL ({attempt+1}/{max_retries}) tras error: {err}")
                    time.sleep(retry_delay * (2 ** attempt))
                else:
                    logger.error(f"Fallo definitivo al conectar a MySQL tras {max_retries} intentos: {err}")
        if last_exc:
            raise last_exc

    @staticmethod
    def compute_sha256(file_path: Any) -> str:
        """Calcula el hash SHA-256 de un archivo físico de forma eficiente por bloques."""
        import hashlib
        p = Path(file_path)
        if not p.is_file():
            return ""
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

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
                cur.execute("SELECT VERSION() AS ver")
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
        Crea la base de datos y las 5 tablas oficiales:
        1. carpetas_iniciales (Índice de carpetas con UUID y nombre original)
        2. archivos_iniciales (Índice rápido de nombres de PDFs originales por carpeta)
        3. ejecuciones_lotes (Registro maestro de cada ejecución/lote para rollback)
        4. auditoria_movimientos_carpetas (Historial de renombres/traslados de carpetas)
        5. auditoria_movimientos_archivos (Historial de renombres/páginas de PDFs)
        """
        try:
            conn_root = self.get_connection(include_db=False)
            with conn_root.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                )
            conn_root.close()

            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                # 1. Índice inicial de carpetas
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `carpetas_iniciales` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `uuid` VARCHAR(36) NOT NULL UNIQUE,
                        `nombre_original` VARCHAR(255) NOT NULL,
                        `ruta_original` TEXT NOT NULL,
                        `total_archivos_inicial` INT NOT NULL DEFAULT 0,
                        `fecha_registro` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX `idx_nombre_original` (`nombre_original`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # 2. Índice inicial de nombres de archivos PDF
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `archivos_iniciales` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `carpeta_uuid` VARCHAR(36) NOT NULL,
                        `nombre_archivo_original` VARCHAR(255) NOT NULL,
                        `orden_indice` INT NOT NULL DEFAULT 0,
                        `fecha_registro` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX `idx_carpeta_uuid` (`carpeta_uuid`),
                        INDEX `idx_nombre_archivo` (`nombre_archivo_original`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # 3. Conjunto de ejecuciones / lotes (para rollback por ejecución)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `ejecuciones_lotes` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `fecha_inicio` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `fecha_fin` DATETIME NULL,
                        `tipo_ejecucion` VARCHAR(100) NOT NULL,
                        `total_carpetas_afectadas` INT NOT NULL DEFAULT 0,
                        `total_archivos_afectados` INT NOT NULL DEFAULT 0,
                        `estado` VARCHAR(50) NOT NULL DEFAULT 'COMPLETADO',
                        `detalles_json` LONGTEXT NULL,
                        INDEX `idx_estado` (`estado`),
                        INDEX `idx_fecha_inicio` (`fecha_inicio`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # 4. Auditoría de movimientos y renombres de carpetas
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `auditoria_movimientos_carpetas` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `ejecucion_id` INT NOT NULL,
                        `carpeta_uuid` VARCHAR(36) NOT NULL,
                        `tipo_accion` VARCHAR(100) NOT NULL,
                        `nombre_anterior` VARCHAR(255) NOT NULL,
                        `nombre_nuevo` VARCHAR(255) NOT NULL,
                        `ruta_anterior` TEXT NOT NULL,
                        `ruta_nueva` TEXT NOT NULL,
                        `cantidad_archivos` INT NOT NULL DEFAULT 0,
                        `fecha_accion` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX `idx_ejecucion` (`ejecucion_id`),
                        INDEX `idx_carpeta_uuid_mov` (`carpeta_uuid`),
                        INDEX `idx_tipo_accion` (`tipo_accion`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # 5. Auditoría de movimientos, renombres y páginas de archivos PDF
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `auditoria_movimientos_archivos` (
                        `id` INT AUTO_INCREMENT PRIMARY KEY,
                        `ejecucion_id` INT NOT NULL,
                        `carpeta_uuid` VARCHAR(36) NOT NULL,
                        `tipo_accion` VARCHAR(100) NOT NULL,
                        `nombre_anterior` VARCHAR(255) NOT NULL,
                        `nombre_nuevo` VARCHAR(255) NOT NULL,
                        `ruta_anterior` TEXT NOT NULL,
                        `ruta_nueva` TEXT NOT NULL,
                        `cantidad_paginas` INT NOT NULL DEFAULT 0,
                        `backup_path` TEXT NULL,
                        `fecha_accion` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX `idx_ejecucion_arch` (`ejecucion_id`),
                        INDEX `idx_carpeta_uuid_arch` (`carpeta_uuid`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

                # Asegurar columna backup_path si la tabla ya existía previamente
                try:
                    cur.execute("""
                        SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS 
                        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'auditoria_movimientos_archivos' AND COLUMN_NAME = 'backup_path'
                    """, (self.database,))
                    res_col = cur.fetchone()
                    count_val = res_col.get("cnt") if isinstance(res_col, dict) else (res_col[0] if res_col else 0)
                    if count_val == 0:
                        cur.execute("ALTER TABLE `auditoria_movimientos_archivos` ADD COLUMN `backup_path` TEXT NULL")
                except Exception as col_exc:
                    logger.warning(f"Aviso verificando/migrando columna backup_path en MySQL: {col_exc}")

            conn.close()
            return True, f"Base de datos '{self.database}' y las 5 tablas oficiales verificadas/creadas."
        except Exception as e:
            return False, f"Error al inicializar tablas en MySQL: {str(e)}"

    def crear_ejecucion_lote(self, tipo_ejecucion: str, detalles: Optional[Dict[str, Any]] = None) -> int:
        """Abre un nuevo lote de ejecución en `ejecuciones_lotes` y retorna su ID."""
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `ejecuciones_lotes` (`tipo_ejecucion`, `estado`, `detalles_json`)
                    VALUES (%s, 'EN_PROCESO', %s)
                """, (tipo_ejecucion, json.dumps(detalles or {}, ensure_ascii=False)))
                return cur.lastrowid
        finally:
            conn.close()

    def finalizar_ejecucion_lote(
        self,
        ejecucion_id: int,
        total_carpetas: int,
        total_archivos: int,
        estado: str = "COMPLETADO",
        detalles: Optional[Dict[str, Any]] = None
    ):
        """Finaliza y totaliza un lote de ejecución."""
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE `ejecuciones_lotes`
                    SET `fecha_fin` = CURRENT_TIMESTAMP,
                        `total_carpetas_afectadas` = %s,
                        `total_archivos_afectados` = %s,
                        `estado` = %s,
                        `detalles_json` = COALESCE(%s, `detalles_json`)
                    WHERE `id` = %s
                """, (
                    total_carpetas,
                    total_archivos,
                    estado,
                    json.dumps(detalles, ensure_ascii=False) if detalles is not None else None,
                    ejecucion_id
                ))
        finally:
            conn.close()

    def indexar_estado_inicial(self, carpetas_info: List[Dict[str, Any]]) -> Tuple[int, int, int, Dict[str, str]]:
        """
        Registra en `carpetas_iniciales` y `archivos_iniciales` todas las carpetas originales
        y sus PDFs (solo nombres) ANTES de realizar cualquier renombre.
        Si una carpeta ya existe por `ruta_original` o `nombre_original`, reutiliza su UUID.
        Retorna: (ejecucion_id, total_carpetas_indexadas, total_archivos_indexados, mapa_ruta_a_uuid).
        """
        self.init_database_and_tables()
        ejecucion_id = self.crear_ejecucion_lote("INDEXACION_INICIAL", {"total_escaneadas": len(carpetas_info)})
        conn = self.get_connection(include_db=True)
        mapa_uuids: Dict[str, str] = {}
        carpetas_nuevas = 0
        archivos_nuevos = 0

        try:
            with conn.cursor() as cur:
                # Cargar existentes para no duplicar si se vuelve a correr
                cur.execute("SELECT `uuid`, `nombre_original`, `ruta_original` FROM `carpetas_iniciales`")
                existentes_por_ruta = {row["ruta_original"]: row["uuid"] for row in cur.fetchall()}
                # Rutas ACTUALES (tras renombres/movimientos no revertidos) -> misma carpeta, no es nueva
                for u, ruta_actual in self._posiciones_actuales(cur).items():
                    existentes_por_ruta.setdefault(ruta_actual, u)

                for item in carpetas_info:
                    ruta_orig = str(item["ruta_original"])
                    nombre_orig = str(item["nombre_original"])
                    pdf_names: List[str] = item.get("pdf_names", [])

                    if ruta_orig in existentes_por_ruta:
                        c_uuid = existentes_por_ruta[ruta_orig]
                        mapa_uuids[ruta_orig] = c_uuid
                        continue

                    c_uuid = str(uuid.uuid4())
                    mapa_uuids[ruta_orig] = c_uuid
                    cur.execute("""
                        INSERT INTO `carpetas_iniciales` (`uuid`, `nombre_original`, `ruta_original`, `total_archivos_inicial`)
                        VALUES (%s, %s, %s, %s)
                    """, (c_uuid, nombre_orig, ruta_orig, len(pdf_names)))
                    carpetas_nuevas += 1

                    if pdf_names:
                        arch_values = [
                            (c_uuid, pdf_name, idx + 1)
                            for idx, pdf_name in enumerate(pdf_names)
                        ]
                        cur.executemany("""
                            INSERT INTO `archivos_iniciales` (`carpeta_uuid`, `nombre_archivo_original`, `orden_indice`)
                            VALUES (%s, %s, %s)
                        """, arch_values)
                        archivos_nuevos += len(arch_values)

            self.finalizar_ejecucion_lote(
                ejecucion_id,
                total_carpetas=len(carpetas_info),
                total_archivos=archivos_nuevos,
                estado="COMPLETADO",
                detalles={"carpetas_nuevas": carpetas_nuevas, "archivos_nuevos": archivos_nuevos}
            )
            return ejecucion_id, carpetas_nuevas, archivos_nuevos, mapa_uuids
        finally:
            conn.close()

    def obtener_uuid_por_ruta_o_nombre(self, ruta_carpeta: str, nombre_carpeta: str) -> str:
        """
        Busca el UUID de una carpeta en `carpetas_iniciales` o en el último movimiento de
        `auditoria_movimientos_carpetas`. Si no existe (ej. prueba aislada), genera uno y lo registra.
        """
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                # 1. Buscar en movimientos recientes por ruta_nueva
                cur.execute("""
                    SELECT `carpeta_uuid` FROM `auditoria_movimientos_carpetas`
                    WHERE `ruta_nueva` = %s ORDER BY `id` DESC LIMIT 1
                """, (ruta_carpeta,))
                row = cur.fetchone()
                if row:
                    return row["carpeta_uuid"]

                # 2. Buscar en carpetas_iniciales por ruta_original
                cur.execute("""
                    SELECT `uuid` FROM `carpetas_iniciales`
                    WHERE `ruta_original` = %s LIMIT 1
                """, (ruta_carpeta,))
                row = cur.fetchone()
                if row:
                    return row["uuid"]

                # 3. Buscar por nombre_original
                cur.execute("""
                    SELECT `uuid` FROM `carpetas_iniciales`
                    WHERE `nombre_original` = %s LIMIT 1
                """, (nombre_carpeta,))
                row = cur.fetchone()
                if row:
                    return row["uuid"]

                # 4. Si no estaba indexada, registrarla sobre la marcha
                nuevo_uuid = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO `carpetas_iniciales` (`uuid`, `nombre_original`, `ruta_original`, `total_archivos_inicial`)
                    VALUES (%s, %s, %s, 0)
                """, (nuevo_uuid, nombre_carpeta, ruta_carpeta))
                conn.commit()
                return nuevo_uuid
        finally:
            conn.close()

    def registrar_movimiento_carpeta(
        self,
        ejecucion_id: int,
        carpeta_uuid: str,
        tipo_accion: str,
        nombre_anterior: str,
        nombre_nuevo: str,
        ruta_anterior: str,
        ruta_nueva: str,
        cantidad_archivos: int = 0
    ):
        if not ejecucion_id:
            return
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO `auditoria_movimientos_carpetas` (
                        `ejecucion_id`, `carpeta_uuid`, `tipo_accion`,
                        `nombre_anterior`, `nombre_nuevo`, `ruta_anterior`, `ruta_nueva`, `cantidad_archivos`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ejecucion_id, carpeta_uuid, tipo_accion,
                    nombre_anterior, nombre_nuevo, ruta_anterior, ruta_nueva, cantidad_archivos
                ))
        finally:
            conn.close()

    def registrar_movimiento_archivo(
        self,
        ejecucion_id: int,
        carpeta_uuid: str,
        tipo_accion: str,
        nombre_anterior: str,
        nombre_nuevo: str,
        ruta_anterior: str,
        ruta_nueva: str,
        cantidad_paginas: int = 0,
        backup_path: Optional[str] = None,
    ):
        """Registra un movimiento o alteración individual de archivo en `auditoria_movimientos_archivos`."""
        mov = {
            "ejecucion_id": ejecucion_id,
            "carpeta_uuid": carpeta_uuid,
            "tipo_accion": tipo_accion,
            "nombre_anterior": nombre_anterior,
            "nombre_nuevo": nombre_nuevo,
            "ruta_anterior": ruta_anterior,
            "ruta_nueva": ruta_nueva,
            "cantidad_paginas": cantidad_paginas,
            "backup_path": backup_path,
        }
        self.registrar_movimientos_archivos_lote([mov])

    def registrar_movimientos_archivos_lote(self, movimientos: List[Dict[str, Any]]):
        """Inserta en lote registros de `auditoria_movimientos_archivos`."""
        if not movimientos:
            return
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                vals = [
                    (
                        m["ejecucion_id"],
                        m["carpeta_uuid"],
                        m["tipo_accion"],
                        m["nombre_anterior"],
                        m["nombre_nuevo"],
                        m["ruta_anterior"],
                        m["ruta_nueva"],
                        m.get("cantidad_paginas", 0),
                        m.get("backup_path"),
                    )
                    for m in movimientos
                ]
                cur.executemany("""
                    INSERT INTO `auditoria_movimientos_archivos` (
                        `ejecucion_id`, `carpeta_uuid`, `tipo_accion`,
                        `nombre_anterior`, `nombre_nuevo`, `ruta_anterior`, `ruta_nueva`, `cantidad_paginas`, `backup_path`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, vals)
        finally:
            conn.close()

    def obtener_historial_ejecuciones(self) -> List[Dict[str, Any]]:
        """Retorna todas las ejecuciones registradas en `ejecuciones_lotes` en orden cronológico inverso."""
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM `ejecuciones_lotes`
                    ORDER BY `id` DESC
                """)
                return list(cur.fetchall())
        finally:
            conn.close()

    def obtener_todos_movimientos_carpetas(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Retorna los movimientos físicos de carpetas para auditoría en GUI."""
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.*, el.estado AS estado_lote
                    FROM `auditoria_movimientos_carpetas` m
                    LEFT JOIN `ejecuciones_lotes` el ON m.`ejecucion_id` = el.`id`
                    ORDER BY m.`id` DESC
                    LIMIT %s
                """, (limit,))
                return list(cur.fetchall())
        finally:
            conn.close()

    def obtener_todos_movimientos_archivos(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Retorna los movimientos físicos de archivos individuales para auditoría en GUI."""
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.*, el.estado AS estado_lote
                    FROM `auditoria_movimientos_archivos` m
                    LEFT JOIN `ejecuciones_lotes` el ON m.`ejecucion_id` = el.`id`
                    ORDER BY m.`id` DESC
                    LIMIT %s
                """, (limit,))
                return list(cur.fetchall())
        finally:
            conn.close()

    ESTADOS_REVERTIBLES = ("COMPLETADO", "COMPLETADO_CON_ERRORES", "EN_PROCESO", "REVERSION_PARCIAL")

    def obtener_ultima_ejecucion_revertible(self) -> Optional[Dict[str, Any]]:
        """
        Retorna el lote MÁS RECIENTE que todavía no fue revertido por completo
        (COMPLETADO, COMPLETADO_CON_ERRORES, EN_PROCESO huérfano o REVERSION_PARCIAL).
        Estricto orden cronológico inverso: nunca se salta un lote más nuevo.
        Se excluye INDEXACION_INICIAL porque no mueve nada en disco.
        """
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                marks = ",".join(["%s"] * len(self.ESTADOS_REVERTIBLES))
                cur.execute(f"""
                    SELECT * FROM `ejecuciones_lotes`
                    WHERE `estado` IN ({marks})
                      AND `tipo_ejecucion` != 'INDEXACION_INICIAL'
                    ORDER BY `id` DESC LIMIT 1
                """, self.ESTADOS_REVERTIBLES)
                return cur.fetchone()
        finally:
            conn.close()

    def _posiciones_actuales(self, cur) -> Dict[str, str]:
        """uuid -> ruta actual esperada, según el último movimiento NO revertido."""
        cur.execute("""
            SELECT m.`carpeta_uuid`, m.`ruta_nueva`
            FROM `auditoria_movimientos_carpetas` m
            JOIN `ejecuciones_lotes` e ON e.`id` = m.`ejecucion_id`
            WHERE e.`estado` <> 'REVERTIDO'
            ORDER BY m.`id` ASC
        """)
        pos: Dict[str, str] = {}
        for row in cur.fetchall():
            pos[row["carpeta_uuid"]] = row["ruta_nueva"]
        return pos

    @staticmethod
    def _temp_name(path: Path) -> Path:
        return path.parent / f".__rb_{uuid.uuid4().hex[:10]}{path.suffix if path.is_file() else ''}"

    def revertir_ultima_ejecucion(self) -> Dict[str, Any]:
        """
        Rollback estricto del último lote pendiente, en DOS FASES para evitar choques de nombres
        (ej. A->X y B->X_2: se sacan todos a un nombre temporal y luego se colocan en su origen).
        Es idempotente: lo que ya esté en su lugar original se omite.
        Si algo falla, el lote queda como REVERSION_PARCIAL (sigue siendo el siguiente a revertir).
        """
        lote = self.obtener_ultima_ejecucion_revertible()
        if not lote:
            return {
                "success": False,
                "message": "No hay ejecuciones pendientes de revertir.",
                "folders_reverted": 0,
                "files_reverted": 0,
                "errors": []
            }

        ejecucion_id = lote["id"]
        tipo_ejecucion = lote["tipo_ejecucion"]
        conn = self.get_connection(include_db=True)
        folders_reverted = 0
        files_reverted = 0
        errors: List[str] = []

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM `auditoria_movimientos_carpetas` WHERE `ejecucion_id` = %s ORDER BY `id` DESC", (ejecucion_id,))
                mov_carpetas = list(cur.fetchall())
                cur.execute("SELECT * FROM `auditoria_movimientos_archivos` WHERE `ejecucion_id` = %s ORDER BY `id` DESC", (ejecucion_id,))
                mov_archivos = list(cur.fetchall())

                reverted_dir: Dict[str, str] = {}

                # ---- FASE 1 (carpetas): sacar todas a nombre temporal ----
                pending: List[Tuple[Path, Path, Path, str]] = []
                for mc in mov_carpetas:
                    r_nueva, r_ant, c_uuid = Path(mc["ruta_nueva"]), Path(mc["ruta_anterior"]), mc["carpeta_uuid"]
                    if str(r_nueva) == str(r_ant):
                        continue
                    if r_nueva.exists():
                        temp = self._temp_name(r_nueva)
                        try:
                            shutil.move(str(r_nueva), str(temp))
                            pending.append((temp, r_ant, r_nueva, c_uuid))
                        except Exception as exc:
                            errors.append(f"No se pudo mover '{r_nueva.name}': {exc}")
                    elif r_ant.exists():
                        reverted_dir[c_uuid] = str(r_ant)  # ya estaba revertida
                    else:
                        errors.append(f"Carpeta no encontrada en disco (ni en origen ni en destino): {r_nueva}")

                # ---- FASE 2 (carpetas): colocar en su origen ----
                for temp, r_ant, r_nueva, c_uuid in pending:
                    try:
                        if r_ant.exists():
                            shutil.move(str(temp), str(r_nueva))
                            errors.append(f"Conflicto real: ya existe '{r_ant}'. La carpeta se dejó en '{r_nueva}'.")
                        else:
                            r_ant.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(temp), str(r_ant))
                            folders_reverted += 1
                            reverted_dir[c_uuid] = str(r_ant)
                    except Exception as exc:
                        errors.append(f"Error colocando '{r_ant.name}' (quedó como {temp}): {exc}")

                # ---- FASE 1 (archivos) ----
                f_pending: List[Tuple[Path, Path, Path]] = []
                for ma in mov_archivos:
                    c_uuid = ma["carpeta_uuid"]
                    if c_uuid in reverted_dir:
                        base_dir = Path(reverted_dir[c_uuid])
                        current = base_dir / ma["nombre_nuevo"]
                        target = base_dir / ma["nombre_anterior"]
                    else:
                        current = Path(ma["ruta_nueva"])
                        target = Path(ma["ruta_anterior"])

                    # Si existe un respaldo binario asociado (ej. inserción de página 2 o reemplazo de hoja)
                    bkp_str = ma.get("backup_path")
                    if bkp_str and Path(bkp_str).exists():
                        try:
                            if current.exists():
                                shutil.copy2(bkp_str, str(current))
                            elif target.exists():
                                shutil.copy2(bkp_str, str(target))
                        except Exception as bkp_exc:
                            errors.append(f"Error restaurando respaldo binario para '{current.name}': {bkp_exc}")

                    if str(current) == str(target):
                        if bkp_str and Path(bkp_str).exists():
                            files_reverted += 1
                        continue
                    if current.exists():
                        if not target.parent.exists():
                            errors.append(f"Carpeta destino inexistente para archivo {ma['nombre_nuevo']}: {target.parent}")
                            continue
                        temp = self._temp_name(current)
                        try:
                            os.replace(str(current), str(temp))
                            f_pending.append((temp, target, current))
                        except Exception as exc:
                            errors.append(f"No se pudo mover archivo {current.name}: {exc}")
                    elif not target.exists():
                        errors.append(f"Archivo no encontrado: {current}")

                # ---- FASE 2 (archivos) ----
                for temp, target, current in f_pending:
                    try:
                        if target.exists():
                            os.replace(str(temp), str(current))
                            errors.append(f"Conflicto real de archivo: ya existe {target.name}")
                        else:
                            os.replace(str(temp), str(target))
                            files_reverted += 1
                    except Exception as exc:
                        errors.append(f"Error colocando archivo {target.name} (quedó como {temp}): {exc}")

                nuevo_estado = "REVERTIDO" if not errors else "REVERSION_PARCIAL"
                cur.execute("UPDATE `ejecuciones_lotes` SET `estado` = %s WHERE `id` = %s", (nuevo_estado, ejecucion_id))

            return {
                "success": not errors,
                "ejecucion_id": ejecucion_id,
                "tipo_ejecucion": tipo_ejecucion,
                "folders_reverted": folders_reverted,
                "files_reverted": files_reverted,
                "errors": errors,
                "message": (
                    f"Ejecución #{ejecucion_id} ({tipo_ejecucion}) revertida exitosamente."
                    if not errors else
                    f"Ejecución #{ejecucion_id} ({tipo_ejecucion}) revertida PARCIALMENTE ({len(errors)} problema(s))."
                ),
            }
        finally:
            conn.close()

    def verificar_invariante(self, scan_dirs: Optional[List[Tuple[str, int]]] = None) -> Dict[str, Any]:
        """
        Compara la BD contra el disco REAL:
        - Para cada carpeta indexada calcula dónde DEBERÍA estar (último movimiento no revertido, o su ruta original)
          y verifica que exista.
        - Escanea `scan_dirs` [(ruta, profundidad)] y reporta carpetas con '_' que la BD no conoce.
        """
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT `uuid`, `nombre_original`, `ruta_original` FROM `carpetas_iniciales`")
                iniciales = list(cur.fetchall())
                pos = self._posiciones_actuales(cur)
        finally:
            conn.close()

        esperadas: Dict[str, str] = {}
        faltantes: List[str] = []
        for it in iniciales:
            ruta = pos.get(it["uuid"], it["ruta_original"])
            esperadas[os.path.normcase(str(Path(ruta)))] = it["nombre_original"]
            if not Path(ruta).is_dir():
                faltantes.append(f"{it['nombre_original']} -> esperada en {ruta}")

        desconocidas: List[str] = []
        fisicas = 0
        for base, depth in (scan_dirs or []):
            bp = Path(base)
            if not bp.is_dir():
                continue
            level = [bp]
            for _ in range(max(1, depth)):
                nxt: List[Path] = []
                for d in level:
                    try:
                        nxt.extend([c for c in d.iterdir() if c.is_dir()])
                    except Exception as scan_err:
                        logger.debug(f"No se pudo explorar directorio {d}: {scan_err}")
                level = nxt
            for c in level:
                if "_" not in c.name:
                    continue
                fisicas += 1
                if os.path.normcase(str(c.resolve())) not in esperadas and os.path.normcase(str(c)) not in esperadas:
                    desconocidas.append(str(c))

        return {
            "total_indexadas": len(iniciales),
            "fisicas_escaneadas": fisicas,
            "faltantes": faltantes,
            "desconocidas": desconocidas,
            "congruente": not faltantes and not desconocidas and (fisicas == len(iniciales) if scan_dirs else True),
        }

    def get_executive_statistics(self) -> Dict[str, Any]:
        """
        Estadísticas ejecutivas completas en tiempo real desde las 5 tablas de MySQL.
        Provee todos los KPIs requeridos por el Dashboard Ejecutivo y Reportes:
        total_carpetas, total_pdfs, total_imagenes, carpetas_bien, carpetas_mal,
        carpetas_renombradas, carpetas_sin_cambio, pct_carpetas_bien, pct_imagenes_bien,
        promedio_img_carpeta, total_ejecuciones, desglose_movimientos, desglose_veredicto, desglose_caratula.
        """
        stats: Dict[str, Any] = {
            "total_carpetas": 0,
            "total_carpetas_iniciales": 0,
            "carpetas_bien": 0,
            "carpetas_mal": 0,
            "carpetas_renombradas": 0,
            "carpetas_sin_cambio": 0,
            "pct_carpetas_bien": 0.0,
            "total_pdfs": 0,
            "total_archivos_iniciales": 0,
            "pdfs_bien": 0,
            "pdfs_mal": 0,
            "archivos_modificados": 0,
            "total_imagenes": 0,
            "imagenes_bien": 0,
            "imagenes_mal": 0,
            "pct_imagenes_bien": 0.0,
            "promedio_img_carpeta": 0.0,
            "total_ejecuciones": 0,
            "desglose_veredicto": {},
            "desglose_caratula": {},
            "desglose_movimientos": [],
        }
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                # 1. Conteo de carpetas iniciales y archivos sumados
                cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(`total_archivos_inicial`), 0) AS a FROM `carpetas_iniciales`")
                row = cur.fetchone()
                total_carpetas = int(row["c"]) if row else 0
                total_archivos_ci = int(row["a"]) if row else 0
                stats["total_carpetas"] = total_carpetas
                stats["total_carpetas_iniciales"] = total_carpetas

                # 2. Conteo de PDFs iniciales
                cur.execute("SELECT COUNT(*) AS c FROM `archivos_iniciales`")
                row = cur.fetchone()
                total_pdfs = int(row["c"]) if row and int(row["c"]) > 0 else total_archivos_ci
                stats["total_pdfs"] = total_pdfs
                stats["total_archivos_iniciales"] = total_pdfs

                # 3. Lotes completados / activos
                cur.execute("SELECT COUNT(*) AS c FROM `ejecuciones_lotes` WHERE `estado` != 'REVERTIDO'")
                row = cur.fetchone()
                stats["total_ejecuciones"] = int(row["c"]) if row else 0

                # 4. Carpetas con movimientos activos (renombradas / trasladadas)
                cur.execute("""
                    SELECT COUNT(DISTINCT m.`carpeta_uuid`) AS c
                    FROM `auditoria_movimientos_carpetas` m
                    JOIN `ejecuciones_lotes` e ON e.`id` = m.`ejecucion_id`
                    WHERE e.`estado` != 'REVERTIDO'
                """)
                row = cur.fetchone()
                carpetas_renombradas = int(row["c"]) if row else 0
                stats["carpetas_renombradas"] = carpetas_renombradas
                stats["carpetas_bien"] = carpetas_renombradas
                stats["carpetas_sin_cambio"] = max(0, total_carpetas - carpetas_renombradas)
                stats["carpetas_mal"] = 0
                stats["pct_carpetas_bien"] = round(carpetas_renombradas / total_carpetas * 100, 1) if total_carpetas > 0 else 0.0

                # 5. Archivos modificados y páginas
                cur.execute("""
                    SELECT COUNT(*) AS c, COALESCE(SUM(`cantidad_paginas`), 0) AS p
                    FROM `auditoria_movimientos_archivos` m
                    JOIN `ejecuciones_lotes` e ON e.`id` = m.`ejecucion_id`
                    WHERE e.`estado` != 'REVERTIDO'
                """)
                row = cur.fetchone()
                arch_mod = int(row["c"]) if row else 0
                pags_mod = int(row["p"]) if row else 0
                stats["archivos_modificados"] = arch_mod

                # Si no hay auditoría de páginas aún, estimar páginas = total_pdfs (1 foja mín)
                total_imagenes = pags_mod if pags_mod > 0 else total_pdfs
                stats["total_imagenes"] = total_imagenes
                stats["imagenes_bien"] = total_imagenes if carpetas_renombradas > 0 else 0
                stats["imagenes_mal"] = 0
                stats["pct_imagenes_bien"] = stats["pct_carpetas_bien"]
                stats["promedio_img_carpeta"] = round(total_imagenes / total_carpetas, 1) if total_carpetas > 0 else 0.0

                # 6. Desglose de movimientos agrupados
                cur.execute("""
                    SELECT m.`tipo_accion` AS tipo_movimiento, COUNT(*) AS cantidad, COALESCE(SUM(m.`cantidad_archivos`), 0) AS paginas
                    FROM `auditoria_movimientos_carpetas` m
                    JOIN `ejecuciones_lotes` e ON e.`id` = m.`ejecucion_id`
                    WHERE e.`estado` != 'REVERTIDO'
                    GROUP BY m.`tipo_accion`
                """)
                movs = [dict(r) for r in cur.fetchall()]
                if not movs and total_carpetas > 0:
                    movs = [{"tipo_movimiento": "Indexación Inicial (Sin Renombrar)", "cantidad": total_carpetas, "paginas": total_pdfs}]
                stats["desglose_movimientos"] = movs

                # 7. Desglose por veredicto registral
                if carpetas_renombradas > 0:
                    stats["desglose_veredicto"] = {
                        "PASO 1: RENOMBRADO COMPLETO": {"carpetas": carpetas_renombradas, "pdfs": total_pdfs, "imagenes": total_imagenes},
                    }
                    if stats["carpetas_sin_cambio"] > 0:
                        stats["desglose_veredicto"]["SIN MODIFICAR"] = {
                            "carpetas": stats["carpetas_sin_cambio"],
                            "pdfs": 0,
                            "imagenes": 0,
                        }
                else:
                    stats["desglose_veredicto"] = {
                        "PENDIENTE_DIAGNOSTICO": {"carpetas": total_carpetas, "pdfs": total_pdfs, "imagenes": total_imagenes}
                    }

                # 8. Desglose por estado de carátula
                stats["desglose_caratula"] = {
                    "AUTO_INDEXADA_MYSQL": {"carpetas": total_carpetas, "pdfs": total_pdfs, "imagenes": total_imagenes}
                }

            conn.close()
        except Exception as exc:
            logger.error(f"Error al obtener estadísticas ejecutivas desde MySQL: {exc}", exc_info=True)
        return stats

    def obtener_matriz_trazabilidad(self, modo: str = "carpetas") -> List[Dict[str, Any]]:
        """
        Retorna la matriz de trazabilidad cruzada (Original Inicial vs Destino/Actual).
        Soporta dos modos:
        - 'carpetas': 1 fila por cada una de las 950 carpetas registradas.
        - 'archivos': 1 fila por cada archivo PDF contenido en las carpetas.
        """
        items: List[Dict[str, Any]] = []
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                if modo == "carpetas":
                    cur.execute("""
                        SELECT
                            ci.id,
                            ci.uuid,
                            ci.nombre_original,
                            ci.ruta_original,
                            ci.total_archivos_inicial,
                            ci.fecha_registro,
                            m.tipo_accion,
                            m.nombre_anterior,
                            m.nombre_nuevo,
                            m.ruta_anterior,
                            m.ruta_nueva,
                            m.fecha_accion,
                            el.id AS ejecucion_id,
                            el.estado AS estado_lote
                        FROM `carpetas_iniciales` ci
                        LEFT JOIN (
                            SELECT m1.*
                            FROM `auditoria_movimientos_carpetas` m1
                            JOIN (
                                SELECT `carpeta_uuid`, MAX(`id`) AS max_id
                                FROM `auditoria_movimientos_carpetas`
                                GROUP BY `carpeta_uuid`
                            ) m2 ON m1.`id` = m2.max_id
                        ) m ON ci.`uuid` = m.`carpeta_uuid`
                        LEFT JOIN `ejecuciones_lotes` el ON m.`ejecucion_id` = el.`id`
                        ORDER BY ci.`id` ASC
                    """)
                    rows = cur.fetchall()
                    for r in rows:
                        ha_mov = bool(r.get("nombre_nuevo") and r.get("estado_lote") != "REVERTIDO")
                        orig_n = r.get("nombre_original", "")
                        dest_n = r.get("nombre_nuevo", "") if ha_mov else orig_n
                        orig_r = r.get("ruta_original", "")
                        dest_r = r.get("ruta_nueva", "") if ha_mov else orig_r
                        changed = (orig_n != dest_n)

                        oper_str = r.get("tipo_accion") if ha_mov else "Sin Cambios"
                        estado_str = "📁 RENOMBRADO (PASO 1)" if changed else "⏳ INTACTO"
                        fecha_str = str(r.get("fecha_accion") or r.get("fecha_registro") or "")

                        items.append({
                            "nombre_origen": orig_n,
                            "carpeta_origen": orig_n,
                            "ruta_origen": orig_r,
                            "nombre_actual": dest_n,
                            "carpeta_actual": dest_n,
                            "ruta_actual": dest_r,
                            "numero_paginas": int(r.get("total_archivos_inicial") or 0),
                            "tipos_movimiento": oper_str or "Sin Cambios",
                            "cantidad_cambios": 1 if changed else 0,
                            "hash_inicial": "-",
                            "hash_actual": "-",
                            "fecha_ultimo_movimiento": fecha_str,
                            "ha_cambiado": changed,
                            "estado_str": estado_str,
                        })
                else:  # modo == "archivos"
                    cur.execute("""
                        SELECT
                            ai.id,
                            ai.carpeta_uuid,
                            ai.nombre_archivo_original,
                            ai.orden_indice,
                            ai.fecha_registro,
                            ci.nombre_original AS carpeta_nombre_original,
                            ci.ruta_original AS carpeta_ruta_original,
                            m.tipo_accion,
                            m.nombre_anterior,
                            m.nombre_nuevo,
                            m.ruta_anterior,
                            m.ruta_nueva,
                            m.cantidad_paginas,
                            m.fecha_accion,
                            el.estado AS estado_lote
                        FROM `archivos_iniciales` ai
                        LEFT JOIN `carpetas_iniciales` ci ON ai.`carpeta_uuid` = ci.`uuid`
                        LEFT JOIN (
                            SELECT m1.*
                            FROM `auditoria_movimientos_archivos` m1
                            JOIN (
                                SELECT `carpeta_uuid`, `nombre_anterior`, MAX(`id`) AS max_id
                                FROM `auditoria_movimientos_archivos`
                                GROUP BY `carpeta_uuid`, `nombre_anterior`
                            ) m2 ON m1.`id` = m2.max_id
                        ) m ON ai.`carpeta_uuid` = m.`carpeta_uuid` AND ai.`nombre_archivo_original` = m.`nombre_anterior`
                        LEFT JOIN `ejecuciones_lotes` el ON m.`ejecucion_id` = el.`id`
                        ORDER BY ai.`id` ASC
                    """)
                    rows = cur.fetchall()
                    for r in rows:
                        ha_mov = bool(r.get("nombre_nuevo") and r.get("estado_lote") != "REVERTIDO")
                        orig_n = r.get("nombre_archivo_original", "")
                        dest_n = r.get("nombre_nuevo", "") if ha_mov else orig_n
                        carp_orig = r.get("carpeta_nombre_original", "")
                        orig_r = str(Path(r.get("carpeta_ruta_original") or "") / orig_n)
                        dest_r = r.get("ruta_nueva", "") if ha_mov else orig_r
                        carp_dest = Path(dest_r).parent.name if dest_r else carp_orig
                        changed = (orig_n != dest_n or carp_orig != carp_dest)

                        oper_str = r.get("tipo_accion") if ha_mov else "Sin Cambios"
                        estado_str = "✔ MODIFICADO" if changed else "⏳ INTACTO"
                        fecha_str = str(r.get("fecha_accion") or r.get("fecha_registro") or "")

                        items.append({
                            "nombre_origen": orig_n,
                            "carpeta_origen": carp_orig,
                            "ruta_origen": orig_r,
                            "nombre_actual": dest_n,
                            "carpeta_actual": carp_dest,
                            "ruta_actual": dest_r,
                            "numero_paginas": int(r.get("cantidad_paginas") or 1),
                            "tipos_movimiento": oper_str or "Sin Cambios",
                            "cantidad_cambios": 1 if changed else 0,
                            "hash_inicial": "-",
                            "hash_actual": "-",
                            "fecha_ultimo_movimiento": fecha_str,
                            "ha_cambiado": changed,
                            "estado_str": estado_str,
                        })
            conn.close()
        except Exception as exc:
            logger.error(f"Error al obtener matriz de trazabilidad ({modo}) desde MySQL: {exc}", exc_info=True)
        return items

    def reset_database(self) -> Tuple[bool, str]:
        """Vacía todas las tablas para iniciar una corrida desde cero."""
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                for t in (
                    "auditoria_movimientos_archivos",
                    "auditoria_movimientos_carpetas",
                    "ejecuciones_lotes",
                    "archivos_iniciales",
                    "carpetas_iniciales",
                ):
                    cur.execute(f"TRUNCATE TABLE `{t}`;")
            conn.close()
            return True, "Las 5 tablas de MySQL fueron limpiadas a cero exitosamente."
        except Exception as e:
            return False, f"Error al limpiar tablas en MySQL: {str(e)}"
