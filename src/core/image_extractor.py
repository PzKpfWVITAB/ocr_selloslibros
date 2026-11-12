import numpy as np
import cv2
from pathlib import Path
from typing import List, Tuple
import pymupdf

class ImageExtractor:
    """
    Renderiza y extrae en memoria las primeras páginas de un archivo PDF a arrays de OpenCV.
    """

    @staticmethod
    def extract_pages(pdf_path: Path, max_pages: int = 3, dpi: int = 200) -> List[Tuple[int, np.ndarray]]:
        """
        Retorna una lista de tuplas (número_página, imagen_bgr_numpy).
        Las páginas inician en índice 1.
        """
        results: List[Tuple[int, np.ndarray]] = []
        doc = pymupdf.open(str(pdf_path))
        try:
            total_pages = min(max_pages, len(doc))
            for page_idx in range(total_pages):
                page = doc[page_idx]
                pix = page.get_pixmap(dpi=dpi)
                
                # Convertir bytes directos a array NumPy
                img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                
                # Normalizar a espacio BGR de OpenCV
                if pix.n == 4:
                    bgr = cv2.cvtColor(img_data, cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:
                    bgr = cv2.cvtColor(img_data, cv2.COLOR_RGB2BGR)
                elif pix.n == 1:
                    bgr = cv2.cvtColor(img_data, cv2.COLOR_GRAY2BGR)
                else:
                    bgr = img_data

                results.append((page_idx + 1, bgr))
        finally:
            doc.close()

        return results

    @staticmethod
    def extract_specific_page(pdf_path: Path, page_num: int = 1, dpi: int = 200) -> List[Tuple[int, np.ndarray]]:
        """
        Extrae únicamente la página solicitada (1-indexed) del PDF como tupla (page_num, bgr_numpy).
        Ideal para enviar a Claude únicamente la página de la CARÁTULA que contiene texto/sellos.
        """
        results: List[Tuple[int, np.ndarray]] = []
        doc = pymupdf.open(str(pdf_path))
        try:
            idx = max(0, min(len(doc) - 1, int(page_num) - 1))
            if len(doc) > 0:
                page = doc[idx]
                pix = page.get_pixmap(dpi=dpi)
                img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                if pix.n == 4:
                    bgr = cv2.cvtColor(img_data, cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:
                    bgr = cv2.cvtColor(img_data, cv2.COLOR_RGB2BGR)
                elif pix.n == 1:
                    bgr = cv2.cvtColor(img_data, cv2.COLOR_GRAY2BGR)
                else:
                    bgr = img_data
                results.append((idx + 1, bgr))
        finally:
            doc.close()
        return results

    @staticmethod
    def extract_pages_by_numbers(
        pdf_path: Path,
        page_nums: List[int],
        dpi: int = 200
    ) -> List[Tuple[int, np.ndarray]]:
        """
        Extrae un conjunto ordenado de páginas específicas (1-indexed) del PDF como lista de
        tuplas (page_num, bgr_numpy).
        """
        results: List[Tuple[int, np.ndarray]] = []
        doc = pymupdf.open(str(pdf_path))
        try:
            total_doc_pages = len(doc)
            seen_pages = set()
            for p_num in page_nums:
                p_int = int(p_num)
                if p_int in seen_pages:
                    continue
                seen_pages.add(p_int)
                idx = p_int - 1
                if 0 <= idx < total_doc_pages:
                    page = doc[idx]
                    pix = page.get_pixmap(dpi=dpi)
                    img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    if pix.n == 4:
                        bgr = cv2.cvtColor(img_data, cv2.COLOR_RGBA2BGR)
                    elif pix.n == 3:
                        bgr = cv2.cvtColor(img_data, cv2.COLOR_RGB2BGR)
                    elif pix.n == 1:
                        bgr = cv2.cvtColor(img_data, cv2.COLOR_GRAY2BGR)
                    else:
                        bgr = img_data
                    results.append((p_int, bgr))
        finally:
            doc.close()
        return results

