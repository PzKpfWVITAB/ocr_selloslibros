import cv2
import numpy as np
from typing import List, Dict, Any, Optional

try:
    import winocr
    WINOCR_AVAILABLE = True
except ImportError:
    WINOCR_AVAILABLE = False

class WindowsMediaOCREngine:
    """
    Segundo motor OCR basado en Windows Media OCR (WinRT).
    Nativo de Windows 10/11, ultrarrápido (~200ms) y acelerado por hardware.
    """
    _instance: Optional["WindowsMediaOCREngine"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(WindowsMediaOCREngine, cls).__new__(cls)
        return cls._instance

    def extract_text(self, img_bgr: np.ndarray, lang: str = "es") -> List[Dict[str, Any]]:
        """
        Ejecuta el OCR nativo de Windows sobre la imagen.
        Retorna: [{'text': str, 'confidence': float, 'box': list, 'engine': 'WinOCR'}]
        """
        results: List[Dict[str, Any]] = []
        if not WINOCR_AVAILABLE:
            return results

        try:
            # WinOCR funciona de forma óptima con imágenes en escala de grises o BGR
            if len(img_bgr.shape) == 3 and img_bgr.shape[2] == 3:
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            else:
                gray = img_bgr

            raw_res = winocr.recognize_cv2_sync(gray, lang)
            lines = raw_res.get("lines", [])

            for line in lines:
                text = str(line.get("text", "")).strip()
                if text:
                    words = line.get("words", [])
                    box = []
                    if words:
                        # Extraer bounding rect si está disponible
                        rect = words[0].get("bounding_rect", {})
                        x = rect.get("x", 0)
                        y = rect.get("y", 0)
                        w = rect.get("width", 0)
                        h = rect.get("height", 0)
                        box = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]

                    results.append({
                        "text": text,
                        "confidence": 0.85,  # WinOCR no expone float crudo por línea, asignamos base alta
                        "box": box,
                        "engine": "WinOCR"
                    })
        except Exception as e:
            # Fallback seguro sin abortar el flujo
            pass

        return results
