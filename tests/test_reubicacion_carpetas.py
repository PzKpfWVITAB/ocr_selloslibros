import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.service import FolderOCRService
from src.database.mysql_manager import MySQLManager
from src.core.settings_manager import SettingsManager


class TestReubicacionCarpetas(unittest.TestCase):
    """
    Suite de pruebas para la Herramienta General de Reubicación de Carpetas
    con Trazabilidad Total en MySQL, Rollback LIFO y Prevención de Conflictos.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)

        self.mysql = MySQLManager({
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_database": "trazabilidad_selloslibros",
        })
        self.mysql.reset_database()
        self.service = FolderOCRService()
        self.service.mysql = self.mysql

    def tearDown(self):
        if Path(self.test_dir).exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_dummy_pdf(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj\n<<\n>>\nendobj\ntrailer\n<<\n>>\n%%EOF\n")

    def test_reubicacion_exitosa_con_trazabilidad_y_rollback(self):
        """Valida que una carpeta se reubique físicamente, se registre en MySQL y sea 100% revertible."""
        origen_dir = self.test_path / "ORIGEN"
        destino_dir = self.test_path / "DESTINO"
        origen_dir.mkdir()
        destino_dir.mkdir()

        folder_name = "01_01_02_00_00_00_2001"
        folder = origen_dir / folder_name
        folder.mkdir()
        pdf1 = folder / f"{folder_name}_0001_00.pdf"
        pdf2 = folder / f"{folder_name}_CARATULA_00.pdf"
        self._create_dummy_pdf(pdf1)
        self._create_dummy_pdf(pdf2)

        # 1. Indexar en MySQL
        self.mysql.indexar_estado_inicial([{
            "nombre_original": folder_name,
            "ruta_original": str(folder.resolve()),
            "pdf_names": [pdf1.name, pdf2.name]
        }])

        # Simular resultado previo en memoria
        self.service.results_data = [{
            "folder_path": str(folder.resolve()),
            "folder_data": {"folder_name": folder_name},
            "caratula_inspection": {"caratula_path": str(pdf2.resolve()), "caratula_filename": pdf2.name},
            "veredicto_global": "COINCIDE_TOTAL",
        }]

        # 2. Reubicar al directorio destino
        res = self.service.reubicar_carpetas_lote(
            folder_paths=[folder],
            dest_dir=destino_dir,
        )

        self.assertEqual(res["movidas"], 1)
        self.assertEqual(res["archivos_movidos"], 2)
        self.assertEqual(len(res["errores"]), 0)
        self.assertEqual(len(res["omitidas_conflicto"]), 0)

        # Comprobar presencia física en destino y ausencia en origen
        target_folder = destino_dir / folder_name
        self.assertTrue(target_folder.exists(), "La carpeta debe existir en el directorio destino")
        self.assertTrue((target_folder / pdf1.name).exists(), "El PDF 1 debe estar en el destino")
        self.assertTrue((target_folder / pdf2.name).exists(), "El PDF 2 debe estar en el destino")
        self.assertFalse(folder.exists(), "La carpeta ya no debe existir en origen")

        # Comprobar actualización en memoria
        self.assertEqual(self.service.results_data[0]["folder_path"], str(target_folder.resolve()))
        self.assertEqual(
            self.service.results_data[0]["caratula_inspection"]["caratula_path"],
            str((target_folder / pdf2.name).resolve())
        )

        # Comprobar registro en MySQL
        ejec_id = res["ejecucion_id"]
        historial = self.mysql.obtener_historial_ejecuciones()
        self.assertTrue(any(h["id"] == ejec_id and h["tipo_ejecucion"] == "REUBICACION_CARPETAS" for h in historial))

        # 3. Probar Rollback de la Reubicación
        rb = self.service.ejecutar_rollback_ultima_ejecucion()
        self.assertTrue(rb["success"], f"El rollback debe ser exitoso: {rb}")

        # Comprobar que regresó físicamente al origen
        self.assertTrue(folder.exists(), "La carpeta debe haber regresado a su ruta original tras el rollback")
        self.assertTrue(pdf1.exists(), "El PDF 1 debe haber regresado al origen")
        self.assertTrue(pdf2.exists(), "El PDF 2 debe haber regresado al origen")
        self.assertFalse(target_folder.exists(), "La carpeta ya no debe existir en destino tras el rollback")

    def test_omision_por_conflicto_existente_en_destino(self):
        """Valida que si ya existe una carpeta con el mismo nombre en destino, se omita sin alterar nada."""
        origen_dir = self.test_path / "ORIGEN"
        destino_dir = self.test_path / "DESTINO"
        origen_dir.mkdir()
        destino_dir.mkdir()

        folder_name = "01_05_01_00_00_00_1998"
        folder_origen = origen_dir / folder_name
        folder_origen.mkdir()
        pdf_orig = folder_origen / f"{folder_name}_0001_00.pdf"
        self._create_dummy_pdf(pdf_orig)

        # Crear carpeta existente previa en destino con un archivo distinto
        folder_destino = destino_dir / folder_name
        folder_destino.mkdir()
        pdf_dest = folder_destino / f"{folder_name}_EXISTENTE_PREVIO.pdf"
        self._create_dummy_pdf(pdf_dest)

        res = self.service.reubicar_carpetas_lote(
            folder_paths=[folder_origen],
            dest_dir=destino_dir,
        )

        self.assertEqual(res["movidas"], 0)
        self.assertEqual(len(res["omitidas_conflicto"]), 1)
        self.assertIn("Ya existe", res["omitidas_conflicto"][0])

        # Integridad: el origen permanece intacto y el destino no se sobreescribió
        self.assertTrue(folder_origen.exists())
        self.assertTrue(pdf_orig.exists())
        self.assertTrue(folder_destino.exists())
        self.assertTrue(pdf_dest.exists())


if __name__ == "__main__":
    unittest.main()
