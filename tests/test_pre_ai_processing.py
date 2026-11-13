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


class TestPreAIProcessing(unittest.TestCase):
    TEST_DB = "test_pre_ai_trazabilidad"

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_pre_ai_"))
        self.root_dir = self.tmp_dir / "raiz"
        self.validados_dir = self.tmp_dir / "validados"
        self.apartados_dir = self.tmp_dir / "apartados"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.validados_dir.mkdir(parents=True, exist_ok=True)
        self.apartados_dir.mkdir(parents=True, exist_ok=True)

        # 1. Carpeta con Estructura Discrepante (PDF irregular REG) -> clasificacion: APARTADOS
        self.f_discrepante = self.root_dir / "01_01_03_00_00_00_1998"
        self.f_discrepante.mkdir()
        _create_dummy_pdf(self.f_discrepante / "01_01_03_00_00_00_1998_0001_0010.pdf", pages=1)
        _create_dummy_pdf(self.f_discrepante / "REG_60.pdf", pages=1)

        # 2. Carpeta Homogenea sin archivo caratula suelta -> clasificacion: CORRECTOS (caratula embebida)
        self.f_correcta = self.root_dir / "01_01_02_00_00_00_1995"
        self.f_correcta.mkdir()
        _create_dummy_pdf(self.f_correcta / "01_01_02_00_00_00_1995_0001_0010.pdf", pages=2)
        _create_dummy_pdf(self.f_correcta / "01_01_02_00_00_00_1995_0011_0020.pdf", pages=3)

        # 3. Carpeta Homogenea con archivo caratula suelta -> clasificacion: REQUIERE_IA
        self.f_ia = self.root_dir / "01_01_04_00_00_00_2000"
        self.f_ia.mkdir()
        _create_dummy_pdf(self.f_ia / "01_01_04_00_00_00_2000_CARATULA.pdf", pages=1, text="Caratula")
        _create_dummy_pdf(self.f_ia / "01_01_04_00_00_00_2000_0001_0010.pdf", pages=2)

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
        self.assertTrue(ok, f"MySQL debe estar activo: {msg}")
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

    def test_pre_ai_workflow_isolation_and_rollback(self):
        with patch("src.service.SettingsManager.load", return_value=dict(self.test_settings)):
            svc = FolderOCRService()

            # 1. Simulación / Planificación del Chequeo Pre-IA (0 Tokens)
            plan = svc.planificar_paso2_estructura(str(self.root_dir))
            self.assertEqual(plan["total"], 3)
            self.assertEqual(plan["apartados"], 1)
            self.assertEqual(plan["correctos"], 1)
            self.assertEqual(plan["requiere_ia"], 1)

            # 2. Paso 2A: Apartar carpetas con discrepancia estructural (0 Tokens)
            res_2a = svc.ejecutar_paso2a_apartar_estructura(str(self.root_dir), str(self.apartados_dir))
            self.assertEqual(res_2a["movidas"], 1)
            dest_disc = self.apartados_dir / "ESTRUCTURA_DISCREPANTE" / "01_01_03_00_00_00_1998"
            self.assertTrue(dest_disc.exists(), "La carpeta discrepante debe moverse a ESTRUCTURA_DISCREPANTE")
            self.assertFalse(self.f_discrepante.exists(), "No debe seguir en la raiz")
            self.assertTrue(self.f_correcta.exists(), "La carpeta correcta debe seguir en la raiz")
            self.assertTrue(self.f_ia.exists(), "La carpeta REQUIERE_IA debe seguir en la raiz")

            # 3. Paso 2B: Auditar correctas en MySQL (0 Tokens, sin movimiento fisico)
            res_2b = svc.ejecutar_paso2b_auditar_correctos(str(self.root_dir))
            self.assertEqual(res_2b["auditadas"], 1)
            self.assertTrue(self.f_correcta.exists(), "En el Paso 2B la carpeta correcta no se mueve fisicamente")

            # 4. Paso 2C: Trasladar correctas a Validados (0 Tokens)
            res_2c = svc.ejecutar_traslado_validados(str(self.root_dir), str(self.validados_dir))
            self.assertEqual(res_2c["movidas"], 1)
            dest_corr = self.validados_dir / "01_01_02_00_00_00_1995"
            self.assertTrue(dest_corr.exists(), "La carpeta correcta debe estar en validados")
            self.assertFalse(self.f_correcta.exists(), "La carpeta correcta ya no debe estar en la raiz")

            # 5. Invariante: En la raiz debe quedar UNICAMENTE la carpeta que requiere IA
            remaining_in_root = [p.name for p in self.root_dir.iterdir() if p.is_dir()]
            self.assertEqual(remaining_in_root, ["01_01_04_00_00_00_2000"],
                             "En la raiz solo debe quedar la carpeta con caratula suelta que requiere IA")

            # 6. Historial de Lotes en MySQL
            hist = svc.obtener_historial_ejecuciones()
            tipos = [h["tipo_ejecucion"] for h in hist]
            self.assertIn("PASO_2A_APARTAR_ESTRUCTURA", tipos)
            self.assertIn("PASO_2B_AUDITAR_CORRECTOS", tipos)
            self.assertIn("TRASLADO_VALIDADOS_CORRECTOS", tipos)

            # 7. Rollback LIFO estricto:
            # Revertir Traslado a Validados (Paso 2C)
            rb_2c = svc.ejecutar_rollback_ultima_ejecucion()
            self.assertTrue(rb_2c.get("success"), f"Rollback 2C falló: {rb_2c}")
            self.assertTrue(self.f_correcta.exists(), "La carpeta correcta debe volver a la raiz")
            self.assertFalse(dest_corr.exists(), "No debe quedar en validados")

            # Revertir Auditoria Correctos (Paso 2B)
            rb_2b = svc.ejecutar_rollback_ultima_ejecucion()
            self.assertTrue(rb_2b.get("success"), f"Rollback 2B falló: {rb_2b}")

            # Revertir Apartado Discrepantes (Paso 2A)
            rb_2a = svc.ejecutar_rollback_ultima_ejecucion()
            self.assertTrue(rb_2a.get("success"), f"Rollback 2A falló: {rb_2a}")
            self.assertTrue(self.f_discrepante.exists(), "La carpeta discrepante debe volver a la raiz")
            self.assertFalse(dest_disc.exists(), "No debe quedar en apartados")

            # Comprobar que las 3 carpetas originales estan de nuevo en la raiz
            all_restored = sorted([p.name for p in self.root_dir.iterdir() if p.is_dir()])
            self.assertEqual(all_restored, [
                "01_01_02_00_00_00_1995",
                "01_01_03_00_00_00_1998",
                "01_01_04_00_00_00_2000"
            ])

    def test_discrepancy_correction_and_manual_override(self):
        with patch("src.service.SettingsManager.load", return_value=dict(self.test_settings)):
            svc = FolderOCRService()

            # Caso 1: Carpeta en ceros pero OCR/Caratula detectó datos reales
            # La carpeta tiene 01_00_00_00_00_00_1995 y OCR detectó Libro 02
            f_test = self.root_dir / "01_00_00_00_00_00_1995"
            f_test.mkdir()
            _create_dummy_pdf(f_test / "01_00_00_00_00_00_1995_0001_0010.pdf", pages=2)

            diag = {
                "folder_path": str(f_test),
                "folder_data": {"delegacion": "01", "folder_name": f_test.name, "is_valid": True},
                "codigo_base_sugerido": "01_01_02_00_00_00_1995",
                "needs_rename": True,
                "needs_apartar": True,
                "has_discrepancy": True,
                "veredicto_global": "DISCREPANCIA",
                "subcarpeta_revision": "DISCREPANCIA CARATULA VS CARPETA",
                "caratula_inspection": {"status": "OK_CON_INFO", "dup_pdfs": [], "irregular_pdfs": []},
                "ocr_consolidado": {
                    "seccion": "01", "libro": "02", "tomo": "00",
                    "volumen": "00", "fraccionamiento": "00", "anio": "1995"
                }
            }

            # Renombrar usando el código sugerido por la carátula
            ren_res = svc.execute_folder_and_pdfs_rename(diag)
            self.assertEqual(diag["veredicto_global"], "COINCIDE_TOTAL", "Al corregir discrepancia debe pasar a COINCIDE_TOTAL")
            self.assertFalse(diag["needs_apartar"], "Ya no debe estar marcada para apartar")
            self.assertFalse(diag["has_discrepancy"], "Ya no debe tener discrepancia")
            self.assertEqual(diag["subcarpeta_revision"], "")

            # Caso 2: La IA leyó un dato erróneo (ej. Libro 04), pero el usuario revisa la carátula
            # y ve que en realidad es Libro 03, aplicando un override manual
            diag2 = {
                "folder_path": diag["folder_path"],
                "folder_data": diag["folder_data"],
                "codigo_base_sugerido": "01_01_04_00_00_00_1995",  # Dato erróneo de IA
                "needs_rename": True,
                "needs_apartar": True,
                "has_discrepancy": True,
                "veredicto_global": "DISCREPANCIA",
                "caratula_inspection": {"status": "OK_CON_INFO", "dup_pdfs": [], "irregular_pdfs": []},
                "ocr_consolidado": {
                    "seccion": "01", "libro": "04", "tomo": "00",
                    "volumen": "00", "fraccionamiento": "00", "anio": "1995"
                }
            }

            # Usuario aplica corrección manual con custom_clean_base_code
            ren_manual = svc.execute_folder_and_pdfs_rename(diag2, custom_clean_base_code="01_01_03_00_00_00_1995")
            self.assertEqual(diag2["veredicto_global"], "COINCIDE_TOTAL")
            self.assertFalse(diag2["needs_apartar"], "Con la corrección manual debe ser válida y no apartarse")
            self.assertEqual(diag2["ocr_consolidado"]["libro"], "03", "OCR consolidado debe reflejar el libro corregido por el usuario")


if __name__ == "__main__":
    unittest.main()
