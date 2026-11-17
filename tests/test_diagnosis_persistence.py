import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.core.cache_manager import CacheManager
from src.core.file_operations import FileOperations
from src.service import FolderOCRService
from src.database.mysql_manager import MySQLManager


class TestDiagnosisPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_diag_persist_"))
        self.root_dir = self.tmp_dir / "raiz"
        self.root_dir.mkdir(parents=True, exist_ok=True)

        self.cache_dir = self.tmp_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.cache = CacheManager()
        self.cache.cache_dir = self.cache_dir
        self.cache.cache_file = self.cache_dir / "claude_vision_cache.json"
        self.cache.folder_cache_file = self.cache_dir / "folder_diagnoses_cache.json"
        self.cache._data = {}
        self.cache._folder_cache = {}

        self.test_settings = {
            "mysql_enabled": False,
        }
        self.service = FolderOCRService(self.test_settings)
        self.service.cache = self.cache

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(str(self.tmp_dir), ignore_errors=True)

    def test_01_save_and_retrieve_folder_diagnosis_persists_to_disk(self):
        """
        Valida que el diagnóstico completo a nivel carpeta se guarde en
        'cache/folder_diagnoses_cache.json' y se recupere con 0 tokens.
        """
        folder = self.root_dir / "01_01_02_00_00_00_2005"
        folder.mkdir()
        f1 = folder / "01_01_02_00_00_00_2005_0001_0010.pdf"
        f1.write_bytes(b"%PDF-1.4 dummy content 1")

        mock_diagnosis = {
            "folder_path": str(folder),
            "folder_name": folder.name,
            "veredicto_global": "COINCIDE_TOTAL",
            "codigo_base_sugerido": "01_01_02_00_00_00_2005",
            "ocr_consolidado": {
                "seccion": "01",
                "libro": "02",
                "tomo": "00",
                "volumen": "00",
                "fraccionamiento": "00",
                "anio": "2005"
            },
            "caratula_inspection": {"status": "OK_CON_INFO"},
            "applied_actions": ["Diagnóstico completado con IA"],
            "last_action": "Diagnóstico IA OK",
        }

        # Guardar en caché persistente
        self.cache.save_folder_diagnosis(folder, mock_diagnosis)

        # Verificar que el archivo existe en disco
        self.assertTrue(self.cache.folder_cache_file.exists())
        disk_content = json.loads(self.cache.folder_cache_file.read_text(encoding="utf-8"))
        self.assertIn(str(folder.resolve()), disk_content)

        # Recuperar diagnóstico
        retrieved = self.cache.get_folder_diagnosis(folder)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["veredicto_global"], "COINCIDE_TOTAL")
        self.assertEqual(retrieved["ocr_consolidado"]["libro"], "02")
        self.assertEqual(self.cache.stats["local_cache_hits"], 1)

    def test_02_folder_signature_invalidation_on_file_modification(self):
        """
        Valida que si se modifica, agrega o altera un archivo dentro de la carpeta,
        la firma cambie y el caché retorne None para forzar re-análisis.
        """
        folder = self.root_dir / "01_02_03_00_00_00_2010"
        folder.mkdir()
        f1 = folder / "doc1.pdf"
        f1.write_bytes(b"%PDF-1.4 initial content")

        mock_diag = {
            "folder_path": str(folder),
            "veredicto_global": "COINCIDE_TOTAL",
        }
        self.cache.save_folder_diagnosis(folder, mock_diag)
        self.assertIsNotNone(self.cache.get_folder_diagnosis(folder))

        # Modificar archivo agregando contenido (cambia tamaño y mtime)
        f1.write_bytes(b"%PDF-1.4 modified new content with different length")

        # La firma ya no coincide -> Retorna None
        self.assertIsNone(self.cache.get_folder_diagnosis(folder))

    def test_03_preview_folders_caratulas_attaches_cached_diagnosis(self):
        """
        Valida que preview_folders_caratulas consulte el caché persistente
        y adjunte el diagnóstico para recargas instantáneas.
        """
        folder = self.root_dir / "01_03_04_00_00_00_1995"
        folder.mkdir()
        c_pdf = folder / "01_03_04_00_00_00_1995_CARATULA_00.pdf"
        c_pdf.write_bytes(b"%PDF-1.4 caratula content")

        mock_diag = {
            "folder_path": str(folder),
            "veredicto_global": "COINCIDE_TOTAL",
            "ocr_consolidado": {"libro": "04", "anio": "1995"},
        }
        self.cache.save_folder_diagnosis(folder, mock_diag)

        previews = self.service.preview_folders_caratulas([folder])
        self.assertEqual(len(previews), 1)
        self.assertIn("cached_diagnosis", previews[0])
        self.assertIsNotNone(previews[0]["cached_diagnosis"])
        self.assertEqual(previews[0]["cached_diagnosis"]["veredicto_global"], "COINCIDE_TOTAL")


if __name__ == "__main__":
    unittest.main()
