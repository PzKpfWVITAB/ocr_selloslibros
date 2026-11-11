import json
import base64
import re
import urllib.request
import urllib.error
import cv2
import numpy as np
from typing import List, Dict, Any, Optional, Tuple

from src.parsers.text_normalizer import TextNormalizer

# System prompt extenso (>1,024 tokens) diseñado específicamente para activar el
# breakpoint de Prompt Caching de Anthropic (90% de descuento en lecturas subsecuentes)
SYSTEM_PROMPT_REGISTRAL = """Eres un Auditor Senior Experto en Digitalizaciones y Libros del Registro Público de la Propiedad y de Comercio del Estado de Chiapas (Tuxtla Gutiérrez y distritos judiciales).
Tu única función es analizar la imagen de la Carátula / Acta de Apertura de un libro registral y extraer con precisión absoluta los siguientes 6 metadatos para construir el código de 7 posiciones:
[Delegación]_[Sección]_[Libro]_[Tomo]_[Volumen]_[Fraccionamiento]_[Año]

REGLAS ESTRICTAS DE EXTRACCIÓN CAMPO POR CAMPO:

1. "seccion" (ÚNICAMENTE entero del 1 al 5, o null):
   - LAS SECCIONES NO PUEDEN LLEVAR TEXTO ALFANUMÉRICO EN LA POSICIÓN DE SECCIÓN.
   - Solo asigna un número entero (1, 2, 3, 4 o 5) cuando el acta de apertura mencione explícitamente una sección numerada:
     * "SECCIÓN PRIMERA" -> 1
     * "SECCIÓN SEGUNDA" -> 2
     * "SECCIÓN TERCERA" -> 3
     * "SECCIÓN CUARTA" -> 4
     * "SECCIÓN QUINTA" -> 5
   - Si el acta de apertura dice "SECCION DE CREDITO AGRICOLA", "SECCION DE ACTOS Y CONTRATOS", "LIBRO AUXILIAR", etc. (sin decir Sección Primera, Segunda, Tercera, Cuarta o Quinta), "seccion" DEBE SER null (que equivale a 00), y ese nombre alfanumérico ("CREDITO_AGRICOLA", "ACTOS_Y_CONTRATOS", "AUXILIAR") DEBE colocarse en el campo "tomo".
   - REGLA CRÍTICA DE FALSOS POSITIVOS:
     La institución se llama "REGISTRO PÚBLICO DE LA PROPIEDAD Y DE COMERCIO". NUNCA asignes seccion=1 ni seccion=2 solo por el nombre de la institución.

2. "libro" (entero o "AUXILIAR" o null):
   - Es el número de Libro autorizado (puede venir en arábigo "2", "02", "3", "100", en romano "I", "II", "III", o en palabra "UNO", "DOS", "SEGUNDO", "TERCERO").
   - Conviértelo siempre a número entero (ej. "LIBRO NUMERO 2 DOS" -> 2, "LIBRO SEGUNDO AUXILIAR" -> 2, "LIBRO NUMERO 02 VOLUMEN I AUXILIAR" -> 2, "LIBRO 100 AUXILIAR" -> 100).
   - REGLA DE LIBRO CON NÚMERO + AUXILIAR:
     * Si el documento tiene un número de libro Y además dice "AUXILIAR" (ejemplo: "LIBRO SEGUNDO AUXILIAR DEL INDICE DE COLONIAS" o "LIBRO NUMERO 02 VOLUMEN I AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS" o "LIBRO NUMERO 100 AUXILIAR"), entonces:
       - "libro" = el número entero del libro (ej. 2, 100).
       - "tomo" = "AUXILIAR" (porque AUXILIAR ocupa la posición de Tomo cuando ya existe un número de libro).
     * Solo coloca "libro": "AUXILIAR" si el documento dice "LIBRO AUXILIAR" sin ningún número de libro.

3. "tomo" (entero, o cadena alfanumérica como "AUXILIAR", "CREDITO_AGRICOLA", "ACTOS_Y_CONTRATOS", o null):
   - Caso A (Tomo numérico normal): Si el documento menciona un Tomo numérico (ej. "TOMO I", "TOMO 2", "TOMO II (DOS)"), devuelve el número entero (1, 2).
   - Caso B (Libro AUXILIAR con número de libro):
     * Si el documento dice "LIBRO [N] AUXILIAR..." (ej. "LIBRO 1 UNO AUXILIAR PARA LLEVAR EL ORIGINAL DE ACTOS Y CONTRATOS", "LIBRO SEGUNDO AUXILIAR DEL INDICE DE COLONIAS", "LIBRO NUMERO 02 VOLUMEN I AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS"):
       - "libro" = N (ej. 1, 2)
       - "tomo" = "AUXILIAR"
       - Y si además menciona la materia/categoría del libro auxiliar (como "ACTOS Y CONTRATOS", "CREDITO AGRICOLA", "COLONIAS", "FRACCIONAMIENTOS Y COLONIAS"), coloca esa materia en "fraccionamiento" (ej. "ACTOS_Y_CONTRATOS", "COLONIAS", "FRACCIONAMIENTOS_Y_COLONIAS").
   - Caso C (Sección o categoría alfanumérica cuando el libro NO es AUXILIAR):
     * Como la posición de Sección solo acepta números, cuando el libro NO es Auxiliar y pertenece a una sección alfanumérica que no es fraccionamiento, colócala en "tomo":
       - "LIBRO NUMERO 2 DOS PARA LLEVAR EL ORIGINAL DE LA SECCION DE CREDITO AGRICOLA" -> "seccion": null, "libro": 2, "tomo": "CREDITO_AGRICOLA", "fraccionamiento": null
       - "LIBRO NUMERO 2 DOS PARA LLEVAR EL ORIGINAL DE LA SECCION DE ACTOS Y CONTRATOS" -> "seccion": null, "libro": 2, "tomo": "ACTOS_Y_CONTRATOS", "fraccionamiento": null
   - Si no hay tomo numérico ni categoría alfanumérica para la posición de tomo, devuelve null.

4. "volumen" (entero o null):
   - Si el documento especifica un número de Volumen (ej. "VOLUMEN 1", "VOLUMEN I", "VOL. 2"), conviértelo a número entero (1, 2).
   - Si no se menciona Volumen, devuelve null.

5. "fraccionamiento" (cadena en mayúsculas con guiones bajos, o null):
   - Se utiliza en dos situaciones:
     a) Cuando el libro está dedicado a Fraccionamientos, Colonias o Predios específicos:
        * "INDICE DE COLONIAS" o "COLONIAS" -> "COLONIAS"
        * "FRACCIONAMIENTOS Y COLONIAS" -> "FRACCIONAMIENTOS_Y_COLONIAS"
        * "FRACCIONAMIENTO SOLIDARIDAD" -> "FRACCIONAMIENTO_SOLIDARIDAD"
        * "FRACCIONAMIENTO PATRIA NUEVA" -> "FRACCIONAMIENTO_PATRIA_NUEVA"
        * "FRACCIONAMIENTO RESIDENCIAL HACIENDA" -> "FRACCIONAMIENTO_RESIDENCIAL_HACIENDA"
        * "FRACCIONAMIENTO LOMAS DEL SUR" -> "FRACCIONAMIENTO_LOMAS_DEL_SUR"
     b) Cuando la posición de "tomo" ya está ocupada por "AUXILIAR" y el libro auxiliar especifica una materia como "ACTOS Y CONTRATOS" o "CREDITO AGRICOLA":
        * Ejemplo: "LIBRO 1 UNO AUXILIAR PARA LLEVAR EL ORIGINAL DE ACTOS Y CONTRATOS" -> "libro": 1, "tomo": "AUXILIAR", "fraccionamiento": "ACTOS_Y_CONTRATOS".
   - Si el libro NO es AUXILIAR, entonces "CREDITO_AGRICOLA" y "ACTOS_Y_CONTRATOS" van en "tomo" y "fraccionamiento" debe ser null.

6. "anio" (entero de 4 dígitos entre 1900 y 2035, o null):
   - Es el año para el cual se autoriza el libro o en que se fecha el acta de apertura (ej. "PARA EL AÑO DE 1986 MIL NOVECIENTOS OCHENTA Y SEIS" -> 1986, "26 DE NOVIEMBRE DE MIL NOVECIENTOS OCHENTA Y TRES" -> 1983).
   - Conviértelo siempre a entero de 4 dígitos.

EJEMPLOS DE SALIDA CORRECTA:
Ejemplo 1 ("AUTORIZA EL PRESENTE LIBRO NUMERO 2 DOS PARA LLEVAR EL ORIGINAL DE LA SECCION DE CREDITO AGRICOLA ... PARA EL AÑO DE 1986"):
{
  "seccion": null,
  "libro": 2,
  "tomo": "CREDITO_AGRICOLA",
  "volumen": null,
  "fraccionamiento": null,
  "anio": 1986,
  "es_auxiliar": false,
  "evidencias": [
    "Libro detectado: 'LIBRO NUMERO 2 DOS' -> libro=2",
    "Sección alfanumérica colocada en posición de Tomo: 'SECCION DE CREDITO AGRICOLA' -> tomo='CREDITO_AGRICOLA'",
    "Año detectado: 'PARA EL AÑO DE 1986' -> anio=1986"
  ]
}

Ejemplo 2 ("AUTORIZA EL PRESENTE LIBRO NUMERO 02 VOLUMEN I AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS ... 22 DE JUNIO DE 1990"):
{
  "seccion": null,
  "libro": 2,
  "tomo": "AUXILIAR",
  "volumen": 1,
  "fraccionamiento": "FRACCIONAMIENTOS_Y_COLONIAS",
  "anio": 1990,
  "es_auxiliar": true,
  "evidencias": [
    "Libro detectado: 'LIBRO NUMERO 02' -> libro=2",
    "Categoría Auxiliar en posición de Tomo: 'AUXILIAR' -> tomo='AUXILIAR'",
    "Volumen detectado: 'VOLUMEN I' -> volumen=1",
    "Fraccionamiento detectado: 'FRACCIONAMIENTOS Y COLONIAS'",
    "Año detectado: '1990'"
  ]
}

Responde ÚNICAMENTE con el objeto JSON válido, sin texto adicional fuera del JSON.
"""

