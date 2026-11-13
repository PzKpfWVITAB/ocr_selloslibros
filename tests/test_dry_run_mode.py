import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pymupdf as fitz

from src.core.caratula_inspector import CaratulaInspector
from src.core.file_operations import FileOperations
from src.database.mysql_manager import MySQLManager
from src.service import FolderOCRService


def _create_test_pdf(path: Path, pages_content: list):
    """Crea un PDF de prueba donde cada elemento de pages_content es el texto de una página (o None para blanco)."""
    doc = fitz.open()
    for text in pages_content:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), str(text))
    doc.save(str(path))
    doc.close()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestDryRunMode(unittest.TestCase):
    TEST_DB = "test_dry_run_db"

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_dry_run_"))
        self.root_dir = self.tmp_dir / "raiz"
        self.dry_out_dir = self.tmp_dir / "salida_dry_run"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.dry_out_dir.mkdir(parents=True, exist_ok=True)

        self.test_settings = {
            "mysql_enabled": True,
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_database": self.TEST_DB,
            "dry_run_mode": False,
            "dry_run_output_dir": str(self.dry_out_dir),
        }
        self.mysql = MySQLManager(self.test_settings)
        ok, msg = self.mysql.init_database_and_tables()
        self.assertTrue(ok, f"MySQL debe estar activo para pruebas: {msg}")
        self.mysql.reset_database()

        self.service = FolderOCRService(self.test_settings)
        self.service.mysql = self.mysql

    def tearDown(self):
        try:
            conn = self.mysql.get_connection(include_db=False)
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{self.TEST_DB}`")
            conn.close()
        except Exception:
            pass

        if self.tmp_dir.exists():
            shutil.rmtree(str(self.tmp_dir), ignore_errors=True)

    def test_01_dry_run_copy_leaves_original_untouched(self):
        """
        Garantiza que la simulación Dry Run copie y transforme en destino SIN alterar
        los hashes SHA-256 ni los metadatos de los archivos originales en origen.
        """
        folder = self.root_dir / "01_00_00_00_00_00_2005"
        folder.mkdir()

        caratula_p2_text = "REGISTRO PUBLICO SECCION 02 LIBRO 05 TOMO 00 VOL 00 ANIO 2005"
        pdf1 = folder / "01_00_00_00_00_00_2005_0001_0010.pdf"
        pdf2 = folder / "01_00_00_00_00_00_2005_0011_0020.pdf"
        _create_test_pdf(pdf1, [None, caratula_p2_text])
        _create_test_pdf(pdf2, [None])

        # Registrar hashes y timestamps antes de la ejecución
        hash_pdf1_before = _file_sha256(pdf1)
        hash_pdf2_before = _file_sha256(pdf2)
        mtime_pdf1_before = pdf1.stat().st_mtime_ns
        mtime_pdf2_before = pdf2.stat().st_mtime_ns

        # Ejecución de Dry Run
        res = FileOperations.execute_folder_dry_run_copy(
            folder_path=folder,
            dry_run_dest_dir=self.dry_out_dir,
            clean_base_code="01_02_05_00_00_00_2005",
            caratula_source_pdf=pdf1,
            info_page_num=2,
            target_pdfs=[pdf2],
            mysql_manager=self.mysql,
        )

        # Verificación en ORIGEN: hashes y mtimes 100% idénticos
        self.assertEqual(_file_sha256(pdf1), hash_pdf1_before, "PDF 1 original no debe sufrir alteraciones")
        self.assertEqual(_file_sha256(pdf2), hash_pdf2_before, "PDF 2 original no debe sufrir alteraciones")
        self.assertEqual(pdf1.stat().st_mtime_ns, mtime_pdf1_before)
        self.assertEqual(pdf2.stat().st_mtime_ns, mtime_pdf2_before)

        # Verificación en DESTINO (Copia)
        dest_folder = Path(res["dest_folder_path"])
        self.assertTrue(dest_folder.exists())
        self.assertEqual(dest_folder.name, "01_02_05_00_00_00_2005")

        dest_pdf1 = dest_folder / "01_02_05_00_00_00_2005_0001_0010.pdf"
        dest_pdf2 = dest_folder / "01_02_05_00_00_00_2005_0011_0020.pdf"
        self.assertTrue(dest_pdf1.exists())
        self.assertTrue(dest_pdf2.exists())

        # El PDF 2 en destino debe tener ahora 2 páginas y la pág 2 contiene caratula_p2_text
        doc2 = fitz.open(str(dest_pdf2))
        self.assertEqual(len(doc2), 2, "El PDF 2 en destino debe tener 2 páginas tras la inserción")
        self.assertIn(caratula_p2_text, doc2[1].get_text())
        doc2.close()

    def test_02_service_dry_run_toggle_and_transparent_redirection(self):
        """
        Valida que al activar dry_run_mode en service o pasar dry_run=True:
        1. execute_all_pending_corrections redirige a execute_dry_run_transformation.
        2. Los originales permanecen intactos.
        3. Los metadatos de diag_result reflejan el veredicto DRY RUN.
        """
        folder = self.root_dir / "01_00_00_00_00_00_2008"
        folder.mkdir()

        caratula_p2_text = "LIBRO 04 SECCION 01 ANIO 2008 REGISTRAL"
        pdf1 = folder / "01_00_00_00_00_00_2008_0001_0010.pdf"
        pdf2 = folder / "01_00_00_00_00_00_2008_0011_0020.pdf"
        _create_test_pdf(pdf1, [None, caratula_p2_text])
        _create_test_pdf(pdf2, [None])

        diag_result = {
            "folder_path": str(folder),
            "folder_name": folder.name,
            "initial_folder_path": str(folder),
            "codigo_base_sugerido": "01_01_04_00_00_00_2008",
            "folder_data": {"delegacion": "01", "seccion": "00", "libro": "00", "anio": "2008"},
            "ocr_data": {"seccion": "01", "libro": "04", "anio": "2008"},
            "needs_page_replacement": True,
            "needs_rename": True,
            "caratula_inspection": {
                "caratula_path": str(pdf1),
                "info_page_num": 2,
                "replaceable_pdfs": [{"pdf_name": pdf2.name, "pdf_path": str(pdf2)}],
            },
        }

        # Activar dry_run_mode en las configuraciones
        self.service.settings["dry_run_mode"] = True
        self.service.settings["dry_run_output_dir"] = str(self.dry_out_dir)

        # Ejecución a través del método principal de corrección
        res = self.service.execute_all_pending_corrections(diag_result)

        self.assertEqual(res["clean_base_code"], "01_01_04_00_00_00_2008")
        self.assertTrue(diag_result["dry_run"])
        self.assertEqual(diag_result["veredicto_global"], "COINCIDE_TOTAL (DRY RUN)")

        # Comprobar que en la raíz la carpeta original no se renombró
        self.assertTrue(folder.exists(), "La carpeta original no debió ser renombrada ni eliminada")
        self.assertTrue(pdf1.exists())
        self.assertTrue(pdf2.exists())

        # Comprobar que en la ruta dry_run existe la copia transformada
        dest_folder = self.dry_out_dir / "01_01_04_00_00_00_2008"
        self.assertTrue(dest_folder.exists())
        self.assertTrue((dest_folder / "01_01_04_00_00_00_2008_0001_0010.pdf").exists())
        dest_pdf2 = dest_folder / "01_01_04_00_00_00_2008_0011_0020.pdf"
        self.assertTrue(dest_pdf2.exists())
        d2 = fitz.open(str(dest_pdf2))
        self.assertEqual(len(d2), 2)
        d2.close()

    def test_03_mysql_batch_traceability_dry_run(self):
        """
        Valida que el lote y los movimientos queden auditados en MySQL con
        tipo_ejecucion='DRY_RUN_TRANSFORMACION_COPIA' y tipo_accion='DRY_RUN_COPIA_TRANSFORMADA'.
        """
        folder = self.root_dir / "01_00_00_00_00_00_2010"
        folder.mkdir()

        caratula_p2_text = "REGISTRO 2010 SECCION 03 LIBRO 02"
        pdf1 = folder / "01_00_00_00_00_00_2010_0001_0010.pdf"
        pdf2 = folder / "01_00_00_00_00_00_2010_0011_0020.pdf"
        _create_test_pdf(pdf1, [None, caratula_p2_text])
        _create_test_pdf(pdf2, [None])

        diag_result = {
            "folder_path": str(folder),
            "folder_name": folder.name,
            "codigo_base_sugerido": "01_03_02_00_00_00_2010",
            "folder_data": {"delegacion": "01"},
            "caratula_inspection": {
                "caratula_path": str(pdf1),
                "info_page_num": 2,
                "replaceable_pdfs": [{"pdf_name": pdf2.name, "pdf_path": str(pdf2)}],
            },
        }

        self.service.execute_dry_run_transformation(
            diag_result=diag_result,
            dry_run_dir=str(self.dry_out_dir),
            custom_clean_base_code="01_03_02_00_00_00_2010",
        )

        # Consultar ejecuciones en MySQL
        conn = self.mysql.get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ejecuciones_lotes WHERE tipo_ejecucion = 'DRY_RUN_TRANSFORMACION_COPIA'")
            lotes = cur.fetchall()
            self.assertEqual(len(lotes), 1)
            self.assertEqual(lotes[0]["estado"], "COMPLETADO")

            cur.execute("SELECT * FROM auditoria_movimientos_carpetas WHERE tipo_accion = 'DRY_RUN_COPIA_CARPETA'")
            mov_carpetas = cur.fetchall()
            self.assertEqual(len(mov_carpetas), 1)
            self.assertEqual(mov_carpetas[0]["nombre_nuevo"], "01_03_02_00_00_00_2010")

            cur.execute("SELECT * FROM auditoria_movimientos_archivos WHERE ejecucion_id = %s", (lotes[0]["id"],))
            mov_archivos = cur.fetchall()
            self.assertEqual(len(mov_archivos), 2)
            tipos = [m["tipo_accion"] for m in mov_archivos]
            self.assertIn("DRY_RUN_COPIA_TRANSFORMADA", tipos)
            self.assertIn("DRY_RUN_COPIA_RENOMBRADA", tipos)
        conn.close()

    def test_04_destination_collision_resolution(self):
        """
        Valida que si en el directorio de Dry Run ya existe una carpeta con el mismo
        nombre objetivo, se resuelva la colisión como <clean_code>_2 sin sobrescribir.
        """
        folder = self.root_dir / "01_00_00_00_00_00_1999"
        folder.mkdir()
        pdf1 = folder / "01_00_00_00_00_00_1999_0001_0010.pdf"
        _create_test_pdf(pdf1, [None, "CARATULA 1999"])

        # Pre-crear la carpeta colisionante en el directorio de dry run
        existing_dest = self.dry_out_dir / "01_01_01_00_00_00_1999"
        existing_dest.mkdir()
        (existing_dest / "archivo_previo.txt").write_text("no sobreescribir")

        res = FileOperations.execute_folder_dry_run_copy(
            folder_path=folder,
            dry_run_dest_dir=self.dry_out_dir,
            clean_base_code="01_01_01_00_00_00_1999",
            caratula_source_pdf=pdf1,
            info_page_num=2,
            target_pdfs=[],
            mysql_manager=self.mysql,
        )

        self.assertEqual(res["dest_folder_name"], "01_01_01_00_00_00_1999_2")
        self.assertTrue(Path(res["dest_folder_path"]).exists())
        self.assertTrue((existing_dest / "archivo_previo.txt").exists(), "El contenido previo debe seguir existiendo")


if __name__ == "__main__":
    unittest.main()
