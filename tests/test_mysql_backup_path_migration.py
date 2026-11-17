import unittest
from unittest.mock import MagicMock, patch
import pymysql

from src.database.mysql_manager import MySQLManager


class TestMySQLBackupPathMigration(unittest.TestCase):
    """
    Valida la auto-migración y autorreparación (self-healing) de la columna 'backup_path'
    en la tabla `auditoria_movimientos_archivos`, evitando que traslados o convalidaciones
    fallen con error 1054.
    """

    def setUp(self):
        self.mgr = MySQLManager({
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_database": "trazabilidad_selloslibros",
            "mysql_enabled": True
        })

    def test_01_ensure_schema_creates_column_idempotently(self):
        """Valida que ensure_schema sea idempotente y cree la columna backup_path."""
        self.mgr._schema_verified = False
        ok = self.mgr.ensure_schema()
        self.assertTrue(ok)
        self.assertTrue(self.mgr._schema_verified)

        # Segunda llamada rápida usa el flag en memoria
        ok2 = self.mgr.ensure_schema()
        self.assertTrue(ok2)

    def test_02_registrar_movimientos_archivos_lote_auto_migrates_on_error_1054(self):
        """
        Valida que si cur.executemany lanza error 1054 ('Unknown column backup_path'),
        la función ejecute ALTER TABLE y reintente exitosamente el insert.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        call_count = {"count": 0}

        def mock_executemany(sql, vals):
            call_count["count"] += 1
            if call_count["count"] == 1:
                # Simular error 1054 en el primer intento
                raise pymysql.err.OperationalError(1054, "Unknown column 'backup_path' in 'field list'")
            # En el segundo intento (tras ALTER TABLE) tiene éxito
            return len(vals)

        mock_cur.executemany.side_effect = mock_executemany

        with patch.object(self.mgr, "get_connection", return_value=mock_conn):
            movs = [
                {
                    "ejecucion_id": 999,
                    "carpeta_uuid": "test-uuid-1",
                    "tipo_accion": "MOVIDO_A_CORRECTOS",
                    "nombre_anterior": "doc1.pdf",
                    "nombre_nuevo": "doc1.pdf",
                    "ruta_anterior": "C:/orig/doc1.pdf",
                    "ruta_nueva": "C:/dest/doc1.pdf",
                    "cantidad_paginas": 5,
                    "backup_path": "C:/bkp/doc1.pdf",
                }
            ]
            # No debe lanzar excepción
            self.mgr.registrar_movimientos_archivos_lote(movs)

        # Se debe haber intentado 2 veces
        self.assertEqual(call_count["count"], 2)
        # Se debe haber ejecutado ALTER TABLE
        mock_cur.execute.assert_called_with("ALTER TABLE `auditoria_movimientos_archivos` ADD COLUMN `backup_path` TEXT NULL")

    def test_03_fallback_insert_without_backup_path_if_alter_fails(self):
        """
        Valida que si tanto el primer insert como el ALTER TABLE fallan (ej. usuario sin permisos DDL),
        se realice el fallback de insertar sin la columna backup_path para no abortar el traslado.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        executed_sqls = []

        def mock_executemany(sql, vals):
            executed_sqls.append(sql)
            if len(executed_sqls) == 1:
                raise pymysql.err.OperationalError(1054, "Unknown column 'backup_path' in 'field list'")
            return len(vals)

        mock_cur.executemany.side_effect = mock_executemany
        mock_cur.execute.side_effect = pymysql.err.OperationalError(1142, "ALTER command denied to user")

        with patch.object(self.mgr, "get_connection", return_value=mock_conn):
            movs = [
                {
                    "ejecucion_id": 999,
                    "carpeta_uuid": "test-uuid-2",
                    "tipo_accion": "MOVIDO_A_CORRECTOS",
                    "nombre_anterior": "doc2.pdf",
                    "nombre_nuevo": "doc2.pdf",
                    "ruta_anterior": "C:/orig/doc2.pdf",
                    "ruta_nueva": "C:/dest/doc2.pdf",
                    "cantidad_paginas": 3,
                    "backup_path": "C:/bkp/doc2.pdf",
                }
            ]
            self.mgr.registrar_movimientos_archivos_lote(movs)

        self.assertEqual(len(executed_sqls), 2)
        # El segundo SQL ejecutado no debe contener backup_path
        self.assertNotIn("backup_path", executed_sqls[1])


if __name__ == "__main__":
    unittest.main()
