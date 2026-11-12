import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

from src.database.mysql_manager import MySQLManager
from src.service import FolderOCRService


def _create_dummy_pdf(path: Path, pages: int = 1, text: str = "Libro 01"):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} - Pag {i + 1}")
    doc.save(str(path))
    doc.close()


class TestMySQLPipelineAndRollback(unittest.TestCase):
    TEST_DB = "test_trazabilidad_selloslibros"

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_test_mysql_"))
        self.root_dir = self.tmp_dir / "raiz"
        self.correctos_dir = self.tmp_dir / "correctos"
        self.apartados_dir = self.tmp_dir / "apartados"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.correctos_dir.mkdir(parents=True, exist_ok=True)
        self.apartados_dir.mkdir(parents=True, exist_ok=True)

        # Carpeta 1: Homogenea sin archivo caratula (se renombrara de _OLD a 01_01_02_00_00_00_1995)
        self.f_ok = self.root_dir / "01_01_02_00_00_00_1995_OLD"
        self.f_ok.mkdir()
        _create_dummy_pdf(self.f_ok / "01_01_02_00_00_00_1995_0001_0010.pdf", pages=2)
        _create_dummy_pdf(self.f_ok / "01_01_02_00_00_00_1995_0011_0020.pdf", pages=3)

        # Carpeta 2: Estructura discrepante (PDF interno tiene otro libro 05 != 03)
        self.f_mismatch = self.root_dir / "01_01_03_00_00_00_1998"
        self.f_mismatch.mkdir()
        _create_dummy_pdf(self.f_mismatch / "01_01_03_00_00_00_1998_0001_0010.pdf", pages=1)
        _create_dummy_pdf(self.f_mismatch / "01_01_05_00_00_00_1998_0011_0020.pdf", pages=1)

        # Carpeta 3: Archivo irregular REG
        self.f_reg = self.root_dir / "01_01_04_00_00_00_1999"
        self.f_reg.mkdir()
        _create_dummy_pdf(self.f_reg / "01_01_04_00_00_00_1999_0001_0010.pdf", pages=1)
        _create_dummy_pdf(self.f_reg / "REG_60.pdf", pages=1)

        self.test_settings = {
            "mysql_enabled": True,
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_database": self.TEST_DB,
        }
        self.mysql = MySQLManager(self.test_settings)
        ok, msg = self.mysql.init_database_and_tables()
        self.assertTrue(ok, f"MySQL debe estar activo para las pruebas: {msg}")
        self.mysql.reset_database()

    def tearDown(self):
        try:
            conn = self.mysql._get_connection(with_db=False)
            cur = conn.cursor()
            cur.execute(f"DROP DATABASE IF EXISTS `{self.TEST_DB}`")
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_full_3step_workflow_and_strict_batch_rollback(self):
        with patch("src.service.SettingsManager.load", return_value=dict(self.test_settings)):
            svc = FolderOCRService()

            # --- PASO 1: Indexar estado inicial y renombrar por 1er PDF ---
            res1 = svc.ejecutar_paso1_indexar_y_renombrar(str(self.root_dir))
            self.assertEqual(res1["total_carpetas"], 3)
            self.assertEqual(res1["nuevas_indexadas"], 3)
            self.assertEqual(res1["archivos_indexados"], 6)
            self.assertEqual(res1["renombradas"], 1)

            renamed_ok = self.root_dir / "01_01_02_00_00_00_1995"
            self.assertTrue(renamed_ok.exists())
            self.assertFalse(self.f_ok.exists())

            # --- PASO 2: Chequeo de Estructura + Embebidas OK (0 llamadas a Claude) ---
            r_ok = svc.process_folder(str(renamed_ok), save_to_db=False)
            self.assertEqual(r_ok["veredicto_global"], "COINCIDE_TOTAL")
            self.assertFalse(r_ok["needs_apartar"])

            r_mis = svc.process_folder(str(self.f_mismatch), save_to_db=False)
            self.assertEqual(r_mis["veredicto_global"], "ESTRUCTURA_DISCREPANTE")
            self.assertTrue(r_mis["needs_apartar"])

            r_reg = svc.process_folder(str(self.f_reg), save_to_db=False)
            self.assertEqual(r_reg["veredicto_global"], "ESTRUCTURA_DISCREPANTE")
            self.assertTrue(r_reg["needs_apartar"])

            # --- PASO 3: Traslado por lotes a Correctos y Apartados ---
            all_res = [r_ok, r_mis, r_reg]
            res3_ok = svc.ejecutar_paso3_trasladar_lote(all_res, str(self.correctos_dir), mode="CORRECTOS")
            self.assertEqual(res3_ok["movidas"], 1)
            self.assertTrue((self.correctos_dir / "01_01_02_00_00_00_1995").exists())

            res3_ap = svc.ejecutar_paso3_trasladar_lote(all_res, str(self.apartados_dir), mode="APARTADOS")
            self.assertEqual(res3_ap["movidas"], 2)

            # Verificar historial de ejecuciones (INDEXACION_INICIAL + PASO_1 + PASO_3_CORRECTOS + PASO_3_APARTADOS)
            hist = svc.obtener_historial_ejecuciones()
            self.assertEqual(len(hist), 4)
            self.assertEqual(hist[0]["tipo_ejecucion"], "PASO_3_MOVER_APARTADOS")

            # --- ROLLBACK ESTRICTO POR LOTE (orden inverso: 3B Apartados -> 3A Correctos -> 1 Renombrado) ---
            rb1 = svc.ejecutar_rollback_ultima_ejecucion()
            self.assertTrue(rb1["success"])
            self.assertEqual(rb1["folders_reverted"], 2)
            self.assertTrue(self.f_mismatch.exists())
            self.assertTrue(self.f_reg.exists())

            rb2 = svc.ejecutar_rollback_ultima_ejecucion()
            self.assertTrue(rb2["success"])
            self.assertEqual(rb2["folders_reverted"], 1)
            self.assertTrue(renamed_ok.exists())
            self.assertFalse((self.correctos_dir / "01_01_02_00_00_00_1995").exists())

            rb3 = svc.ejecutar_rollback_ultima_ejecucion()
            self.assertTrue(rb3["success"])
            self.assertEqual(rb3["folders_reverted"], 1)
            self.assertTrue(self.f_ok.exists())
            self.assertFalse(renamed_ok.exists())

            # Si queda INDEXACION_INICIAL (0 movimientos fisicos), revertirla tambien deja 0 lotes pendientes
            if svc.mysql.obtener_ultima_ejecucion_revertible():
                rb4 = svc.ejecutar_rollback_ultima_ejecucion()
                self.assertTrue(rb4["success"])
                self.assertEqual(rb4["folders_reverted"], 0)

            self.assertIsNone(svc.mysql.obtener_ultima_ejecucion_revertible())


if __name__ == "__main__":
    unittest.main()
