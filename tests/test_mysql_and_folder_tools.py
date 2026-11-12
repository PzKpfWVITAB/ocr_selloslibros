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

    def test_06_irregular_files_manual_review_and_three_digit_books(self):
        """
        Verifica que:
        1. Libros con hasta 3 dígitos (ej. 880) sean respetados y no forzados a 2 dígitos.
        2. Archivos fuera de formato (ej. REG_-60.pdf) no creen carpetas extrañas al separar,
           permanezcan en la carpeta original y se marquen para revisión manual.
        """
        f_test = self.temp_dir / "2003_02_01_00_00_0001-0476"
        f_test.mkdir()

        # Archivos legítimos del libro 880 (3 dígitos)
        self._create_dummy_pdf(f_test / "01_02_880_00_00_00_2003_0001_00.pdf")
        self._create_dummy_pdf(f_test / "01_02_880_00_00_00_2003_0002_00.pdf")

        # Archivo con nombre irregular fuera de estándar
        self._create_dummy_pdf(f_test / "REG_-60.pdf")

        # Test Renombrador: debe detectar el primer PDF válido (libro 880)
        renamer = FolderRenamerByFirstPdf()
        prev_ren = renamer.preview_folder_rename(f_test)
        self.assertEqual(prev_ren["suggested_name"], "01_02_880_00_00_00_2003")
        self.assertTrue(prev_ren["needs_rename"])

        # Test Separador: no debe separar REG_-60.pdf en una carpeta 'REG_-60'
        separator = FolderFileSeparator()
        insp = separator.inspect_folder_groups(f_test)
        self.assertFalse(insp["is_mixed"])
        self.assertEqual(insp["status"], "HOMOGENEA_CON_IRREGULARES")
        self.assertEqual(insp["total_groups"], 1)
        self.assertEqual(insp["primary_group"], "01_02_880_00_00_00_2003")
        self.assertEqual(insp["irregular_files_count"], 1)
        self.assertEqual(insp["irregular_files"][0]["pdf_name"], "REG_-60.pdf")

        # Intentar ejecutar separación: no debe mover ningún archivo
        split_res = separator.execute_folder_split(f_test)
        self.assertFalse(split_res["applied"])
        self.assertTrue((f_test / "REG_-60.pdf").exists())
        self.assertFalse((self.temp_dir / "REG_-60").exists())

    def test_07_skip_duplicate_pdfs_with_dup_suffix_when_renaming(self):
        """
        Verifica que si el primer archivo alfabéticamente tiene sufijo de duplicado
        (_DUP, _DUP1, etc.), el renombrador lo omite y toma el siguiente PDF en
        formato estándar limpio.
        """
        f_dup = self.temp_dir / "0000_01_00_00_00_00_00"
        f_dup.mkdir()

        # Archivo que viene primero alfabéticamente pero tiene _DUP1
        self._create_dummy_pdf(f_dup / "01_00_00_00_00_00_0000_0001_00_DUP1.pdf")
        # Siguiente archivo en formato estándar sin DUP
        self._create_dummy_pdf(f_dup / "01_00_00_00_00_00_0000_0021_00.pdf")

        renamer = FolderRenamerByFirstPdf()
        prev = renamer.preview_folder_rename(f_dup)

        self.assertEqual(prev["first_pdf"], "01_00_00_00_00_00_0000_0021_00.pdf")
        self.assertEqual(prev["suggested_name"], "01_00_00_00_00_00_0000")
        self.assertTrue(prev["needs_rename"])

        # Verificar que al renombrar los archivos internos:
        # El archivo DUP NO se omite, se renombra manteniendo su sufijo _DUP1 y se marca para revisión manual
        from src.core.file_operations import FileOperations
        from src.database.db_manager import DatabaseManager
        db = DatabaseManager(self.temp_dir / "test_dup.db")
        res_rename = FileOperations.rename_folder_and_internal_pdfs(
            folder_path=f_dup,
            clean_base_code="01_00_00_00_00_00_1990",
            db_manager=db
        )
        self.assertEqual(res_rename["files_renamed_count"], 2)
        new_folder = Path(res_rename["new_folder_path"])
        # Verificar que ambos archivos existen con el nuevo código y el DUP conserva _DUP1
        self.assertTrue((new_folder / "01_00_00_00_00_00_1990_0001_00_DUP1.pdf").exists())
        self.assertTrue((new_folder / "01_00_00_00_00_00_1990_0021_00.pdf").exists())

        # Verificar que en SQLite se registró el movimiento marcado como RENOMBRADO_DUP
        comparison = db.get_traceability_comparison()
        dup_items = [it for it in comparison if "DUP" in it["nombre_actual"]]
        self.assertEqual(len(dup_items), 1)
        self.assertEqual(dup_items[0]["estado_str"], "REVISIÓN MANUAL (DUP)")

    def test_08_registration_number_resembling_year_not_confused_with_year(self):
        """
        Verifica que números de registro/inscripción de 4 dígitos (como 1905, 1891, 1803)
        que caen en el rango 1800-2035 NO se confundan con el año del libro registral.
        El año real siempre precede al número de registro en la estructura de libro.
        """
        # Caso 1: Trámite con registro 1905 y año 2000 (caso reportado por el usuario)
        pdf1 = "01_02_05_00_00_00_2000_1905_00.pdf"
        folder1 = "01_02_05_00_00_00_2000_1905"
        code1, msg1 = FolderRenamerByFirstPdf.get_first_pdf_code_up_to_year(pdf1, folder1)
        self.assertEqual(code1, "01_02_05_00_00_00_2000")

        # Caso 2: Folio 0000 con año 0000 heredando de carpeta 2007 (no debe quedar 0000_2007)
        pdf2 = "01_00_00_00_00_00_0000_0000_00.pdf"
        folder2 = "2007_00_00_00_00_0000-0001"
        code2, msg2 = FolderRenamerByFirstPdf.get_first_pdf_code_up_to_year(pdf2, folder2)
        self.assertEqual(code2, "01_00_00_00_00_00_2007")

        # Caso 3: Folio 0000 con año real 1995 (no debe duplicar el año como 1995_1995)
        pdf3 = "01_04_01_00_00_00_1995_0000_00.pdf"
        folder3 = "1995_04_01_00_00_0001-0100"
        code3, msg3 = FolderRenamerByFirstPdf.get_first_pdf_code_up_to_year(pdf3, folder3)
        self.assertEqual(code3, "01_04_01_00_00_00_1995")

        # Caso 4: En el separador de libros, group_key debe ser exactamente el libro hasta el año
        sep_info = FolderFileSeparator.parse_pdf_group_key("01_02_05_00_00_00_2000_1905_00.pdf")
        self.assertEqual(sep_info["group_key"], "01_02_05_00_00_00_2000")
        self.assertFalse(sep_info["is_irregular"])

    def test_09_dup_and_reg_folders_route_directly_to_apartados(self):
        """
        Verifica que al hacer análisis de OCR:
        - Las carpetas que contienen archivos con DUP o archivos irregulares tipo REG
          se clasifiquen DIRECTAMENTE para el apartado de revisión (needs_apartar = True).
        - Se determinen las subcarpetas descriptivas:
          'ARCHIVOS CON DUP', 'ARCHIVOS IRREGULARES REG' o 'ARCHIVOS CON DUP Y REG'.
        - Se puedan mover a Apartados y restaurar con Rollback.
        """
        from src.core.caratula_inspector import CaratulaInspector
        from src.core.file_operations import FileOperations

        # 1. Verificar subcarpetas descriptivas en FileOperations
        sub_dup = FileOperations.determine_review_subfolder("OK_CON_INFO", "REVISION_ARCHIVOS_DUP")
        self.assertEqual(sub_dup, "ARCHIVOS CON DUP")

        sub_reg = FileOperations.determine_review_subfolder("OK_CON_INFO", "REVISION_ARCHIVOS_REG")
        self.assertEqual(sub_reg, "ARCHIVOS IRREGULARES REG")

        sub_both = FileOperations.determine_review_subfolder("OK_CON_INFO", "REVISION_DUP_Y_REG")
        self.assertEqual(sub_both, "ARCHIVOS CON DUP Y REG")

        # 2. Crear carpeta de prueba con carátula, archivo normal, archivo DUP y archivo REG
        f_test = self.temp_dir / "01_02_05_00_00_00_2000"
        f_test.mkdir(parents=True, exist_ok=True)
        # Carátula con texto oficial suficiente (>= 12 caracteres) para ser detectada con información
        self._create_dummy_pdf(
            f_test / "01_02_05_00_00_00_2000_CARATULA_00.pdf",
            text="SECCION SEGUNDA LIBRO QUINTO TOMO CERO PROPIEDAD 2000"
        )
        self._create_dummy_pdf(
            f_test / "01_02_05_00_00_00_2000_0001_00.pdf",
            text="SECCION SEGUNDA LIBRO QUINTO TOMO CERO PROPIEDAD 2000 TRAMITE 1"
        )
        self._create_dummy_pdf(
            f_test / "01_02_05_00_00_00_2000_0001_00_DUP1.pdf",
            text="SECCION SEGUNDA LIBRO QUINTO TOMO CERO PROPIEDAD 2000 TRAMITE 1 DUP"
        )
        self._create_dummy_pdf(
            f_test / "REG_-60.pdf",
            text="DOCUMENTO SUELTO REGISTRO 60 FUERA DE FORMATO"
        )

        insp = CaratulaInspector.inspect_folder(f_test)
        self.assertIn("01_02_05_00_00_00_2000_0001_00_DUP1.pdf", insp["dup_pdfs"])
        self.assertIn("REG_-60.pdf", insp["irregular_pdfs"])

        # 3. Diagnosticar con FolderOCRService
        from src.service import FolderOCRService
        service = FolderOCRService(db_path=self.temp_dir / "test_apartados.db")
        diag = service.process_folder(str(f_test), save_to_db=True)

        self.assertTrue(diag["needs_apartar"])
        self.assertTrue(diag["has_discrepancy"])
        self.assertEqual(diag["veredicto_global"], "REVISION_DUP_Y_REG")
        self.assertEqual(diag["subcarpeta_revision"], "ARCHIVOS CON DUP Y REG")

        # 4. Mover a apartados y verificar estructura en disco
        apartados_root = self.temp_dir / "DIR_APARTADOS"
        apartados_root.mkdir(parents=True, exist_ok=True)
        res_move = service.execute_move_to_apartados(diag, str(apartados_root))

        moved_folder = Path(res_move["new_folder_path"])
        self.assertTrue(moved_folder.exists())
        self.assertEqual(moved_folder.parent.name, "ARCHIVOS CON DUP Y REG")
        self.assertTrue((moved_folder / "REG_-60.pdf").exists())
        self.assertTrue((moved_folder / "01_02_05_00_00_00_2000_0001_00_DUP1.pdf").exists())

        # 5. Rollback total: la carpeta regresa intacta a su ubicación original
        from src.core.file_operations import FileOperations
        res_rb = FileOperations.rollback_folder_to_initial_state(
            initial_folder_path=f_test,
            db_manager=service.db,
        )
        self.assertEqual(len(res_rb["errors"]), 0)
        self.assertGreater(res_rb["restored_count"], 0)
        self.assertTrue(f_test.exists())
        self.assertTrue((f_test / "REG_-60.pdf").exists())

    def test_10_multipage_caratula_reads_page2_and_avoids_false_sin_datos(self):
        """
        Verifica que en carátulas de múltiples páginas (donde la página 1 es la portada comercial
        y la página 2 es el Acta de Apertura oficial):
        1. CaratulaInspector identifique ambas páginas y priorice la pág. 2 con el acta.
        2. FolderOCRService analice ambas páginas de la carátula sin declararla falsamente como 'SIN DATOS'.
        3. Se extraiga correctamente Sección 3, Libro 1, Año 1994 y sugiera el renombrado a 01_03_01_00_00_00_1994.
        """
        import pymupdf as fitz
        from unittest.mock import patch
        from src.core.caratula_inspector import CaratulaInspector
        from src.service import FolderOCRService

        f_test = self.temp_dir / "01_00_00_00_00_00_1994_5"
        f_test.mkdir(parents=True, exist_ok=True)

        caratula_pdf = f_test / "01_00_00_00_00_00_0000_CARATULA_00.pdf"
        doc = fitz.open()
        # Página 1: Portada comercial
        p1 = doc.new_page(width=612, height=792)
        p1.insert_text((72, 100), "LIBRO FLORETE ACTAS 240 HOJAS", fontsize=18)
        p1.insert_text((72, 700), "TALLERES ESTRELLA S.A. DE C.V. TOLUCA MEXICO", fontsize=12)

        # Página 2: Acta de Apertura oficial
        p2 = doc.new_page(width=612, height=792)
        p2.insert_text(
            (72, 120),
            "EL SUSCRITO SECRETARIO DE GOBIERNO DEL ESTADO DE CHIAPAS AUTORIZA EL PRESENTE "
            "LIBRO NUMERO 1 UNO PARA LLEVAR EL DUPLICADO DE LA SECCION TERCERA DEL REGISTRO "
            "PUBLICO DE LA PROPIEDAD Y DE COMERCIO PARA EL AÑO DE 1994",
            fontsize=12
        )
        doc.save(str(caratula_pdf))
        doc.close()

        # Crear archivos regulares de prueba con página 1 de carátula
        self._create_dummy_pdf(
            f_test / "01_00_00_00_00_00_0000_0001_00.pdf",
            text="INSCRIPCION REGISTRAL REGULAR DE PRUEBA EN LIBRO"
        )

        insp = CaratulaInspector.inspect_folder(f_test)
        self.assertEqual(insp["caratula_total_pages"], 2)
        self.assertEqual(insp["info_pages"], [1, 2])
        self.assertEqual(insp["info_page_num"], 2)

        # Mock de ClaudeVisionEngine para simular la lectura de ambas páginas
        mock_parsed = {
            "seccion": 3,
            "libro": 1,
            "tomo": None,
            "volumen": None,
            "fraccionamiento": None,
            "anio": 1994,
            "es_auxiliar": False,
            "texto_descriptivo": "Acta de apertura Libro 1 Duplicado Sección 3 Año 1994",
            "evidencias": [
                "'LIBRO NUMERO 1 UNO' -> libro=1",
                "'SECCION TERCERA' -> seccion=3",
                "'AÑO DE 1994' -> anio=1994"
            ]
        }
        mock_usage = {
            "input_tokens": 1200,
            "cache_read_input_tokens": 0,
            "model_used": "claude-sonnet-5"
        }

        service = FolderOCRService(db_path=self.temp_dir / "test_multipage.db")
        with patch("src.core.claude_engine.ClaudeVisionEngine.analyze_document_pages", return_value=(mock_parsed, mock_usage)):
            diag = service.process_folder(str(f_test), save_to_db=True)

        self.assertFalse(diag["needs_apartar"])
        self.assertEqual(diag["veredicto_global"], "RELLENO_DE_CEROS_PROPUESTO")
        self.assertEqual(diag["codigo_base_sugerido"], "01_03_01_00_00_00_1994")
        self.assertEqual(int(diag["ocr_consolidado"]["seccion"]), 3)
        self.assertEqual(int(diag["ocr_consolidado"]["libro"]), 1)
        self.assertEqual(int(diag["ocr_consolidado"]["anio"]), 1994)


if __name__ == "__main__":
    unittest.main()
