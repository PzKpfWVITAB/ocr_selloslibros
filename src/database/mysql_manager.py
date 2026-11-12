import pymysql
import json
import uuid
import shutil
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple


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
                        `fecha_accion` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX `idx_ejecucion_arch` (`ejecucion_id`),
                        INDEX `idx_carpeta_uuid_arch` (`carpeta_uuid`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

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
        if not self.enabled or not ejecucion_id:
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

    def registrar_movimientos_archivos_lote(self, movimientos: List[Dict[str, Any]]):
        """Inserta en lote registros de `auditoria_movimientos_archivos`."""
        if not self.enabled or not movimientos:
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
                    )
                    for m in movimientos
                ]
                cur.executemany("""
                    INSERT INTO `auditoria_movimientos_archivos` (
                        `ejecucion_id`, `carpeta_uuid`, `tipo_accion`,
                        `nombre_anterior`, `nombre_nuevo`, `ruta_anterior`, `ruta_nueva`, `cantidad_paginas`
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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

    def obtener_ultima_ejecucion_revertible(self) -> Optional[Dict[str, Any]]:
        """
        Retorna la ejecución más reciente en estado 'COMPLETADO' que realizó cambios en disco
        (excluyendo puramente INDEXACION_INICIAL que no mueve archivos).
        """
        conn = self.get_connection(include_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM `ejecuciones_lotes`
                    WHERE `estado` = 'COMPLETADO'
                      AND `tipo_ejecucion` != 'INDEXACION_INICIAL'
                    ORDER BY `id` DESC LIMIT 1
                """)
                return cur.fetchone()
        finally:
            conn.close()

    def revertir_ultima_ejecucion(self) -> Dict[str, Any]:
        """
        Rollback estricto por ejecución en orden cronológico inverso:
        1. Localiza la última ejecución 'COMPLETADO' con modificaciones físicas.
        2. Revierte los movimientos de carpetas (ruta_nueva -> ruta_anterior) en orden inverso.
        3. Revierte los movimientos/renombres de archivos PDF en orden inverso.
        4. Marca la ejecución como 'REVERTIDO'.
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
                cur.execute("""
                    SELECT * FROM `auditoria_movimientos_carpetas`
                    WHERE `ejecucion_id` = %s ORDER BY `id` DESC
                """, (ejecucion_id,))
                mov_carpetas = list(cur.fetchall())

                cur.execute("""
                    SELECT * FROM `auditoria_movimientos_archivos`
                    WHERE `ejecucion_id` = %s ORDER BY `id` DESC
                """, (ejecucion_id,))
                mov_archivos = list(cur.fetchall())

                # Mapa para ubicar carpetas revertidas por si los archivos aún están dentro de ruta_nueva o ruta_anterior
                ruta_carpeta_por_uuid: Dict[str, str] = {}

                # 1. Revertir carpetas (ruta_nueva -> ruta_anterior)
                for mc in mov_carpetas:
                    r_nueva = Path(mc["ruta_nueva"])
                    r_ant = Path(mc["ruta_anterior"])
                    c_uuid = mc["carpeta_uuid"]

                    try:
                        if r_nueva.exists() and str(r_nueva) != str(r_ant):
                            r_ant.parent.mkdir(parents=True, exist_ok=True)
                            if r_ant.exists():
                                errors.append(f"Destino de rollback ya existe: {r_ant}")
                            else:
                                shutil.move(str(r_nueva), str(r_ant))
                                folders_reverted += 1
                                ruta_carpeta_por_uuid[c_uuid] = str(r_ant)
                        elif r_ant.exists():
                            ruta_carpeta_por_uuid[c_uuid] = str(r_ant)
                    except Exception as exc:
                        errors.append(f"Error al revertir carpeta {r_nueva.name}: {exc}")

                # 2. Revertir nombres/rutas de archivos PDF (nombre_nuevo -> nombre_anterior)
                for ma in mov_archivos:
                    r_nueva = Path(ma["ruta_nueva"])
                    r_ant = Path(ma["ruta_anterior"])
                    c_uuid = ma["carpeta_uuid"]

                    try:
                        # Si la carpeta fue revertida arriba, buscar el archivo dentro de la carpeta revertida
                        current_file_p = r_nueva
                        target_file_p = r_ant

                        if not current_file_p.exists() and c_uuid in ruta_carpeta_por_uuid:
                            reverted_dir = Path(ruta_carpeta_por_uuid[c_uuid])
                            candidate = reverted_dir / ma["nombre_nuevo"]
                            if candidate.exists():
                                current_file_p = candidate
                                target_file_p = reverted_dir / ma["nombre_anterior"]

                        if current_file_p.exists() and str(current_file_p) != str(target_file_p):
                            if not target_file_p.exists():
                                os.rename(str(current_file_p), str(target_file_p))
                                files_reverted += 1
                            else:
                                errors.append(f"Archivo destino ya existe al revertir: {target_file_p.name}")
                    except Exception as exc:
                        errors.append(f"Error al revertir archivo {ma['nombre_nuevo']}: {exc}")

                # 3. Marcar ejecución como REVERTIDO
                cur.execute("""
                    UPDATE `ejecuciones_lotes`
                    SET `estado` = 'REVERTIDO'
                    WHERE `id` = %s
                """, (ejecucion_id,))

            return {
                "success": True,
                "ejecucion_id": ejecucion_id,
                "tipo_ejecucion": tipo_ejecucion,
                "folders_reverted": folders_reverted,
                "files_reverted": files_reverted,
                "errors": errors,
                "message": f"Ejecución #{ejecucion_id} ({tipo_ejecucion}) revertida exitosamente."
            }
        finally:
            conn.close()

    def get_executive_statistics(self) -> Dict[str, Any]:
        """Estadísticas en tiempo real desde las 5 tablas de MySQL."""
        stats = {
            "total_carpetas_iniciales": 0,
            "total_archivos_iniciales": 0,
            "total_ejecuciones": 0,
            "total_movimientos_carpetas": 0,
            "total_movimientos_archivos": 0,
            "total_paginas_auditadas": 0,
        }
        if not self.enabled:
            return stats
        try:
            conn = self.get_connection(include_db=True)
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(`total_archivos_inicial`), 0) AS a FROM `carpetas_iniciales`")
                row = cur.fetchone()
                if row:
                    stats["total_carpetas_iniciales"] = int(row["c"])
                    stats["total_archivos_iniciales"] = int(row["a"])

                cur.execute("SELECT COUNT(*) AS c FROM `ejecuciones_lotes` WHERE `estado` = 'COMPLETADO'")
                row = cur.fetchone()
                if row:
                    stats["total_ejecuciones"] = int(row["c"])

                cur.execute("SELECT COUNT(*) AS c FROM `auditoria_movimientos_carpetas`")
                row = cur.fetchone()
                if row:
                    stats["total_movimientos_carpetas"] = int(row["c"])

                cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(`cantidad_paginas`), 0) AS p FROM `auditoria_movimientos_archivos`")
                row = cur.fetchone()
                if row:
                    stats["total_movimientos_archivos"] = int(row["c"])
                    stats["total_paginas_auditadas"] = int(row["p"])
            conn.close()
        except Exception:
            pass
        return stats

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
