import re
from pathlib import Path
from typing import Dict, Any, Optional, List


class FolderParser:
    """
    Parsea la estructura estándar de las carpetas de libros registrales:
    Formato esperado de 7 posiciones hasta el año:
    [0] Delegación (siempre numérica de 2 dígitos, ej. '01' - inmutable)
    [1] Sección (SOLO numérica: '00', '01', '02', '03', '04', '05')
    [2] Libro (numérico: '01', '02', '100', etc.)
    [3] Tomo (numérico '00', '01'... O nombre alfanumérico de sección/tipo de libro cuando no es
              fraccionamiento, ej. 'CREDITO_AGRICOLA', 'ACTOS_Y_CONTRATOS', 'AUXILIAR')
    [4] Volumen (numérico: '00', '01', '02'...)
    [5] Fraccionamiento ('00' o nombre compuesto como 'COLONIAS', 'FRACCIONAMIENTOS_Y_COLONIAS',
                        'FRACCIONAMIENTO_PATRIA_NUEVA')
    [6] Año (4 dígitos: '1986', '1995', '2005', '0000')
    [7+] Sufijos adicionales (ej. 'REVISAR', 'REVISAR_SEPARADO', '2')
    """

    @staticmethod
    def parse(folder_input: Any) -> Dict[str, Any]:
        folder_name = Path(folder_input).name if isinstance(folder_input, (str, Path)) else str(folder_input)
        parts: List[str] = folder_name.split("_")

        year_pattern = re.compile(r"^(19\d{2}|20\d{2}|0000)$")
        year_idx: Optional[int] = None

        # Buscar el año de 4 dígitos (empezando desde la posición 6 en adelante)
        if len(parts) >= 7:
            for idx in range(6, len(parts)):
                if year_pattern.match(parts[idx]):
                    year_idx = idx
                    break

        if year_idx is not None and year_idx > 3:
            delegacion = parts[0]
            seccion_raw = parts[1]
            libro_raw = parts[2]
            anio_raw = parts[year_idx]
            sufijo = "_".join(parts[year_idx + 1:]) if (year_idx + 1) < len(parts) else None

            mid = parts[3:year_idx]
            if len(mid) == 3:
                tomo_raw, volumen_raw, fraccionamiento_raw = mid[0], mid[1], mid[2]
            elif len(mid) > 3:
                # Caso 1: Los dos últimos elementos antes del año son numéricos (ej. CREDITO_AGRICOLA_00_00 o ACTOS_Y_CONTRATOS_00_00)
                if mid[-2].isdigit() and mid[-1].isdigit():
                    tomo_raw = "_".join(mid[:-2])
                    volumen_raw = mid[-2]
                    fraccionamiento_raw = mid[-1]
                else:
                    # Caso 2: Buscar el token numérico de Volumen entre mid[1] y mid[-2]
                    vol_idx = None
                    for k in range(1, len(mid) - 1):
                        if mid[k].isdigit():
                            vol_idx = k
                            break
                    if vol_idx is not None:
                        tomo_raw = "_".join(mid[:vol_idx])
                        volumen_raw = mid[vol_idx]
                        fraccionamiento_raw = "_".join(mid[vol_idx + 1:])
                    else:
                        tomo_raw = mid[0]
                        volumen_raw = mid[1]
                        fraccionamiento_raw = "_".join(mid[2:])
            elif len(mid) == 2:
                tomo_raw, volumen_raw, fraccionamiento_raw = mid[0], mid[1], "00"
            elif len(mid) == 1:
                tomo_raw, volumen_raw, fraccionamiento_raw = mid[0], "00", "00"
            else:
                tomo_raw, volumen_raw, fraccionamiento_raw = "00", "00", "00"
        else:
            delegacion = parts[0] if len(parts) > 0 else None
            seccion_raw = parts[1] if len(parts) > 1 else None
            libro_raw = parts[2] if len(parts) > 2 else None
            tomo_raw = parts[3] if len(parts) > 3 else None
            volumen_raw = parts[4] if len(parts) > 4 else None
            fraccionamiento_raw = parts[5] if len(parts) > 5 else None
            anio_raw = parts[6] if len(parts) > 6 else None
            sufijo = "_".join(parts[7:]) if len(parts) > 7 else None

        data: Dict[str, Any] = {
            "folder_name": folder_name,
            "delegacion": delegacion,
            "seccion_raw": seccion_raw,
            "libro_raw": libro_raw,
            "tomo_raw": tomo_raw,
            "volumen_raw": volumen_raw,
            "fraccionamiento_raw": fraccionamiento_raw,
            "anio_raw": anio_raw,
            "sufijo": sufijo,
        }

        def to_int_or_str(val: Optional[str]) -> Optional[Any]:
            if val is None:
                return None
            val_clean = val.strip()
            if val_clean.isdigit():
                return int(val_clean)
            return val_clean

        data["seccion_val"] = to_int_or_str(data["seccion_raw"])
        data["libro_val"] = to_int_or_str(data["libro_raw"])
        data["tomo_val"] = to_int_or_str(data["tomo_raw"])
        data["volumen_val"] = to_int_or_str(data["volumen_raw"])
        data["fraccionamiento_val"] = to_int_or_str(data["fraccionamiento_raw"])
        data["anio_val"] = to_int_or_str(data["anio_raw"])

        data["tiene_ceros"] = any(
            data[field] in [0, "0", "00", "0000"]
            for field in ["seccion_val", "libro_val", "tomo_val"]
        )

        return data
