import re
from typing import Dict, Any, List, Optional
from src.parsers.text_normalizer import TextNormalizer


class FolderComparator:
    """
    Compara los metadatos de la carpeta contra los resultados consolidados de Claude/OCR
    y genera un veredicto de concordancia detallado campo por campo.
    Regla:
    - Si no se encuentra información de un campo, su valor por defecto es '00' (o '0000' en año).
    - Las secciones nunca son alfanuméricas; cualquier sección o categoría alfanumérica que no
      sea fraccionamiento (ej. CREDITO_AGRICOLA, ACTOS_Y_CONTRATOS, AUXILIAR) se ubica en 'tomo'.
    """

    @classmethod
    def synthesize_detections(cls, doc_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        final: Dict[str, Any] = {
            "seccion": None,
            "libro": None,
            "tomo": None,
            "volumen": None,
            "fraccionamiento": None,
            "anio": None,
            "es_auxiliar": False,
            "evidencias_globales": []
        }

        # 1. Libro
        if "CARATULA" in doc_results and doc_results["CARATULA"].get("libro") is not None:
            final["libro"] = doc_results["CARATULA"]["libro"]
            final["evidencias_globales"].append(f"Libro={final['libro']} autorizado en sello oficial de CARÁTULA")
            if final["libro"] == "AUXILIAR":
                final["es_auxiliar"] = True
        else:
            for doc_key, res in doc_results.items():
                if res.get("libro") == "AUXILIAR":
                    final["es_auxiliar"] = True
                    final["libro"] = "AUXILIAR"
                    final["evidencias_globales"].append(f"Regla Auxiliar: Detectado en documento '{doc_key}'")
                    break

            if final["libro"] is None:
                for doc_key in ["PRIMERO", "ENMEDIO", "PENULTIMO"]:
                    if doc_key in doc_results and doc_results[doc_key].get("libro") is not None:
                        final["libro"] = doc_results[doc_key]["libro"]
                        final["evidencias_globales"].append(f"Libro={final['libro']} tomado de '{doc_key}'")
                        break

        # 2. Tomo (numérico o alfanumérico como CREDITO_AGRICOLA, ACTOS_Y_CONTRATOS, AUXILIAR)
        for doc_key in ["CARATULA", "PRIMERO", "ENMEDIO", "PENULTIMO"]:
            if doc_key in doc_results and doc_results[doc_key].get("tomo") is not None:
                final["tomo"] = doc_results[doc_key]["tomo"]
                if final["tomo"] == "AUXILIAR":
                    final["es_auxiliar"] = True
                final["evidencias_globales"].append(f"Tomo={final['tomo']} tomado de '{doc_key}'")
                break

        # 3. Sección (solo numérica)
        for doc_key in ["CARATULA", "PRIMERO", "ENMEDIO", "PENULTIMO"]:
            if doc_key in doc_results and doc_results[doc_key].get("seccion") is not None:
                final["seccion"] = doc_results[doc_key]["seccion"]
                final["evidencias_globales"].append(f"Sección={final['seccion']} tomado de '{doc_key}'")
                break

        # 4. Volumen
        for doc_key in ["CARATULA", "PRIMERO", "ENMEDIO", "PENULTIMO"]:
            if doc_key in doc_results and doc_results[doc_key].get("volumen") is not None:
                final["volumen"] = doc_results[doc_key]["volumen"]
                final["evidencias_globales"].append(f"Volumen={final['volumen']} tomado de '{doc_key}'")
                break

        # 5. Año
        for doc_key in ["CARATULA", "PRIMERO", "ENMEDIO", "PENULTIMO"]:
            if doc_key in doc_results and doc_results[doc_key].get("anio") is not None:
                final["anio"] = doc_results[doc_key]["anio"]
                final["evidencias_globales"].append(f"Año={final['anio']} tomado de '{doc_key}'")
                break

        # 6. Fraccionamiento / Colonia
        for doc_key in ["CARATULA", "PRIMERO", "ENMEDIO", "PENULTIMO"]:
            if doc_key in doc_results and doc_results[doc_key].get("fraccionamiento"):
                final["fraccionamiento"] = doc_results[doc_key]["fraccionamiento"]
                final["evidencias_globales"].append(f"Fraccionamiento={final['fraccionamiento']} tomado de '{doc_key}'")
                break

        for field in ["seccion", "libro", "tomo", "volumen", "fraccionamiento"]:
            if final[field] is None:
                final[field] = "00"
                final["evidencias_globales"].append(f"{field.capitalize()}: No detectado -> asignado '00'")

        if final["anio"] is None:
            final["anio"] = "0000"
            final["evidencias_globales"].append("Año: No detectado -> asignado '0000'")

        return final

    @classmethod
    def _normalize_for_comparison(cls, val: Any) -> Any:
        if val is None:
            return 0
        s = TextNormalizer.strip_accents(str(val).strip().upper())
        if s in ["", "0", "00", "000", "0000", "NULL", "NONE"]:
            return 0
        if s.isdigit():
            return int(s)
        s = re.sub(r"^INDICE[\s_]+DE[\s_]+", "", s)
        s = re.sub(r"^SECCION[\s_]+DE[\s_]+", "", s)
        s = re.sub(r"[^A-Z0-9]+", "_", s).strip("_")
        return s

    @classmethod
    def compare(cls, folder_data: Dict[str, Any], ocr_final: Dict[str, Any]) -> Dict[str, Any]:
        detalles_campos: Dict[str, Dict[str, Any]] = {}
        discrepancias: List[str] = []
        coincidencias: List[str] = []
        propuestas_ceros: List[str] = []

        campos = [
            ("seccion", "seccion_val"),
            ("libro", "libro_val"),
            ("tomo", "tomo_val"),
            ("volumen", "volumen_val"),
            ("fraccionamiento", "fraccionamiento_val"),
            ("anio", "anio_val"),
        ]

        for nombre_campo, folder_key in campos:
            val_folder = folder_data.get(folder_key)
            val_ocr = ocr_final.get(nombre_campo)

            norm_folder = cls._normalize_for_comparison(val_folder)
            norm_ocr = cls._normalize_for_comparison(val_ocr)

            is_folder_zero = (norm_folder == 0)
            is_ocr_zero = (norm_ocr == 0)

            if is_folder_zero and is_ocr_zero:
                estado = "COINCIDE"
                coincidencias.append(nombre_campo)
            elif is_folder_zero and not is_ocr_zero:
                estado = "CARPETA_EN_CEROS"
                propuestas_ceros.append(f"{nombre_campo.capitalize()}: Carpeta=00 -> Detectado={val_ocr}")
            elif not is_folder_zero and is_ocr_zero:
                estado = "DISCREPANCIA"
                discrepancias.append(f"{nombre_campo.capitalize()}: Carpeta={val_folder} vs Detectado=00")
            elif norm_folder == norm_ocr:
                estado = "COINCIDE"
                coincidencias.append(nombre_campo)
            else:
                estado = "DISCREPANCIA"
                discrepancias.append(f"{nombre_campo.capitalize()}: Carpeta={val_folder} vs Detectado={val_ocr}")

            detalles_campos[nombre_campo] = {
                "carpeta": val_folder,
                "ocr": val_ocr,
                "estado": estado
            }

        if discrepancias:
            veredicto = "DISCREPANCIA"
        elif propuestas_ceros:
            veredicto = "RELLENO_DE_CEROS_PROPUESTO"
        elif coincidencias:
            veredicto = "COINCIDE_TOTAL"
        else:
            veredicto = "INDETERMINADO"

        return {
            "veredicto": veredicto,
            "discrepancias": discrepancias,
            "coincidencias": coincidencias,
            "propuestas_ceros": propuestas_ceros,
            "detalles_campos": detalles_campos,
            "ocr_consolidado": ocr_final,
            "folder_data": folder_data
        }
