"""
Configuración global del motor OCR y reglas de negocio para sellos y carátulas de libros.
"""
from pathlib import Path

# Configuración de procesamiento de imágenes
# 200 DPI proporciona la escala ideal para DBNet/RapidOCR tanto en sellos grandes como en mecanografiados finos
PDF_RENDER_DPI = 200  
MAX_PAGES_PER_DOC = 3  # Analizar las primeras 3 páginas de cada documento

# Filtro CLAHE (Contrast Limited Adaptive Histogram Equalization) para texto opaco / pálido
CLAHE_CLIP_LIMIT = 3.0
CLAHE_TILE_GRID_SIZE = (8, 8)

# Posiciones en el nombre de la carpeta (separado por guiones bajos)
FOLDER_STRUCTURE = {
    0: "delegacion",      # Ignorar
    1: "seccion",         # 2da posición
    2: "libro",           # 3ra posición (puede ser numérico o 'AUXILIAR')
    3: "tomo",            # 4ta posición
    4: "volumen",         # 5ta posición
    5: "fraccionamiento", # 6ta posición (puede contener nombre textual de fraccionamiento/predio)
    6: "anio"             # 7ma posición
}

# Equivalencias semánticas de Secciones en Registros Públicos
SECCION_MAP = {
    "PROPIEDAD": 1,
    "PRIMERA": 1,
    "PRTMERA": 1,  # OCR en tipografías antiguas (I confundida con T)
    "PRHMERA": 1,  # OCR en sellos (I confundida con H)
    "HRMERA": 1,
    "CRMERA": 1,
    "DUMERA": 1,
    "PUMERA": 1,
    "PHMERA": 1,
    "PR1MERA": 1,
    "1": 1,
    "01": 1,
    "COMERCIO": 2,
    "SEGUNDA": 2,
    "SEGUNOA": 2,
    "2": 2,
    "02": 2,
    "HIPOTECAS": 3,
    "GRAVAMENES": 3,
    "TERCERA": 3,
    "3": 3,
    "03": 3,
    "SENTENCIAS": 4,
    "EMBARGOS": 4,
    "CUARTA": 4,
    "4": 4,
    "04": 4,
}

# Diccionario de números en palabras y ordinales en español
PALABRAS_NUMEROS = {
    "UN": 1,
    "UNO": 1,
    "UN0": 1,
    "UMO": 1,
    "PRIMER": 1,
    "PRIMERO": 1,
    "PRIMERA": 1,
    "PRTMERA": 1,
    "PRHMERA": 1,
    "HRMERA": 1,
    "DUMERA": 1,
    "PUMERA": 1,
    "1ERO": 1,
    "1ER": 1,
    "1RO": 1,
    "1RA": 1,
    "DOS": 2,
    "DGS": 2,  # OCR confunde O con G en sellos redondeados
    "D0S": 2,
    "DO5": 2,
    "OOS": 2,
    "SEGUNDO": 2,
    "SEGUNDA": 2,
    "2DO": 2,
    "2DA": 2,
    "TRES": 3,
    "TR3S": 3,
    "TERCER": 3,
    "TERCERO": 3,
    "TERCERA": 3,
    "3ER": 3,
    "3RO": 3,
    "3RA": 3,
    "CUATRO": 4,
    "CUARTO": 4,
    "CUARTA": 4,
    "4TO": 4,
    "4TA": 4,
    "CINCO": 5,
    "QUINTO": 5,
    "QUINTA": 5,
    "5TO": 5,
    "5TA": 5,
    "SEIS": 6,
    "SEXTO": 6,
    "SEXTA": 6,
    "6TO": 6,
    "6TA": 6,
    "SIETE": 7,
    "SEPTIMO": 7,
    "SEPTIMA": 7,
    "7MO": 7,
    "7MA": 7,
    "OCHO": 8,
    "OCTAVO": 8,
    "OCTAVA": 8,
    "8VO": 8,
    "8VA": 8,
    "NUEVE": 9,
    "NOVENO": 9,
    "NOVENA": 9,
    "9NO": 9,
    "9NA": 9,
    "DIEZ": 10,
    "DECIMO": 10,
    "DECIMA": 10,
    "10MO": 10,
    "10MA": 10,
    "ONCE": 11,
    "DOCE": 12,
    "TRECE": 13,
    "CATORCE": 14,
    "QUINCE": 15,
    "UNICO": 1,
    "UNICA": 1
}

# Números romanos comunes en sellos y tomos (incluyendo errores frecuentes de OCR como #I, II, lI)
ROMANOS_MAP = {
    "I": 1,
    "#I": 2,  # Típico cuando el sello une II y el primer trazo parece almohadilla/cruz
    "II": 2,
    "LI": 2,
    "IL": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
}
