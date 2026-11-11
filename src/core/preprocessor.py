import cv2
import numpy as np
from typing import Tuple

class ImagePreprocessor:
    """
    Pipeline de filtros de Visión por Computadora para digitalizaciones con texto opaco,
    sellos desteñidos o fondos irregulares.
    """

    def __init__(self, clip_limit: float = 3.0, tile_grid_size: Tuple[int, int] = (8, 8)):
        self.clip_limit = clip_limit
        self.tile_grid_size = tile_grid_size
        self.clahe = cv2.createCLAHE(clipLimit=self.clip_limit, tileGridSize=self.tile_grid_size)

    def enhance_faint_text(self, img_bgr: np.ndarray, apply_unsharp: bool = True) -> np.ndarray:
        """
        Aplica realce adaptativo de contraste (CLAHE) y máscara de enfoque a texto opaco.
        Mantiene el formato BGR requerido por el motor OCR.
        """
        # 1. Convertir a escala de grises si tiene 3 canales
        if len(img_bgr.shape) == 3 and img_bgr.shape[2] == 3:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        else:
            gray = img_bgr.copy()

        # 2. Aplicar CLAHE para maximizar el contraste local de la tinta pálida/sellos
        enhanced_gray = self.clahe.apply(gray)

        # 3. Máscara de enfoque suave (Unsharp Masking) para definir bordes de texto antiguo
        if apply_unsharp:
            blurred = cv2.GaussianBlur(enhanced_gray, (0, 0), sigmaX=2.0)
            enhanced_gray = cv2.addWeighted(enhanced_gray, 1.4, blurred, -0.4, 0)

        # 4. Devolver en formato BGR de 3 canales (óptimo para RapidOCR)
        return cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2BGR)

    def binarize_adaptive(self, img_bgr: np.ndarray) -> np.ndarray:
        """
        Binarización adaptativa para casos extremos con manchas o fondos muy oscuros.
        """
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
        )
        return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
