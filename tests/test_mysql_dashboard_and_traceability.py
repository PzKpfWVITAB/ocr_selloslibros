import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.core.settings_manager import SettingsManager
from src.database.mysql_manager import MySQLManager
from src.service import FolderOCRService as OCRService


class TestMySQLDashboardAndTraceability(unittest.TestCase):
    def setUp(self):
        self.sm = SettingsManager()
        self.cfg = self.sm.load()
        self.mm = MySQLManager(self.cfg)
        ok, msg = self.mm.test_connection_params(
            self.mm.host, self.mm.port, self.mm.user, self.mm.password
        )
        if not ok:
            self.skipTest(f"MySQL no disponible en {self.mm.host}:{self.mm.port}: {msg}")

        self.service = OCRService()
        self.tmp_dir = tempfile.mkdtemp(prefix="test_mysql_dash_")
        self.raiz = Path(self.tmp_dir) / "raiz"
        self.raiz.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_settings_mysql_enabled_by_default(self):
        """Verifica que mysql_enabled esté activo por defecto."""
        settings = self.sm.load()
        self.assertTrue(settings.get("mysql_enabled"))
        self.assertTrue(self.service.mysql.enabled)

    def test_paso1_records_audit_and_populates_dashboard_stats(self):
        """
        Crea carpetas dummy, corre Paso 1 y valida:
        - Indexación en carpetas_iniciales y archivos_iniciales.
        - Registro en auditoria_movimientos_carpetas.
        - Estadísticas ejecutivas > 0.
        - Matriz de trazabilidad en ambos modos (carpetas y archivos).
        - Reversión atómica LIFO de vuelta al origen.
        """
        # Crear 2 carpetas de prueba
        f1 = self.raiz / "0000_01_00_00_00_00_00"
        f1.mkdir()
        (f1 / "01_00_00_00_00_00_0000_0001_0010.pdf").write_bytes(b"%PDF-1.4 dummy")

        f2 = self.raiz / "1982_03_01_00_00_00_0001-0009"
        f2.mkdir()
        (f2 / "01_03_01_00_00_00_1982_0001_0009.pdf").write_bytes(b"%PDF-1.4 dummy")

        # Ejecutar Paso 1
        res = self.service.ejecutar_paso1_indexar_y_renombrar(self.raiz)
        self.assertGreaterEqual(res["renombradas"], 1)
        ejec_id = res["ejecucion_id"]
        self.assertIsNotNone(ejec_id)

        # 1. Validar Estadísticas Ejecutivas
        stats = self.service.mysql.get_executive_statistics()
        self.assertGreaterEqual(stats["total_carpetas"], 2)
        self.assertGreaterEqual(stats["total_pdfs"], 2)
        self.assertGreaterEqual(stats["carpetas_renombradas"], 1)
        self.assertIn("desglose_movimientos", stats)
        self.assertGreater(len(stats["desglose_movimientos"]), 0)

        # 2. Validar Matriz de Trazabilidad en Modo Carpetas
        matriz_c = self.service.mysql.obtener_matriz_trazabilidad("carpetas")
        self.assertGreaterEqual(len(matriz_c), 2)
        # Buscar la fila de f1 o f2 por su ruta exacta
        f1_row = next((r for r in matriz_c if str(Path(r["ruta_origen"])) == str(f1.resolve())), None)
        self.assertIsNotNone(f1_row)
        self.assertEqual(f1_row["nombre_actual"], "01_00_00_00_00_00_0000")
        self.assertTrue(f1_row["ha_cambiado"])
        self.assertEqual(f1_row["estado_str"], "📁 RENOMBRADO (PASO 1)")

        # 3. Validar Matriz de Trazabilidad en Modo Archivos
        matriz_a = self.service.mysql.obtener_matriz_trazabilidad("archivos")
        self.assertGreaterEqual(len(matriz_a), 2)
        pdf1_row = next((r for r in matriz_a if str(Path(r["ruta_origen"])) == str((f1 / "01_00_00_00_00_00_0000_0001_0010.pdf").resolve())), None)
        self.assertIsNotNone(pdf1_row)
        self.assertEqual(pdf1_row["carpeta_origen"], "0000_01_00_00_00_00_00")

        # 4. Validar Rollback LIFO
        rb = self.service.ejecutar_rollback_ultima_ejecucion()
        self.assertTrue(rb["success"])
        self.assertGreaterEqual(rb["folders_reverted"], 1)

        # Comprobar que en disco volvieron a sus nombres originales
        self.assertTrue(f1.exists())
        self.assertTrue(f2.exists())
        self.assertFalse((self.raiz / "01_00_00_00_00_00_0000").exists())


if __name__ == "__main__":
    unittest.main()
