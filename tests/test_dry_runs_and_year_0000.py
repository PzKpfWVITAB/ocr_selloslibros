import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.service import FolderOCRService
from src.database.mysql_manager import MySQLManager
from src.parsers.folder_parser import FolderParser


def _dummy_pdf(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4\n%test\n%%EOF")


class TestDryRunsAndYear0000(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ocr_dry_"))
        self.root = self.tmp / "raiz"
        self.root.mkdir()
        self.test_db = "trazabilidad_test_dry"
        self.cfg = {
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_database": self.test_db,
            "apartados_dir": str(self.tmp / "apartados"),
            "validados_dir": str(self.tmp / "correctos"),
        }
        self.patcher = patch("src.core.settings_manager.SettingsManager.load", return_value=self.cfg)
        self.patcher.start()

        m = MySQLManager(self.cfg)
        ok, msg = m.init_database_and_tables()
        if not ok:
            self.skipTest(f"MySQL no disponible: {msg}")
        m.reset_database()

    def tearDown(self):
        try:
            m = MySQLManager(self.cfg)
            m.reset_database()
        except Exception:
            pass
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_year_0000_in_first_pdf_is_strictly_preserved(self):
        """
        Regla del usuario: 'sin importar si la carpeta tiene el primer pdf como año en 0000
        debe de renombrar tal cual hasta ese espacio de año.'
        """
        f = self.root / "01_01_03_00_00_00_1985_ORIGINAL"
        f.mkdir()
        # Primer PDF tiene año 0000
        _dummy_pdf(f / "01_01_03_00_00_00_0000_0001_0010.pdf")
        _dummy_pdf(f / "01_01_03_00_00_00_0000_0011_0020.pdf")

        svc = FolderOCRService()
        plan = svc.planificar_paso1(str(self.root))
        self.assertEqual(plan["total"], 1)
        self.assertEqual(plan["renombrar"], 1)
        item = plan["items"][0]
        # Debe ser exactamente 01_01_03_00_00_00_0000 (NO 1985)
        self.assertEqual(item["nuevo"], "01_01_03_00_00_00_0000")

        # Ejecutar renombre y comprobar en disco y MySQL
        res = svc.ejecutar_paso1_indexar_y_renombrar(str(self.root))
        self.assertEqual(res["renombradas"], 1)
        self.assertTrue((self.root / "01_01_03_00_00_00_0000").exists())
        self.assertFalse(f.exists())

        # Rollback atómico de 2 fases
        rb = svc.ejecutar_rollback_ultima_ejecucion()
        self.assertTrue(rb["success"])
        self.assertTrue(f.exists())
        self.assertFalse((self.root / "01_01_03_00_00_00_0000").exists())

    def test_legacy_folder_starting_with_year_does_not_destroy_delegacion_01(self):
        """
        Verifica que carpetas con formato antiguo que empiezan por el año (ej. 1982_03_01_00_00_00_0001-0009
        o 0000_01_00_00_00_00_00) NO destruyan la delegación 01 ni pongan el año en ambos extremos.
        """
        f1 = self.root / "1982_03_01_00_00_00_0001-0009"
        f1.mkdir()
        _dummy_pdf(f1 / "01_03_01_00_00_00_1982_0001_00.pdf")
        _dummy_pdf(f1 / "01_03_01_00_00_00_1982_0002_00.pdf")

        f2 = self.root / "0000_01_00_00_00_00_00"
        f2.mkdir()
        _dummy_pdf(f2 / "01_00_00_00_00_00_0000_0001_00_DUP1.pdf")
        _dummy_pdf(f2 / "01_00_00_00_00_00_0000_0021_00.pdf")

        svc = FolderOCRService()
        plan = svc.planificar_paso1(str(self.root))
        by_name = {i["carpeta"]: i["nuevo"] for i in plan["items"]}

        # f1 DEBE empezar por 01_ y terminar en _1982 (NO 1982_..._1982)
        self.assertEqual(by_name[f1.name], "01_03_01_00_00_00_1982")
        self.assertFalse(by_name[f1.name].startswith("1982"))

        # f2 DEBE empezar por 01_ y terminar en _0000 (NO 0000_..._0000)
        self.assertEqual(by_name[f2.name], "01_00_00_00_00_00_0000")
        self.assertFalse(by_name[f2.name].startswith("0000"))

    def test_planificar_paso2_clasifica_sin_tokens(self):
        """
        Prueba la clasificación pre-IA (0 tokens):
        - Carpeta 1: Tiene un PDF con diferente libro -> APARTADOS (Paso 2A)
        - Carpeta 2: Homogénea sin carátula externa -> CORRECTOS (Carátula embebida, Paso 2B)
        - Carpeta 3: Homogénea con archivo carátula -> REQUIERE_IA (Paso 2C)
        """
        # Carpeta 1: Discrepante
        c1 = self.root / "01_01_01_00_00_00_1990"
        c1.mkdir()
        _dummy_pdf(c1 / "01_01_01_00_00_00_1990_0001_0010.pdf")
        _dummy_pdf(c1 / "01_01_02_00_00_00_1990_0011_0020.pdf")  # Difiere en libro: 02 != 01

        # Carpeta 2: Homogénea embebida (sin archivo _CARATULA)
        c2 = self.root / "01_01_03_00_00_00_1992"
        c2.mkdir()
        _dummy_pdf(c2 / "01_01_03_00_00_00_1992_0001_0010.pdf")
        _dummy_pdf(c2 / "01_01_03_00_00_00_1992_0011_0020.pdf")

        # Carpeta 3: Homogénea con archivo de carátula
        c3 = self.root / "01_01_04_00_00_00_1994"
        c3.mkdir()
        _dummy_pdf(c3 / "01_01_04_00_00_00_1994_CARATULA_00.pdf")
        _dummy_pdf(c3 / "01_01_04_00_00_00_1994_0001_0010.pdf")

        svc = FolderOCRService()
        plan2 = svc.planificar_paso2_estructura(str(self.root))
        self.assertEqual(plan2["total"], 3)
        self.assertEqual(plan2["apartados"], 1)
        self.assertEqual(plan2["correctos"], 1)
        self.assertEqual(plan2["requiere_ia"], 1)

        by_folder = {i["carpeta"]: i for i in plan2["items"]}
        self.assertEqual(by_folder[c1.name]["clasificacion"], "APARTADOS")
        self.assertEqual(by_folder[c2.name]["clasificacion"], "CORRECTOS")
        self.assertEqual(by_folder[c3.name]["clasificacion"], "REQUIERE_IA")


if __name__ == "__main__":
    unittest.main()
