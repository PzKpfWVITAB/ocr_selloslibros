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
   - REGLA CRÍTICA PARA LIBROS AUXILIARES CON SECCIÓN NUMERADA:
     * Si el libro es AUXILIAR (con o sin número de libro, ej. "LIBRO 247 AUXILIAR", "LIBRO SEGUNDO AUXILIAR") Y el documento menciona además una sección numerada (ej. "SECCIÓN PRIMERA", "(SECCIÓN PRIMERA)", "RELATIVO A FRACCIONAMIENTOS Y COLONIAS (SECCIÓN PRIMERA)"):
       - "seccion" DEBE SER EL NÚMERO CORRESPONDIENTE (1, 2, 3, 4 o 5).
       - ESTÁ ESTRICTAMENTE PROHIBIDO poner seccion=null o 00 pensando que la palabra AUXILIAR anula la sección numerada.
       - "AUXILIAR" ocupa la posición de Tomo (o Libro si no tiene número), pero "seccion" CONSERVA SU NÚMERO (ej. seccion=1).
   - Si el acta de apertura dice "SECCION DE CREDITO AGRICOLA", "SECCION DE ACTOS Y CONTRATOS", "DOCUMENTOS PRIVADOS", "ADQUISICIONES DE GOBIERNO", "LIBRO AUXILIAR", etc. (sin decir Sección Primera, Segunda, Tercera, Cuarta o Quinta), "seccion" DEBE SER null (que equivale a 00), y ese nombre alfanumérico ("CREDITO_AGRICOLA", "ACTOS_Y_CONTRATOS", "DOCUMENTOS_PRIVADOS", "ADQUISICIONES_DE_GOBIERNO", "AUXILIAR", "AUXILIAR_ACTOS_Y_CONTRATOS", "AUXILIAR_CREDITO_AGRICOLA", "AUXILIAR_DOCUMENTOS_PRIVADOS") DEBE colocarse en el campo "tomo".
   - REGLA CRÍTICA DE FALSOS POSITIVOS:
     La institución se llama "REGISTRO PÚBLICO DE LA PROPIEDAD Y DE COMERCIO". NUNCA asignes seccion=1 ni seccion=2 solo por el nombre de la institución.

2. "libro" (entero o "AUXILIAR" o null):
   - Es el número de Libro autorizado (puede venir en arábigo "2", "02", "3", "100", en romano "I", "II", "III", o en palabra "UNO", "DOS", "SEGUNDO", "TERCERO").
   - Conviértelo siempre a número entero (ej. "LIBRO NUMERO 2 DOS" -> 2, "LIBRO SEGUNDO AUXILIAR" -> 2, "LIBRO NUMERO 02 VOLUMEN I AUXILIAR" -> 2, "LIBRO 100 AUXILIAR" -> 100).
   - REGLA DE LIBRO CON NÚMERO + AUXILIAR:
     * Si el documento tiene un número de libro Y además dice "AUXILIAR" (ejemplo: "LIBRO SEGUNDO AUXILIAR DEL INDICE DE COLONIAS" o "LIBRO NUMERO 02 VOLUMEN I AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS" o "LIBRO NUMERO 100 AUXILIAR"), entonces:
       - "libro" = el número entero del libro (ej. 2, 100).
       - "tomo" = "AUXILIAR" (o la combinación auxiliar correspondiente como "AUXILIAR_ACTOS_Y_CONTRATOS", "AUXILIAR_CREDITO_AGRICOLA", "AUXILIAR_DOCUMENTOS_PRIVADOS", o "AUXILIAR_<NUMERO>" si dice explícitamente "AUX. 25").
     * Solo coloca "libro": "AUXILIAR" si el documento dice "LIBRO AUXILIAR" sin ningún número de libro.

3. "tomo" (entero, o cadena alfanumérica permitida en Tomo, o null):
   - Valores alfanuméricos permitidos en "tomo":
     * "AUXILIAR"
     * "AUXILIAR_<NUMERO>" (ÚNICAMENTE cuando en raras ocasiones dice explícitamente algo como "AUX. 25" indicando que ese es el número específico de auxiliar; nunca tomes números de fojas, libros o años).
     * "ACTOS_Y_CONTRATOS"
     * "AUXILIAR_ACTOS_Y_CONTRATOS" (cuando el libro es Auxiliar Y de Actos y Contratos)
     * "CREDITO_AGRICOLA"
     * "AUXILIAR_CREDITO_AGRICOLA" (cuando el libro es Auxiliar Y de Crédito Agrícola)
     * "DOCUMENTOS_PRIVADOS"
     * "AUXILIAR_DOCUMENTOS_PRIVADOS" (cuando el libro es Auxiliar Y de Documentos Privados)
     * "ADQUISICIONES_DE_GOBIERNO"
   - Caso A (Tomo numérico normal): Si el documento menciona un Tomo numérico (ej. "TOMO I", "TOMO 2", "TOMO II (DOS)"), devuelve el número entero (1, 2).
   - Caso B (Libro AUXILIAR con o sin materia de Tomo):
     * "LIBRO 1 UNO AUXILIAR PARA LLEVAR EL ORIGINAL DE ACTOS Y CONTRATOS" -> "libro": 1, "tomo": "AUXILIAR_ACTOS_Y_CONTRATOS", "fraccionamiento": null
     * "LIBRO 1 AUXILIAR DE CREDITO AGRICOLA" -> "libro": 1, "tomo": "AUXILIAR_CREDITO_AGRICOLA", "fraccionamiento": null
     * "LIBRO 1 AUXILIAR DE DOCUMENTOS PRIVADOS" -> "libro": 1, "tomo": "AUXILIAR_DOCUMENTOS_PRIVADOS", "fraccionamiento": null
     * "LIBRO NUMERO DOS... SEGUNDO AUXILIAR... ESCRITURAS DE SOCIEDADES Y PODERES" -> "libro": 2, "tomo": "AUXILIAR", "fraccionamiento": null (NUNCA pongas "SOCIEDADES Y PODERES" en tomo ni en fraccionamiento).
   - Caso C (Categoría en Tomo cuando el libro NO es AUXILIAR):
     * "LIBRO NUMERO 2 DOS PARA LLEVAR EL ORIGINAL DE LA SECCION DE CREDITO AGRICOLA" -> "seccion": null, "libro": 2, "tomo": "CREDITO_AGRICOLA", "fraccionamiento": null
     * "LIBRO NUMERO 2 DOS PARA LLEVAR EL ORIGINAL DE LA SECCION DE ACTOS Y CONTRATOS" -> "seccion": null, "libro": 2, "tomo": "ACTOS_Y_CONTRATOS", "fraccionamiento": null
     * "LIBRO NUMERO 1 UNO DE DOCUMENTOS PRIVADOS" -> "libro": 1, "tomo": "DOCUMENTOS_PRIVADOS", "fraccionamiento": null
     * "LIBRO DE ADQUISICIONES DE GOBIERNO" -> "tomo": "ADQUISICIONES_DE_GOBIERNO", "fraccionamiento": null
   - Si no hay tomo numérico ni ninguna de las categorías permitidas arriba, devuelve null.

4. "volumen" (entero o null):
   - Si el documento especifica un número de Volumen (ej. "VOLUMEN 1", "VOLUMEN I", "VOL. 2"), conviértelo a número entero (1, 2).
   - Si no se menciona Volumen, devuelve null.

