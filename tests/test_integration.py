import unittest
from pathlib import Path
from src.service import FolderOCRService

TEST_FOLDER = r"\\172.40.5.84\ssdirec\REVISANDO\ENTREGA 1\4 ENTREGA SELLOS LIBROS\SELLOS LIBROS\Tuxtla Gutiérrez\KERUBIN\01_01_03_02_00_00_2005_REVISAR_SEPARADO"

class TestIntegration(unittest.TestCase):
    def test_integration_real_folder(self):
        if not Path(TEST_FOLDER).exists():
            self.skipTest(f"Carpeta de red no accesible: {TEST_FOLDER}")

        service = FolderOCRService()
        result = service.process_folder(TEST_FOLDER)

        print("\n" + "=" * 60)
        print("REPORTE DE INTEGRACIÓN - ANÁLISIS DE CARPETA REAL")
        print("=" * 60)
        print(f"Carpeta: {result['folder_path']}")
        print(f"Tiempo total: {result['total_elapsed_seconds']} s")
        print(f"Páginas procesadas: {result['processed_pages_count']}")
        print(f"Archivos analizados: {result['target_pdfs_analyzed']}")
        print("-" * 60)
        print("DETECCIÓN OCR CONSOLIDADA:")
        for k, v in result['ocr_consolidado'].items():
            if k != "evidencias_globales":
                print(f"  {k}: {v}")
        print("-" * 60)
        print("VEREDICTO DE COMPARACIÓN:")
        comp = result['comparacion']
        print(f"  Veredicto: {comp['veredicto']}")
        print(f"  Coincidencias: {comp['coincidencias']}")
        print(f"  Discrepancias: {comp['discrepancias']}")
        print("Detalle de campos:")
        for campo, det in comp['detalles_campos'].items():
            print(f"  [{det['estado']}] {campo}: Carpeta={det['carpeta']} | OCR={det['ocr']}")
        print("=" * 60)

        # Aserciones clave sobre la carpeta real
        self.assertIn("CARATULA", result["target_pdfs_analyzed"])
        self.assertIn("PRIMERO", result["target_pdfs_analyzed"])
        self.assertGreater(result["processed_pages_count"], 0)
        self.assertEqual(result["ocr_consolidado"]["seccion"], 1)  # Sección 1 (Primera)
        self.assertEqual(result["ocr_consolidado"]["libro"], 1)  # Libro 1 (Uno)
        self.assertEqual(result["ocr_consolidado"]["tomo"], 2)  # Tomo 2 (Dos)
        self.assertEqual(result["ocr_consolidado"]["volumen"], "00")  # Volumen 00
        self.assertEqual(result["ocr_consolidado"]["anio"], 2005)  # Año 2005 (Dos Mil Cinco)
        self.assertEqual(result["comparacion"]["detalles_campos"]["seccion"]["estado"], "COINCIDE")
        self.assertEqual(result["comparacion"]["detalles_campos"]["tomo"]["estado"], "COINCIDE")
        self.assertEqual(result["comparacion"]["detalles_campos"]["volumen"]["estado"], "COINCIDE")
        self.assertEqual(result["comparacion"]["detalles_campos"]["anio"]["estado"], "COINCIDE")
        self.assertEqual(result["comparacion"]["detalles_campos"]["libro"]["estado"], "DISCREPANCIA")

    def test_integration_1997_folder(self):
        folder_1997 = r"\\172.40.5.84\ssdirec\REVISANDO\ENTREGA 1\4 ENTREGA SELLOS LIBROS\SELLOS LIBROS\Tuxtla Gutiérrez\KERUBIN\01_01_03_01_00_00_1997"
        if not Path(folder_1997).exists():
            self.skipTest(f"Carpeta de red no accesible: {folder_1997}")

        service = FolderOCRService()
        result = service.process_folder(folder_1997)

        self.assertEqual(result["ocr_consolidado"]["seccion"], 1)
        self.assertEqual(result["ocr_consolidado"]["libro"], 3)
        self.assertEqual(result["ocr_consolidado"]["tomo"], 1)
        self.assertEqual(result["ocr_consolidado"]["anio"], 1997)
        self.assertEqual(result["ocr_consolidado"]["volumen"], "00")

        comp = result["comparacion"]
        self.assertEqual(comp["detalles_campos"]["seccion"]["estado"], "COINCIDE")
        self.assertEqual(comp["detalles_campos"]["libro"]["estado"], "COINCIDE")
        self.assertEqual(comp["detalles_campos"]["tomo"]["estado"], "COINCIDE")
        self.assertEqual(comp["detalles_campos"]["anio"]["estado"], "COINCIDE")
        self.assertEqual(comp["detalles_campos"]["volumen"]["estado"], "COINCIDE")

if __name__ == "__main__":
    unittest.main()
