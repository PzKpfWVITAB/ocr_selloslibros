import unittest
import tempfile
from pathlib import Path
from src.parsers.folder_parser import FolderParser
from src.core.pdf_selector import PDFSelector

class TestFolderParser(unittest.TestCase):
    def test_parse_standard_folder(self):
        folder_str = "01_01_03_02_00_00_2005_REVISAR_SEPARADO"
        res = FolderParser.parse(folder_str)

        self.assertEqual(res["delegacion"], "01")
        self.assertEqual(res["seccion_val"], 1)
        self.assertEqual(res["libro_val"], 3)
        self.assertEqual(res["tomo_val"], 2)
        self.assertEqual(res["volumen_val"], 0)
        self.assertEqual(res["fraccionamiento_val"], 0)
        self.assertEqual(res["anio_val"], 2005)
        self.assertEqual(res["sufijo"], "REVISAR_SEPARADO")
        self.assertFalse(res["tiene_ceros"])

    def test_parse_folder_with_ceros(self):
        folder_str = "01_00_00_00_00_00_2001_REVISAR"
        res = FolderParser.parse(folder_str)

        self.assertEqual(res["delegacion"], "01")
        self.assertEqual(res["seccion_val"], 0)
        self.assertEqual(res["libro_val"], 0)
        self.assertEqual(res["tomo_val"], 0)
        self.assertTrue(res["tiene_ceros"])

    def test_parse_folder_clean(self):
        folder_str = "01_01_05_01_00_00_1998"
        res = FolderParser.parse(folder_str)

        self.assertEqual(res["seccion_val"], 1)
        self.assertEqual(res["libro_val"], 5)
        self.assertEqual(res["tomo_val"], 1)
        self.assertEqual(res["anio_val"], 1998)
        self.assertIsNone(res["sufijo"])
        self.assertFalse(res["tiene_ceros"])

    def test_parse_folder_with_compound_fraccionamiento(self):
        folder_str = "01_01_01_00_04_FRACCIONAMIENTO_PATRIA_NUEVA_1998"
        res = FolderParser.parse(folder_str)

        self.assertEqual(res["seccion_val"], 1)
        self.assertEqual(res["libro_val"], 1)
        self.assertEqual(res["tomo_val"], 0)
        self.assertEqual(res["volumen_val"], 4)
        self.assertEqual(res["fraccionamiento_val"], "FRACCIONAMIENTO_PATRIA_NUEVA")
        self.assertEqual(res["anio_val"], 1998)

    def test_parse_alphanumeric_tomo_credito_agricola_and_actos_y_contratos(self):
        # Caso 48: CREDITO_AGRICOLA en posición de Tomo
        res_cred = FolderParser.parse("01_00_02_CREDITO_AGRICOLA_00_00_1986")
        self.assertEqual(res_cred["delegacion"], "01")
        self.assertEqual(res_cred["seccion_val"], 0)
        self.assertEqual(res_cred["libro_val"], 2)
        self.assertEqual(res_cred["tomo_val"], "CREDITO_AGRICOLA")
        self.assertEqual(res_cred["volumen_val"], 0)
        self.assertEqual(res_cred["fraccionamiento_val"], 0)
        self.assertEqual(res_cred["anio_val"], 1986)

        # Caso 45: ACTOS_Y_CONTRATOS en posición de Tomo
        res_act = FolderParser.parse("01_00_02_ACTOS_Y_CONTRATOS_00_00_1995")
        self.assertEqual(res_act["seccion_val"], 0)
        self.assertEqual(res_act["libro_val"], 2)
        self.assertEqual(res_act["tomo_val"], "ACTOS_Y_CONTRATOS")
        self.assertEqual(res_act["volumen_val"], 0)
        self.assertEqual(res_act["fraccionamiento_val"], 0)
        self.assertEqual(res_act["anio_val"], 1995)

        # Caso 47: AUXILIAR en posición de Tomo + FRACCIONAMIENTOS_Y_COLONIAS en posición de Fraccionamiento
        res_aux = FolderParser.parse("01_00_02_AUXILIAR_01_FRACCIONAMIENTOS_Y_COLONIAS_1990")
        self.assertEqual(res_aux["seccion_val"], 0)
        self.assertEqual(res_aux["libro_val"], 2)
        self.assertEqual(res_aux["tomo_val"], "AUXILIAR")
        self.assertEqual(res_aux["volumen_val"], 1)
        self.assertEqual(res_aux["fraccionamiento_val"], "FRACCIONAMIENTOS_Y_COLONIAS")
        self.assertEqual(res_aux["anio_val"], 1990)

    def test_root_folder_filtering_ignores_buenos_malos_and_subfolders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Carpetas válidas en la raíz con '_'
            (root / "01_01_03_01_00_00_1997").mkdir()
            (root / "01_01_03_02_00_00_1998").mkdir()
            # Carpetas sin '_' en la raíz (NO deben revisarse)
            (root / "BUENOS").mkdir()
            (root / "MALOS").mkdir()
            # Carpetas con '_' pero dentro de subcarpetas BUENOS / MALOS (NO deben revisarse)
            (root / "BUENOS" / "01_01_01_01_00_00_2004").mkdir()
            (root / "MALOS" / "01_01_02_01_00_00_2005").mkdir()

            valid = PDFSelector.get_valid_subfolders(str(root))
            valid_names = [p.name for p in valid]

            self.assertEqual(valid_names, ["01_01_03_01_00_00_1997", "01_01_03_02_00_00_1998"])

if __name__ == "__main__":
    unittest.main()
