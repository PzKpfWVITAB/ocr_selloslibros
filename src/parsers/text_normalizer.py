import re
import unicodedata
from typing import Optional, Union
from config import PALABRAS_NUMEROS, ROMANOS_MAP, SECCION_MAP

class TextNormalizer:
    """
    Normalizador semántico para convertir palabras, números romanos y fechas en texto
    a valores numéricos y nombres limpios.
    """

    @staticmethod
    def strip_accents(text: str) -> str:
        """Remueve acentos y diacríticos."""
        nfkd = unicodedata.normalize('NFKD', text)
        return "".join([c for c in nfkd if not unicodedata.combining(c)])

    @classmethod
    def clean_token(cls, token: str) -> str:
        """Limpia caracteres especiales alrededor de un token."""
        clean = cls.strip_accents(token).upper()
        clean = re.sub(r'[^A-Z0-9#]', '', clean)
        return clean

    @classmethod
    def parse_number_or_word(cls, text: str) -> Optional[int]:
        """
        Interpreta un token como número (arábigo, romano o en palabras en español).
        Ejemplos: '1' -> 1, 'UNO' -> 1, 'PRIMERO' -> 1, 'II' -> 2, '#I' -> 2, 'O2' -> 2, 'TRES' -> 3.
        """
        if not text:
            return None

        # Si viene con paréntesis, ej. '#I (DOS)' o '1 (UNO)' o '#L (DOS)'
        match_paren = re.search(r'\((.*?)\)', text)
        if match_paren:
            inside = cls.parse_number_or_word(match_paren.group(1))
            if inside is not None:
                return inside

        token = cls.clean_token(text)

        # 1. Corrección OCR: letra 'O' por número '0' al inicio (ej. 'O2' -> '02')
        m_o_num = re.match(r'^[O](\d+)$', token)
        if m_o_num:
            return int(m_o_num.group(1))

        # 2. Es dígito puro
        if token.isdigit():
            return int(token)

        # 3. Es número romano (o variante de sello como #L, #I)
        if token in ROMANOS_MAP:
            return ROMANOS_MAP[token]
        if token in ["#L", "L#", "#1", "#!", "IL", "LI"]:
            return 2

        # 4. Es palabra u ordinal en español
        if token in PALABRAS_NUMEROS:
            return PALABRAS_NUMEROS[token]

        # 5. Caso compuesto (ej. '1ERO', '2DO', '3ER')
        m_num = re.match(r'^(\d+)(?:ER|ERO|RO|RA|DO|DA|TO|TA|VO|VA|MO|MA)?$', token)
        if m_num:
            return int(m_num.group(1))

        return None

    @classmethod
    def parse_spanish_year(cls, text: str) -> Optional[int]:
        """
        Detecta años tanto en dígitos (ej. '2005') como en texto ('DOS MIL CINCO', 'DOSMIL ONCO').
        """
        clean = cls.strip_accents(text).upper()

        # 1. Buscar año en dígitos de 4 cifras (1900-2099)
        m_year = re.search(r'\b(19\d{2}|20\d{2})\b', clean)
        if m_year:
            return int(m_year.group(1))

        # 2. Tolerancia a OCR para 'DOS MIL X' (ej. 'DOS MIL CINCO', 'DOSMLONCO', 'DOS ML CNCO')
        # Casos compuestos pegados típicos de sellos (DOSMLONCO, DOSMILONCO, DOSMCN)
        if re.search(r'\bDOSM[IL]*(?:ONCO|CNCO|CINCO|CN)\b', clean):
            return 2005

        m_dosmil = re.search(r'\b(?:DOS\s*M[IL]*|DOSMIL|DOSM)\s*(.*)', clean)
        if m_dosmil:
            resto = m_dosmil.group(1).strip()
            # Si inmediatamente hay dígitos (ej. 'DOS MIL 5')
            m_dig = re.match(r'^(\d{1,4})', resto)
            if m_dig:
                val = int(m_dig.group(1))
                return val if val >= 1900 else 2000 + val

            unidades = [
                ("CINCO", 5), ("ONCO", 5), ("CNCO", 5), ("CN", 5),
                ("CUATRO", 4), ("TRES", 3), ("SEIS", 6),
                ("SIETE", 7), ("OCHO", 8), ("NUEVE", 9),
                ("DIEZ", 10), ("ONCE", 11), ("DOCE", 12),
                ("TRECE", 13), ("CATORCE", 14), ("QUINCE", 15),
                ("DOS", 2), ("UNO", 1), ("UN", 1), ("CERO", 0)
            ]
            resto_tokens = re.split(r'[^A-Z0-9]', resto)
            for palabra, val in unidades:
                if palabra in resto_tokens or resto.startswith(palabra):
                    return 2000 + val

            return 2000

        # 3. Siglo XX: 'MIL NOVECIENTOS ...'
        if re.search(r'MIL\s+NOVEC[A-Z]*TOS', clean):
            decadas = [
                ("NOVENTA", 1990), ("OCHENTA", 1980), ("SETENTA", 1970),
                ("SESENTA", 1960), ("CINCUENTA", 1950)
            ]
            for dec_word, base in decadas:
                if dec_word in clean:
                    resto_dec = clean.split(dec_word, 1)[1]
                    tokens_dec = re.split(r'[^A-Z0-9]+', resto_dec)
                    unidades = [
                        ("CINCO", 5), ("ONCO", 5), ("CUATRO", 4), ("TRES", 3),
                        ("SEIS", 6), ("SIETE", 7), ("STETE", 7), ("SIETB", 7),
                        ("OCHO", 8), ("NUEVE", 9), ("DOS", 2), ("UNO", 1), ("UN", 1)
                    ]
                    for u_word, u_val in unidades:
                        if u_word in tokens_dec:
                            return base + u_val
                    return base

        return None

    @classmethod
    def normalize_section(cls, text: str) -> Optional[int]:
        """
        Normaliza el nombre o número de sección (ej. 'COMERCIO' -> 2, 'PRIMERA' -> 1, 'PRTMERA' -> 1, '01' -> 1).
        Incluye tolerancia a errores comunes de OCR en documentos mecanografiados.
        """
        token = cls.clean_token(text)
        if token in SECCION_MAP:
            return SECCION_MAP[token]

        # Tolerancia difusa a typos de 'PRIMERA' (PRTMERA, PRHMERA, PUMERA, DUMERA)
        if len(token) >= 5:
            # Distancia simple con PRIMERA
            target = "PRIMERA"
            diffs = sum(1 for a, b in zip(token, target) if a != b) + abs(len(token) - len(target))
            if diffs <= 2:
                return 1

            # Distancia con SEGUNDA
            target_seg = "SEGUNDA"
            diffs_seg = sum(1 for a, b in zip(token, target_seg) if a != b) + abs(len(token) - len(target_seg))
            if diffs_seg <= 2:
                return 2

            # Distancia con TERCERA
            target_ter = "TERCERA"
            diffs_ter = sum(1 for a, b in zip(token, target_ter) if a != b) + abs(len(token) - len(target_ter))
            if diffs_ter <= 2:
                return 3

        parsed_num = cls.parse_number_or_word(token)
        return parsed_num
