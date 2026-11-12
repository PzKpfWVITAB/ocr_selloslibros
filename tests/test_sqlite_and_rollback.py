import shutil
import tempfile
import unittest
from pathlib import Path
import pymupdf as fitz

from src.database.db_manager import DatabaseManager
from src.core.caratula_inspector import CaratulaInspector
from src.core.file_operations import FileOperations
from src.core.claude_engine import ClaudeVisionEngine


class TestSQLiteCaratulaAndRollback(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_ocr_libros_"))
        self.db_path = self.temp_dir / "test_auditoria_libros.db"
        self.db = DatabaseManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _create_pdf_with_pages(pdf_path: Path, pages_spec: list):
        """
        pages_spec: lista de strings. Si el string es vacío "", crea una página en blanco;
        si tiene texto, dibuja múltiples líneas de texto y un recuadro de sello para simular información real.
        """
        doc = fitz.open()
        for text_content in pages_spec:
            page = doc.new_page(width=612, height=792)
            if text_content:
                page.draw_rect(fitz.Rect(80, 80, 520, 320), color=(0, 0, 0), width=2)
                for i in range(6):
                    page.insert_text(
                        fitz.Point(100, 115 + i * 30),
                        f"{text_content} - LINEA OFICIAL DE APERTURA {i + 1}",
                        fontsize=12,
                    )
        doc.save(str(pdf_path))
        doc.close()

    def test_01_build_clean_folder_code_and_collision_rules(self):
        # La posición 1 (delegación) nunca debe cambiar y llega únicamente hasta el año
        code = FileOperations.build_clean_folder_code(
            delegacion="01",
            seccion="1",
            libro="3",
            tomo="2",
            volumen="00",
            fraccionamiento="00",
            anio="2005",
        )
        self.assertEqual(code, "01_01_03_02_00_00_2005")

        # Nombre de PDF interno nunca lleva el sufijo de colisión _2
        new_pdf = FileOperations.build_new_pdf_name(
            old_pdf_name="01_01_03_01_00_00_2005_0001_00.pdf",
            clean_base_code="01_01_03_02_00_00_2005",
            current_folder_name="01_01_03_01_00_00_2005_REVISAR_SEPARADO",
        )
        self.assertEqual(new_pdf, "01_01_03_02_00_00_2005_0001_00.pdf")

        caratula_pdf = FileOperations.build_new_pdf_name(
            old_pdf_name="01_01_03_01_00_00_2005_CARATULA_00.pdf",
            clean_base_code="01_01_03_02_00_00_2005",
            current_folder_name="01_01_03_01_00_00_2005_REVISAR_SEPARADO",
        )
        self.assertEqual(caratula_pdf, "01_01_03_02_00_00_2005_CARATULA_00.pdf")

    def test_02_caratula_inspector_blank_vs_replaceable(self):
        # 1. Carpeta con Carátula donde Página 1 está en blanco y Página 2 tiene la información,
        #    y los PDFs regulares tienen adjunta la página en blanco
        folder_rep = self.temp_dir / "01_01_03_01_00_00_2005_REVISAR"
        folder_rep.mkdir(parents=True)

        caratula_path = folder_rep / "01_01_03_01_00_00_2005_CARATULA_00.pdf"
        self._create_pdf_with_pages(caratula_path, ["", "SECCION PRIMERA LIBRO 3 TOMO 2 ANIO 2005"])

        doc1_path = folder_rep / "01_01_03_01_00_00_2005_0001_00.pdf"
        self._create_pdf_with_pages(doc1_path, ["", "INSCRIPCION 0001 CONTENIDO DEL ACTO"])

        insp = CaratulaInspector.inspect_folder(folder_rep)
        self.assertEqual(insp["status"], "REEMPLAZABLE_HOJA_BLANCA")
        self.assertEqual(insp["info_page_num"], 2)
        self.assertEqual(insp["pdfs_with_blank_page"], 1)

        # 2. Carpeta con Carátula totalmente en blanco
        folder_blank = self.temp_dir / "01_01_00_00_00_00_2005"
        folder_blank.mkdir(parents=True)
        c_blank = folder_blank / "01_01_00_00_00_00_2005_CARATULA_00.pdf"
        self._create_pdf_with_pages(c_blank, ["", ""])
        d_blank = folder_blank / "01_01_00_00_00_00_2005_0001_00.pdf"
        self._create_pdf_with_pages(d_blank, ["", "TEXTO DEL DOCUMENTO"])

        insp_blank = CaratulaInspector.inspect_folder(folder_blank)
        self.assertEqual(insp_blank["status"], "TOTALMENTE_BLANCA")

    def test_03_chained_operations_and_full_rollback_to_initial_state(self):
        """
        Verifica el flujo completo:
        1. Crear carpeta existente '01_01_03_02_00_00_2005' para provocar colisión _2.
        2. Crear carpeta a corregir '01_01_03_00_00_00_2005_REVISAR_SEPARADO' con Carátula (Pág 1 blanca, Pág 2 info)
           y dos PDFs regulares con la Pág 1 en blanco.
        3. Guardar los hashes SHA-256 y rutas iniciales antes de cualquier cambio.
        4. Ejecutar Cambio 1: Reemplazo de la hoja en blanco por la Pág 2 de la Carátula.
        5. Ejecutar Cambio 2: Renombrado de carpeta y PDFs internos a '01_01_03_02_00_00_2005':
           - Verificar que la carpeta se llame '01_01_03_02_00_00_2005_2' (por colisión).
           - Verificar que los PDFs internos se llamen '01_01_03_02_00_00_2005_0001_00.pdf' (SIN _2).
        6. Ejecutar Rollback de la carpeta al punto inicial:
           - Verificar que la carpeta vuelve a '01_01_03_00_00_00_2005_REVISAR_SEPARADO'.
           - Verificar que los PDFs vuelven a sus nombres originales y que su hash SHA-256
             es 100% idéntico al que tenían ANTES del reemplazo de hoja en blanco.
        """
        existing_collision_dir = self.temp_dir / "01_01_03_02_00_00_2005"
        existing_collision_dir.mkdir(parents=True)

        initial_folder = self.temp_dir / "01_01_03_00_00_00_2005_REVISAR_SEPARADO"
        initial_folder.mkdir(parents=True)

        caratula_file = initial_folder / "01_01_03_00_00_00_2005_CARATULA_00.pdf"
        pdf_1 = initial_folder / "01_01_03_00_00_00_2005_0001_00.pdf"
        pdf_2 = initial_folder / "01_01_03_00_00_00_2005_0002_00.pdf"

        self._create_pdf_with_pages(caratula_file, ["", "CARATULA CON SELLO OFICIAL TOMO 2"])
        self._create_pdf_with_pages(pdf_1, ["", "FOLIO 0001 CONTENIDO"])
        self._create_pdf_with_pages(pdf_2, ["", "FOLIO 0002 CONTENIDO"])

        # Capturar hashes y rutas antes del primer cambio
        initial_hash_c = FileOperations.compute_sha256(caratula_file)
        initial_hash_1 = FileOperations.compute_sha256(pdf_1)
        initial_hash_2 = FileOperations.compute_sha256(pdf_2)

        insp = CaratulaInspector.inspect_folder(initial_folder)
        self.assertEqual(insp["status"], "REEMPLAZABLE_HOJA_BLANCA")

        # CAMBIO 1: Reemplazar hoja en blanco en pdf_1 y pdf_2
        rep_res = FileOperations.replace_blank_caratula_in_folder_pdfs(
            folder_path=initial_folder,
            caratula_path=caratula_file,
            info_page_num=insp["info_page_num"],
            replaceable_pdfs=insp["replaceable_pdfs"],
            db_manager=self.db,
        )
        self.assertEqual(rep_res["replaced_count"], 2)
        self.assertNotEqual(FileOperations.compute_sha256(pdf_1), initial_hash_1)

        # CAMBIO 2: Renombrar carpeta (con colisión _2) y PDFs internos (sin _2)
        clean_code = "01_01_03_02_00_00_2005"
        ren_res = FileOperations.rename_folder_and_internal_pdfs(
            folder_path=initial_folder,
            clean_base_code=clean_code,
            db_manager=self.db,
        )
        self.assertTrue(ren_res["collision_applied"])
        self.assertEqual(ren_res["new_folder_name"], "01_01_03_02_00_00_2005_2")

        new_folder_path = Path(ren_res["new_folder_path"])
        self.assertTrue(new_folder_path.exists())
        self.assertFalse(initial_folder.exists())

        # Los PDFs dentro de la carpeta _2 NO deben tener _2 en su nombre
        self.assertTrue((new_folder_path / "01_01_03_02_00_00_2005_0001_00.pdf").exists())
        self.assertTrue((new_folder_path / "01_01_03_02_00_00_2005_0002_00.pdf").exists())
        self.assertTrue((new_folder_path / "01_01_03_02_00_00_2005_CARATULA_00.pdf").exists())

        # Verificar que hay 3 archivos candidatos en Rollback
        candidates = self.db.get_rollback_candidates()
        self.assertEqual(len(candidates), 3)

        # EJECUTAR ROLLBACK DE LA CARPETA AL PUNTO INICIAL (ANTES DEL CAMBIO 1)
        rb_res = FileOperations.rollback_folder_to_initial_state(
            initial_folder_path=initial_folder,
            db_manager=self.db,
        )
        self.assertEqual(rb_res["restored_count"], 3)
        self.assertEqual(len(rb_res["errors"]), 0)

        # Verificar que la carpeta inicial y los 3 archivos originales existen con su hash original intacto
        self.assertTrue(initial_folder.exists())
        self.assertFalse(new_folder_path.exists())
        self.assertTrue(caratula_file.exists())
        self.assertTrue(pdf_1.exists())
        self.assertTrue(pdf_2.exists())

        self.assertEqual(FileOperations.compute_sha256(caratula_file), initial_hash_c)
        self.assertEqual(FileOperations.compute_sha256(pdf_1), initial_hash_1)
        self.assertEqual(FileOperations.compute_sha256(pdf_2), initial_hash_2)

        # Verificar que ya no quedan candidatos pendientes de rollback
        self.assertEqual(len(self.db.get_rollback_candidates()), 0)

    def test_04_alphanumeric_tomo_and_move_to_validados_with_rollback(self):
        # 1. Verificar regla de CREDITO_AGRICOLA en posición de Tomo (y Sección en None -> 00)
        raw_json_48 = """{
            "seccion": "CREDITO AGRICOLA",
            "libro": 2,
            "tomo": null,
            "volumen": null,
            "fraccionamiento": null,
            "anio": 1986,
            "es_auxiliar": false,
            "evidencias": ["AUTORIZA EL PRESENTE LIBRO NUMERO 2 DOS PARA LLEVAR EL ORIGINAL DE LA SECCION DE CREDITO AGRICOLA"]
        }"""
        parsed_48 = ClaudeVisionEngine._parse_claude_json(raw_json_48)
        self.assertIsNone(parsed_48["seccion"])
        self.assertEqual(parsed_48["libro"], 2)
        self.assertEqual(parsed_48["tomo"], "CREDITO_AGRICOLA")
        self.assertEqual(parsed_48["anio"], 1986)

        code_48 = FileOperations.build_clean_folder_code(
            delegacion="01",
            seccion=parsed_48["seccion"],
            libro=parsed_48["libro"],
            tomo=parsed_48["tomo"],
            volumen=parsed_48["volumen"],
            fraccionamiento=parsed_48["fraccionamiento"],
            anio=parsed_48["anio"],
        )
        self.assertEqual(code_48, "01_00_02_CREDITO_AGRICOLA_00_00_1986")

        # 2. Verificar movimiento de carpeta COINCIDE_TOTAL al directorio de Correctos y su Rollback
        good_folder = self.temp_dir / "01_00_02_CREDITO_AGRICOLA_00_00_1986"
        good_folder.mkdir(parents=True)
        good_pdf = good_folder / "01_00_02_CREDITO_AGRICOLA_00_00_1986_CARATULA_00.pdf"
        self._create_pdf_with_pages(good_pdf, ["SECCION DE CREDITO AGRICOLA LIBRO 2 1986"])
        orig_hash = FileOperations.compute_sha256(good_pdf)

        validados_dir = self.temp_dir / "CARPETAS_CORRECTAS"
        mv_res = FileOperations.move_folder_to_validados(
            folder_path=good_folder,
            validados_dir=validados_dir,
            db_manager=self.db,
        )
        self.assertEqual(mv_res["files_moved_count"], 1)
        self.assertFalse(good_folder.exists())
        self.assertTrue((validados_dir / "01_00_02_CREDITO_AGRICOLA_00_00_1986").exists())

        # Rollback de regreso a la raíz original
        rb = FileOperations.rollback_folder_to_initial_state(good_folder, self.db)
        self.assertEqual(rb["restored_count"], 1)
        self.assertTrue(good_folder.exists())
        self.assertEqual(FileOperations.compute_sha256(good_pdf), orig_hash)

        # 3. Verificar caso Libro 1 Auxiliar de Actos y Contratos -> Tomo=AUXILIAR, Fraccionamiento=ACTOS_Y_CONTRATOS
        raw_json_aux_actos = """{
            "seccion": null,
            "libro": 1,
            "tomo": "AUXILIAR",
            "volumen": null,
            "fraccionamiento": null,
            "anio": 1997,
            "es_auxiliar": true,
            "evidencias": [
                "Sello de autorización: 'AUTORIZA EL PRESENTE LIBRO 1 UNO AUXILIAR PARA LLEVAR EL ORIGINAL DE ACTOS Y CONTRATOS DEL REGISTRO PUBLICO DE LA PROPIEDAD Y DE COMERCIO DEL DISTRITO JUDICIAL DE TUXTLA'",
                "Año detectado: 'PARA EL AÑO DE 1997, MIL NOVECIENTOS NOVENTA Y SIETE'"
            ]
        }"""
        parsed_aux_act = ClaudeVisionEngine._parse_claude_json(raw_json_aux_actos)
        self.assertIsNone(parsed_aux_act["seccion"])
        self.assertEqual(parsed_aux_act["libro"], 1)
        self.assertEqual(parsed_aux_act["tomo"], "AUXILIAR")
        self.assertEqual(parsed_aux_act["fraccionamiento"], "ACTOS_Y_CONTRATOS")
        self.assertEqual(parsed_aux_act["anio"], 1997)

        code_aux_act = FileOperations.build_clean_folder_code(
            delegacion="01",
            seccion=parsed_aux_act["seccion"],
            libro=parsed_aux_act["libro"],
            tomo=parsed_aux_act["tomo"],
            volumen=parsed_aux_act["volumen"],
            fraccionamiento=parsed_aux_act["fraccionamiento"],
            anio=parsed_aux_act["anio"],
        )
        self.assertEqual(code_aux_act, "01_00_01_AUXILIAR_00_ACTOS_Y_CONTRATOS_1997")

    def test_05_move_to_apartados_with_error_subfolders_and_rollback(self):
        # Verificar nombres intuitivos de subcarpetas de error
        self.assertEqual(
            FileOperations.determine_review_subfolder("TOTALMENTE_BLANCA", "APARTAR_CARATULA_BLANCA"),
            "CARATULA EN BLANCO",
        )
        self.assertEqual(
            FileOperations.determine_review_subfolder("PDFS_SIN_CARATULA", "DISCREPANCIA_SIN_CARATULA"),
            "PDFS SIN CARATULA",
        )
        self.assertEqual(
            FileOperations.determine_review_subfolder("SIN_ARCHIVO_CARATULA", "SIN_ARCHIVO_CARATULA"),
            "SIN ARCHIVO CARATULA",
        )
        self.assertEqual(
            FileOperations.determine_review_subfolder("OK_CON_INFO", "SIN_INFO_MANTENER_NOMBRE", found_any_ocr_info=False),
            "CARATULA SIN DATOS",
        )

        # Crear carpeta con sufijo sucio que tiene carátula en blanco
        dirty_folder = self.temp_dir / "01_00_02_CREDITO_AGRICOLA_00_00_1986_REVISAR_SEPARADO"
        dirty_folder.mkdir(parents=True)
        dirty_pdf = dirty_folder / "01_00_02_CREDITO_AGRICOLA_00_00_1986_REVISAR_SEPARADO_CARATULA_00.pdf"
        self._create_pdf_with_pages(dirty_pdf, [""])
        orig_hash = FileOperations.compute_sha256(dirty_pdf)

        apartados_dir = self.temp_dir / "APARTADOS"
        mv_res = FileOperations.move_folder_to_apartados(
            folder_path=dirty_folder,
            apartados_dir=apartados_dir,
            db_manager=self.db,
            tipo_movimiento="APARTADO: CARATULA EN BLANCO",
            error_subfolder="CARATULA EN BLANCO",
        )

        expected_moved_folder = apartados_dir / "CARATULA EN BLANCO" / "01_00_02_CREDITO_AGRICOLA_00_00_1986"
        expected_moved_pdf = expected_moved_folder / "01_00_02_CREDITO_AGRICOLA_00_00_1986_CARATULA_00.pdf"
        self.assertEqual(mv_res["error_subfolder"], "CARATULA EN BLANCO")
        self.assertTrue(expected_moved_folder.exists())
        self.assertTrue(expected_moved_pdf.exists())

        # Rollback: debe restaurar la carpeta sucia original y limpiar también la subcarpeta 'CARATULA EN BLANCO' vacía
        rb = FileOperations.rollback_folder_to_initial_state(dirty_folder, self.db)
        self.assertEqual(rb["restored_count"], 1)
        self.assertTrue(dirty_folder.exists())
        self.assertTrue(dirty_pdf.exists())
        self.assertEqual(FileOperations.compute_sha256(dirty_pdf), orig_hash)
        self.assertFalse(expected_moved_folder.exists())
        self.assertFalse((apartados_dir / "CARATULA EN BLANCO").exists())

    def test_06_no_false_auxiliar_from_negative_evidence_and_cache_sanitization(self):
        # 1. Cuando Claude escribe "No se menciona AUXILIAR ni materia alfanumérica adicional" en evidencias,
        #    NO debe activarse es_auxiliar ni desplazarse el tomo a volumen.
        raw_json_1989 = """{
            "seccion": 1,
            "libro": 2,
            "tomo": 1,
            "volumen": null,
            "fraccionamiento": null,
            "anio": 1989,
            "es_auxiliar": false,
            "evidencias": [
                "Libro detectado: 'LIBRO NUMERO DOS TOMO UNO' -> libro=2",
                "Tomo detectado: 'TOMO UNO' -> tomo=1",
                "Sección numerada detectada: 'SECCION PRIMERA' -> seccion=1",
                "Año detectado: 'PARA EL AÑO DE 1989' -> anio=1989",
                "No se menciona AUXILIAR ni materia alfanumérica adicional"
            ]
        }"""
        parsed_1989 = ClaudeVisionEngine._parse_claude_json(raw_json_1989)
        self.assertEqual(parsed_1989["seccion"], 1)
        self.assertEqual(parsed_1989["libro"], 2)
        self.assertEqual(parsed_1989["tomo"], 1)
        self.assertIsNone(parsed_1989["volumen"])
        self.assertFalse(parsed_1989["es_auxiliar"])

        code_1989 = FileOperations.build_clean_folder_code(
            delegacion="01",
            seccion=parsed_1989["seccion"],
            libro=parsed_1989["libro"],
            tomo=parsed_1989["tomo"],
            volumen=parsed_1989["volumen"],
            fraccionamiento=parsed_1989["fraccionamiento"],
            anio=parsed_1989["anio"],
        )
        self.assertEqual(code_1989, "01_01_02_01_00_00_1989")

        # 2. Reparación automática de una entrada previamente guardada en caché con el falso AUXILIAR
        corrupted_cached = {
            "seccion": 1,
            "libro": 2,
            "tomo": "AUXILIAR",
            "volumen": 1,
            "fraccionamiento": None,
            "anio": 1989,
            "es_auxiliar": True,
            "evidencias": [
                "Libro detectado: 'LIBRO NUMERO DOS TOMO UNO' -> libro=2",
                "Tomo detectado: 'TOMO UNO' -> tomo=1",
                "Sección numerada detectada: 'SECCION PRIMERA' -> seccion=1",
                "Año detectado: 'PARA EL AÑO DE 1989' -> anio=1989",
                "No se menciona AUXILIAR ni materia alfanumérica adicional",
            ],
        }
        repaired_doc, changed = ClaudeVisionEngine.sanitize_cached_doc(corrupted_cached)
        self.assertTrue(changed)
        self.assertEqual(repaired_doc["tomo"], 1)
        self.assertIsNone(repaired_doc["volumen"])
        self.assertFalse(repaired_doc["es_auxiliar"])

    def test_07_structured_output_with_descriptive_text(self):
        # Salida estructurada nativa (Tool Use: registrar_datos_caratula) que separa
        # datos_estructurados del texto_descriptivo y evidencias
        structured_payload = """{
            "datos_estructurados": {
                "seccion": 1,
                "libro": 2,
                "tomo": 1,
                "volumen": null,
                "fraccionamiento": null,
                "anio": 1989,
                "es_auxiliar": false
            },
            "texto_descriptivo": "Se autoriza el Libro Número Dos, Tomo Uno, de la Sección Primera del Registro Público para el año 1989 (no es un libro AUXILIAR).",
            "evidencias": [
                "AUTORIZA EL PRESENTE LIBRO NUMERO DOS TOMO UNO DE LA SECCION PRIMERA",
                "PARA EL AÑO DE 1989"
            ]
        }"""
        parsed = ClaudeVisionEngine._parse_claude_json(structured_payload)
        self.assertEqual(parsed["seccion"], 1)
        self.assertEqual(parsed["libro"], 2)
        self.assertEqual(parsed["tomo"], 1)
        self.assertIsNone(parsed["volumen"])
        self.assertIsNone(parsed["fraccionamiento"])
        self.assertEqual(parsed["anio"], 1989)
        self.assertFalse(parsed["es_auxiliar"])
        self.assertTrue(parsed["texto_descriptivo"].startswith("Se autoriza el Libro"))
        self.assertTrue(any(ev.startswith("📝 Resumen:") for ev in parsed["evidencias"]))

    def test_08_explicit_auxiliar_rule_and_1992_tomo_recovery(self):
        # Caso real reportado por el usuario:
        # 'Distrito Judicial mencionado sin especificar fraccionamiento o auxiliar'
        # Libro 1, Tomo III, Año 1992.
        # Si no aparece explícitamente la palabra AUXILIAR en el documento,
        # NUNCA debe marcarse como Auxiliar y Tomo debe ser 3.
        corrupted_1992_cache = {
            "seccion": 1,
            "libro": 1,
            "tomo": "AUXILIAR",
            "volumen": 3,
            "fraccionamiento": None,
            "anio": 1992,
            "es_auxiliar": True,
            "evidencias": [
                "Libro detectado: 'LIBRO NUMERO 1 UNO' -> libro=1",
                "Tomo detectado: 'TOMO III' -> tomo=3",
                "Sección detectada: 'SECCION PRIMERA DEL REGISTRO PUBLICO DE LA PROPIEDAD Y DE COMERCIO' -> seccion=1",
                "Año detectado: 'PARA EL AÑO DE 1992, MIL NOVECIENTOS NOVENTA Y DOS' -> anio=1992",
                "Distrito Judicial mencionado sin especificar fraccionamiento o auxiliar",
            ],
        }

        repaired, changed = ClaudeVisionEngine.sanitize_cached_doc(corrupted_1992_cache)
        self.assertTrue(changed)
        self.assertEqual(repaired["seccion"], 1)
        self.assertEqual(repaired["libro"], 1)
        self.assertEqual(repaired["tomo"], 3)
        self.assertIsNone(repaired["volumen"])
        self.assertFalse(repaired["es_auxiliar"])

        # Verificar que el código generado sea 01_01_01_03_00_00_1992
        clean_code = FileOperations.build_clean_folder_code(
            delegacion="01",
            seccion=repaired["seccion"],
            libro=repaired["libro"],
            tomo=repaired["tomo"],
            volumen=repaired["volumen"],
            fraccionamiento=repaired["fraccionamiento"],
            anio=repaired["anio"],
        )
        self.assertEqual(clean_code, "01_01_01_03_00_00_1992")

        # Verificar que si Claude llegara a devolver es_auxiliar=True por alucinación,
        # _parse_claude_json lo anule si no hay mención afirmativa de AUXILIAR en el documento
        raw_hallucinated = """{
            "datos_estructurados": {
                "seccion": 1,
                "libro": 1,
                "tomo": "AUXILIAR",
                "volumen": 3,
                "fraccionamiento": null,
                "anio": 1992,
                "es_auxiliar": true
            },
            "texto_descriptivo": "Acta de apertura de libro 1 tomo 3",
            "evidencias": [
                "Libro detectado: 'LIBRO NUMERO 1 UNO' -> libro=1",
                "Tomo detectado: 'TOMO III' -> tomo=3",
                "Sección detectada: 'SECCION PRIMERA' -> seccion=1",
                "Año detectado: '1992'",
                "Distrito Judicial mencionado sin especificar fraccionamiento o auxiliar"
            ]
        }"""
        parsed_h = ClaudeVisionEngine._parse_claude_json(raw_hallucinated)
        self.assertFalse(parsed_h["es_auxiliar"])
        self.assertEqual(parsed_h["tomo"], 3)
        self.assertIsNone(parsed_h["volumen"])

    def test_09_isolated_comentarios_ia_in_json(self):
        """
        Verifica que los comentarios de la IA ('comentarios_ia') sobre ausencia de datos
        ('sin especificar auxiliar', 'no menciona fraccionamiento') estén estrictamente
        aislados y NUNCA contaminen los datos numéricos y categóricos que nos sirven a nosotros.
        """
        raw_json_with_comments = """{
            "transcripcion_literal": "AUTORIZA EL PRESENTE LIBRO NUMERO 1 UNO TOMO III CORRESPONDIENTE A LA SECCION PRIMERA DEL REGISTRO PUBLICO DE LA PROPIEDAD Y DE COMERCIO PARA EL AÑO DE 1992",
            "datos_estructurados": {
                "seccion": 1,
                "libro": 1,
                "tomo": 3,
                "volumen": null,
                "fraccionamiento": null,
                "anio": 1992,
                "es_auxiliar": false
            },
            "comentarios_ia": "Distrito Judicial de Tuxtla Gutiérrez sin especificar fraccionamiento o auxiliar. No se menciona libro auxiliar.",
            "texto_descriptivo": "Libro 1 Tomo III de la Sección Primera para 1992.",
            "evidencias": [
                "Libro detectado: 'LIBRO NUMERO 1 UNO' -> libro=1",
                "Tomo detectado: 'TOMO III' -> tomo=3",
                "Sección detectada: 'SECCION PRIMERA' -> seccion=1",
                "Año detectado: 'PARA EL AÑO DE 1992' -> anio=1992"
            ]
        }"""
        parsed = ClaudeVisionEngine._parse_claude_json(raw_json_with_comments)

        # Los datos estructurados deben ser exactos e inalterados
        self.assertEqual(parsed["seccion"], 1)
        self.assertEqual(parsed["libro"], 1)
        self.assertEqual(parsed["tomo"], 3)
        self.assertIsNone(parsed["volumen"])
        self.assertIsNone(parsed["fraccionamiento"])
        self.assertEqual(parsed["anio"], 1992)
        self.assertFalse(parsed["es_auxiliar"])

        # El código construido debe ser exactamente 01_01_01_03_00_00_1992 (COINCIDE TOTAL)
        code = FileOperations.build_clean_folder_code(
            delegacion="01",
            seccion=parsed["seccion"],
            libro=parsed["libro"],
            tomo=parsed["tomo"],
            volumen=parsed["volumen"],
            fraccionamiento=parsed["fraccionamiento"],
            anio=parsed["anio"],
        )
        self.assertEqual(code, "01_01_01_03_00_00_1992")

        # El comentario debe estar aislado como observación informativa, sin contaminar datos
        self.assertTrue(any(ev.startswith("💬 Observación IA:") for ev in parsed["evidencias"]))

    def test_10_executive_statistics_and_traceability_matrix(self):
        """
        Verifica el dimensionamiento de Volumetría (Carpetas, PDFs e Imágenes)
        y la generación de la matriz de trazabilidad cruzada (Original vs Final).
        """
        # 1. Registrar una detección con veredicto COINCIDE_TOTAL (Bien)
        diag_ok = {
            "folder_path": str(self.temp_dir / "01_01_01_01_00_00_1990"),
            "folder_data": {"folder_name": "01_01_01_01_00_00_1990", "delegacion": "01", "seccion_raw": "01", "libro_raw": "01", "tomo_raw": "01", "volumen_raw": "00", "fraccionamiento_raw": "00", "anio_raw": "1990"},
            "caratula_inspection": {
                "caratula_filename": "01_01_01_01_00_00_1990_CARATULA_00.pdf",
                "status": "OK_CON_INFO",
                "info_page_num": 1,
                "all_pdfs_have_caratula": True,
                "total_regular_pdfs": 10,
                "total_pages_in_folder": 25,  # 25 imágenes
                "pdfs_with_info_page": 10,
                "pdfs_with_blank_page": 0,
                "pdfs_missing_caratula": []
            },
            "ocr_consolidado": {"seccion": 1, "libro": 1, "tomo": 1, "volumen": 0, "fraccionamiento": "00", "anio": 1990, "evidencias_globales": []},
            "veredicto_global": "COINCIDE_TOTAL",
            "codigo_base_sugerido": "01_01_01_01_00_00_1990"
        }
        self.db.save_detection(diag_ok)

        # 2. Registrar una detección con veredicto DISCREPANCIA (Mal)
        diag_bad = {
            "folder_path": str(self.temp_dir / "01_01_02_01_00_00_1985"),
            "folder_data": {"folder_name": "01_01_02_01_00_00_1985", "delegacion": "01", "seccion_raw": "01", "libro_raw": "02", "tomo_raw": "01", "volumen_raw": "00", "fraccionamiento_raw": "00", "anio_raw": "1985"},
            "caratula_inspection": {
                "caratula_filename": "01_01_02_01_00_00_1985_CARATULA_00.pdf",
                "status": "REEMPLAZABLE_HOJA_BLANCA",
                "info_page_num": 1,
                "all_pdfs_have_caratula": True,
                "total_regular_pdfs": 5,
                "total_pages_in_folder": 15,  # 15 imágenes
                "pdfs_with_info_page": 0,
                "pdfs_with_blank_page": 5,
                "pdfs_missing_caratula": []
            },
            "ocr_consolidado": {"seccion": 1, "libro": 2, "tomo": 3, "volumen": 0, "fraccionamiento": "00", "anio": 1985, "evidencias_globales": []},
            "veredicto_global": "DISCREPANCIA_TOMO",
            "codigo_base_sugerido": "01_01_02_03_00_00_1985"
        }
        self.db.save_detection(diag_bad)

        # 3. Registrar un movimiento físico
        self.db.log_file_movement(
            tipo_movimiento="RENOMBRADO",
            ruta_anterior=self.temp_dir / "ORIG" / "archivo_01.pdf",
            ruta_nueva=self.temp_dir / "DEST" / "archivo_01_renombrado.pdf",
            nombre_anterior="archivo_01.pdf",
            nombre_nuevo="archivo_01_renombrado.pdf",
            hash_actual="hash123",
            numero_paginas=3,
            hash_origen_inicial="hash123"
        )

        # 4. Obtener estadísticas ejecutivas
        stats = self.db.get_executive_statistics()
        self.assertEqual(stats["total_carpetas"], 2)
        self.assertEqual(stats["carpetas_bien"], 1)
        self.assertEqual(stats["carpetas_mal"], 1)
        self.assertEqual(stats["pct_carpetas_bien"], 50.0)
        self.assertEqual(stats["total_pdfs"], 15)
        self.assertEqual(stats["pdfs_bien"], 10)
        self.assertEqual(stats["pdfs_mal"], 5)
        self.assertEqual(stats["total_imagenes"], 40)  # 25 + 15
        self.assertEqual(stats["imagenes_bien"], 25)
        self.assertEqual(stats["imagenes_mal"], 15)
        self.assertEqual(stats["pct_imagenes_bien"], 62.5)
        self.assertEqual(stats["archivos_modificados"], 1)

        # 5. Obtener matriz de comparativa
        comparison = self.db.get_traceability_comparison()
        self.assertEqual(len(comparison), 1)
        item = comparison[0]
        self.assertEqual(item["nombre_origen"], "archivo_01.pdf")
        self.assertEqual(item["nombre_actual"], "archivo_01_renombrado.pdf")
        self.assertEqual(item["numero_paginas"], 3)
        self.assertTrue(item["ha_cambiado"])
        self.assertEqual(item["estado_str"], "MODIFICADO")


if __name__ == "__main__":
    unittest.main()