5. "fraccionamiento" (cadena en mayúsculas con guiones bajos, o null):
   - REGLA CRÍTICA DE EXCLUSIVIDAD PARA FRACCIONAMIENTO (POSICIÓN 6):
     * Este campo es EXCLUSIVAMENTE para Fraccionamientos, Colonias o CORETT.
     * Si el documento NO trata de Fraccionamientos, Colonias ni CORETT (por ejemplo, si trata de "Sociedades y Poderes", "Actos y Contratos", "Crédito Agrícola", "Documentos Privados", "Adquisiciones de Gobierno", "Comercio", etc.), "fraccionamiento" DEBE SER OBLIGATORIAMENTE null (que equivale a 00).
     * ESTÁ ESTRICTAMENTE PROHIBIDO poner materias como "SOCIEDADES_Y_PODERES" o anteponer "FRACCIONAMIENTO_" a cosas que no son un fraccionamiento.
   - Reglas de Formato cuando el documento SÍ es de Fraccionamientos, Colonias o CORETT:
     a) Si menciona explícitamente un FRACCIONAMIENTO (ej. "FRACCIONAMIENTO SAN FERNANDO", "FRACCIONAMIENTO VILLA 24 DE JUNIO"):
        * Lleva el prefijo "FRACCIONAMIENTO_": "FRACCIONAMIENTO_SAN_FERNANDO", "FRACCIONAMIENTO_VILLA_24_DE_JUNIO".
     b) Si menciona explícitamente el nombre de una COLONIA (ej. "COLONIA LAS GRANJAS", "COLONIA BIENESTAR SOCIAL"):
        * Se pone SOLO el nombre de la colonia en mayúsculas con guiones bajos SIN "FRACCIONAMIENTO_" y SIN "COLONIA_" al inicio (ej. "LAS_GRANJAS", "BIENESTAR_SOCIAL").
     c) Si menciona "CORETT":
        * Se pone "CORETT".
     d) Si el libro es de "FRACCIONAMIENTOS Y COLONIAS" o "INDICE DE COLONIAS" pero NO tiene un nombre explícito de fraccionamiento ni de colonia:
        * Se pone simplemente "FRACCIONAMIENTOS_Y_COLONIAS".
   - REGLA CRÍTICA DE LIMPIEZA: NUNCA incluyas conectores como "DEL", "DE", "PARA EL AÑO" ni el año al final de "fraccionamiento", ni frases explicativas o comentarios de la IA.

6. "anio" (entero de 4 dígitos entre 1900 y 2035, o null):
   - Es el año para el cual se autoriza el libro o en que se fecha el acta de apertura (ej. "PARA EL AÑO DE 1986 MIL NOVECIENTOS OCHENTA Y SEIS" -> 1986, "26 DE NOVIEMBRE DE MIL NOVECIENTOS OCHENTA Y TRES" -> 1983).
   - Conviértelo siempre a entero de 4 dígitos.

========================================================================================
REGLA DE ORO: SEPARACIÓN TOTAL ENTRE DATOS ESTRUCTURADOS Y COMENTARIOS DE LA IA
========================================================================================
1. Envía SIEMPRE tu respuesta estructurada en formato JSON mediante la herramienta 'registrar_datos_caratula'.
2. El objeto 'datos_estructurados' contiene EXCLUSIVAMENTE los datos numéricos y categóricos que
   servirán para construir el código registral de 7 posiciones.
3. Tus comentarios, observaciones o notas sobre lo que NO contiene el documento (ejemplo:
   "Distrito judicial sin especificar fraccionamiento o auxiliar", "No se menciona sección", etc.)
   DEBEN ir ÚNICAMENTE en el campo 'comentarios_ia'. NUNCA los mezcles con los datos útiles.
4. NUNCA alteres ni pongas 'es_auxiliar': true por notas de ausencia. Si la palabra "AUXILIAR" no
   está literalmente impresa o escrita en el sello, 'es_auxiliar' DEBE ser false y 'tomo' NUNCA
   puede ser 'AUXILIAR'.
5. Las 'evidencias' son exclusivamente citas textuales afirmativas entre comillas ('...').

EJEMPLOS DE SALIDA ESTRUCTURADA EN FORMATO JSON:

Ejemplo 1 (Crédito Agrícola - Sección alfanumérica en Tomo):
{
  "transcripcion_literal": "EL CIUDADANO JUEZ DE PRIMERA INSTANCIA DEL RAMO CIVIL... AUTORIZA EL PRESENTE LIBRO NUMERO 2 DOS PARA LLEVAR EL ORIGINAL DE LA SECCION DE CREDITO AGRICOLA... PARA EL AÑO DE 1986",
  "datos_estructurados": {
    "seccion": null,
    "libro": 2,
    "tomo": "CREDITO_AGRICOLA",
    "volumen": null,
    "fraccionamiento": null,
    "anio": 1986,
    "es_auxiliar": false
  },
  "comentarios_ia": "Distrito Judicial de Tuxtla Gutiérrez.",
  "texto_descriptivo": "Acta de apertura de libro de Crédito Agrícola para 1986.",
  "evidencias": [
    "Libro detectado: 'LIBRO NUMERO 2 DOS' -> libro=2",
    "Sección alfanumérica colocada en posición de Tomo: 'SECCION DE CREDITO AGRICOLA' -> tomo='CREDITO_AGRICOLA'",
    "Año detectado: 'PARA EL AÑO DE 1986' -> anio=1986"
  ]
}

Ejemplo 2 (Libro normal con Tomo numérico, Libro numérico, Sección 1 y Año 1992):
{
  "transcripcion_literal": "AUTORIZA EL PRESENTE LIBRO NUMERO 1 UNO TOMO III CORRESPONDIENTE A LA SECCION PRIMERA DEL REGISTRO PUBLICO DE LA PROPIEDAD Y DE COMERCIO... PARA EL AÑO DE 1992",
  "datos_estructurados": {
    "seccion": 1,
    "libro": 1,
    "tomo": 3,
    "volumen": null,
    "fraccionamiento": null,
    "anio": 1992,
    "es_auxiliar": false
  },
  "comentarios_ia": "Distrito Judicial de Tuxtla Gutiérrez sin fraccionamiento ni auxiliar.",
  "texto_descriptivo": "Libro 1 Tomo III de la Sección Primera para el año de 1992.",
  "evidencias": [
    "Sección detectada: 'SECCION PRIMERA' -> seccion=1",
    "Libro detectado: 'LIBRO NUMERO 1 UNO' -> libro=1",
    "Tomo detectado: 'TOMO III' -> tomo=3",
    "Año detectado: 'PARA EL AÑO DE 1992' -> anio=1992"
  ]
}

Ejemplo 3 (Libro AUXILIAR real donde la palabra 'AUXILIAR' SÍ aparece escrita):
{
  "transcripcion_literal": "AUTORIZA EL PRESENTE LIBRO NUMERO 02 VOLUMEN I AUXILIAR DE FRACCIONAMIENTOS Y COLONIAS... 22 DE JUNIO DE 1990",
  "datos_estructurados": {
    "seccion": null,
    "libro": 2,
    "tomo": "AUXILIAR",
    "volumen": 1,
    "fraccionamiento": "FRACCIONAMIENTOS_Y_COLONIAS",
    "anio": 1990,
    "es_auxiliar": true
  },
  "comentarios_ia": "Libro Auxiliar con materia de Fraccionamientos y Colonias.",
  "texto_descriptivo": "Libro número 02 volumen I Auxiliar de Fraccionamientos y Colonias de 1990.",
  "evidencias": [
    "Libro detectado: 'LIBRO NUMERO 02' -> libro=2",
    "Categoría Auxiliar en posición de Tomo: 'AUXILIAR' -> tomo='AUXILIAR'",
    "Volumen detectado: 'VOLUMEN I' -> volumen=1",
    "Fraccionamiento detectado: 'FRACCIONAMIENTOS Y COLONIAS'",
    "Año detectado: '1990'"
  ]
}

