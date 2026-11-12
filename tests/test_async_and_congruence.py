import unittest
import tempfile
import shutil
from pathlib import Path
import fitz

from src.service import FolderOCRService
from src.database.db_manager import DatabaseManager
from src.core.file_operations import FileOperations
from src.parsers.folder_parser import FolderParser


class TestAsyncAndCongruence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)
        self.db_path = self.root / "test_audit.db"
        self.db = DatabaseManager(self.db_path)
        self.service = FolderOCRService(db_path=self.db_path)
        self.service.db = self.db

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_pdf(self, path: Path, num_pages: int = 1, text: str = "Test"):
        doc = fitz.open()
        for i in range(num_pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"{text} - Page {i+1}")
        doc.save(str(path))
        doc.close()

    def test_embedded_caratula_creation_independent_of_rename(self):
        """
        Verifica que una carpeta cuyo nombre ya coincide con el código base (needs_rename=False)
        extraiga físicamente la carátula embebida y conmute a COINCIDE_TOTAL al ejecutar
        execute_all_pending_corrections.
        """
        folder_name = "01_01_01_02_71_00_1990"
        folder_p = self.root / folder_name
        folder_p.mkdir()

        # Crear PDFs con la carátula embebida en página 1
        pdf1 = folder_p / f"{folder_name}_1321_00.pdf"
        pdf2 = folder_p / f"{folder_name}_1322_00.pdf"
        self._create_dummy_pdf(pdf1, num_pages=2, text="Acta de Apertura Libro 1 Tomo 2 1990")
        self._create_dummy_pdf(pdf2, num_pages=2, text="Acta de Apertura Libro 1 Tomo 2 1990")

        diag = {
            "folder_path": str(folder_p),
            "folder_data": FolderParser.parse(folder_name),
            "codigo_base_sugerido": folder_name,
            "veredicto_global": "RELLENO_DE_CEROS_PROPUESTO",
            "needs_rename": False,
            "needs_page_replacement": False,
            "needs_apartar": False,
            "caratula_inspection": {
                "status": "CARATULA_EMBEBIDA_VALIDA",
                "caratula_filename": f"[EMBEBIDA] {pdf1.name}",
                "caratula_path": str(pdf1),
                "replaceable_pdfs": [],
                "dup_pdfs": [],
                "irregular_pdfs": [],
            },
            "embedded_caratula_to_create": {
                "source_pdf": str(pdf1),
                "page_num": 1,
                "target_name": f"{folder_name}_CARATULA_00.pdf",
            },
            "applied_actions": [],
        }

        # Ejecutar correcciones pendientes
        res = self.service.execute_all_pending_corrections(diag)
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["modified_count"], 1)

        # Comprobar que el archivo físico se creó en disco
        target_caratula = folder_p / f"{folder_name}_CARATULA_00.pdf"
        self.assertTrue(target_caratula.exists())

        # Comprobar que en memoria el veredicto conmutó a COINCIDE_TOTAL y se retiraron las banderas
        self.assertEqual(diag["veredicto_global"], "COINCIDE_TOTAL")
        self.assertFalse(diag["needs_rename"])
        self.assertFalse(diag["needs_page_replacement"])
        self.assertEqual(diag["caratula_inspection"]["status"], "OK_CON_INFO")
        self.assertEqual(diag["caratula_inspection"]["caratula_filename"], target_caratula.name)

    def test_audit_folder_congruence_and_resync(self):
        """
        Verifica que audit_folder_congruence detecte cuando una carátula física ya existe en disco
        pero en memoria figura como [EMBEBIDA] o needs_rename=True, y que resync_folder_with_physical_disk
        la alinee a la perfección.
        """
        folder_name = "01_01_01_02_00_00_1990"
        folder_p = self.root / folder_name
        folder_p.mkdir()

        # Crear la carátula física ya existente en disco
        caratula_p = folder_p / f"{folder_name}_CARATULA_00.pdf"
        self._create_dummy_pdf(caratula_p, num_pages=1, text="Caratula Libro 1")

        pdf1 = folder_p / f"{folder_name}_2384_00.pdf"
        self._create_dummy_pdf(pdf1, num_pages=2, text="Contenido")

        # Estado en memoria desactualizado (como quedó antes del refresco)
        diag = {
            "folder_path": str(folder_p),
            "folder_data": FolderParser.parse(folder_name),
            "codigo_base_sugerido": folder_name,
            "veredicto_global": "RENOMBRAR_CARPETA",
            "needs_rename": True,
            "needs_page_replacement": False,
            "needs_apartar": False,
            "caratula_inspection": {
                "status": "CARATULA_EMBEBIDA_VALIDA",
                "caratula_filename": f"[EMBEBIDA] {pdf1.name}",
                "caratula_path": str(pdf1),
                "replaceable_pdfs": [],
                "dup_pdfs": [],
                "irregular_pdfs": [],
            },
            "embedded_caratula_to_create": {
                "source_pdf": str(pdf1),
                "page_num": 1,
                "target_name": f"{folder_name}_CARATULA_00.pdf",
            },
            "applied_actions": [],
        }

        # 1. Auditar congruencia -> Debe detectar discrepancias
        audit_res = self.service.audit_folder_congruence(diag)
        self.assertFalse(audit_res["is_congruent"])
        self.assertGreater(len(audit_res["discrepancies"]), 0)
        self.assertEqual(audit_res["physical_caratula"], caratula_p.name)

        # 2. Resincronizar con disco físico
        sync_res = self.service.resync_folder_with_physical_disk(diag)
        self.assertTrue(sync_res["success"])

        # 3. Comprobar que diag en memoria quedó 100% alineado
        self.assertEqual(diag["veredicto_global"], "COINCIDE_TOTAL")
        self.assertFalse(diag["needs_rename"])
        self.assertEqual(diag["caratula_inspection"]["caratula_filename"], caratula_p.name)
        self.assertEqual(diag["caratula_inspection"]["status"], "OK_CON_INFO")

        # 4. Re-auditar -> Debe ser 100% congruente ahora
        re_audit = self.service.audit_folder_congruence(diag)
        self.assertTrue(re_audit["is_congruent"])
        self.assertEqual(len(re_audit["discrepancies"]), 0)


if __name__ == "__main__":
    unittest.main()
