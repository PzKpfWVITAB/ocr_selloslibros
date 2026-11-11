import unittest
from src.parsers.stamp_parser import StampParser
from src.core.claude_engine import ClaudeVisionEngine
from src.core.settings_manager import SettingsManager, DEFAULT_API_KEY

class TestStampParser(unittest.TestCase):
    def test_parse_official_stamp(self):
        # Líneas típicas detectadas por el OCR sobre el sello de carátula
        mock_ocr_lines = [
            {"text": "RUBEN F. VELAZQUEZ LOPEZ SECRETARIO DE GOBIERNO", "confidence": 0.85},
            {"text": "AUTORIZA EL LIBRO UNO TOMO #I (DOS) CORRESPONDIENTE A LA", "confidence": 0.75},
            {"text": "SECCION COMERCIO, PARA EL DISTRITO DE TUXTLA DEL ANO DOS MIL CINCO", "confidence": 0.88},
            {"text": "DEL MES DE ENERO DEL ANO DOS MIL CINCO.", "confidence": 0.90}
        ]

        result = StampParser.parse_page_lines(mock_ocr_lines)

        self.assertEqual(result["libro_sello"], 1)
        self.assertEqual(result["tomo"], 2)
        self.assertEqual(result["seccion"], 2)  # Comercio -> 2
        self.assertEqual(result["anio"], 2005)

    def test_parse_1997_stamp_with_numero(self):
        mock_ocr_lines = [
            {"text": "DEL ESTADO, AUTORIZA EL PRESENTE LIBRO NUMERO 3 TOMO I PARA", "confidence": 0.89},
            {"text": "LLEVAR EL ORIGINAL DE LA SECCION PRTMERA DEL REGISTRO PUBLI", "confidence": 0.91},
            {"text": "ANO DE 1997 MIL NOVECIENTOS NOVENTA Y SIETE, CONSTANTE DE", "confidence": 0.90},
        ]

        result = StampParser.parse_page_lines(mock_ocr_lines)

        self.assertEqual(result["libro"], 3)
        self.assertEqual(result["tomo"], 1)
        self.assertEqual(result["seccion"], 1)
        self.assertEqual(result["anio"], 1997)

    def test_parse_auxiliar_rule(self):
        # Regla: Si un documento especifica Libro Auxiliar
        mock_ocr_lines = [
            {"text": "COMPRAVENTA", "confidence": 0.95},
            {"text": "ANTECEDENTES REGISTRO NO. 30,916", "confidence": 0.90},
            {"text": "Tomo 1 Libro Auxiliar", "confidence": 0.80},
            {"text": "C. Ma. Leon Mejia Mendez", "confidence": 0.85},
            {"text": "Auxiliar", "confidence": 0.99}
        ]

        result = StampParser.parse_page_lines(mock_ocr_lines)

        self.assertEqual(result["libro"], "AUXILIAR")

    def test_parse_fraccionamiento(self):
        mock_ocr_lines = [
            {"text": "Una fraccion de terreno ubicado en la diecisiete Calle Oriente Sur", "confidence": 0.92},
            {"text": "Respecto a una fraccion del predio rustico denominado San Antonio", "confidence": 0.90}
        ]

        result = StampParser.parse_page_lines(mock_ocr_lines)

        self.assertIsNotNone(result["fraccionamiento"])
        self.assertTrue(
            "TERRENO UBICADO EN" in result["fraccionamiento"].upper() or 
            "PREDIO RUSTICO" in result["fraccionamiento"].upper()
        )

    def test_claude_json_parser_and_default_settings(self):
        cfg = SettingsManager.load()
        self.assertEqual(cfg["api_key"], DEFAULT_API_KEY)
        self.assertEqual(cfg["model"], "claude-sonnet-5")
        self.assertTrue(cfg["use_prompt_cache"])
        self.assertTrue(cfg["use_local_cache"])

        sample_json = """```json
        {
          "seccion": "PRIMERA",
          "libro": "3",
          "tomo": "II",
          "volumen": null,
          "fraccionamiento": null,
          "anio": "DOS MIL CINCO",
          "es_auxiliar": false,
          "evidencias": ["Sello oficial: Libro 3 Tomo II"]
        }
        ```"""
        parsed = ClaudeVisionEngine._parse_claude_json(sample_json)
        self.assertEqual(parsed["seccion"], 1)
        self.assertEqual(parsed["libro"], 3)
        self.assertEqual(parsed["tomo"], 2)
        self.assertIsNone(parsed["volumen"])
        self.assertEqual(parsed["anio"], 2005)

if __name__ == "__main__":
    unittest.main()
