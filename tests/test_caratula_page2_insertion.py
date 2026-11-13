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
    """
    Crea un PDF de prueba donde pages_content es una lista de strings o None.
    None o "" genera una página totalmente en blanco.
    Un string genera una página con dicho texto.
    """
    doc = fitz.open()
    for text in pages_content:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), str(text))
    doc.save(str(path))
    doc.close()


class TestCaratulaPage2Insertion(unittest.TestCase):
    TEST_DB = "test_page2_insertion_db"

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_p2_ins_"))
        self.root_dir = self.tmp_dir / "raiz"
        self.root_dir.mkdir(parents=True, exist_ok=True)

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

    def test_01_pattern_detection_shared_blank_p1_with_p2_info(self):
        """
        Valida que CaratulaInspector identifique correctamente:
        - Página 1 en blanco compartida
        - Página 2 con datos registrales en el primer PDF
        - Lista de PDFs subsecuentes a los que les falta la página 2
        """
        folder = self.root_dir / "01_02_05_00_00_00_2005"
        folder.mkdir()

        p1_caratula_text = "REGISTRO PUBLICO DE LA PROPIEDAD SECCION 02 LIBRO 05 TOMO 00 VOL 00 ANIO 2005"
        # PDF 1: 2 páginas (Pág 1 blanco, Pág 2 texto carátula)
        _create_test_pdf(folder / "01_02_05_00_00_00_2005_0001_0010.pdf", [None, p1_caratula_text])
        # PDF 2: 1 página (Pág 1 blanco)
        _create_test_pdf(folder / "01_02_05_00_00_00_2005_0011_0020.pdf", [None])
        # PDF 3: 2 páginas (Pág 1 blanco, Pág 2 asiento registral diferente)
        _create_test_pdf(folder / "01_02_05_00_00_00_2005_0021_0030.pdf", [None, "INSCRIPCION HIPOTECA 500"])

        insp = CaratulaInspector.inspect_folder(folder)

        self.assertEqual(insp["status"], "CARATULA_PAGINA_2_COMPARTIDA")
        self.assertEqual(insp["info_page_num"], 2)
        self.assertIn("01_02_05_00_00_00_2005_0001_0010.pdf", insp["caratula_path"])
        # Debe marcar para inserción a PDF 2 y PDF 3
        replaceable_names = [p["pdf_name"] for p in insp["replaceable_pdfs"]]
        self.assertIn("01_02_05_00_00_00_2005_0011_0020.pdf", replaceable_names)
        self.assertIn("01_02_05_00_00_00_2005_0021_0030.pdf", replaceable_names)
        self.assertEqual(len(replaceable_names), 2)

    def test_02_pre_ai_structure_routes_to_requiere_ia(self):
        """
        Valida que una carpeta homogénea con el patrón Pág. 1 blanco / Pág. 2 info
        NUNCA sea clasificada como 'CORRECTOS' en el Chequeo Pre-IA (Paso 2B),
        sino como 'REQUIERE_IA'.
        """
        folder = self.root_dir / "01_02_03_00_00_00_1990"
        folder.mkdir()

        caratula_text = "LIBRO DE REGISTRO SECCION SEGUNDA LIBRO 03 TOMO 00 VOL 00 ANIO 1990"
        _create_test_pdf(folder / "01_02_03_00_00_00_1990_0001_0010.pdf", [None, caratula_text])
        _create_test_pdf(folder / "01_02_03_00_00_00_1990_0011_0020.pdf", [None])

        plan = self.service.planificar_paso2_estructura(str(self.root_dir))
        self.assertEqual(plan["total"], 1)
        item = plan["items"][0]

        self.assertEqual(item["clasificacion"], "REQUIERE_IA")
        self.assertIn("Inspeccionar Pág. 2 con IA", item["accion"])
        self.assertNotEqual(item["clasificacion"], "CORRECTOS")

    def test_03_physical_binary_insertion_under_blank_page(self):
        """
        Valida la inserción física de la página 2 debajo de la página 1 en blanco:
        - PDF 2 tenía 1 página (blanco). Tras inserción, debe tener 2 páginas (Pág 1 blanco, Pág 2 carátula).
        - PDF 3 tenía 2 páginas (blanco + asiento). Tras inserción, debe tener 3 páginas:
          Pág 1 blanco, Pág 2 carátula, Pág 3 asiento original.
        - Se guardan respaldos binarios en backups_rollback/.
        """
        folder = self.root_dir / "01_01_01_00_00_00_1985"
        folder.mkdir()

        caratula_text = "CARATULA REGISTRAL LIBRO 01 TOMO 00 ANIO 1985"
        asiento_text = "ASIENTO DEL REGISTRO NUMERO 45678"

        f1 = folder / "01_01_01_00_00_00_1985_0001_0010.pdf"
        f2 = folder / "01_01_01_00_00_00_1985_0011_0020.pdf"
        f3 = folder / "01_01_01_00_00_00_1985_0021_0030.pdf"

        _create_test_pdf(f1, [None, caratula_text])
        _create_test_pdf(f2, [None])
        _create_test_pdf(f3, [None, asiento_text])

        ejecucion_id = self.mysql.crear_ejecucion_lote("TEST_INSERCION", {"folder": folder.name})

        res = FileOperations.insert_page2_caratula_into_folder_pdfs(
            folder_path=folder,
            source_pdf=f1,
            info_page_num=2,
            target_pdfs=[f2, f3],
            mysql_manager=self.mysql,
            ejecucion_id=ejecucion_id,
        )

        self.assertEqual(res["inserted_count"], 2)

        # Verificar f2
        doc2 = fitz.open(str(f2))
        self.assertEqual(len(doc2), 2)
        # Pág 1 debe ser en blanco
        has_t0, _, _ = CaratulaInspector.analyze_page_has_text(doc2[0])
        self.assertFalse(has_t0)
        # Pág 2 debe ser la carátula insertada
        self.assertIn("CARATULA REGISTRAL", doc2[1].get_text())
        doc2.close()

        # Verificar f3
        doc3 = fitz.open(str(f3))
        self.assertEqual(len(doc3), 3)
        # Pág 1 en blanco
        has_t0, _, _ = CaratulaInspector.analyze_page_has_text(doc3[0])
        self.assertFalse(has_t0)
        # Pág 2 carátula insertada
        self.assertIn("CARATULA REGISTRAL", doc3[1].get_text())
        # Pág 3 asiento original
        self.assertIn("ASIENTO DEL REGISTRO", doc3[2].get_text())
        doc3.close()

        self.mysql.finalizar_ejecucion_lote(ejecucion_id, 1, 2)

    def test_04_compensatory_rollback_on_insertion_failure(self):
        """
        Valida que si falla la inserción en el segundo PDF (por ejemplo, bloqueo o error de disco),
        se ejecuta una reversión compensatoria física que restaura el primer PDF a su estado original intacto.
        """
        folder = self.root_dir / "01_01_02_00_00_00_1990"
        folder.mkdir()

        caratula_text = "CARATULA REGISTRAL LIBRO 02 ANIO 1990"
        f1 = folder / "01_01_02_00_00_00_1990_0001.pdf"
        f2 = folder / "01_01_02_00_00_00_1990_0002.pdf"
        f3 = folder / "01_01_02_00_00_00_1990_0003.pdf"

        _create_test_pdf(f1, [None, caratula_text])
        _create_test_pdf(f2, [None])
        _create_test_pdf(f3, [None])

        orig_f2_hash = FileOperations.compute_sha256(f2)

        # Simular fallo al intentar reemplazar f3
        real_safe_replace = FileOperations.safe_replace if hasattr(FileOperations, 'safe_replace') else None

        call_count = 0
        def mock_replace(src, dst, **kwargs):
            nonlocal call_count
            call_count += 1
            if "0003.pdf" in str(dst):
                raise PermissionError("Bloqueo simulado en f3")
            shutil.move(str(src), str(dst))

        with patch("src.core.file_operations.safe_replace", side_effect=mock_replace):
            with self.assertRaises(PermissionError):
                FileOperations.insert_page2_caratula_into_folder_pdfs(
                    folder_path=folder,
                    source_pdf=f1,
                    info_page_num=2,
                    target_pdfs=[f2, f3],
                )

        # f2 debe haber sido restaurado compensatoriamente a su archivo original
        doc2 = fitz.open(str(f2))
        self.assertEqual(len(doc2), 1, "f2 debe tener 1 página tras el rollback compensatorio")
        doc2.close()
        self.assertEqual(FileOperations.compute_sha256(f2), orig_f2_hash)

    def test_05_end_to_end_pipeline_insertion_rename_and_coincide_total(self):
        """
        Valida el flujo integral:
        1. Carpeta con nombre incorrecto y patrón Pág. 2 compartida.
        2. Análisis de IA (mockeado) extrae datos correctos de la Pág. 2.
        3. Se marca con needs_page_replacement=True y needs_rename=True.
        4. Se ejecutan las correcciones pendientes (execute_all_pending_corrections).
        5. La carátula se inserta físicamente, la carpeta y PDFs se renombran,
           y el veredicto pasa a 'COINCIDE_TOTAL'.
        """
        folder = self.root_dir / "01_00_00_00_00_00_0000"
        folder.mkdir()

        caratula_text = "REGISTRO PUBLICO SECCION 03 LIBRO 07 TOMO 00 VOL 00 FRACC 00 ANIO 1995"
        f1 = folder / "01_00_00_00_00_00_0000_0001.pdf"
        f2 = folder / "01_00_00_00_00_00_0000_0002.pdf"

        _create_test_pdf(f1, [None, caratula_text])
        _create_test_pdf(f2, [None])

        # Mockear Claude Vision para simular lectura exitosa de Pág. 2
        mock_doc = {
            "seccion": "03",
            "libro": "07",
            "tomo": "00",
            "volumen": "00",
            "fraccionamiento": "00",
            "anio": "1995",
            "es_auxiliar": False,
            "evidencias": ["Leído de Página 2: Sección 03, Libro 07, Año 1995"],
        }

        with patch("src.core.claude_engine.ClaudeVisionEngine.analyze_document_pages", return_value=(mock_doc, {"input_tokens": 100})):
            diag = self.service.process_folder(str(folder), save_to_db=False)

        self.assertEqual(diag["codigo_base_sugerido"], "01_03_07_00_00_00_1995")
        self.assertTrue(diag["needs_page_replacement"])
        self.assertTrue(diag["needs_rename"])
        self.assertFalse(diag["needs_apartar"])

        # Ejecutar correcciones pendientes
        corr_res = self.service.execute_all_pending_corrections(diag)
        self.assertTrue(corr_res["success"])

        # Validar nuevo nombre de carpeta y archivos
        new_folder = Path(diag["folder_path"])
        self.assertEqual(new_folder.name, "01_03_07_00_00_00_1995")
        self.assertEqual(diag["veredicto_global"], "COINCIDE_TOTAL")
        self.assertFalse(diag["needs_apartar"])
        self.assertFalse(diag["has_discrepancy"])

        # Validar que los PDFs internos se hayan renombrado e insertado
        new_f2 = new_folder / "01_03_07_00_00_00_1995_0002.pdf"
        self.assertTrue(new_f2.exists())
        doc2 = fitz.open(str(new_f2))
        self.assertEqual(len(doc2), 2)
        self.assertIn("REGISTRO PUBLICO", doc2[1].get_text())
        doc2.close()

    def test_06_mysql_rollback_of_execution_restores_original_pdfs(self):
        """
        Valida que al llamar revertir_ultima_ejecucion en MySQL:
        - Los PDFs regresen a su número de páginas original (1 página)
        - Se restauren sus hashes SHA-256 iniciales
        """
        folder = self.root_dir / "01_02_04_00_00_00_1988"
        folder.mkdir()

        caratula_text = "CARATULA REGISTRAL LIBRO 04 ANIO 1988"
        f1 = folder / "01_02_04_00_00_00_1988_0001.pdf"
        f2 = folder / "01_02_04_00_00_00_1988_0002.pdf"

        _create_test_pdf(f1, [None, caratula_text])
        _create_test_pdf(f2, [None])

        orig_f2_hash = FileOperations.compute_sha256(f2)

        ejecucion_id = self.mysql.crear_ejecucion_lote("TEST_INSERCION_Y_ROLLBACK", {"folder": folder.name})
        FileOperations.insert_page2_caratula_into_folder_pdfs(
            folder_path=folder,
            source_pdf=f1,
            info_page_num=2,
            target_pdfs=[f2],
            mysql_manager=self.mysql,
            ejecucion_id=ejecucion_id,
        )
        self.mysql.finalizar_ejecucion_lote(ejecucion_id, 1, 1)

        # Confirmar que f2 tiene 2 páginas
        doc2 = fitz.open(str(f2))
        self.assertEqual(len(doc2), 2)
        doc2.close()

        # Revertir la ejecución en MySQL
        rb_res = self.mysql.revertir_ultima_ejecucion()
        self.assertTrue(rb_res["success"], f"Error en rollback: {rb_res.get('errors')}")

        # Confirmar que f2 regresó a 1 página y su hash original
        doc2_rev = fitz.open(str(f2))
        self.assertEqual(len(doc2_rev), 1)
        doc2_rev.close()
        self.assertEqual(FileOperations.compute_sha256(f2), orig_f2_hash)


if __name__ == "__main__":
    unittest.main()
