import unittest
import tempfile
import shutil
from pathlib import Path
import pymupdf as fitz

from src.database.mysql_manager import MySQLManager
from src.core.folder_renamer_by_pdf import FolderRenamerByFirstPdf
from src.core.folder_file_separator import FolderFileSeparator


class TestMySQLAndFolderTools(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_mysql_tools_"))

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_pdf(self, path: Path, text: str = "TEST PAGE"):
        doc = fitz.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((50, 50), text)
        doc.save(str(path))
        doc.close()

    def test_01_extract_year_from_folder_name(self):
        # Carpeta estilo remoto: 1981_CYA_02_00_00_00_0176-0237
        y1 = FolderRenamerByFirstPdf.extract_year_from_folder_name("1981_CYA_02_00_00_00_0176-0237")
        self.assertEqual(y1, "1981")

        # Carpeta normal: 01_01_01_03_00_00_1992
        y2 = FolderRenamerByFirstPdf.extract_year_from_folder_name("01_01_01_03_00_00_1992")
        self.assertEqual(y2, "1992")

        # Carpeta con año intermedio
        y3 = FolderRenamerByFirstPdf.extract_year_from_folder_name("LIBROS_1975_ESPECIAL")
        self.assertEqual(y3, "1975")

    def test_02_get_first_pdf_code_up_to_year_normal_and_0000(self):
        # Caso A: PDF normal con año
        pdf1 = "01_05_01_CREDITO_AGRICOLA_00_00_1981_0176_00.pdf"
        folder1 = "1981_CYA_02_00_00_00_0176-0237"
        code1, msg1 = FolderRenamerByFirstPdf.get_first_pdf_code_up_to_year(pdf1, folder1)
        self.assertEqual(code1, "01_05_01_CREDITO_AGRICOLA_00_00_1981")

        # Caso B: PDF con año en 0000 (Debe heredar el año de la carpeta original)
        pdf2 = "01_03_02_00_00_0000_0176_00.pdf"
        folder2 = "1985_LIBROS_CIVIL"
        code2, msg2 = FolderRenamerByFirstPdf.get_first_pdf_code_up_to_year(pdf2, folder2)
        self.assertEqual(code2, "01_03_02_00_00_1985")
        self.assertIn("sustituido por año 1985", msg2)

    def test_03_rename_folder_by_first_pdf_execution_and_collision(self):
        # Crear estructura de carpetas
        f_orig = self.temp_dir / "1981_CYA_TEMP"
        f_orig.mkdir()
        self._create_dummy_pdf(f_orig / "01_05_01_CREDITO_AGRICOLA_00_00_1981_0176_00.pdf")
        self._create_dummy_pdf(f_orig / "01_05_01_CREDITO_AGRICOLA_00_00_1981_0177_00.pdf")

        renamer = FolderRenamerByFirstPdf(mysql_manager=None)
        res = renamer.execute_folder_rename(f_orig)

        self.assertTrue(res["applied"])
        self.assertEqual(res["suggested_name"], "01_05_01_CREDITO_AGRICOLA_00_00_1981")
        self.assertTrue((self.temp_dir / "01_05_01_CREDITO_AGRICOLA_00_00_1981").exists())

        # Probar colisión con segunda carpeta idéntica
        f_orig2 = self.temp_dir / "OTRA_CARPETA_DUPLICADA"
        f_orig2.mkdir()
        self._create_dummy_pdf(f_orig2 / "01_05_01_CREDITO_AGRICOLA_00_00_1981_0178_00.pdf")

        res2 = renamer.execute_folder_rename(f_orig2)
        self.assertTrue(res2["applied"])
        self.assertEqual(res2["suggested_name"], "01_05_01_CREDITO_AGRICOLA_00_00_1981_2")
        self.assertTrue((self.temp_dir / "01_05_01_CREDITO_AGRICOLA_00_00_1981_2").exists())

    def test_04_folder_file_separator_mixed_books_and_caratula(self):
        f_mixed = self.temp_dir / "CARPETA_MEZCLADA"
        f_mixed.mkdir()

        # Grupo 1 (3 archivos: libro 1)
        self._create_dummy_pdf(f_mixed / "01_01_01_01_00_00_1990_CARATULA_00.pdf")
        self._create_dummy_pdf(f_mixed / "01_01_01_01_00_00_1990_0001_00.pdf")
        self._create_dummy_pdf(f_mixed / "01_01_01_01_00_00_1990_0002_00.pdf")

        # Grupo 2 (2 archivos que no van aquí: libro 2)
        self._create_dummy_pdf(f_mixed / "01_01_02_01_00_00_1990_CARATULA_00.pdf")
        self._create_dummy_pdf(f_mixed / "01_01_02_01_00_00_1990_0001_00.pdf")

        separator = FolderFileSeparator(mysql_manager=None)
        insp = separator.inspect_folder_groups(f_mixed)

        self.assertTrue(insp["is_mixed"])
        self.assertEqual(insp["total_groups"], 2)
        self.assertEqual(insp["primary_group"], "01_01_01_01_00_00_1990")

        # Ejecutar separación
        res = separator.execute_folder_split(f_mixed)
        self.assertTrue(res["applied"])

        # Verificar que la nueva carpeta fue creada con sus 2 archivos
        target_group2 = self.temp_dir / "01_01_02_01_00_00_1990"
        self.assertTrue(target_group2.exists())
        self.assertTrue((target_group2 / "01_01_02_01_00_00_1990_CARATULA_00.pdf").exists())
        self.assertTrue((target_group2 / "01_01_02_01_00_00_1990_0001_00.pdf").exists())

        # Verificar que el grupo 1 se quedó en la carpeta original
        self.assertTrue((f_mixed / "01_01_01_01_00_00_1990_CARATULA_00.pdf").exists())
        self.assertFalse((f_mixed / "01_01_02_01_00_00_1990_0001_00.pdf").exists())

    def test_05_sha256_hash_computation(self):
        dummy_file = self.temp_dir / "test_hash.pdf"
        self._create_dummy_pdf(dummy_file, "CONTENIDO PARA HASH")
        h = MySQLManager.compute_sha256(dummy_file)
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)


if __name__ == "__main__":
    unittest.main()