Responde ÚNICAMENTE con el objeto JSON válido de la herramienta, sin texto adicional fuera del JSON.
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
        "Envía en formato JSON estructurado exclusivamente los datos registrales objetivos de la carátula. "
        "Separa estrictamente los datos útiles verificados de cualquier comentario o nota interna, "
        "para que ningún comentario de ausencia ('sin especificar...', 'no se menciona...') pueda confundirse con los datos del libro. "
        "REGLA ESTRICTA: Si la palabra 'AUXILIAR' NO aparece explícitamente escrita en el documento visible, "
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
                        "description": (
                            "Solo entero 1..5 si dice explícitamente SECCIÓN PRIMERA..QUINTA (ej. 'SECCIÓN PRIMERA' -> 1). "
                            "OBLIGATORIO: Incluso si el libro es AUXILIAR (ej. LIBRO 247 AUXILIAR), si el documento dice 'SECCIÓN PRIMERA', "
                            "debe asignarse seccion=1 (NUNCA anular la sección numerada por ser auxiliar). Si no tiene sección numerada, null."
                        ),
                    },
                    "libro": {
                        "type": ["integer", "string", "null"],
                        "description": "Número entero del libro (ej. 1, 2, 100). Solo usar 'AUXILIAR' si dice LIBRO AUXILIAR sin número.",
                    },
                    "tomo": {
                        "type": ["integer", "string", "null"],
                        "description": (
                            "Número entero del Tomo (ej. 1, 2, 3), o una de las categorías permitidas en Tomo: "
                            "'AUXILIAR', 'AUXILIAR_<NUMERO>' (solo si dice explícitamente AUX. <N> indicando número de auxiliar), "
                            "'ACTOS_Y_CONTRATOS', 'AUXILIAR_ACTOS_Y_CONTRATOS', "
                            "'CREDITO_AGRICOLA', 'AUXILIAR_CREDITO_AGRICOLA', "
                            "'DOCUMENTOS_PRIVADOS', 'AUXILIAR_DOCUMENTOS_PRIVADOS', "
                            "'ADQUISICIONES_DE_GOBIERNO'. Si no aplica ninguno, null."
                        ),
                    },
                    "volumen": {
                        "type": ["integer", "null"],
                        "description": "Número entero de Volumen si se menciona explícitamente VOLUMEN (ej. 1, 2), o null.",
                    },
                    "fraccionamiento": {
                        "type": ["string", "null"],
                        "description": (
                            "EXCLUSIVAMENTE para libros de Fraccionamientos, Colonias o CORETT. "
                            "Si el documento NO es de fraccionamientos ni colonias (ej. Sociedades y Poderes, Actos y Contratos, Comercio), DEBE SER null. "
                            "Formato: si es un fraccionamiento específico -> 'FRACCIONAMIENTO_<NOMBRE>'; "
                            "si es una colonia específica -> solo '<NOMBRE_COLONIA>' sin prefijo; "
                            "si es CORETT -> 'CORETT'; "
                            "si es genérico de fraccionamientos/colonias sin nombre específico -> 'FRACCIONAMIENTOS_Y_COLONIAS'."
                        ),
                    },
                    "anio": {
                        "type": ["integer", "null"],
                        "description": "Año de 4 dígitos (1900..2035) del acta de apertura, o null.",
                    },
                    "es_auxiliar": {
                        "type": "boolean",
                        "description": "true ÚNICAMENTE si la palabra 'AUXILIAR' aparece explícitamente escrita en el texto visible del documento; false en cualquier otro caso.",
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
            "comentarios_ia": {
                "type": "string",
                "description": (
                    "Comentarios, observaciones o notas de la IA sobre lo que NO aparece en el documento "
                    "(ej. 'distrito judicial sin especificar fraccionamiento ni auxiliar', 'no se especifica tomo'). "
                    "Este campo está aislado para no confundir comentarios con los datos estructurados."
                ),
            },
            "texto_descriptivo": {
                "type": "string",
                "description": "Resumen descriptivo claro en español de lo que autoriza el sello o acta de apertura.",
            },
            "evidencias": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Citas textuales literales afirmativas entre comillas simples ('...') extraídas de la imagen que respaldan cada campo detectado. "
                    "PROHIBIDO incluir notas sobre datos que NO aparecen."
                ),
            },
        },
        "required": ["transcripcion_literal", "datos_estructurados", "evidencias"],
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
                "Extrae exclusivamente los datos registrales objetivos en formato JSON estructurado llamando a 'registrar_datos_caratula'. "
                "Separa estrictamente los datos verificados de cualquier comentario o nota interna en 'comentarios_ia'. "
                "REGLA CRÍTICA: Si la palabra 'AUXILIAR' no está explícitamente escrita en el documento visible, "
                "'es_auxiliar' DEBE ser false y 'tomo' jamás puede ser 'AUXILIAR'."
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

    VALID_TOMO_CATEGORIES = {
        "AUXILIAR",
        "ACTOS_Y_CONTRATOS",
        "AUXILIAR_ACTOS_Y_CONTRATOS",
        "CREDITO_AGRICOLA",
        "AUXILIAR_CREDITO_AGRICOLA",
        "DOCUMENTOS_PRIVADOS",
        "AUXILIAR_DOCUMENTOS_PRIVADOS",
        "ADQUISICIONES_DE_GOBIERNO",
    }

    @classmethod
    def is_valid_tomo_category(cls, val: Any) -> bool:
        if not val:
            return False
        s = str(val).strip().upper()
        if s in cls.VALID_TOMO_CATEGORIES:
            return True
        if re.match(r"^AUXILIAR_\d+$", s):
            return True
        return False

    @staticmethod
    def _clean_alnum_label(val: str) -> str:
        s = TextNormalizer.strip_accents(val.strip().upper())
        s = re.sub(r"^SECCION\s+DE\s+", "", s)
        s = re.sub(r"^SECCION\s+", "", s)
        s = re.sub(r"^INDICE\s+DE\s+", "", s)
        s = re.sub(r"^INDICE_DE_", "", s)
        has_aux = bool(re.search(r"\bAUX(?:ILIAR|\.)?\b", s))
        if "ADQUISICION" in s and "GOBIERNO" in s:
            return "ADQUISICIONES_DE_GOBIERNO"
        if "ACTOS" in s and "CONTRATOS" in s:
            return "AUXILIAR_ACTOS_Y_CONTRATOS" if has_aux else "ACTOS_Y_CONTRATOS"
        if "CREDITO" in s and "AGRICOLA" in s:
            return "AUXILIAR_CREDITO_AGRICOLA" if has_aux else "CREDITO_AGRICOLA"
        if "DOCUMENTO" in s and "PRIVADO" in s:
            return "AUXILIAR_DOCUMENTOS_PRIVADOS" if has_aux else "DOCUMENTOS_PRIVADOS"
        m_aux_num = re.match(r"^AUX(?:ILIAR|\.)?[\s_]+(?:NUMERO[\s_]+|NO\.?[\s_]+|NRO\.?[\s_]+)?(\d+)$", s)
        if m_aux_num:
            return f"AUXILIAR_{int(m_aux_num.group(1))}"
        s = re.sub(r"[^A-Z0-9]+", "_", s).strip("_")
        return s

    @classmethod
    def extract_tomo_category(cls, text: str, is_auxiliar: bool = False) -> Optional[str]:
        """
        Detecta determinísticamente si el texto afirmativo menciona alguna de las categorías
        que van en la posición de Tomo:
        - ADQUISICIONES_DE_GOBIERNO
        - DOCUMENTOS_PRIVADOS / AUXILIAR_DOCUMENTOS_PRIVADOS
        - CREDITO_AGRICOLA / AUXILIAR_CREDITO_AGRICOLA
        - ACTOS_Y_CONTRATOS / AUXILIAR_ACTOS_Y_CONTRATOS
        - AUXILIAR_<NUMERO> (únicamente si dice explícitamente 'AUX. <N>' o 'AUXILIAR NUMERO <N>')
        """
        if not text:
            return None
        t = TextNormalizer.strip_accents(str(text).upper())
        if re.search(r"\bADQUISICIONES?[\s_]+DE[\s_]+GOBIERNO\b", t):
            return "ADQUISICIONES_DE_GOBIERNO"
        if re.search(r"\bDOCUMENTOS?[\s_]+PRIVADOS?\b", t):
            return "AUXILIAR_DOCUMENTOS_PRIVADOS" if is_auxiliar else "DOCUMENTOS_PRIVADOS"
        if re.search(r"\bCREDITO[\s_]+AGRICOLA\b", t):
            return "AUXILIAR_CREDITO_AGRICOLA" if is_auxiliar else "CREDITO_AGRICOLA"
        if re.search(r"\bACTOS[\s_]+Y[\s_]+CONTRATOS\b", t):
            return "AUXILIAR_ACTOS_Y_CONTRATOS" if is_auxiliar else "ACTOS_Y_CONTRATOS"
        m_aux_n = re.search(r"\b(?:AUX\.\s*(?:NUMERO\s+|NO\.?\s+)?(\d+)|AUXILIAR\s+(?:NUMERO|NO\.?|NRO\.?)\s*(\d+))\b", t)
        if m_aux_n:
            num_str = m_aux_n.group(1) or m_aux_n.group(2)
            return f"AUXILIAR_{int(num_str)}"
        return None

    @classmethod
    def _has_fraccionamiento_or_colonia_in_document(cls, text: str) -> bool:
        """
        Verifica si el texto del documento menciona explícitamente Fraccionamiento(s),
        Colonia(s), Predio(s) o CORETT. Si un documento NO menciona ninguno de estos
        términos (ej. es un libro de Sociedades y Poderes, Actos y Contratos, Comercio, etc.),
        la Posición 6 (Fraccionamiento) debe quedar en null ('00').
        """
        if not text:
            return False
        t = TextNormalizer.strip_accents(str(text).upper())
        return bool(
            re.search(
                r"\b(?:FRACCIONAMIENTOS?|FRACCION|COLONIAS?|PREDIOS?|LOTIFICACION|CORETT)\b",
                t
            )
        )

    @staticmethod
    def clean_fraccionamiento_name(raw: str) -> str:
        stop_words = [
            r'\bDE\s+ESTA\s+CIUDAD\b',
            r'\bEN\s+ESTA\s+CIUDAD\b',
            r'\bUBICADO\s+EN\b',
            r'\bSITO\s+EN\b',
            r'\bEN\s+TUXTLA\s+GUTIERREZ\b',
            r'\bDE\s+TUXTLA\s+GUTIERREZ\b',
            r'\bTUXTLA\s+GUTIERREZ\b',
            r'\bDEL\s+DISTRITO\b',
            r'\bEN\s+EL\s+DISTRITO\b',
            r'\bDE\s+TUXTLA\b',
            r'\bEN\s+TUXTLA\b',
            r'\bMUNICIPIO\s+DE\b',
            r'\bDEL\s+MUNICIPIO\b',
            r'\bEN\s+EL\s+MUNICIPIO\b',
            r'\bDEL\s+ESTADO\b',
            r'\bEN\s+EL\s+ESTADO\b',
            r'\bCHIAPAS\b',
            r'\bPARA\s+EL\s+A[NÑ]O\b',
            r'\bDEL\s+A[NÑ]O\b',
            r'\b(?:19\d{2}|20\d{2})\b',
            r'\bCORRESPONDIENTE\b',
            r'\bAUTORIZA\b',
            r'\bQUE\s+CONSTA\b',
            r'\bLIBRO\b',
            r'\bTOMO\b',
            r'\bVOLUMEN\b',
            r'\bSECCION\b',
            r'\bFOLIO\b',
            r'\bREGISTRO\s+PUBLICO\b'
        ]
        s = TextNormalizer.strip_accents(str(raw).strip().upper())
        for sw in stop_words:
            s = re.split(sw, s, flags=re.IGNORECASE)[0].strip()
        s = re.sub(r'^[\s,.;:"\'\-_]+|[\s,.;:"\'\-_]+$', '', s)
        s = re.sub(r'[^A-Z0-9]+', '_', s).strip('_')

        # Limpieza iterativa de años y conectores finales (ej. _2004, _DEL_ANO, _DEL, _DE, _PARA, etc.)
        while True:
            old_s = s
            # Quitar año final de 4 dígitos (ej. _2004, _1997)
            s = re.sub(r'_(?:19\d{2}|20\d{2})$', '', s)
            # Quitar referencias de año o tiempo al final (ej. _DEL_ANO, _DEL_ANIO, _A_O, _ANO, _ANIO, _ANOS)
            s = re.sub(r'_(?:DEL|DE|PARA|EN|AL)?_?(?:ANO|ANIO|A_O|ANOS|ANIOS)$', '', s)
            # Quitar conectores o preposiciones sueltas al final (ej. _DEL, _DE, _PARA_EL, _PARA, _EN_EL, _EN, _AL, _A, _CON, _POR, _Y, _O)
            s = re.sub(r'_(?:PARA_EL|PARA_LA|EN_EL|EN_LA|DE_EL|DE_LA|DE_LOS|DE_LAS|DEL|DE|PARA|EN|AL|A|CON|POR|Y|O)$', '', s)
            if s == old_s:
                break

        return s

    @classmethod
    def extract_numbered_section(cls, text: str) -> Optional[int]:
        """
        Detecta determinísticamente si el texto contiene una mención de sección numerada
        (ej. 'SECCIÓN PRIMERA', 'SECCIÓN SEGUNDA', '(SECCIÓN PRIMERA)').
        Nunca se anula una sección numerada porque el libro sea AUXILIAR.
        """
        if not text:
            return None
        t = TextNormalizer.strip_accents(str(text).upper())
        m = re.search(
            r'\bSECCION\s+(?:(?:NUMERO|NUERO|NRO\.?|NUM\.?|NO\.?)\s+)?(PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|1|2|3|4|5|I|II|III|IV|V)\b',
            t
        )
        if m:
            val = m.group(1)
            return TextNormalizer.normalize_section(val)
        return None

    DISALLOWED_FRACCIONAMIENTO_NAMES = {
        "Y_COLONIAS", "O_COLONIAS", "COLONIAS", "COLONIA", "FRACCIONAMIENTOS", "FRACCIONAMIENTO",
        "FRACCION", "FRACCIONES", "DE", "DEL", "LOS", "LAS", "LA", "EL", "EN", "AL", "UN", "UNA",
        "DETECTADO", "DETECTADA", "DETECTADOS", "DETECTADAS",
        "ENCONTRADO", "ENCONTRADA", "ENCONTRADOS", "ENCONTRADAS",
        "MENCIONADO", "MENCIONADA", "MENCIONADOS", "MENCIONADAS",
        "OBSERVADO", "OBSERVADA", "OBSERVADOS", "OBSERVADAS",
        "PROPUESTO", "PROPUESTA", "PROPUESTOS", "PROPUESTAS",
        "SUGERIDO", "SUGERIDA", "SUGERIDOS", "SUGERIDAS",
        "IDENTIFICADO", "IDENTIFICADA", "IDENTIFICADOS", "IDENTIFICADAS",
        "ESPECIFICADO", "ESPECIFICADA", "ESPECIFICADOS", "ESPECIFICADAS",
        "ESPECIFICO", "ESPECIFICA", "ESPECIFICOS", "ESPECIFICAS",
        "GENERAL", "GENERALES", "GENERICO", "GENERICA", "GENERICOS", "GENERICAS",
        "INDICADO", "INDICADA", "INDICADOS", "INDICADAS",
        "REGISTRADO", "REGISTRADA", "REGISTRADOS", "REGISTRADAS",
        "DESCONOCIDO", "DESCONOCIDA", "NO_ESPECIFICADO", "SIN_ESPECIFICAR",
        "RELATIVO", "RELATIVOS", "RELATIVA", "RELATIVAS",
        "INDICE", "REGISTRO", "OFICIAL", "AUXILIAR", "URBANO", "URBANOS", "RURAL", "RURALES",
        "TERRENO", "TERRENOS", "PREDIO", "PREDIOS", "LOTE", "LOTES",
        "NULL", "NONE", "NINGUNO", "NINGUNA", "NO", "SIN",
        "CONCRETO", "CONCRETA", "CONCRETOS", "CONCRETAS",
        "TEXTO", "TEXTOS", "VISIBLE", "VISIBLES", "LEGIBLE", "LEGIBLES", "ILEGIBLE", "ILEGIBLES",
        "PARECE", "PARECEN", "REFERIRSE", "REFIERE", "REFIEREN", "REFERENCIA", "REFERENCIAS",
        "ESPECIFICA", "ESPECIFICAR", "ESPECIFIQUE", "MENCIONA", "MENCIONAR", "MENCIONE",
        "OBSERVA", "OBSERVAR", "COMENTARIO", "COMENTARIOS", "DESCRIPCION", "DESCRIPCIONES",
        "EXPLICACION", "EXPLICACIONES", "ANALISIS", "DOCUMENTO", "DOCUMENTOS",
        "INDICA", "INDICAR", "SENALA", "SENALAR", "ESTIPULA", "ESTIPULAR",
        "PROBABLE", "PROBABLEMENTE", "APARENTE", "APARENTEMENTE",
        "EXISTE", "EXISTEN", "CONSTA", "CONSTAN", "FIGURA", "FIGURAN",
        "NINGUN", "NINGUNO", "NINGUNA", "NINGUNOS", "NINGUNAS",
    }

    NON_FRACCIONAMIENTO_MATTERS = {
        "SOCIEDADES", "PODERES", "SOCIEDADES_Y_PODERES", "ESCRITURAS", "COMERCIO",
        "MERCANTIL", "MERCANTILES", "HIPOTECAS", "HIPOTECA", "EMBARGOS", "EMBARGO",
        "SENTENCIAS", "SENTENCIA", "ARRENDAMIENTOS", "TESTAMENTOS",
    }

    @classmethod
    def extract_specific_fraccionamiento(cls, text: str) -> Optional[str]:
        if not text:
            return None
        # Limpiar prefijos de explicaciones o etiquetas de IA en evidencias
        # (ej. 'Fraccionamiento detectado:', 'Fraccionamiento mencionado:') para evitar falsos positivos
        clean_text = re.sub(
            r'\bFRACCIONAMIENTOS?\s+(?:DETECTAD[OA]S?|ENCONTRAD[OA]S?|MENCIONAD[OA]S?|OBSERVAD[OA]S?|PROPUEST[OA]S?|SUGERID[OA]S?|IDENTIFICAD[OA]S?|ESPECIFICAD[OA]S?)\s*:\s*',
            ' ',
            str(text),
            flags=re.IGNORECASE
        )
        normalized = TextNormalizer.strip_accents(clean_text.upper())
        if re.search(r"\bCORETT\b", normalized):
            return "CORETT"

        pat = re.compile(
            r'\b(?:DEL\s+|PARA\s+EL\s+|PARA\s+LA\s+|EN\s+EL\s+|EN\s+LA\s+|LA\s+)?'
            r'(FRACCIONAMIENTO|FRACCION\s+D[EC]|COLONIA|PREDIO\s+DENOMINADO|PREDIO)\s+'
            r'(?!Y\s+COLONIAS\b|O\s+COLONIAS\b|URBANOS?\b|RURALES?\b|DETECTAD[OA]S?\b|ENCONTRAD[OA]S?\b|MENCIONAD[OA]S?\b|'
            r'OBSERVAD[OA]S?\b|PROPUEST[OA]S?\b|SUGERID[OA]S?\b|IDENTIFICAD[OA]S?\b|GENERAL(?:ES)?\b|ESPECIFIC[OA]S?\b|'
            r'ESPECIFICAD[OA]S?\b|INDICAD[OA]S?\b|REGISTRAD[OA]S?\b|NO\s+|SIN\s+)'
            r'(?:["\']([^"\']{3,60})["\']|([A-Z0-9\s\.\-_]{3,60}))',
            re.IGNORECASE
        )
        disallowed_in_body = {
            "CONCRETO", "TEXTO", "VISIBLE", "PARECE", "REFERIRSE", "ESPECIFICA", "MENCIONA",
            "OBSERVA", "COMENTARIO", "DESCRIPCION", "INDICA", "SENALA", "NINGUN", "NINGUNO",
            "EXISTE", "CONSTA", "FIGURA", "APARENTE", "PROBABLE", "DETECTADO", "ENCONTRADO",
            "ESPECIFICO", "GENERAL", "EXPLICACION"
        } | cls.NON_FRACCIONAMIENTO_MATTERS
        for m in pat.finditer(normalized):
            kind = (m.group(1) or "").strip().upper()
            raw_match = m.group(2) or m.group(3) or ""
            candidate = cls.clean_fraccionamiento_name(raw_match)
            if not candidate or len(candidate) < 3:
                continue
            if candidate == "CORETT":
                return "CORETT"
            cand_tokens = set(candidate.split("_"))
            # Si el candidato completo es inválido o contiene palabras de razonamiento de la IA / otras materias
            if candidate in cls.DISALLOWED_FRACCIONAMIENTO_NAMES or cand_tokens.intersection(disallowed_in_body):
                continue
            if len(candidate.split("_")) > 5:
                continue
            first_tok = candidate.split("_")[0]
            if first_tok in cls.DISALLOWED_FRACCIONAMIENTO_NAMES and first_tok not in ("EL", "LA", "LOS", "LAS"):
                sub_cand = "_".join(candidate.split("_")[1:]).strip("_")
                if len(sub_cand) < 3 or sub_cand in cls.DISALLOWED_FRACCIONAMIENTO_NAMES:
                    continue
                candidate = sub_cand
            # Regla solicitada: Si es una COLONIA, se pone SOLO el nombre de la colonia sin 'FRACCIONAMIENTO_' al inicio
            if kind == "COLONIA":
                return candidate
            return f"FRACCIONAMIENTO_{candidate}"
        return None

    @classmethod
    def normalize_fraccionamiento_label(cls, val: str) -> Optional[str]:
        if not val:
            return val
        s = TextNormalizer.strip_accents(str(val).strip().upper())
        if s == "CORETT":
            return "CORETT"
        if s in ["COLONIAS", "FRACCIONAMIENTOS", "FRACCIONAMIENTOS_Y_COLONIAS"]:
            return "FRACCIONAMIENTOS_Y_COLONIAS"
        if s in [
            "ACTOS_Y_CONTRATOS", "CREDITO_AGRICOLA", "DOCUMENTOS_PRIVADOS", "ADQUISICIONES_DE_GOBIERNO",
            "AUXILIAR_ACTOS_Y_CONTRATOS", "AUXILIAR_CREDITO_AGRICOLA", "AUXILIAR_DOCUMENTOS_PRIVADOS",
            "00", "0", "NULL", "NONE"
        ]:
            return s

        is_colonia = False
        if s.startswith("FRACCIONAMIENTO_"):
            s_body = s[16:]
        elif s.startswith("FRACCION_"):
            s_body = s[9:]
        elif s.startswith("COLONIA_"):
            s_body = s[8:]
            is_colonia = True
        else:
            s_body = s

        cleaned_body = cls.clean_fraccionamiento_name(s_body)
        if cleaned_body == "CORETT":
            return "CORETT"

        body_tokens = set(cleaned_body.split("_")) if cleaned_body else set()
        # Si contiene materias ajenas a fraccionamientos (ej. SOCIEDADES_Y_PODERES), devolver None ('00')
        if cleaned_body in cls.NON_FRACCIONAMIENTO_MATTERS or body_tokens.intersection(cls.NON_FRACCIONAMIENTO_MATTERS):
            return None

        if not cleaned_body or cleaned_body in cls.DISALLOWED_FRACCIONAMIENTO_NAMES:
            return "FRACCIONAMIENTOS_Y_COLONIAS"

        # Validar que ningún token dentro del cuerpo pertenezca a frases de razonamiento de la IA
        disallowed_in_body = {
            "CONCRETO", "TEXTO", "VISIBLE", "PARECE", "REFERIRSE", "ESPECIFICA", "MENCIONA",
            "OBSERVA", "COMENTARIO", "DESCRIPCION", "INDICA", "SENALA", "NINGUN", "NINGUNO",
            "EXISTE", "CONSTA", "FIGURA", "APARENTE", "PROBABLE", "DETECTADO", "ENCONTRADO",
            "ESPECIFICO", "GENERAL", "EXPLICACION"
        }
        if body_tokens.intersection(disallowed_in_body) or len(cleaned_body.split("_")) > 5:
            return "FRACCIONAMIENTOS_Y_COLONIAS"

        first_tok = cleaned_body.split("_")[0]
        if first_tok in cls.DISALLOWED_FRACCIONAMIENTO_NAMES and first_tok not in ("EL", "LA", "LOS", "LAS"):
            sub_body = "_".join(cleaned_body.split("_")[1:]).strip("_")
            if not sub_body or sub_body in cls.DISALLOWED_FRACCIONAMIENTO_NAMES:
                return "FRACCIONAMIENTOS_Y_COLONIAS"
            cleaned_body = sub_body

        if is_colonia:
            return cleaned_body
        return f"FRACCIONAMIENTO_{cleaned_body}"

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
            if not s_item or s_item.startswith(("⚡", "🤖", "📄", "📝", "💬", "ℹ")):
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
            if not s_item or s_item.startswith(("⚡", "🤖", "📄", "💬", "ℹ")):
                continue
            if cls._is_negative_or_absence_line(s_item):
                continue

            line_up = TextNormalizer.strip_accents(s_item.upper())
            if s_item.startswith("📝"):
                # En la línea de Resumen, exigir mención afirmativa clara como 'LIBRO ... AUXILIAR'
                line_no_reg = re.sub(r"\bREGISTRADOR\s+AUXILIAR\b", "", line_up)
                if re.search(r"\bLIBRO\s+(?:NUMERO\s+)?(?:\d+\s+)?(?:VOLUMEN\s+[A-Z0-9\"\']+\s+)?AUXILIAR\b", line_no_reg):
                    return True
                continue

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
        1A. Si NO aparece explícitamente la palabra 'AUXILIAR' en el texto literal del documento,
            el documento NO es auxiliar (restaura el Tomo numérico original y limpia Volumen).
        1B. Si SÍ aparece explícitamente la palabra 'AUXILIAR' en el texto literal del documento,
            garantiza es_auxiliar=True y que 'AUXILIAR' (o 'AUXILIAR_<CATEGORIA>' / 'AUXILIAR_<NUMERO>')
            ocupe la posición de Tomo (cuando hay número de libro).
        1C. Las categorías 'ADQUISICIONES_DE_GOBIERNO', 'DOCUMENTOS_PRIVADOS', 'CREDITO_AGRICOLA',
            'ACTOS_Y_CONTRATOS' (y sus variantes 'AUXILIAR_...') van SIEMPRE en 'tomo', NUNCA en 'fraccionamiento'.
        2.  En 'fraccionamiento' SOLO van fraccionamientos ('FRACCIONAMIENTO_<NOMBRE>'), colonias ('<NOMBRE>'),
            'CORETT', o 'FRACCIONAMIENTOS_Y_COLONIAS'. Si el documento NO menciona fraccionamientos/colonias/CORETT
            (ej. dice 'SOCIEDADES Y PODERES'), 'fraccionamiento' se limpia a None ('00').
        """
        if not isinstance(doc, dict):
            return doc, False

        evidencias = doc.get("evidencias", [])
        if not isinstance(evidencias, list):
            return doc, False

        transcripcion_literal = str(doc.get("transcripcion_literal") or "").strip()
        texto_descriptivo = str(doc.get("texto_descriptivo") or "").strip()
        has_any_text_evidence = bool(transcripcion_literal) or any(
            not str(ev).strip().startswith(("⚡", "🤖", "📄")) for ev in evidencias
        )

        repaired = dict(doc)
        changed = False

        all_text = f"{transcripcion_literal} {texto_descriptivo} " + " ".join(
            str(e) for e in evidencias
            if not str(e).strip().startswith(("⚡", "🤖", "📄", "💬", "ℹ"))
            and not cls._is_negative_or_absence_line(str(e))
        )

        has_positive_aux = False
        # --- REGLA 1: Sanitización y recuperación determinística de AUXILIAR ---
        if has_any_text_evidence:
            has_positive_aux = cls._has_explicit_auxiliar_in_document(evidencias, transcripcion_literal)
            curr_tom_up = str(doc.get("tomo") or "").strip().upper()
            if (curr_tom_up.startswith("AUXILIAR") or doc.get("es_auxiliar") or doc.get("libro") == "AUXILIAR") and not has_positive_aux:
                repaired["es_auxiliar"] = False

                # Recuperar el tomo real desde las evidencias afirmativas o desde el volumen desplazado
                restored_tomo: Optional[Any] = None
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
                elif curr_tom_up.startswith("AUXILIAR"):
                    # Si era AUXILIAR_ACTOS_Y_CONTRATOS pero no tiene auxiliar en texto, dejar ACTOS_Y_CONTRATOS
                    stripped_cat = curr_tom_up.replace("AUXILIAR_", "").strip("_")
                    if stripped_cat in cls.VALID_TOMO_CATEGORIES and stripped_cat != "AUXILIAR":
                        repaired["tomo"] = stripped_cat
                    else:
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

                changed = True

            elif has_positive_aux:
                # REGLA 1B: El documento SÍ dice explícitamente AUXILIAR en su texto literal.
                if not repaired.get("es_auxiliar"):
                    repaired["es_auxiliar"] = True
                    changed = True

                curr_lib = repaired.get("libro")
                curr_tom = repaired.get("tomo")
                if curr_lib not in (None, "0", "00", 0, "", "NULL", "NONE", "AUXILIAR"):
                    if curr_tom in (None, "0", "00", 0, "", "NULL", "NONE"):
                        repaired["tomo"] = "AUXILIAR"
                        changed = True
                    elif isinstance(curr_tom, int) or (isinstance(curr_tom, str) and str(curr_tom).strip().isdigit()):
                        if repaired.get("volumen") in (None, "0", "00", 0, "", "NULL", "NONE"):
                            repaired["volumen"] = int(str(curr_tom).strip())
                        repaired["tomo"] = "AUXILIAR"
                        changed = True
                    elif isinstance(curr_tom, str):
                        up_ct = cls._clean_alnum_label(str(curr_tom))
                        if up_ct in ("ACTOS_Y_CONTRATOS", "CREDITO_AGRICOLA", "DOCUMENTOS_PRIVADOS"):
                            repaired["tomo"] = f"AUXILIAR_{up_ct}"
                            changed = True
                        elif not cls.is_valid_tomo_category(up_ct):
                            repaired["tomo"] = "AUXILIAR"
                            changed = True

                # Si volumen está vacío pero en las citas literales se menciona explícitamente VOLUMEN <N> (ej. VOLUMEN "I")
                if repaired.get("volumen") in (None, "0", "00", 0, "", "NULL", "NONE"):
                    all_up_vol = TextNormalizer.strip_accents(all_text.upper())
                    m_vol = re.search(
                        r'\bVOLUMEN\s+(?:(?:NUMERO|NRO\.?|NUM\.?|NO\.?)\s+)?["\']?([A-Z0-9]+(?:\s*\([A-Z0-9]+\))?)["\']?',
                        all_up_vol
                    )
                    if m_vol:
                        parsed_v = TextNormalizer.parse_number_or_word(m_vol.group(1))
                        if parsed_v is not None:
                            repaired["volumen"] = parsed_v
                            changed = True

        # --- REGLA 1C: Categorías de Tomo (ADQUISICIONES_DE_GOBIERNO, DOCUMENTOS_PRIVADOS, CREDITO_AGRICOLA, ACTOS_Y_CONTRATOS) ---
        is_aux_now = bool(repaired.get("es_auxiliar") or str(repaired.get("tomo") or "").upper().startswith("AUXILIAR"))
        curr_fracc_check = str(repaired.get("fraccionamiento") or "").strip().upper()
        if curr_fracc_check in (
            "DOCUMENTOS_PRIVADOS", "CREDITO_AGRICOLA", "ACTOS_Y_CONTRATOS", "ADQUISICIONES_DE_GOBIERNO",
            "AUXILIAR_DOCUMENTOS_PRIVADOS", "AUXILIAR_CREDITO_AGRICOLA", "AUXILIAR_ACTOS_Y_CONTRATOS"
        ):
            # Esas categorías van en Tomo, nunca en Fraccionamiento
            base_cat = curr_fracc_check.replace("AUXILIAR_", "")
            if is_aux_now and base_cat in ("DOCUMENTOS_PRIVADOS", "CREDITO_AGRICOLA", "ACTOS_Y_CONTRATOS"):
                new_tom = f"AUXILIAR_{base_cat}"
            else:
                new_tom = base_cat
            if repaired.get("tomo") in (None, "0", "00", 0, "", "NULL", "NONE", "AUXILIAR"):
                repaired["tomo"] = new_tom
            repaired["fraccionamiento"] = None
            changed = True

        if has_any_text_evidence:
            detected_tomo_cat = cls.extract_tomo_category(all_text, is_auxiliar=is_aux_now)
            if detected_tomo_cat:
                if repaired.get("tomo") in (None, "0", "00", 0, "", "NULL", "NONE", "AUXILIAR"):
                    if repaired.get("tomo") != detected_tomo_cat:
                        repaired["tomo"] = detected_tomo_cat
                        changed = True

            # Si en tomo quedó un texto que no es numérico ni una categoría válida de Tomo, limpiarlo
            curr_t_val = repaired.get("tomo")
            if isinstance(curr_t_val, str) and not curr_t_val.strip().isdigit() and curr_t_val.strip().upper() not in ("0", "00", "NULL", "NONE", ""):
                cleaned_t = cls._clean_alnum_label(curr_t_val)
                if cls.is_valid_tomo_category(cleaned_t):
                    if cleaned_t != curr_t_val:
                        repaired["tomo"] = cleaned_t
                        changed = True
                else:
                    repaired["tomo"] = "AUXILIAR" if is_aux_now else None
                    changed = True

        # --- REGLA 2: Exclusividad y Prioridad de Fraccionamientos / Colonias / CORETT en Posición 6 ---
        curr_fracc = repaired.get("fraccionamiento")
        curr_fracc_str = str(curr_fracc or "").strip().upper()

        # Si hay evidencia textual del documento y el documento NO habla en absoluto de Fraccionamientos, Colonias, Predios ni CORETT
        # (por ejemplo, el libro es de 'SOCIEDADES Y PODERES'), Posición 6 DEBE ser None ('00').
        if has_any_text_evidence and not cls._has_fraccionamiento_or_colonia_in_document(all_text):
            if curr_fracc not in (None, "00", "0", ""):
                repaired["fraccionamiento"] = None
                ev_list = [
                    e for e in repaired.get("evidencias", [])
                    if not str(e).strip().startswith("ℹ Fraccionamiento específico detectado:")
                ]
                repaired["evidencias"] = ev_list
                curr_fracc = None
                curr_fracc_str = ""
                changed = True
        else:
            # El documento SÍ trata de Fraccionamientos / Colonias / CORETT (o no tiene texto)
            # 1. Si era 'COLONIAS' o un valor espurio de razonamiento de IA, normalizar a 'FRACCIONAMIENTOS_Y_COLONIAS'
            if curr_fracc_str in [
                "COLONIAS", "FRACCIONAMIENTOS",
                "FRACCIONAMIENTO_DETECTADO", "FRACCIONAMIENTO_ENCONTRADO", "FRACCIONAMIENTO_MENCIONADO",
                "FRACCIONAMIENTO_PROPUESTO", "FRACCIONAMIENTO_SUGERIDO", "FRACCIONAMIENTO_ESPECIFICO",
                "FRACCIONAMIENTO_GENERAL"
            ] or any(bad in curr_fracc_str for bad in [
                "CONCRETO", "TEXTO", "VISIBLE", "PARECE", "REFERIRSE", "ESPECIFICA",
                "MENCIONA", "OBSERVA", "COMENTARIO", "EXPLICACION", "DESCRIPCION"
            ]) or len(curr_fracc_str.split("_")) > 6:
                curr_fracc = "FRACCIONAMIENTOS_Y_COLONIAS"
                repaired["fraccionamiento"] = "FRACCIONAMIENTOS_Y_COLONIAS"
                changed = True

            # 2. Siempre verificar si en el texto se nombra un Fraccionamiento específico, una Colonia específica o CORETT
            specific_f = cls.extract_specific_fraccionamiento(all_text) if has_any_text_evidence else None
            if specific_f:
                if specific_f != repaired.get("fraccionamiento"):
                    old_f = repaired.get("fraccionamiento")
                    repaired["fraccionamiento"] = specific_f
                    ev_list = [
                        e for e in repaired.get("evidencias", [])
                        if not str(e).strip().startswith("ℹ Fraccionamiento específico detectado:")
                    ]
                    ev_list.append(f"ℹ Fraccionamiento/Colonia detectado: '{specific_f}' (anterior: '{old_f}')")
                    repaired["evidencias"] = ev_list
                    changed = True
            elif curr_fracc and str(curr_fracc).strip().upper() not in ["FRACCIONAMIENTOS_Y_COLONIAS", "CORETT", "NULL", "NONE", "00", "0"]:
                norm_f = cls.normalize_fraccionamiento_label(str(curr_fracc))
                if norm_f != curr_fracc:
                    repaired["fraccionamiento"] = norm_f
                    ev_list = [
                        (
                            f"ℹ Fraccionamiento/Colonia detectado: '{norm_f}' (normalizado)"
                            if str(e).strip().startswith("ℹ Fraccionamiento")
                            else e
                        )
                        for e in repaired.get("evidencias", [])
                        if not (norm_f is None and str(e).strip().startswith("ℹ Fraccionamiento"))
                    ]
                    repaired["evidencias"] = ev_list
                    changed = True

        # --- REGLA 3: Recuperación determinística de Sección Numerada si está en null o ceros ---
        curr_sec = repaired.get("seccion")
        if curr_sec is None or str(curr_sec).strip() in ["0", "00", "NULL", "NONE"]:
            found_sec = cls.extract_numbered_section(all_text)
            if found_sec is not None and found_sec != curr_sec:
                repaired["seccion"] = found_sec
                ev_list = list(repaired.get("evidencias", []))
                ev_list.append(f"ℹ Sección numerada detectada en texto: 'SECCIÓN {found_sec}' (recuperada determinísticamente)")
                repaired["evidencias"] = ev_list
                changed = True

        return (repaired, True) if changed else (doc, False)

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

        transcripcion_literal = str(data.get("transcripcion_literal") or "").strip()
        texto_descriptivo = str(data.get("texto_descriptivo") or "").strip()
        comentarios_ia = str(data.get("comentarios_ia") or "").strip()
        if not texto_descriptivo and comentarios_ia:
            texto_descriptivo = comentarios_ia

        if texto_descriptivo:
            resumen_item = f"📝 Resumen: {texto_descriptivo}"
            if not any(str(ev).startswith("📝 Resumen:") for ev in evidencias):
                evidencias = [resumen_item] + list(evidencias)

        if comentarios_ia and comentarios_ia != texto_descriptivo:
            nota_item = f"💬 Observación IA: {comentarios_ia}"
            if not any(str(ev).startswith("💬 Observación IA:") for ev in evidencias):
                evidencias.append(nota_item)

        if isinstance(data.get("datos_estructurados"), dict):
            fields_src: Dict[str, Any] = dict(data["datos_estructurados"])
        elif isinstance(data.get("datos_extraidos"), dict):
            fields_src: Dict[str, Any] = dict(data["datos_extraidos"])
        else:
            fields_src = dict(data)
        evidencias_joined = cls._extract_positive_evidencias_text(evidencias)
        all_text_context = f"{transcripcion_literal}\n{evidencias_joined}\n{texto_descriptivo}"

        # 1. Sección: SOLO puede ser numérica (1..5) o None. Si viene texto alfanumérico, se evalúa para Tomo.
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
                if cleaned_s and cleaned_s not in ["NULL", "NONE", "0", "00"] and cls.is_valid_tomo_category(cleaned_s):
                    alnum_from_sec = cleaned_s
        else:
            sec_val = None

        # REGLA DETERMINÍSTICA: Si la sección no vino definida numéricamente (null o 0),
        # pero el documento menciona explícitamente una sección numerada (ej. 'SECCIÓN PRIMERA',
        # '(SECCIÓN PRIMERA)', etc.), recuperarla determinísticamente del texto.
        if sec_val is None:
            detected_sec = cls.extract_numbered_section(all_text_context)
            if detected_sec is not None:
                sec_val = detected_sec

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
                num_in_lib = TextNormalizer.parse_number_or_word(re.sub(r"AUXILIAR", "", up_lib).strip())
                if num_in_lib is not None:
                    lib_val = num_in_lib
                else:
                    lib_val = "AUXILIAR"
            else:
                parsed_l = TextNormalizer.parse_number_or_word(up_lib)
                lib_val = parsed_l if parsed_l is not None else up_lib

        if lib_val == "AUXILIAR" and all_text_context:
            m_lib = re.search(
                r"LIBRO\s+(?:NUMERO\s+)?(\d+|PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|UNO|DOS|TRES|CUATRO|CINCO)\b",
                TextNormalizer.strip_accents(all_text_context.upper())
            )
            if m_lib:
                num_ev = TextNormalizer.parse_number_or_word(m_lib.group(1))
                if num_ev is not None:
                    lib_val = num_ev
                    lib_had_auxiliar = True

        # 3. Tomo (numérico o categoría alfanumérica permitida en Tomo)
        raw_tom = fields_src.get("tomo")
        tom_val: Optional[Any] = None
        if isinstance(raw_tom, int):
            tom_val = raw_tom
        elif isinstance(raw_tom, str) and raw_tom.strip():
            up_tom = raw_tom.strip().upper()
            if up_tom not in ["NULL", "NONE", "0", "00"]:
                parsed_t = TextNormalizer.parse_number_or_word(up_tom)
                if parsed_t is not None and up_tom.isdigit():
                    tom_val = parsed_t
                elif parsed_t is not None and not any(
                    w in up_tom for w in ["CREDITO", "ACTOS", "AUXILIAR", "AUX", "CONTRATOS", "AGRICOLA", "DOCUMENTO", "PRIVADO", "ADQUISICION", "GOBIERNO"]
                ):
                    tom_val = parsed_t
                else:
                    cleaned_tom = cls._clean_alnum_label(up_tom)
                    if cls.is_valid_tomo_category(cleaned_tom):
                        tom_val = cleaned_tom

        # REGLA FUNDAMENTAL DE AUXILIAR:
        has_real_auxiliar = cls._has_explicit_auxiliar_in_document(evidencias, transcripcion_literal)

        if not has_real_auxiliar:
            es_aux = False
            lib_had_auxiliar = False
            if isinstance(tom_val, str) and tom_val.startswith("AUXILIAR"):
                stripped_t = tom_val.replace("AUXILIAR_", "").strip("_")
                tom_val = stripped_t if (stripped_t in cls.VALID_TOMO_CATEGORIES and stripped_t != "AUXILIAR") else None
            if lib_val == "AUXILIAR":
                lib_val = None

            has_positive_vol = any(
                "VOLUMEN" in TextNormalizer.strip_accents(str(ev).upper())
                and not cls._is_negative_or_absence_line(str(ev))
                for ev in evidencias
            )
            raw_vol_val = fields_src.get("volumen")
            if tom_val is None and isinstance(raw_vol_val, int) and not has_positive_vol:
                tom_val = raw_vol_val
                fields_src["volumen"] = None

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

        is_auxiliar_doc = bool(has_real_auxiliar)

        if is_auxiliar_doc:
            es_aux = True
            if lib_val is not None and lib_val != "AUXILIAR":
                if tom_val is None:
                    tom_val = "AUXILIAR"
                elif isinstance(tom_val, int) and fields_src.get("volumen") is None:
                    fields_src["volumen"] = tom_val
                    tom_val = "AUXILIAR"
                elif isinstance(tom_val, str) and not tom_val.startswith("AUXILIAR"):
                    if tom_val in ("ACTOS_Y_CONTRATOS", "CREDITO_AGRICOLA", "DOCUMENTOS_PRIVADOS"):
                        tom_val = f"AUXILIAR_{tom_val}"
                    else:
                        tom_val = "AUXILIAR"
            elif lib_val is None:
                lib_val = "AUXILIAR"

        # Detectar categoría de Tomo desde el texto (ADQUISICIONES_DE_GOBIERNO, DOCUMENTOS_PRIVADOS, CREDITO_AGRICOLA, ACTOS_Y_CONTRATOS, AUXILIAR_<N>)
        detected_cat = cls.extract_tomo_category(all_text_context, is_auxiliar=is_auxiliar_doc)
        if detected_cat:
            if tom_val in (None, "AUXILIAR"):
                tom_val = detected_cat
        elif tom_val is None and alnum_from_sec:
            if is_auxiliar_doc and alnum_from_sec in ("ACTOS_Y_CONTRATOS", "CREDITO_AGRICOLA", "DOCUMENTOS_PRIVADOS"):
                tom_val = f"AUXILIAR_{alnum_from_sec}"
            else:
                tom_val = alnum_from_sec

        # 4. Volumen
        raw_vol = fields_src.get("volumen")
        if isinstance(raw_vol, int):
            vol_val = raw_vol
        elif isinstance(raw_vol, str) and raw_vol.strip():
            parsed_v = TextNormalizer.parse_number_or_word(raw_vol)
            vol_val = parsed_v if parsed_v is not None else None
        else:
            vol_val = None

        if vol_val is None and all_text_context:
            all_up_vol = TextNormalizer.strip_accents(all_text_context.upper())
            m_vol = re.search(
                r'\bVOLUMEN\s+(?:(?:NUMERO|NRO\.?|NUM\.?|NO\.?)\s+)?["\']?([A-Z0-9]+(?:\s*\([A-Z0-9]+\))?)["\']?',
                all_up_vol
            )
            if m_vol:
                vol_val = TextNormalizer.parse_number_or_word(m_vol.group(1))

        # 5. Fraccionamiento (EXCLUSIVAMENTE para Fraccionamientos, Colonias o CORETT)
        raw_fracc = fields_src.get("fraccionamiento")
        fracc_val: Optional[str] = None
        if raw_fracc and str(raw_fracc).strip().upper() not in ["NULL", "NONE", "00", "0"]:
            cleaned_f = cls._clean_alnum_label(str(raw_fracc))
            if cls.is_valid_tomo_category(cleaned_f) and cleaned_f != "AUXILIAR":
                # Si Claude puso una categoría de Tomo en fraccionamiento, moverla a Tomo
                base_c = cleaned_f.replace("AUXILIAR_", "")
                if is_auxiliar_doc and base_c in ("ACTOS_Y_CONTRATOS", "CREDITO_AGRICOLA", "DOCUMENTOS_PRIVADOS"):
                    tom_val = f"AUXILIAR_{base_c}"
                elif tom_val in (None, "AUXILIAR"):
                    tom_val = cleaned_f
                fracc_val = None
            else:
                fracc_val = cls.normalize_fraccionamiento_label(str(raw_fracc))

        # Si hay texto en el documento y NO menciona en absoluto Fraccionamientos, Colonias, Predios ni CORETT,
        # la Posición 6 (fraccionamiento) DEBE ser None ('00').
        has_any_doc_text = bool(all_text_context.strip())
        if has_any_doc_text and not cls._has_fraccionamiento_or_colonia_in_document(all_text_context):
            fracc_val = None
        else:
            specific_fracc = cls.extract_specific_fraccionamiento(all_text_context)
            if specific_fracc:
                fracc_val = specific_fracc
            elif fracc_val in (None, "COLONIAS", "FRACCIONAMIENTOS") and cls._has_fraccionamiento_or_colonia_in_document(all_text_context):
                fracc_val = "FRACCIONAMIENTOS_Y_COLONIAS"

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
            "es_auxiliar": es_aux or str(tom_val or "").startswith("AUXILIAR") or (lib_val == "AUXILIAR"),
            "transcripcion_literal": transcripcion_literal,
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
