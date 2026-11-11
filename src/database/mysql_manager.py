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
                        INDEX `idx_veredicto` (`veredicto`),
                        INDEX `idx_nom_carp` (`nombre_carpeta`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)

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
