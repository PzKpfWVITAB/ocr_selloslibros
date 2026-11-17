import shutil
import tempfile
import unittest
from pathlib import Path

from src.core.file_operations import FileOperations
from src.database.mysql_manager import MySQLManager
from src.service import FolderOCRService


class TestMoveToValidadosEdgeCases(unittest.TestCase):
    TEST_DB = "test_validados_edge_db"

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_val_edge_"))
        self.root_dir = self.tmp_dir / "BUENOS"
        self.root_dir.mkdir(parents=True, exist_ok=True)

        self.test_settings = {
            "mysql_enabled": True,
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_database": self.TEST_DB,
            "validados_dir": str(self.root_dir),
        }
        self.mysql = MySQLManager(self.test_settings)
        ok, msg = self.mysql.init_database_and_tables()
        self.assertTrue(ok, f"MySQL debe estar activo: {msg}")
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

    def test_01_move_folder_when_already_in_destination_no_samefile_error(self):
        """
        Garantiza que si folder.parent == validados_dir (ej. el usuario está trabajando
        directamente en la carpeta BUENOS), NO se dispare SameFileError y se convalide con éxito.
        """
        folder = self.root_dir / "01_01_01_00_00_00_2000"
        folder.mkdir()
        pdf1 = folder / "01_01_01_00_00_00_2000_0001_0010.pdf"
        pdf1.write_bytes(b"%PDF-1.4 dummy content")

        res = FileOperations.move_folder_to_validados(
            folder_path=folder,
            validados_dir=self.root_dir,
            mysql_manager=self.mysql,
        )

        self.assertTrue(res.get("already_in_destination"))
        self.assertEqual(res["new_folder_path"], str(folder))
        self.assertTrue(folder.exists())
        self.assertTrue(pdf1.exists())

    def test_02_service_execute_move_to_validados_handles_same_destination(self):
        """
        Valida que execute_move_to_validados maneje carpetas que ya residen en validados_dir
        actualizando los metadatos a 'Convalidada en Correctos' y registrando en MySQL sin error.
        """
        folder = self.root_dir / "01_02_03_00_00_00_2005"
        folder.mkdir()
        pdf1 = folder / "01_02_03_00_00_00_2005_0001_0010.pdf"
        pdf1.write_bytes(b"%PDF-1.4 dummy content")

        diag_result = {
            "folder_path": str(folder),
            "folder_name": folder.name,
            "veredicto_global": "COINCIDE_TOTAL",
            "needs_apartar": False,
        }

        res = self.service.execute_move_to_validados(
            diag_result=diag_result,
            validados_dir=str(self.root_dir),
        )

        self.assertEqual(diag_result["veredicto_global"], "MOVIDO_A_CORRECTOS")
        self.assertIn("Convalidada en Correctos", diag_result["last_action"])
        self.assertEqual(diag_result["moved_destination_type"], "CORRECTOS")

    def test_03_paso3_batch_move_handles_same_destination_with_zero_errors(self):
        """
        Valida que el Paso 3 (Traslado por Lote) con múltiples carpetas que ya residen
        en validados_dir finalice con 0 errores y todas procesadas.
        """
        folder1 = self.root_dir / "01_01_01_00_00_00_1990"
        folder2 = self.root_dir / "01_01_02_00_00_00_1991"
        folder1.mkdir()
        folder2.mkdir()
        (folder1 / "doc1.pdf").write_bytes(b"%PDF-1.4")
        (folder2 / "doc2.pdf").write_bytes(b"%PDF-1.4")

        candidates = [
            {"folder_path": str(folder1), "folder_data": {"folder_name": folder1.name}, "veredicto_global": "COINCIDE_TOTAL", "needs_apartar": False},
            {"folder_path": str(folder2), "folder_data": {"folder_name": folder2.name}, "veredicto_global": "COINCIDE_TOTAL", "needs_apartar": False},
        ]

        batch_res = self.service.ejecutar_paso3_trasladar_lote(
            results_list=candidates,
            target_dir=str(self.root_dir),
            mode="CORRECTOS",
        )

        self.assertEqual(len(batch_res["errores"]), 0, f"No debe haber errores: {batch_res['errores']}")
        self.assertEqual(batch_res["movidas"], 2)


if __name__ == "__main__":
    unittest.main()
