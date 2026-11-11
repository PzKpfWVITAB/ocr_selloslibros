import re
from typing import List, Dict, Any, Optional
from src.parsers.text_normalizer import TextNormalizer

class StampParser:
    """
    Analiza las líneas de texto extraídas por el OCR para detectar:
    - SECCIÓN
    - LIBRO (incluyendo regla especial de AUXILIAR)
    - TOMO
    - VOLUMEN
    - FRACCIONAMIENTO / PREDIO
    - AÑO
    """

    @classmethod
    def parse_page_lines(cls, ocr_lines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Recibe la lista de líneas detectadas en una página: [{'text': str, 'confidence': float}]
        Retorna los campos detectados con sus evidencias.
        """
        detections: Dict[str, Any] = {
            "seccion": None,
            "libro": None,
            "tomo": None,
            "volumen": None,
            "fraccionamiento": None,
            "anio": None,
            "evidencias": []
        }

        # Texto concatenado de la página para búsquedas multilínea
        full_page_text = " ".join([line["text"] for line in ocr_lines])
        normalized_full = TextNormalizer.strip_accents(full_page_text).upper()

        # Normalizar typos comunes de OCR
        normalized_full = re.sub(r'\b(?:LIRO|LHRO|L1BRO)\b', 'LIBRO', normalized_full)
        normalized_full = re.sub(r'\b(?:LOMO|TONO|RONO|ROMO|1OMO|TOHO|TOM0)\b', 'TOMO', normalized_full)
        normalized_full = re.sub(r'\b(?:SEOCION|SECC1ON|SEGCION|SECOION|SBCCION)\b', 'SECCION', normalized_full)

        # -------------------------------------------------------------
        # 1. REGLA AUXILIAR:
        # Solo clasificar como libro AUXILIAR si el texto especifica "LIBRO AUXILIAR",
        # "L. AUXILIAR" o en el contexto del libro/sección, evitando falsos positivos
        # por cargos o firmas de funcionarios ("C. Registrador Auxiliar").
        # -------------------------------------------------------------
        es_libro_auxiliar = bool(re.search(
            r'\b(?:LIBRO|L\.|SECCION|TOMO)\s*(?:DE\s+)?AUXILIAR\b|\bLIBRO\s*:\s*AUXILIAR\b',
            normalized_full
        ))
        if es_libro_auxiliar:
            detections["libro"] = "AUXILIAR"
            detections["evidencias"].append("Detectada mención explícita 'LIBRO AUXILIAR'")

        # -------------------------------------------------------------
        # 2. PATRÓN COMPUESTO DEL SELLO OFICIAL:
        # Soporta tanto "AUTORIZA EL LIBRO [X] TOMO [Y]" como "AUTORIZA EL PRESENTE LIBRO NUMERO [X] TOMO [Y]"
        # -------------------------------------------------------------
        sello_match = re.search(
            r'LIBRO\s+(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+(?:\s*\([A-Z0-9]+\))?)\s+TOMO\s+(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+(?:\s*\([A-Z0-9]+\))?)',
            normalized_full
        )
        if sello_match:
            raw_libro = sello_match.group(1).strip()
            raw_tomo = sello_match.group(2).strip()

            parsed_libro = TextNormalizer.parse_number_or_word(raw_libro)
            parsed_tomo = TextNormalizer.parse_number_or_word(raw_tomo)

            if parsed_libro is not None:
                detections["libro"] = parsed_libro
            else:
                detections["libro"] = raw_libro
            detections["libro_sello"] = parsed_libro or raw_libro

            if parsed_tomo is not None:
                detections["tomo"] = parsed_tomo
            else:
                detections["tomo"] = raw_tomo

            detections["evidencias"].append(f"Sello oficial: Libro='{raw_libro}' (={parsed_libro}), Tomo='{raw_tomo}' (={parsed_tomo})")

        # Búsqueda individual de LIBRO si aún no se detectó
        if detections["libro"] is None:
            for m_lib in re.finditer(r'\bLIBRO\s*[:\.]?\s*(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+(?:\s*\([A-Z0-9]+\))?)', normalized_full):
                raw_l = m_lib.group(1).strip()
                val_l = TextNormalizer.parse_number_or_word(raw_l)
                if val_l is not None:
                    detections["libro"] = val_l
                    detections["evidencias"].append(f"Línea individual: Libro='{raw_l}' (={val_l})")
                    break

        # Búsqueda individual de TOMO si aún no se detectó
        if detections["tomo"] is None:
            for m_tom in re.finditer(r'\bTOMO\s*[:\.]?\s*(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+(?:\s*\([A-Z0-9]+\))?)', normalized_full):
                raw_t = m_tom.group(1).strip()
                val_t = TextNormalizer.parse_number_or_word(raw_t)
                if val_t is not None:
                    detections["tomo"] = val_t
                    detections["evidencias"].append(f"Línea individual: Tomo='{raw_t}' (={val_t})")
                    break
            if detections["tomo"] is None:
                # Si en el contexto del sello aparece '(DOS)' o '(UNO)' después de TOMO
                m_paren_num = re.search(r'TOMO\b[^\(]{0,20}\(([A-Z0-9]+)\)', normalized_full)
                if m_paren_num:
                    val_t = TextNormalizer.parse_number_or_word(m_paren_num.group(1))
                    if val_t:
                        detections["tomo"] = val_t
                        detections["evidencias"].append(f"Tomo por paréntesis en sello: {val_t}")

        # -------------------------------------------------------------
        # 3. SECCIÓN:
        # Detectar la sección autorizada o especificada.
        # NOTA IMPORTANTE: La institución se llama "REGISTRO PÚBLICO DE LA PROPIEDAD Y DE COMERCIO",
        # por lo que las palabras PROPIEDAD o COMERCIO no deben asignarse como sección salvo que
        # correspondan específicamente a la sección autorizada.
        # -------------------------------------------------------------
        # Caso A: Fórmula oficial de sello: "CORRESPONDIENTE A LA [SECCIÓN ...] DEL REGISTRO"
        m_sello_sec = re.search(
            r'CORR[A-Z]*\s*(?:A\s*LA)?\s*(?:SECCION|LLLLIOM|SECC[A-Z]*)?\s*([A-Z0-9]+)\s*(?:DEL|OEL)?\s*REGIST',
            normalized_full
        )
        if m_sello_sec:
            raw_s_token = m_sello_sec.group(1).strip()
            val_s = TextNormalizer.normalize_section(raw_s_token)
            if val_s is not None:
                detections["seccion"] = val_s
                detections["seccion_nombre"] = raw_s_token
                detections["evidencias"].append(f"Sección en sello oficial: '{raw_s_token}' (={val_s})")

        # Caso B: Etiqueta directa "SECCION [VALOR]"
        if detections["seccion"] is None:
            m_sec = re.search(r'\bSECCION\s*[:\.]?\s*([A-Z0-9]+)', normalized_full)
            if m_sec:
                raw_s = m_sec.group(1).strip()
                val_s = TextNormalizer.normalize_section(raw_s)
                if val_s is not None:
                    detections["seccion"] = val_s
                    detections["seccion_nombre"] = raw_s
                    detections["evidencias"].append(f"Sección detectada: '{raw_s}' (={val_s})")

        # Caso C: Mención explícita de "SECCION COMERCIO" o "SECCION PRIMERA/PROPIEDAD"
        if detections["seccion"] is None:
            if re.search(r'\bSECCION\s+COMERCIO\b', normalized_full):
                detections["seccion"] = 2
                detections["seccion_nombre"] = "COMERCIO"
                detections["evidencias"].append("Detectada 'SECCION COMERCIO' (Sección 2)")
            elif re.search(r'\bSECCION\s+(?:PRIMERA|PROPIEDAD)\b', normalized_full):
                detections["seccion"] = 1
                detections["seccion_nombre"] = "PROPIEDAD"
                detections["evidencias"].append("Detectada 'SECCION PRIMERA' (Sección 1)")

        # -------------------------------------------------------------
        # 4. VOLUMEN: (ej. "VOLUMEN 1", "VOL. 2", "VOLUMEN II")
        # -------------------------------------------------------------
        for m_vol in re.finditer(r'\bVOL(?:UMEN)?\s*[:\.]?\s*(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+)', normalized_full):
            raw_v = m_vol.group(1).strip()
            val_v = TextNormalizer.parse_number_or_word(raw_v)
            if val_v is not None:
                detections["volumen"] = val_v
                detections["evidencias"].append(f"Volumen detectado: '{raw_v}' (={val_v})")
                break

        # -------------------------------------------------------------
        # 5. FRACCIONAMIENTO / PREDIO:
        # "normalmente en fraccionamiento lleva otro texto con el nombre del mismo"
        # -------------------------------------------------------------
        m_fracc = re.search(
            r'\b(?:FRACCIONAMIENTO|FRACCION D[EC]|PREDIO RUSTICO DENOMINADO)\s+([A-Z0-9\s,.-]{5,40})',
            normalized_full
        )
        if m_fracc:
            raw_fracc = m_fracc.group(1).strip()
            # Limpiar trailing chars
            raw_fracc = re.sub(r'[\r\n]+', ' ', raw_fracc)
            detections["fraccionamiento"] = raw_fracc
            detections["evidencias"].append(f"Fraccionamiento/Predio detectado: '{raw_fracc}'")

        # -------------------------------------------------------------
        # 6. AÑO: (ej. "DEL AÑO DOS MIL CINCO", "2005", "10-Noviembre-2004")
        # -------------------------------------------------------------
        # Dar prioridad a menciones de "AÑO DOS MIL..." o "DEL AÑO 20..."
        m_anio_ctx = re.search(r'\bDEL\s+ANO\s+([A-Z0-9\s]+?)(?:CONTENIENDO|\.|\,|$)', normalized_full)
        if m_anio_ctx:
            anio_val = TextNormalizer.parse_spanish_year(m_anio_ctx.group(1))
            if anio_val:
                detections["anio"] = anio_val
                detections["evidencias"].append(f"Año por contexto de sello: {anio_val}")

        if detections["anio"] is None:
            # Buscar año general en el texto
            for line in ocr_lines:
                y = TextNormalizer.parse_spanish_year(line["text"])
                if y and (1900 <= y <= 2030):
                    detections["anio"] = y
                    detections["evidencias"].append(f"Año encontrado: {y} en '{line['text']}'")
                    break

        return detections