MODEL_FALLBACK_CHAIN = [
    "claude-sonnet-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet-20241022",
]

STRUCTURED_OUTPUT_TOOL: Dict[str, Any] = {
    "name": "registrar_datos_caratula",
    "description": (
        "Transcribe literalmente el texto del sello/acta de apertura y extrae sus datos. "
        "REGLA ESTRICTA: Si la palabra 'AUXILIAR' NO aparece explícitamente escrita en el documento, "
        "'es_auxiliar' DEBE ser false y 'tomo' NUNCA puede ser 'AUXILIAR'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "transcripcion_literal": {
                "type": "string",
                "description": (
                    "Transcripción literal palabra por palabra del texto visible en el sello o acta de apertura "
                    "de la carátula, sin agregar comentarios ni interpretaciones."
                ),
            },
            "datos_estructurados": {
                "type": "object",
                "description": "Valores estructurados exactos de las posiciones 2 a 7 del código del libro.",
                "properties": {
                    "seccion": {
                        "type": ["integer", "null"],
                        "description": "Solo entero 1..5 si dice SECCIÓN PRIMERA..QUINTA. Si no tiene sección numerada o es sección alfanumérica (ej. Crédito Agrícola, Actos y Contratos), DEBE ser null.",
                    },
                    "libro": {
                        "type": ["integer", "string", "null"],
                        "description": "Número entero del libro (ej. 1, 2, 100). Solo usar 'AUXILIAR' si dice LIBRO AUXILIAR sin número.",
                    },
                    "tomo": {
                        "type": ["integer", "string", "null"],
                        "description": (
                            "Número entero del Tomo (ej. 1, 2, 3 para TOMO I, II, III), o 'AUXILIAR' ÚNICAMENTE cuando el documento dice explícitamente la palabra AUXILIAR, "
                            "o 'CREDITO_AGRICOLA' / 'ACTOS_Y_CONTRATOS' cuando el libro NO es Auxiliar y pertenece a esa sección alfanumérica. "
                            "Si no aplica ninguno, null."
                        ),
                    },
                    "volumen": {
                        "type": ["integer", "null"],
                        "description": "Número entero de Volumen si se menciona explícitamente VOLUMEN (ej. 1, 2), o null.",
                    },
                    "fraccionamiento": {
                        "type": ["string", "null"],
                        "description": (
                            "Nombre del fraccionamiento/colonia en mayúsculas con guiones bajos (ej. 'COLONIAS', 'FRACCIONAMIENTOS_Y_COLONIAS'), "
                            "o la materia ('ACTOS_Y_CONTRATOS', 'CREDITO_AGRICOLA') cuando 'tomo' ya está ocupado por 'AUXILIAR'. Si no aplica, null."
                        ),
                    },
                    "anio": {
                        "type": ["integer", "null"],
                        "description": "Año de 4 dígitos (1900..2035) del acta de apertura, o null.",
                    },
                    "es_auxiliar": {
                        "type": "boolean",
                        "description": "true ÚNICAMENTE si la palabra 'AUXILIAR' aparece explícitamente escrita en el texto del acta; false en cualquier otro caso.",
                    },
                },
                "required": [
                    "seccion",
                    "libro",
                    "tomo",
                    "volumen",
                    "fraccionamiento",
                    "anio",
                    "es_auxiliar",
                ],
            },
            "texto_descriptivo": {
                "type": "string",
                "description": "Resumen descriptivo claro en español de lo que autoriza el sello o acta de apertura.",
            },
            "evidencias": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Citas textuales literales entre comillas simples ('...') extraídas de la imagen que respaldan cada campo detectado. "
                    "PROHIBIDO incluir notas sobre datos que NO aparecen (nunca escribas 'sin especificar auxiliar' ni 'no se menciona auxiliar')."
                ),
            },
        },
        "required": ["transcripcion_literal", "datos_estructurados", "texto_descriptivo", "evidencias"],
    },
}


