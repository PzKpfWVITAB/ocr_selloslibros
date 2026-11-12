from concurrent.futures import ThreadPoolExecutor
import hashlib
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import cv2
import pymupdf as fitz
import numpy as np


class CaratulaInspector:
    """
    Inspector local ultrarrápido (0 tokens de Claude) para:
    1. Auto-detectar el archivo PDF de CARÁTULA en una carpeta (o usar el confirmado por el usuario).
    2. Determinar qué página(s) de la CARÁTULA contienen texto/sellos y cuáles están en blanco
       (filtrando renglones horizontales de cuaderno y bordes de escáner).
    3. Verificar en todos los demás PDFs de la carpeta (usando firma de stream en los primeros 64KB
       en paralelo + fallback PyMuPDF MD5 y hash perceptual dHash de 64 bits):
       - Si tienen adjunta al menos una página de la CARÁTULA.
       - Si la página de carátula adjunta en los PDFs tiene información ('OK_CON_INFO') o si es
         la página en blanco pero el archivo CARÁTULA sí tiene otra página con información
         ('REEMPLAZABLE_HOJA_BLANCA').
       - Si todas las páginas de la CARÁTULA están en blanco ('TOTALMENTE_BLANCA').
       - Si existen PDFs que no tienen ninguna página de la CARÁTULA ('PDFS_SIN_CARATULA').
    """

    @staticmethod
    def auto_detect_caratula(folder_path: Any) -> Optional[Path]:
        p = Path(folder_path)
        if not p.exists() or not p.is_dir():
            return None
        all_pdfs = sorted(
            [f for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"],
            key=lambda x: x.name
        )
        for pdf in all_pdfs:
            if "CARATULA" in pdf.name.upper():
                return pdf
        return None

    @staticmethod
    def list_folder_pdfs(folder_path: Any) -> List[Path]:
        p = Path(folder_path)
        if not p.exists() or not p.is_dir():
            return []
        return sorted(
            [f for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"],
            key=lambda x: x.name
        )

    @staticmethod
    def is_duplicate_pdf_name(filename: str) -> bool:
        """Detecta si el archivo PDF tiene sufijo de duplicado (_DUP, _DUP1, etc.)."""
        stem = Path(filename).stem
        return bool(re.search(r"[_+-]DUP\d*(\.pdf)?$", stem, re.IGNORECASE)) or "_DUP" in stem.upper()

    @staticmethod
    def is_irregular_reg_pdf_name(filename: str) -> bool:
        """
        Detecta si el archivo PDF es un documento irregular o fuera de formato
        (ej. 'REG_-60.pdf', 'REG 60.pdf' o archivos sin estructura registral de libro/año).
        """
        stem = Path(filename).stem
        if "CARATULA" in stem.upper():
            return False
        # Si contiene 'REG' y no empieza con el código registral de 2 dígitos estándar
        if re.search(r"\bREG\b|REG[-_\s]", stem, re.IGNORECASE) and not re.match(r"^\d{2}_", stem):
            return True
        # Si no tiene año de 4 dígitos ni estructura registral estándar
        clean_stem = re.sub(r"[_+-]DUP\d*.*$", "", stem, flags=re.IGNORECASE)
        tokens = clean_stem.split("_")
        has_year = any(
            (tok.isdigit() and len(tok) == 4 and 1800 <= int(tok) <= 2035) or tok == "0000"
            for tok in tokens[2:]
        )
        return not has_year

    @staticmethod
    def _render_page_gray(page: fitz.Page, dpi: int = 100) -> np.ndarray:
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 3:
            return cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        elif pix.n == 4:
            return cv2.cvtColor(img_np, cv2.COLOR_RGBA2GRAY)
        return img_np

    @staticmethod
    def _compute_dhash(gray_img: np.ndarray, hash_size: int = 16) -> int:
        resized = cv2.resize(gray_img, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
        diff = resized[:, 1:] > resized[:, :-1]
        val = 0
        for bit in diff.flatten():
            val = (val << 1) | int(bool(bit))
        return val

    @staticmethod
    def _hamming_distance(h1: int, h2: int) -> int:
        return bin(h1 ^ h2).count("1")

    @staticmethod
    def _extract_page_image_signatures(doc: fitz.Document, page: fitz.Page) -> Tuple[List[str], List[bytes]]:
        """
        Extrae los hashes MD5 de los streams de imagen de la página y firmas binarias de 256 bytes
        después del encabezado JPEG/EXIF (offset 1024..1280) para verificación instantánea en cabecera.
        """
        md5_list: List[str] = []
        raw_sigs: List[bytes] = []
        try:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                raw_bytes = doc.xref_stream_raw(xref)
                if raw_bytes:
                    md5_list.append(hashlib.md5(raw_bytes).hexdigest())
                    if len(raw_bytes) >= 1400:
                        raw_sigs.append(raw_bytes[1024:1280])
                    elif len(raw_bytes) >= 320:
                        raw_sigs.append(raw_bytes[len(raw_bytes) // 2 : (len(raw_bytes) // 2) + 128])
        except Exception:
            pass
        return md5_list, raw_sigs

    @classmethod
    def analyze_page_has_text(cls, page: fitz.Page, dpi: int = 100) -> Tuple[bool, float, np.ndarray]:
        """
        Determina si una página de PDF contiene texto/sellos reales o si está en blanco
        (incluso si es una hoja rayada de libro contable/registral en blanco).
        Retorna: (has_text: bool, text_score: float, gray_image: np.ndarray)
        """
        gray = cls._render_page_gray(page, dpi=dpi)

        # 1. Verificar texto digital embebido en el PDF
        native_text = (page.get_text("text") or "").strip()
        alnum_chars = sum(1 for c in native_text if c.isalnum())
        if alnum_chars >= 12:
            return True, 1.0, gray

        h, w = gray.shape[:2]
        if h < 40 or w < 40:
            return False, 0.0, gray

        # 2. Recortar márgenes exteriores (8%) para ignorar sombras de escáner o perforaciones
        my, mx = int(h * 0.08), int(w * 0.08)
        roi = gray[my:h - my, mx:w - mx]
        rh, rw = roi.shape[:2]

        # 3. Umbralización adaptativa para aislar trazos oscuros sobre fondo de papel
        blur = cv2.GaussianBlur(roi, (3, 3), 0)
        binary = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12
        )

        # 4. Eliminar líneas horizontales continuas (renglones del libro) y verticales largas (márgenes)
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, rw // 12), 1))
        horiz_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel)
        vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(35, rh // 12)))
        vert_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vert_kernel)

        text_mask = cv2.subtract(binary, horiz_lines)
        text_mask = cv2.subtract(text_mask, vert_lines)

        clean_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        text_mask = cv2.morphologyEx(text_mask, cv2.MORPH_OPEN, clean_kernel)

        # 5. Medir gradiente horizontal (Sobel X) que captura trazos verticales de letras/sellos
        sobel_x = cv2.Sobel(blur, cv2.CV_16S, 1, 0, ksize=3)
        abs_sobel_x = cv2.convertScaleAbs(sobel_x)
        _, sx_bin = cv2.threshold(abs_sobel_x, 48, 255, cv2.THRESH_BINARY)
        sx_bin = cv2.subtract(sx_bin, vert_lines)

        ink_ratio = float(np.count_nonzero(text_mask)) / float(rh * rw)
        sx_ratio = float(np.count_nonzero(sx_bin)) / float(rh * rw)
        score = round((ink_ratio * 0.65) + (sx_ratio * 0.35), 5)

        has_text = score >= 0.0085
        return has_text, score, gray

    @classmethod
    def inspect_caratula_pdf(cls, caratula_path: Path) -> Dict[str, Any]:
        """
        Analiza todas las páginas del archivo PDF de CARÁTULA.
        """
        pages_info: List[Dict[str, Any]] = []
        info_pages: List[int] = []
        blank_pages: List[int] = []

        doc = fitz.open(str(caratula_path))
        try:
            for idx in range(len(doc)):
                page = doc[idx]
                page_num = idx + 1
                has_text, score, gray = cls.analyze_page_has_text(page, dpi=100)
                md5s, raw_sigs = cls._extract_page_image_signatures(doc, page)
                dhash = cls._compute_dhash(gray)

                p_data = {
                    "page_num": page_num,
                    "has_text": has_text,
                    "text_score": score,
                    "stream_md5s": md5s,
                    "raw_sigs": raw_sigs,
                    "dhash": dhash,
                }
                pages_info.append(p_data)
                if has_text:
                    info_pages.append(page_num)
                else:
                    blank_pages.append(page_num)
        finally:
            doc.close()

        # Si ninguna página superó el umbral absoluto pero hay al menos 2 páginas y una tiene
        # claramente mayor densidad de trazos que la otra, tomarla como página con info
        if not info_pages and len(pages_info) >= 2:
            sorted_by_score = sorted(pages_info, key=lambda x: x["text_score"], reverse=True)
            best = sorted_by_score[0]
            second = sorted_by_score[1]
            if best["text_score"] >= 0.001 and best["text_score"] > (second["text_score"] * 1.15):
                best["has_text"] = True
                info_pages = [best["page_num"]]
                blank_pages = [p["page_num"] for p in pages_info if p["page_num"] != best["page_num"]]

        primary_info_page: Optional[int] = None
        if info_pages:
            text_pages_objs = [p for p in pages_info if p["has_text"]]
            # Si hay 2 páginas y ambas tienen contenido de texto/trazos:
            # En la estructura física de libros registrales de Chiapas, la página 1 es típicamente
            # la portada o guarda comercial exterior ("ESTRELLA", "LIBRO FLORETE"), mientras que la
            # página 2 es la primera foja útil que contiene el Acta de Apertura mecanografiada oficial.
            # Por ello, si ambas páginas tienen contenido, se prioriza la página 2 como página principal de información.
            if len(pages_info) == 2 and 1 in info_pages and 2 in info_pages:
                primary_info_page = 2
            else:
                text_pages_objs.sort(key=lambda x: x["text_score"], reverse=True)
                primary_info_page = text_pages_objs[0]["page_num"]
        elif pages_info:
            primary_info_page = 1

        return {
            "caratula_path": str(caratula_path),
            "caratula_filename": caratula_path.name,
            "total_pages": len(pages_info),
            "pages": pages_info,
            "info_pages": info_pages,
            "blank_pages": blank_pages,
            "primary_info_page": primary_info_page,
            "all_pages_blank": len(info_pages) == 0,
        }

    @classmethod
    def _match_page_against_caratula(
        cls,
        doc: fitz.Document,
        page: fitz.Page,
        caratula_pages: List[Dict[str, Any]],
        allow_dhash_for_text: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Compara una página de un PDF regular contra las páginas de la CARÁTULA:
        1° Por hash MD5 exacto del raw stream de imagen embebida.
        2° Por distancia de Hamming del dHash perceptual de 256 bits (<= 16 bits de 256).
        """
        page_md5s, _ = cls._extract_page_image_signatures(doc, page)
        page_md5_set = set(page_md5s)
        if page_md5_set:
            for c_page in caratula_pages:
                if page_md5_set.intersection(c_page["stream_md5s"]):
                    return c_page

        if not allow_dhash_for_text:
            return None

        page_has_text, _, gray = cls.analyze_page_has_text(page, dpi=100)
        page_dhash = cls._compute_dhash(gray)
        best_match = None
        best_dist = 9999
        for c_page in caratula_pages:
            # Una página con texto útil no debe coincidir con una página en blanco de carátula por dHash
            if page_has_text != c_page.get("has_text", False):
                continue
            dist = cls._hamming_distance(page_dhash, c_page["dhash"])
            if dist < best_dist:
                best_dist = dist
                best_match = c_page

        if best_match is not None and best_dist <= 16:
            return best_match
        return None

    @classmethod
    def _inspect_single_regular_pdf(
        cls,
        pdf_file: Path,
        caratula_pages: List[Dict[str, Any]],
        has_blank_in_caratula: bool,
    ) -> Dict[str, Any]:
        """
        Inspecciona un PDF individual verificando si contiene una página con info de la carátula,
        una página en blanco de la carátula, o ninguna.
        Usa un filtro rápido de firma en los primeros 8KB para evitar leer archivos completos por red.
        Captura además el número total de páginas (imágenes digitalizadas) del archivo.
        """
        page_cnt = 1
        try:
            # Nivel 1: Verificación ultrarrápida en los primeros 4KB del archivo (un solo bloque SMB)
            with open(pdf_file, "rb", buffering=4096) as f:
                header_bytes = f.read(4096)

            matched_fast = False
            fast_kind = None
            fast_blank_idx = 0
            for c_page in caratula_pages:
                for sig in c_page.get("raw_sigs", []):
                    if sig and sig in header_bytes:
                        matched_fast = True
                        if c_page["has_text"]:
                            fast_kind = "INFO"
                        else:
                            fast_kind = "BLANK"
                            fast_blank_idx = 0
                        break
                if matched_fast:
                    break

            # Abrir con PyMuPDF para obtener recuento de páginas (imágenes) y verificación profunda si hace falta
            doc = fitz.open(str(pdf_file))
            try:
                page_cnt = max(1, len(doc))
                if matched_fast and fast_kind:
                    res = {"kind": fast_kind, "pdf_file": pdf_file, "page_count": page_cnt}
                    if fast_kind == "BLANK":
                        res["blank_page_index"] = fast_blank_idx
                    return res

                if len(doc) == 0:
                    return {"kind": "MISSING", "pdf_file": pdf_file, "page_count": 0}

                # Primero evaluar la página 1 (índice 0), que es donde se adjunta la carátula en cada PDF
                p0_match = cls._match_page_against_caratula(doc, doc[0], caratula_pages, allow_dhash_for_text=True)
                if p0_match is not None:
                    if p0_match["has_text"]:
                        return {"kind": "INFO", "pdf_file": pdf_file, "page_count": page_cnt}
                    else:
                        # La página 1 coincide con la hoja en blanco de la carátula.
                        # Solo considerar que ya tiene la info si la página 2 tiene el stream exacto de la carátula con info.
                        if len(doc) >= 2:
                            p1_exact = cls._match_page_against_caratula(
                                doc, doc[1], caratula_pages, allow_dhash_for_text=False
                            )
                            if p1_exact is not None and p1_exact["has_text"]:
                                return {"kind": "INFO", "pdf_file": pdf_file, "page_count": page_cnt}
                        return {"kind": "BLANK", "pdf_file": pdf_file, "blank_page_index": 0, "page_count": page_cnt}

                # Si la página 1 no hizo match directo pero está totalmente en blanco y la carátula tiene página en blanco:
                p0_has_text, _, _ = cls.analyze_page_has_text(doc[0], dpi=100)
                if not p0_has_text and has_blank_in_caratula:
                    return {"kind": "BLANK", "pdf_file": pdf_file, "blank_page_index": 0, "page_count": page_cnt}

                # Revisar página 2 (índice 1) por si la carátula estuviera en la segunda página
                if len(doc) >= 2:
                    p1_match = cls._match_page_against_caratula(doc, doc[1], caratula_pages, allow_dhash_for_text=True)
                    if p1_match is not None:
                        if p1_match["has_text"]:
                            return {"kind": "INFO", "pdf_file": pdf_file, "page_count": page_cnt}
                        else:
                            return {"kind": "BLANK", "pdf_file": pdf_file, "blank_page_index": 1, "page_count": page_cnt}

                return {"kind": "MISSING", "pdf_file": pdf_file, "page_count": page_cnt}
            finally:
                doc.close()
        except Exception:
            return {"kind": "MISSING", "pdf_file": pdf_file, "page_count": page_cnt}

    @classmethod
    def detect_embedded_caratula_sample(
        cls,
        regular_pdfs: List[Path],
        sample_count: int = 3
    ) -> Optional[Dict[str, Any]]:
        """
        Verifica si los primeros archivos (muestra de hasta 'sample_count', típicamente 3)
        comparten exactamente la misma página 1 como carátula embebida y dicha página
        contiene texto/sellos reales (no está en blanco).
        """
        if not regular_pdfs:
            return None

        clean_pdfs = [
            f for f in regular_pdfs
            if not cls.is_duplicate_pdf_name(f.name) and not cls.is_irregular_reg_pdf_name(f.name)
        ]
        target_list = clean_pdfs if clean_pdfs else regular_pdfs
        needed = min(sample_count, len(target_list))
        sample = target_list[:needed]
        if not sample:
            return None

        pages_data = []
        for pf in sample:
            try:
                doc = fitz.open(str(pf))
            except Exception:
                return None
            try:
                if len(doc) == 0:
                    return None
                p0 = doc[0]
                has_text, score, gray = cls.analyze_page_has_text(p0, dpi=100)
                if not has_text:
                    # Si la primera página está en blanco, no es una carátula válida
                    return None
                md5s, raw_sigs = cls._extract_page_image_signatures(doc, p0)
                dhash = cls._compute_dhash(gray)
                pages_data.append({
                    "pdf": pf,
                    "score": score,
                    "md5s": md5s,
                    "raw_sigs": raw_sigs,
                    "dhash": dhash,
                })
            finally:
                doc.close()

        if len(pages_data) < len(sample):
            return None

        # Si hay más de 1 archivo en la muestra, verificar que todos coincidan con el primero
        first = pages_data[0]
        for other in pages_data[1:]:
            same_stream = any(m in other["md5s"] for m in first["md5s"] if m)
            dist = cls._hamming_distance(first["dhash"], other["dhash"])
            # Coinciden si comparten stream MD5 o si la distancia Hamming dHash es <= 10
            if not same_stream and dist > 10:
                return None

        return {
            "source_pdf": first["pdf"],
            "source_pdf_name": first["pdf"].name,
            "page_num": 1,
            "sample_verified_count": len(pages_data),
            "total_regular_pdfs": len(regular_pdfs),
        }

    @classmethod
    def inspect_folder(
        cls,
        folder_path: Any,
        custom_caratula_path: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Ejecuta la inspección local completa de la carpeta y de todos sus PDFs respecto a la CARÁTULA.
        """
        folder = Path(folder_path)
        all_pdfs = cls.list_folder_pdfs(folder)
        dup_pdfs = [f.name for f in all_pdfs if cls.is_duplicate_pdf_name(f.name)]
        irregular_pdfs = [f.name for f in all_pdfs if cls.is_irregular_reg_pdf_name(f.name)]

        caratula_pdf: Optional[Path] = None
        if custom_caratula_path:
            cp = Path(custom_caratula_path)
            if cp.exists() and cp.is_file():
                caratula_pdf = cp
        if caratula_pdf is None:
            caratula_pdf = cls.auto_detect_caratula(folder)

        if caratula_pdf is None:
            # Intentar detectar carátula embebida en la primera página de los archivos regulares
            embedded_caratula = cls.detect_embedded_caratula_sample(all_pdfs, sample_count=3)
            if embedded_caratula is not None:
                return {
                    "status": "CARATULA_EMBEBIDA_CANDIDATA",
                    "caratula_path": str(embedded_caratula["source_pdf"]),
                    "caratula_filename": f"[EMBEBIDA] {embedded_caratula['source_pdf_name']} (Pág. 1)",
                    "info_page_num": 1,
                    "info_pages": [1],
                    "blank_pages": [],
                    "caratula_total_pages": 1,
                    "all_pdfs_have_caratula": True,
                    "total_regular_pdfs": len(all_pdfs),
                    "total_pages_in_folder": len(all_pdfs),
                    "pdf_page_counts": {f.name: 1 for f in all_pdfs},
                    "pdfs_with_info_page": len(all_pdfs),
                    "pdfs_with_blank_page": 0,
                    "pdfs_missing_caratula": [],
                    "replaceable_pdfs": [],
                    "dup_pdfs": dup_pdfs,
                    "irregular_pdfs": irregular_pdfs,
                    "embedded_caratula_info": embedded_caratula,
                    "summary_message": (
                        f"Sin archivo de carátula independiente, pero se detectó la misma Página 1 en "
                        f"muestra de {embedded_caratula['sample_verified_count']} archivos "
                        f"('{embedded_caratula['source_pdf_name']}'). Candidata a carátula para análisis OCR."
                    ),
                }

            return {
                "status": "SIN_ARCHIVO_CARATULA",
                "caratula_path": None,
                "caratula_filename": None,
                "info_page_num": None,
                "info_pages": [],
                "blank_pages": [],
                "caratula_total_pages": 0,
                "all_pdfs_have_caratula": False,
                "total_regular_pdfs": len(all_pdfs),
                "total_pages_in_folder": len(all_pdfs),
                "pdf_page_counts": {f.name: 1 for f in all_pdfs},
                "pdfs_with_info_page": 0,
                "pdfs_with_blank_page": 0,
                "pdfs_missing_caratula": [f.name for f in all_pdfs],
                "replaceable_pdfs": [],
                "dup_pdfs": dup_pdfs,
                "irregular_pdfs": irregular_pdfs,
                "summary_message": "No se encontró un archivo PDF con 'CARATULA' ni carátula embebida en la carpeta.",
            }

        c_info = cls.inspect_caratula_pdf(caratula_pdf)
        caratula_pages = c_info["pages"]
        primary_info_page = c_info["primary_info_page"]
        has_blank_in_caratula = len(c_info["blank_pages"]) > 0

        regular_pdfs = [
            f for f in all_pdfs
            if f.resolve() != caratula_pdf.resolve()
        ]

        pdfs_with_info_page = 0
        pdfs_with_blank_page = 0
        pdfs_missing_caratula: List[str] = []
        replaceable_pdfs: List[Dict[str, Any]] = []
        total_pages_in_folder = int(c_info.get("total_pages", 0))
        pdf_page_counts: Dict[str, int] = {}
        if caratula_pdf and caratula_pdf.exists():
            pdf_page_counts[caratula_pdf.name] = total_pages_in_folder

        if regular_pdfs:
            workers = min(16, max(1, len(regular_pdfs)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(
                        cls._inspect_single_regular_pdf,
                        pdf_file,
                        caratula_pages,
                        has_blank_in_caratula,
                    )
                    for pdf_file in regular_pdfs
                ]
                for fut in futures:
                    res_item = fut.result()
                    kind = res_item["kind"]
                    pf: Path = res_item["pdf_file"]
                    p_cnt = int(res_item.get("page_count", 1))
                    total_pages_in_folder += p_cnt
                    pdf_page_counts[pf.name] = p_cnt

                    is_reg = cls.is_irregular_reg_pdf_name(pf.name)

                    if kind == "INFO":
                        pdfs_with_info_page += 1
                    elif kind == "BLANK":
                        pdfs_with_blank_page += 1
                        # Si la carátula tiene alguna página con texto (primary_info_page) y no es 100% en blanco,
                        # reemplazar la hoja en blanco en los PDFs regulares y DUP (nunca en REG)
                        if not is_reg and primary_info_page is not None and not c_info.get("all_pages_blank", False):
                            replaceable_pdfs.append({
                                "pdf_path": str(pf),
                                "pdf_name": pf.name,
                                "action": "REPLACE",
                                "blank_page_index": int(res_item.get("blank_page_index", 0)),
                            })
                    else:
                        # Si a un PDF le falta la carátula y NO es REG, registrarlo para inserción al inicio.
                        # (A los archivos REG nunca se les inserta carátula ni se marcan como faltantes)
                        if not is_reg:
                            pdfs_missing_caratula.append(pf.name)
                            replaceable_pdfs.append({
                                "pdf_path": str(pf),
                                "pdf_name": pf.name,
                                "action": "INSERT",
                                "blank_page_index": -1,
                            })

        all_have_caratula = len(pdfs_missing_caratula) == 0

        if c_info.get("all_pages_blank", False):
            status = "TOTALMENTE_BLANCA"
            if replaceable_pdfs:
                msg = (
                    f"La carátula '{caratula_pdf.name}' está 100% en blanco. Se asegurará la inserción de la "
                    f"hoja en blanco en {len(replaceable_pdfs)} PDF(s) que carecían de ella (archivos REG intactos)."
                )
            else:
                msg = (
                    f"Todas las páginas ({c_info['total_pages']}) de '{caratula_pdf.name}' están en blanco. "
                    "Todos los PDFs ya contienen la hoja en blanco (0 tokens consumidos)."
                )
        elif pdfs_missing_caratula:
            status = "PDFS_SIN_CARATULA"
            msg = (
                f"{len(pdfs_missing_caratula)} de {len(regular_pdfs)} PDFs NO contienen ninguna página "
                f"de la carátula '{caratula_pdf.name}'."
            )
        elif pdfs_with_blank_page > 0 and primary_info_page is not None:
            status = "REEMPLAZABLE_HOJA_BLANCA"
            msg = (
                f"{pdfs_with_blank_page} PDFs tienen adjunta la hoja en blanco de la carátula, "
                f"pero la página {primary_info_page} de '{caratula_pdf.name}' sí tiene información/texto "
                "y puede reemplazarla automáticamente (archivos REG intactos)."
            )
        else:
            status = "OK_CON_INFO"
            if len(c_info.get("info_pages", [])) > 1:
                info_pgs_str = ", ".join(str(p) for p in c_info["info_pages"])
                msg = (
                    f"Todos los {len(regular_pdfs)} PDFs contienen la carátula '{caratula_pdf.name}'. "
                    f"La carátula tiene {c_info['total_pages']} páginas (Págs. {info_pgs_str} con contenido para OCR)."
                )
            else:
                msg = (
                    f"Todos los {len(regular_pdfs)} PDFs contienen la página con información "
                    f"(Pág. {primary_info_page}) de '{caratula_pdf.name}'."
                )

        return {
            "status": status,
            "caratula_path": str(caratula_pdf),
            "caratula_filename": caratula_pdf.name,
            "caratula_total_pages": c_info["total_pages"],
            "info_page_num": primary_info_page,
            "info_pages": c_info.get("info_pages", [primary_info_page] if primary_info_page else [1]),
            "blank_pages": c_info.get("blank_pages", []),
            "caratula_pages": c_info.get("pages", []),
            "all_pdfs_have_caratula": all_have_caratula,
            "total_regular_pdfs": len(regular_pdfs),
            "total_pages_in_folder": total_pages_in_folder,
            "pdf_page_counts": pdf_page_counts,
            "pdfs_with_info_page": pdfs_with_info_page,
            "pdfs_with_blank_page": pdfs_with_blank_page,
            "pdfs_missing_caratula": pdfs_missing_caratula,
            "replaceable_pdfs": replaceable_pdfs,
            "dup_pdfs": dup_pdfs,
            "irregular_pdfs": irregular_pdfs,
            "summary_message": msg,
        }
