import unittest
from src.matcher.comparator import FolderComparator

class TestComparator(unittest.TestCase):
    def test_default_zeros_when_not_found(self):
        # Resultados vacíos de OCR
        empty_doc_results = {
            "CARATULA": {"seccion": None, "libro": None, "tomo": None, "volumen": None, "fraccionamiento": None, "anio": None}
        }
        synthesized = FolderComparator.synthesize_detections(empty_doc_results)

        self.assertEqual(synthesized["seccion"], "00")
        self.assertEqual(synthesized["libro"], "00")
        self.assertEqual(synthesized["tomo"], "00")
        self.assertEqual(synthesized["volumen"], "00")
        self.assertEqual(synthesized["fraccionamiento"], "00")
        self.assertEqual(synthesized["anio"], "0000")

    def test_compare_matching_zeros(self):
        # Carpeta tiene volumen en 00 y OCR no lo detectó (00) -> Debe ser COINCIDE
        folder_data = {
            "seccion_val": 1,
            "libro_val": 1,
            "tomo_val": 2,
            "volumen_val": 0,
            "anio_val": 2005
        }
        ocr_final = {
            "seccion": 1,
            "libro": 1,
            "tomo": 2,
            "volumen": "00",
            "anio": 2005
        }
        comp = FolderComparator.compare(folder_data, ocr_final)
        self.assertEqual(comp["veredicto"], "COINCIDE_TOTAL")
        self.assertEqual(comp["detalles_campos"]["volumen"]["estado"], "COINCIDE")

    def test_compare_folder_ceros_with_detected_ocr(self):
        # Carpeta en ceros y OCR detectó valores
        folder_data = {
            "seccion_val": 0,
            "libro_val": 0,
            "tomo_val": 0,
            "volumen_val": 0,
            "anio_val": 2001
        }
        ocr_final = {
            "seccion": 1,
            "libro": "AUXILIAR",
            "tomo": 1,
            "volumen": "00",
            "anio": 2001
        }
        comp = FolderComparator.compare(folder_data, ocr_final)
        self.assertEqual(comp["veredicto"], "RELLENO_DE_CEROS_PROPUESTO")
        self.assertEqual(comp["detalles_campos"]["libro"]["estado"], "CARPETA_EN_CEROS")
        self.assertEqual(comp["detalles_campos"]["volumen"]["estado"], "COINCIDE")

    def test_compare_zeros_both_sides_no_valid_libro(self):
        # Caso 2002: Carpeta en ceros y OCR solo detectó año (libro en 00 en ambos lados)
        # Jamás debe ser COINCIDE_TOTAL, debe ser SIN_DATOS_CARATULA
        folder_data = {
            "seccion_val": 0,
            "libro_val": 0,
            "tomo_val": 0,
            "volumen_val": 0,
            "anio_val": 2002
        }
        ocr_final = {
            "seccion": "00",
            "libro": "00",
            "tomo": "00",
            "volumen": "00",
            "anio": 2002
        }
        comp = FolderComparator.compare(folder_data, ocr_final)
        self.assertEqual(comp["veredicto"], "SIN_DATOS_CARATULA")
        self.assertEqual(comp["detalles_campos"]["libro"]["estado"], "AMBOS_EN_CEROS")
        self.assertEqual(comp["detalles_campos"]["anio"]["estado"], "COINCIDE")

if __name__ == "__main__":
    unittest.main()