class ClaudeVisionEngine:
    """
    Cliente para la API de Claude (Anthropic Messages API) con soporte nativo para:
    - Prompt Caching ('cache_control': {'type': 'ephemeral'}) para ahorrar hasta 90% en tokens de sistema.
    - Thinking desactivado ('thinking': {'type': 'disabled'}) y max_tokens=1536 para evitar truncamiento
      y ahorrar tokens de salida en claude-sonnet-5.
    """

    API_URL = "https://api.anthropic.com/v1/messages"
    _resolved_model: Optional[str] = None

    @staticmethod
    def encode_image_optimized(
        img_bgr: np.ndarray,
        max_dim: int = 1400,
        jpeg_quality: int = 82
    ) -> str:
        h, w = img_bgr.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / float(max(h, w))
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img_bgr = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if not ok:
            raise RuntimeError("No se pudo codificar la página a JPEG.")
        return base64.b64encode(buf.tobytes()).decode("ascii")

    @classmethod
    def analyze_document_pages(
        cls,
        pages_bgr: List[Tuple[int, np.ndarray]],
        api_key: str,
        model: str = "claude-sonnet-5",
        use_prompt_cache: bool = True,
        max_dim: int = 1400,
        jpeg_quality: int = 82,
        doc_role: str = "CARATULA"
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not api_key or not api_key.strip():
            raise ValueError("No se ha configurado una API Key de Claude. Ábrela en '⚙ Configuración API'.")

        content_blocks: List[Dict[str, Any]] = []
        for page_num, img_bgr in pages_bgr:
            b64_jpg = cls.encode_image_optimized(img_bgr, max_dim=max_dim, jpeg_quality=jpeg_quality)
            content_blocks.append({
                "type": "text",
                "text": f"--- Documento: {doc_role} | Página {page_num} ---"
            })
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64_jpg
                }
            })

        content_blocks.append({
            "type": "text",
            "text": (
                "Extrae los datos registrales (seccion, libro, tomo, volumen, fraccionamiento, anio, es_auxiliar, evidencias) "
                "aplicando las reglas de posición de Tomo para secciones alfanuméricas (CREDITO_AGRICOLA, ACTOS_Y_CONTRATOS, AUXILIAR) "
                "y responde únicamente con el objeto JSON."
            )
        })

        models_to_try = []
        if cls._resolved_model and cls._resolved_model == model:
            models_to_try.append(cls._resolved_model)
        else:
            models_to_try.append(model)
            for fb in MODEL_FALLBACK_CHAIN:
                if fb not in models_to_try:
                    models_to_try.append(fb)

        last_error = None
        for candidate_model in models_to_try:
            try:
                raw_resp, usage = cls._send_messages_request(
                    api_key=api_key.strip(),
                    model=candidate_model,
                    content_blocks=content_blocks,
                    use_prompt_cache=use_prompt_cache
                )
                cls._resolved_model = candidate_model
                parsed = cls._parse_claude_json(raw_resp)
                return parsed, usage
            except urllib.error.HTTPError as http_err:
                err_body = ""
                try:
                    err_body = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                if http_err.code == 404 or "not_found_error" in err_body:
                    last_error = f"Modelo '{candidate_model}' no disponible ({err_body})"
                    continue
                elif http_err.code == 401:
                    raise RuntimeError(
                        "Error 401 (No autorizado): La API Key de Claude es inválida o ha expirado. "
                        "Puedes actualizarla en el botón '⚙ API Key / Caché'."
                    )
                else:
                    raise RuntimeError(f"Error HTTP {http_err.code} de Anthropic API: {err_body}")
            except Exception as exc:
                last_error = str(exc)
                break

        raise RuntimeError(f"Error al consultar Claude API: {last_error}")

    @classmethod
    def _send_messages_request(
        cls,
        api_key: str,
        model: str,
        content_blocks: List[Dict[str, Any]],
        use_prompt_cache: bool = True
    ) -> Tuple[str, Dict[str, Any]]:
        system_block: Dict[str, Any] = {
            "type": "text",
            "text": SYSTEM_PROMPT_REGISTRAL
        }
        if use_prompt_cache:
            system_block["cache_control"] = {"type": "ephemeral"}

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": 1536,
            "thinking": {"type": "disabled"},
            "system": [system_block],
            "tools": [STRUCTURED_OUTPUT_TOOL],
            "tool_choice": {"type": "tool", "name": "registrar_datos_caratula"},
            "messages": [
                {
                    "role": "user",
                    "content": content_blocks
                }
            ]
        }

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31"
        }

        try:
            req = urllib.request.Request(
                cls.API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            # Si un modelo anterior no soporta el campo 'thinking', reintentar sin 'thinking'
            err_text = ""
            try:
                err_text = err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if err.code == 400 and "thinking" in err_text:
                payload.pop("thinking", None)
                req2 = urllib.request.Request(
                    cls.API_URL,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req2, timeout=60) as resp2:
                    resp_data = json.loads(resp2.read().decode("utf-8"))
            else:
                raise RuntimeError(f"Error HTTP {err.code} de Anthropic API: {err_text}")

        text_parts = []
        for block in resp_data.get("content", []):
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                text_parts.append(json.dumps(block["input"], ensure_ascii=False))
            elif block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        usage = resp_data.get("usage", {})
        usage["model_used"] = resp_data.get("model", model)
        return "\n".join(text_parts), usage

    @staticmethod
    def _clean_alnum_label(val: str) -> str:
        s = TextNormalizer.strip_accents(val.strip().upper())
        s = re.sub(r"^SECCION\s+DE\s+", "", s)
        s = re.sub(r"^SECCION\s+", "", s)
        s = re.sub(r"^INDICE\s+DE\s+", "", s)
        s = re.sub(r"^INDICE_DE_", "", s)
        if "ACTOS" in s and "CONTRATOS" in s:
            return "ACTOS_Y_CONTRATOS"
        if "CREDITO" in s and "AGRICOLA" in s:
            return "CREDITO_AGRICOLA"
        s = re.sub(r"[^A-Z0-9]+", "_", s).strip("_")
        return s

    @staticmethod
    def _is_negative_or_absence_line(line: str) -> bool:
        """
        Detecta si una línea de evidencia es un comentario negativo o de ausencia
        (ej. 'No se menciona AUXILIAR', 'Distrito Judicial mencionado sin especificar fraccionamiento o auxiliar',
        'No se especifica Sección, Tomo ni Volumen').
        """
        line_up = TextNormalizer.strip_accents(str(line).strip().upper())
        line_clean = re.sub(r"^[•\-\*\s]+", "", line_up)
        # Quitar el contenido dentro de comillas simples (que son citas literales del documento)
        outside_quotes = re.sub(r"'[^']*'", " ", line_clean)
        if re.search(
            r"\b(?:NO|SIN|NI|NINGUN|NINGUNA|NINGUNO|TAMPOCO|OMITE|OMITIENDO|CARECE|FALTA|AUSENCIA|INEXISTENTE)\b",
            outside_quotes
        ):
            return True
        return False

    @classmethod
    def _extract_positive_evidencias_text(cls, evidencias: List[Any]) -> str:
        """
        Une únicamente las líneas afirmativas de evidencias, descartando cualquier oración
        con negaciones o ausencias ('no...', 'sin...', 'ni...', 'ningún...', etc.).
        """
        positive_lines: List[str] = []
        for item in evidencias:
            s_item = str(item).strip()
            if not s_item or s_item.startswith(("⚡", "🤖", "📄", "📝 Resumen:")):
                continue
            if cls._is_negative_or_absence_line(s_item):
                continue
            line_up = TextNormalizer.strip_accents(s_item.upper())
            line_clean = re.sub(r"^[•\-\*\s]+", "", line_up)
            positive_lines.append(line_clean)
        return " | ".join(positive_lines)

    @classmethod
    def _has_explicit_auxiliar_in_document(
        cls,
        evidencias: List[Any],
        transcripcion_literal: str = ""
    ) -> bool:
        """
        Verifica determinísticamente si la palabra 'AUXILIAR' aparece explícitamente
        en el texto literal del documento (citas entre comillas '...' o transcripción literal),
        ignorando cualquier comentario negativo como 'sin especificar fraccionamiento o auxiliar'
        o 'No se menciona AUXILIAR'.
        """
        if transcripcion_literal and transcripcion_literal.strip():
            trans_up = TextNormalizer.strip_accents(transcripcion_literal.upper())
            # Ignorar 'REGISTRADOR AUXILIAR' si fuera firma/cargo
            trans_clean = re.sub(r"\bREGISTRADOR\s+AUXILIAR\b", "", trans_up)
            if re.search(r"\bAUXILIAR\b", trans_clean) and not re.search(r"\b(?:NO|SIN|NI)\s+(?:ES\s+|ESPECIFICAR\s+|MENCIONA\s+)?AUXILIAR\b", trans_clean):
                return True

        for item in evidencias:
            s_item = str(item).strip()
            if not s_item or s_item.startswith(("⚡", "🤖", "📄", "📝 Resumen:")):
                continue
            if cls._is_negative_or_absence_line(s_item):
                continue

            line_up = TextNormalizer.strip_accents(s_item.upper())
            # 1. Si la línea tiene citas literales entre comillas simples antes de '->', revisar dentro de las comillas
            before_arrow = line_up.split("->")[0]
            quotes = re.findall(r"'([^']+)'", before_arrow)
            if quotes:
                for q in quotes:
                    q_clean = re.sub(r"\bREGISTRADOR\s+AUXILIAR\b", "", q)
                    if re.search(r"\bAUXILIAR\b", q_clean):
                        return True
            else:
                # 2. Línea afirmativa sin comillas (ej. en pruebas unitarias 'Sello: LIBRO 1 UNO AUXILIAR...')
                line_no_reg = re.sub(r"\bREGISTRADOR\s+AUXILIAR\b", "", line_up)
                if re.search(r"\bAUXILIAR\b", line_no_reg):
                    return True

        return False

    @classmethod
    def sanitize_cached_doc(cls, doc: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """
        Verifica determinísticamente una detección (de caché o recién salida de Claude)
        contra las citas literales del documento:
        Si NO aparece explícitamente la palabra 'AUXILIAR' en el texto literal del documento,
        el documento NO es auxiliar (restaura el Tomo numérico original y limpia Volumen).
        """
        if not isinstance(doc, dict):
            return doc, False

        evidencias = doc.get("evidencias", [])
        if not isinstance(evidencias, list):
            return doc, False

        transcripcion_literal = str(doc.get("transcripcion_literal") or "").strip()
        has_any_text_evidence = bool(transcripcion_literal) or any(
            not str(ev).strip().startswith(("⚡", "🤖", "📄")) for ev in evidencias
        )
        if not has_any_text_evidence:
            return doc, False

        has_positive_aux = cls._has_explicit_auxiliar_in_document(evidencias, transcripcion_literal)

        if (doc.get("tomo") == "AUXILIAR" or doc.get("es_auxiliar") or doc.get("libro") == "AUXILIAR") and not has_positive_aux:
            repaired = dict(doc)
            repaired["es_auxiliar"] = False

            # Recuperar el tomo real desde las evidencias afirmativas o desde el volumen desplazado
            restored_tomo: Optional[int] = None
            for ev in evidencias:
                s_ev = str(ev).strip()
                if cls._is_negative_or_absence_line(s_ev):
                    continue
                ev_up = TextNormalizer.strip_accents(s_ev.upper())

                # Caso A: '-> tomo=3'
                m_tom = re.search(r"TOMO\s*=\s*(\d+)", ev_up)
                if m_tom:
                    restored_tomo = int(m_tom.group(1))
                    break

                # Caso B: 'TOMO III', 'TOMO UNO', 'TOMO 2', 'TOMO II (DOS)', etc.
                m_tom_tok = re.search(
                    r"\bTOMO\s+(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+(?:\s*\([A-Z0-9]+\))?)",
                    ev_up
                )
                if m_tom_tok:
                    parsed_t = TextNormalizer.parse_number_or_word(m_tom_tok.group(1))
                    if parsed_t is not None:
                        restored_tomo = parsed_t
                        break

            # Si también existe transcripcion_literal y aún no se halló tomo, buscar ahí
            if restored_tomo is None and transcripcion_literal:
                trans_up = TextNormalizer.strip_accents(transcripcion_literal.upper())
                m_tom_tr = re.search(
                    r"\bTOMO\s+(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+(?:\s*\([A-Z0-9]+\))?)",
                    trans_up
                )
                if m_tom_tr:
                    restored_tomo = TextNormalizer.parse_number_or_word(m_tom_tr.group(1))

            has_positive_vol = any(
                "VOLUMEN" in TextNormalizer.strip_accents(str(ev).upper())
                and not cls._is_negative_or_absence_line(str(ev))
                for ev in evidencias
            )

            if restored_tomo is not None:
                repaired["tomo"] = restored_tomo
                if not has_positive_vol and repaired.get("volumen") == restored_tomo:
                    repaired["volumen"] = None
            elif isinstance(repaired.get("volumen"), int) and not has_positive_vol:
                repaired["tomo"] = repaired["volumen"]
                repaired["volumen"] = None
            elif repaired.get("tomo") == "AUXILIAR":
                repaired["tomo"] = None

            if repaired.get("libro") == "AUXILIAR":
                repaired["libro"] = None

            # Limpiar de evidencias los comentarios negativos sobre auxiliar que causaban confusión
            cleaned_evidencias = [
                ev for ev in evidencias
                if not (
                    cls._is_negative_or_absence_line(str(ev))
                    and "AUXILIAR" in TextNormalizer.strip_accents(str(ev).upper())
                )
            ]
            if cleaned_evidencias:
                repaired["evidencias"] = cleaned_evidencias

            return repaired, True

        return doc, False

    @classmethod
    def _parse_claude_json(cls, raw_text: str) -> Dict[str, Any]:
        """Extrae y normaliza el JSON devuelto por Claude aplicando las reglas de negocio."""
        clean = raw_text.strip()
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end != -1 and end > start:
            clean = clean[start:end + 1]

        try:
            data = json.loads(clean)
        except Exception:
            data = {}

        evidencias = data.get("evidencias", [])
        if not isinstance(evidencias, list):
            evidencias = [str(evidencias)]

        texto_descriptivo = str(data.get("texto_descriptivo") or "").strip()
        if texto_descriptivo:
            resumen_item = f"📝 Resumen: {texto_descriptivo}"
            if not any(str(ev).startswith("📝 Resumen:") for ev in evidencias):
                evidencias = [resumen_item] + list(evidencias)

        has_structured = isinstance(data.get("datos_estructurados"), dict)
        fields_src: Dict[str, Any] = dict(data["datos_estructurados"]) if has_structured else dict(data)
        evidencias_joined = "" if has_structured else cls._extract_positive_evidencias_text(evidencias)

        # 1. Sección: SOLO puede ser numérica (1..5) o None. Si viene texto alfanumérico, va a Tomo.
        raw_sec = fields_src.get("seccion")
        alnum_from_sec: Optional[str] = None
        if isinstance(raw_sec, int):
            sec_val = raw_sec if 1 <= raw_sec <= 9 else None
        elif isinstance(raw_sec, str) and raw_sec.strip():
            norm_s = TextNormalizer.normalize_section(raw_sec)
            if norm_s is not None:
                sec_val = norm_s
            else:
                sec_val = None
                cleaned_s = cls._clean_alnum_label(raw_sec)
                if cleaned_s and cleaned_s not in ["NULL", "NONE", "0", "00"]:
                    alnum_from_sec = cleaned_s
        else:
            sec_val = None

        # 2. Libro y detección de AUXILIAR
        es_aux = bool(fields_src.get("es_auxiliar", False))
        raw_lib = fields_src.get("libro")
        lib_val: Optional[Any] = None
        lib_had_auxiliar = False

        if isinstance(raw_lib, int):
            lib_val = raw_lib
        elif isinstance(raw_lib, str) and raw_lib.strip():
            up_lib = raw_lib.strip().upper()
            if "AUXILIAR" in up_lib:
                lib_had_auxiliar = True
                # Verificar si además de AUXILIAR venía un número en raw_lib (ej. "2 AUXILIAR")
                num_in_lib = TextNormalizer.parse_number_or_word(re.sub(r"AUXILIAR", "", up_lib).strip())
                if num_in_lib is not None:
                    lib_val = num_in_lib
                else:
                    lib_val = "AUXILIAR"
            else:
                parsed_l = TextNormalizer.parse_number_or_word(up_lib)
                lib_val = parsed_l if parsed_l is not None else up_lib

        # Si Claude puso libro="AUXILIAR", verificar en las evidencias si había un "LIBRO NUMERO X" o "LIBRO SEGUNDO"
        if lib_val == "AUXILIAR" and evidencias_joined:
            m_lib = re.search(
                r"LIBRO\s+(?:NUMERO\s+)?(\d+|PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|UNO|DOS|TRES|CUATRO|CINCO)\b",
                evidencias_joined
            )
            if m_lib:
                num_ev = TextNormalizer.parse_number_or_word(m_lib.group(1))
                if num_ev is not None:
                    lib_val = num_ev
                    lib_had_auxiliar = True

        # 3. Tomo (numérico o categoría alfanumérica como CREDITO_AGRICOLA, ACTOS_Y_CONTRATOS, AUXILIAR)
        raw_tom = fields_src.get("tomo")
        tom_val: Optional[Any] = None
        displaced_alnum_from_tomo: Optional[str] = None
        if isinstance(raw_tom, int):
            tom_val = raw_tom
        elif isinstance(raw_tom, str) and raw_tom.strip():
            up_tom = raw_tom.strip().upper()
            if up_tom not in ["NULL", "NONE", "0", "00"]:
                parsed_t = TextNormalizer.parse_number_or_word(up_tom)
                if parsed_t is not None and up_tom.isdigit():
                    tom_val = parsed_t
                elif parsed_t is not None and not any(w in up_tom for w in ["CREDITO", "ACTOS", "AUXILIAR", "CONTRATOS", "AGRICOLA"]):
                    tom_val = parsed_t
                else:
                    tom_val = cls._clean_alnum_label(up_tom)

        # REGLA FUNDAMENTAL DE AUXILIAR:
        # El documento ÚNICAMENTE es AUXILIAR si la palabra 'AUXILIAR' aparece explícitamente escrita
        # de forma afirmativa en el texto literal del sello o acta de apertura.
        # Si NO aparece, bajo ninguna circunstancia se clasifica como AUXILIAR, evitando confusiones
        # de Claude por notas de ausencia ('sin especificar auxiliar') o alucinaciones.
        transcripcion_literal = str(data.get("transcripcion_literal") or "").strip()
        has_real_auxiliar = cls._has_explicit_auxiliar_in_document(evidencias, transcripcion_literal)

        if not has_real_auxiliar:
            es_aux = False
            lib_had_auxiliar = False
            if tom_val == "AUXILIAR":
                tom_val = None
            if lib_val == "AUXILIAR":
                lib_val = None

            # Si Claude había desplazado el Tomo a Volumen por alucinación de Auxiliar, restaurarlo
            has_positive_vol = any(
                "VOLUMEN" in TextNormalizer.strip_accents(str(ev).upper())
                and not cls._is_negative_or_absence_line(str(ev))
                for ev in evidencias
            )
            raw_vol_val = fields_src.get("volumen")
            if tom_val is None and isinstance(raw_vol_val, int) and not has_positive_vol:
                tom_val = raw_vol_val
                fields_src["volumen"] = None

            # Si aún no se tiene tomo, buscar si venía en las evidencias afirmativas
            if tom_val is None:
                for ev in evidencias:
                    s_ev = str(ev).strip()
                    if cls._is_negative_or_absence_line(s_ev):
                        continue
                    ev_up = TextNormalizer.strip_accents(s_ev.upper())
                    m_tom = re.search(r"TOMO\s*=\s*(\d+)", ev_up)
                    if m_tom:
                        tom_val = int(m_tom.group(1))
                        break
                    m_tom_tok = re.search(
                        r"\bTOMO\s+(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?([A-Z0-9#]+(?:\s*\([A-Z0-9]+\))?)",
                        ev_up
                    )
                    if m_tom_tok:
                        parsed_t = TextNormalizer.parse_number_or_word(m_tom_tok.group(1))
                        if parsed_t is not None:
                            tom_val = parsed_t
                            break

        is_auxiliar_doc = bool(has_real_auxiliar and (es_aux or lib_had_auxiliar or tom_val == "AUXILIAR"))

        # Si el libro es numérico y además el documento es AUXILIAR, entonces AUXILIAR ocupa la posición de Tomo
        if is_auxiliar_doc and isinstance(lib_val, int):
            if tom_val is None:
                tom_val = "AUXILIAR"
            elif isinstance(tom_val, int) and fields_src.get("volumen") is None:
                # El número se desplaza a volumen y AUXILIAR ocupa Tomo
                fields_src["volumen"] = tom_val
                tom_val = "AUXILIAR"
            elif isinstance(tom_val, str) and tom_val != "AUXILIAR":
                # Si en tomo estaba ACTOS_Y_CONTRATOS o CREDITO_AGRICOLA pero el libro también es AUXILIAR,
                # AUXILIAR va en tomo y la categoría pasa a fraccionamiento
                displaced_alnum_from_tomo = tom_val
                tom_val = "AUXILIAR"

        # Si NO es auxiliar y la sección traía nombre alfanumérico o las evidencias mencionan CREDITO AGRICOLA / ACTOS Y CONTRATOS
        if tom_val is None:
            if alnum_from_sec:
                tom_val = alnum_from_sec
            elif "CREDITO AGRICOLA" in evidencias_joined or "CREDITO_AGRICOLA" in evidencias_joined:
                tom_val = "CREDITO_AGRICOLA"
                sec_val = None
            elif "ACTOS Y CONTRATOS" in evidencias_joined or "ACTOS_Y_CONTRATOS" in evidencias_joined:
                tom_val = "ACTOS_Y_CONTRATOS"
                sec_val = None

        # 4. Volumen
        raw_vol = fields_src.get("volumen")
        if isinstance(raw_vol, int):
            vol_val = raw_vol
        elif isinstance(raw_vol, str) and raw_vol.strip():
            parsed_v = TextNormalizer.parse_number_or_word(raw_vol)
            vol_val = parsed_v if parsed_v is not None else None
        else:
            vol_val = None

        # 5. Fraccionamiento (o categoría secundaria cuando Tomo ya está ocupado por AUXILIAR)
        raw_fracc = fields_src.get("fraccionamiento")
        fracc_val: Optional[str] = None
        if raw_fracc and str(raw_fracc).strip().upper() not in ["NULL", "NONE", "00", "0"]:
            cleaned_f = cls._clean_alnum_label(str(raw_fracc))
            if cleaned_f in ["CREDITO_AGRICOLA", "ACTOS_Y_CONTRATOS"]:
                if tom_val == "AUXILIAR":
                    fracc_val = cleaned_f
                elif tom_val is None:
                    tom_val = cleaned_f
                    fracc_val = None
                else:
                    fracc_val = cleaned_f
            else:
                fracc_val = cleaned_f

        # Si Tomo es AUXILIAR y el documento además pertenece a ACTOS_Y_CONTRATOS o CREDITO_AGRICOLA,
        # colocar esa categoría en la posición de Fraccionamiento
        if tom_val == "AUXILIAR" and fracc_val is None:
            if displaced_alnum_from_tomo:
                fracc_val = displaced_alnum_from_tomo
            elif alnum_from_sec:
                fracc_val = alnum_from_sec
            elif "ACTOS Y CONTRATOS" in evidencias_joined or "ACTOS_Y_CONTRATOS" in evidencias_joined:
                fracc_val = "ACTOS_Y_CONTRATOS"
                sec_val = None
            elif "CREDITO AGRICOLA" in evidencias_joined or "CREDITO_AGRICOLA" in evidencias_joined:
                fracc_val = "CREDITO_AGRICOLA"
                sec_val = None

        # 6. Año
        raw_anio = fields_src.get("anio")
        if isinstance(raw_anio, int) and 1900 <= raw_anio <= 2035:
            anio_val = raw_anio
        elif isinstance(raw_anio, str) and raw_anio.strip():
            anio_val = TextNormalizer.parse_spanish_year(raw_anio)
        else:
            anio_val = None

        return {
            "seccion": sec_val,
            "libro": lib_val,
            "tomo": tom_val,
            "volumen": vol_val,
            "fraccionamiento": fracc_val,
            "anio": anio_val,
            "es_auxiliar": es_aux or (tom_val == "AUXILIAR") or (lib_val == "AUXILIAR"),
            "texto_descriptivo": texto_descriptivo,
            "evidencias": evidencias
        }

    @classmethod
    def test_connection(cls, api_key: str, model: str = "claude-sonnet-5") -> Tuple[bool, str]:
        if not api_key or not api_key.strip():
            return False, "La API Key está vacía."

        models_to_try = [model] + [m for m in MODEL_FALLBACK_CHAIN if m != model]
        last_err = ""
        for m in models_to_try:
            try:
                payload = {
                    "model": m,
                    "max_tokens": 32,
                    "thinking": {"type": "disabled"},
                    "messages": [{"role": "user", "content": "Responde OK"}]
                }
                headers = {
                    "Content-Type": "application/json",
                    "x-api-key": api_key.strip(),
                    "anthropic-version": "2023-06-01"
                }
                req = urllib.request.Request(
                    cls.API_URL,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    used = resp_data.get("model", m)
                    cls._resolved_model = m
                    return True, f"Conexión exitosa. Modelo activo: {used}"
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                if e.code == 404 or "not_found_error" in body:
                    last_err = f"Modelo {m} no encontrado"
                    continue
                elif e.code == 401:
                    return False, "Error 401: API Key inválida o no autorizada en Anthropic."
                elif (e.code == 400 and any(w in body.lower() for w in ["credit", "balance", "billing"])) or e.code == 402:
                    return False, "Error de Créditos: Tu saldo de Anthropic API está agotado (credit balance is too low). Recarga créditos en console.anthropic.com."
                else:
                    return False, f"Error HTTP {e.code}: {body}"
            except Exception as ex:
                return False, f"Error de red: {ex}"

        return False, f"No se pudo conectar con ninguno de los modelos: {last_err}"
