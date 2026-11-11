import numpy as np
from typing import List, Dict, Any, Optional
from rapidocr_onnxruntime import RapidOCR
from src.core.secondary_ocr import WindowsMediaOCREngine

class OCREngine:
    """
    Motor OCR dual combinado (Ensemble):
    1. RapidOCR (ONNX Runtime / PP-OCRv4) - Excelente para sellos, texto rotado y digitalizaciones.
    2. Windows Media OCR (WinRT) - Motor nativo de Windows para contrastar y validar resultados.
    """
    _instance: Optional["OCREngine"] = None
    _rapid_engine: Optional[RapidOCR] = None
    _win_engine: Optional[WindowsMediaOCREngine] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(OCREngine, cls).__new__(cls)
            cls._rapid_engine = RapidOCR()
            # Reducir umbrales de DBNet para no descartar cajas de texto con tinta tenue/violeta sobre papel rayado
            if hasattr(cls._rapid_engine, "text_det") and hasattr(cls._rapid_engine.text_det, "postprocess_op"):
                cls._rapid_engine.text_det.postprocess_op.box_thresh = 0.15
                cls._rapid_engine.text_det.postprocess_op.thresh = 0.15
            cls._win_engine = WindowsMediaOCREngine()
        return cls._instance

    def extract_text(self, img_bgr: np.ndarray, use_dual: bool = True) -> List[Dict[str, Any]]:
        """
        Ejecuta ambos motores OCR sobre la imagen y combina los resultados.
        Retorna una lista consolidada: [{'text': str, 'confidence': float, 'box': list, 'engine': str}]
        """
        results: List[Dict[str, Any]] = []
        seen_texts = set()

        # 1. Ejecutar RapidOCR (Motor Principal)
        # use_cls=False evita que las líneas de renglón inviertan 180° las cajas de texto horizontales
        raw_rapid, _ = self._rapid_engine(img_bgr, use_cls=False)
        if raw_rapid:
            for item in raw_rapid:
                box = item[0]
                text = str(item[1]).strip()
                conf = float(item[2])
                if text:
                    norm_key = text.upper()
                    seen_texts.add(norm_key)
                    results.append({
                        "text": text,
                        "confidence": conf,
                        "box": box,
                        "engine": "RapidOCR"
                    })

        # 2. Ejecutar Windows Media OCR (Motor Secundario de Apoyo)
        if use_dual and self._win_engine:
            win_lines = self._win_engine.extract_text(img_bgr, lang="es")
            for item in win_lines:
                norm_key = item["text"].upper()
                # Si WinOCR aportó texto nuevo o diferente, se añade a la lista combinada
                if norm_key not in seen_texts:
                    seen_texts.add(norm_key)
                    results.append(item)

        return results
