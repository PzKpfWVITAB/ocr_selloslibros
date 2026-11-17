import unittest
from src.core.claude_engine import ClaudeVisionEngine
from src.parsers.stamp_parser import StampParser
from src.parsers.folder_parser import FolderParser
from src.matcher.comparator import FolderComparator
from src.core.file_operations import FileOperations


class TestFraccionamientoNamingPriority(unittest.TestCase):
    """
    Pruebas unitarias para la regla de prioridad en nombres de Fraccionamientos:
    Si el documento menciona 'FRACCIONAMIENTOS Y COLONIAS' pero especifica el nombre
    concreto de un fraccionamiento, debe llamarse FRACCIONAMIENTO_<NOMBRE> y nunca
    únicamente FRACCIONAMIENTOS_Y_COLONIAS.
    """

    def test_extract_specific_fraccionamiento(self):
        text_with_name = (
            "EN LA CIUDAD DE TUXTLA GUTIERREZ, CHIAPAS... AUTORIZA EL PRESENTE LIBRO "
            "NUMERO 173 AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS PARA LLEVAR EL REGISTRO "
            "DEL FRACCIONAMIENTO SAN FERNANDO DE ESTA CIUDAD PARA EL AÑO DE 1997"
        )
        res = ClaudeVisionEngine.extract_specific_fraccionamiento(text_with_name)
        self.assertEqual(res, "FRACCIONAMIENTO_SAN_FERNANDO")

    def test_extract_specific_fraccionamiento_solidaridad(self):
        text = "AUTORIZA LIBRO AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS DEL FRACCIONAMIENTO SOLIDARIDAD, CHIAPAS"
        res = ClaudeVisionEngine.extract_specific_fraccionamiento(text)
        self.assertEqual(res, "FRACCIONAMIENTO_SOLIDARIDAD")

    def test_extract_specific_fraccionamiento_3_de_mayo_del_2004(self):
        # Caso reportado por el usuario: el conector 'DEL' o año '2004/2005' nunca debe quedar dentro del fraccionamiento
        text1 = "LIBRO 238 AUXILIAR... FRACCIONAMIENTO 3 DE MAYO DEL 2004"
        self.assertEqual(
            ClaudeVisionEngine.extract_specific_fraccionamiento(text1),
            "FRACCIONAMIENTO_3_DE_MAYO"
        )
        text2 = "FRACCIONAMIENTO 5 DE MAYO DEL 2005"
        self.assertEqual(
            ClaudeVisionEngine.extract_specific_fraccionamiento(text2),
            "FRACCIONAMIENTO_5_DE_MAYO"
        )

    def test_extract_generic_only_returns_none(self):
        text_generic = (
            "AUTORIZA EL LIBRO SEGUNDO AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS "
            "PARA EL DISTRITO JUDICIAL DE TUXTLA DEL AÑO 1990"
        )
        res = ClaudeVisionEngine.extract_specific_fraccionamiento(text_generic)
        self.assertIsNone(res)

    def test_normalize_fraccionamiento_label(self):
        self.assertEqual(
            ClaudeVisionEngine.normalize_fraccionamiento_label("SAN_FERNANDO"),
            "FRACCIONAMIENTO_SAN_FERNANDO"
        )
        self.assertEqual(
            ClaudeVisionEngine.normalize_fraccionamiento_label("FRACCIONAMIENTO_SAN_FERNANDO"),
            "FRACCIONAMIENTO_SAN_FERNANDO"
        )
        self.assertEqual(
            ClaudeVisionEngine.normalize_fraccionamiento_label("FRACCION_SAN_FERNANDO"),
            "FRACCIONAMIENTO_SAN_FERNANDO"
        )
        self.assertEqual(
            ClaudeVisionEngine.normalize_fraccionamiento_label("FRACCIONAMIENTOS_Y_COLONIAS"),
            "FRACCIONAMIENTOS_Y_COLONIAS"
        )
        self.assertEqual(
            ClaudeVisionEngine.normalize_fraccionamiento_label("ACTOS_Y_CONTRATOS"),
            "ACTOS_Y_CONTRATOS"
        )

    def test_parse_claude_json_overrides_generic_with_specific(self):
        raw_json = """
        {
            "transcripcion_literal": "LIBRO 173 AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS... DEL FRACCIONAMIENTO SAN FERNANDO PARA EL AÑO 1997",
            "datos_estructurados": {
                "seccion": null,
                "libro": 173,
                "tomo": "AUXILIAR",
                "volumen": null,
                "fraccionamiento": "FRACCIONAMIENTOS_Y_COLONIAS",
                "anio": 1997,
                "es_auxiliar": true
            },
            "evidencias": [
                "'LIBRO 173 AUXILIAR' -> libro=173, tomo=AUXILIAR",
                "'FRACCIONAMIENTOS Y COLONIAS' -> fraccionamiento='FRACCIONAMIENTOS_Y_COLONIAS'"
            ]
        }
        """
        parsed = ClaudeVisionEngine._parse_claude_json(raw_json)
        self.assertEqual(parsed["libro"], 173)
        self.assertEqual(parsed["tomo"], "AUXILIAR")
        self.assertEqual(parsed["fraccionamiento"], "FRACCIONAMIENTO_SAN_FERNANDO")
        self.assertEqual(parsed["anio"], 1997)

    def test_sanitize_cached_doc_updates_generic_to_specific(self):
        cached_doc = {
            "seccion": None,
            "libro": 173,
            "tomo": "AUXILIAR",
            "volumen": None,
            "fraccionamiento": "FRACCIONAMIENTOS_Y_COLONIAS",
            "anio": 1997,
            "es_auxiliar": True,
            "transcripcion_literal": "LIBRO NUMERO 173 AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS... FRACCIONAMIENTO SOLIDARIDAD 1997",
            "evidencias": ["'LIBRO 173' -> libro=173"]
        }
        repaired, changed = ClaudeVisionEngine.sanitize_cached_doc(cached_doc)
        self.assertTrue(changed)
        self.assertEqual(repaired["fraccionamiento"], "FRACCIONAMIENTO_SOLIDARIDAD")

    def test_stamp_parser_prioritizes_specific_fraccionamiento(self):
        ocr_lines = [
            {"text": "AUTORIZA EL LIBRO NUMERO 173 AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS"},
            {"text": "CORRESPONDIENTE AL FRACCIONAMIENTO PATRIA NUEVA DE ESTA CIUDAD"},
            {"text": "PARA EL AÑO DE 1997"}
        ]
        parsed = StampParser.parse_page_lines(ocr_lines)
        self.assertEqual(parsed["fraccionamiento"], "FRACCIONAMIENTO_PATRIA_NUEVA")
        self.assertEqual(parsed["libro"], 173)
        self.assertEqual(parsed["tomo"], "AUXILIAR")
        self.assertEqual(parsed["anio"], 1997)

    def test_folder_comparator_detects_discrepancy_and_suggests_specific(self):
        folder_data = FolderParser.parse("01_00_173_AUXILIAR_00_FRACCIONAMIENTOS_Y_COLONIAS_1997")
        ocr_final = {
            "seccion": "00",
            "libro": 173,
            "tomo": "AUXILIAR",
            "volumen": "00",
            "fraccionamiento": "FRACCIONAMIENTO_SAN_FERNANDO",
            "anio": 1997,
            "evidencias_globales": []
        }
        comp = FolderComparator.compare(folder_data, ocr_final)
        self.assertEqual(comp["veredicto"], "DISCREPANCIA")
        self.assertTrue(any("Fraccionamiento" in d for d in comp["discrepancias"]))

        sugerido = FileOperations.build_clean_folder_code(
            delegacion=folder_data["delegacion"],
            seccion=ocr_final["seccion"],
            libro=ocr_final["libro"],
            tomo=ocr_final["tomo"],
            volumen=ocr_final["volumen"],
            fraccionamiento=ocr_final["fraccionamiento"],
            anio=ocr_final["anio"]
        )
        self.assertEqual(sugerido, "01_00_173_AUXILIAR_00_FRACCIONAMIENTO_SAN_FERNANDO_1997")

    def test_3_de_mayo_normalizes_and_coincide_total(self):
        # Carpeta física en disco del usuario: 01_01_238_AUXILIAR_00_FRACCIONAMIENTO_3_DE_MAYO_2004
        folder_data = FolderParser.parse("01_01_238_AUXILIAR_00_FRACCIONAMIENTO_3_DE_MAYO_2004")
        
        # Simular que el texto traía 'FRACCIONAMIENTO 3 DE MAYO DEL 2004'
        detected_fracc = ClaudeVisionEngine.normalize_fraccionamiento_label("FRACCIONAMIENTO_3_DE_MAYO_DEL")
        self.assertEqual(detected_fracc, "FRACCIONAMIENTO_3_DE_MAYO")

        ocr_final = {
            "seccion": 1,
            "libro": 238,
            "tomo": "AUXILIAR",
            "volumen": "00",
            "fraccionamiento": detected_fracc,
            "anio": 2004,
            "evidencias_globales": []
        }

        comp = FolderComparator.compare(folder_data, ocr_final)
        self.assertEqual(comp["veredicto"], "COINCIDE_TOTAL")
        self.assertEqual(len(comp["discrepancias"]), 0)

        sugerido = FileOperations.build_clean_folder_code(
            delegacion=folder_data["delegacion"],
            seccion=ocr_final["seccion"],
            libro=ocr_final["libro"],
            tomo=ocr_final["tomo"],
            volumen=ocr_final["volumen"],
            fraccionamiento=ocr_final["fraccionamiento"],
            anio=ocr_final["anio"]
        )
        self.assertEqual(sugerido, "01_01_238_AUXILIAR_00_FRACCIONAMIENTO_3_DE_MAYO_2004")


if __name__ == "__main__":
    unittest.main()
