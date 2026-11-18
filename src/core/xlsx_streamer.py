"""
Generador nativo de archivos Excel (.xlsx) en modo Streaming (0 dependencias externas, ~0 MB RAM).
Diseñado para soportar millones de filas dividiendo automáticamente en múltiples pestañas
cuando se alcanza el límite seguro por hoja (por defecto 900,000 filas, bajo el límite de 1,048,576 de Excel).
"""
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple, Dict, Any
from xml.sax.saxutils import escape


def _col_letter(col_idx_1based: int) -> str:
    """Convierte índice de columna base 1 (1 -> A, 2 -> B, 27 -> AA) a letra de Excel."""
    result = []
    n = col_idx_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result.append(chr(65 + rem))
    return "".join(reversed(result))


def _sanitize_xml_text(val: Any) -> str:
    """Limpia caracteres de control inválidos en XML 1.0 y escapa entidades XML."""
    if val is None:
        return ""
    s = str(val)
    # Eliminar caracteres de control ASCII no permitidos en XML (excepto \t, \n, \r)
    clean_chars = [
        ch for ch in s
        if (ord(ch) >= 32 or ch in ("\t", "\n", "\r")) and ord(ch) not in (0xFFFE, 0xFFFF)
    ]
    return escape("".join(clean_chars))


class StreamingXlsxWriter:
    """
    Escritor de libros Excel (.xlsx) en streaming directo a disco.
    Cada hoja se escribe línea por línea en un archivo temporal XML sin acumular filas en RAM.
    """

    MAX_ROWS_PER_SHEET = 900_000

    def __init__(self, output_path: Any):
        self.output_path = Path(output_path)
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="xlsx_stream_"))
        self.sheets: List[Tuple[str, Path, int]] = []  # (sheet_title, xml_temp_path, data_rows_count)

    def __enter__(self) -> "StreamingXlsxWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            self.save()
        else:
            self.cleanup()

    def add_sheet_from_iter(
        self,
        title: str,
        headers: Sequence[str],
        rows_iter: Iterable[Sequence[Any]],
        col_widths: Sequence[int] = (42, 48, 85),
    ) -> int:
        """
        Crea una hoja en el Excel con los encabezados y las filas de `rows_iter`.
        Retorna la cantidad de filas de datos escritas.
        """
        sheet_idx = len(self.sheets) + 1
        xml_path = self.tmp_dir / f"sheet{sheet_idx}.xml"
        data_count = 0

        with open(xml_path, "w", encoding="utf-8") as f:
            self._write_sheet_header(f, len(headers), col_widths)
            # Fila 1: Encabezados (estilo s="1")
            self._write_row(f, 1, headers, style_id=1)

            row_num = 2
            for row in rows_iter:
                self._write_row(f, row_num, row, style_id=0)
                row_num += 1
                data_count += 1

            self._write_sheet_footer(f)

        safe_title = self._sanitize_sheet_title(title)
        self.sheets.append((safe_title, xml_path, data_count))
        return data_count

    def add_paginated_sheets_from_iter(
        self,
        base_title: str,
        headers: Sequence[str],
        rows_iter: Iterable[Sequence[Any]],
        max_rows_per_sheet: int = MAX_ROWS_PER_SHEET,
        col_widths: Sequence[int] = (45, 50, 90),
    ) -> Dict[str, Any]:
        """
        Escribe un flujo potencialmente masivo de filas (ej. >1 millón de PDFs) dividiéndolo
        automáticamente en múltiples hojas ('PDFs_Correctos_1', 'PDFs_Correctos_2', ...) cada
        `max_rows_per_sheet` filas (por defecto 900,000).
        Retorna {"total_rows": total_filas_datos, "sheets_created": lista_nombres_hojas}.
        """
        part_num = 1
        total_data_rows = 0
        current_file = None
        current_xml_path = None
        current_sheet_rows = 0
        row_num = 2
        created_titles: List[str] = []

        def _open_new_part(p_num: int):
            nonlocal current_file, current_xml_path, current_sheet_rows, row_num
            sheet_idx = len(self.sheets) + 1 + (p_num - 1)
            current_xml_path = self.tmp_dir / f"sheet_pag_{sheet_idx}_{p_num}.xml"
            current_file = open(current_xml_path, "w", encoding="utf-8")
            self._write_sheet_header(current_file, len(headers), col_widths)
            self._write_row(current_file, 1, headers, style_id=1)
            current_sheet_rows = 0
            row_num = 2

        def _close_current_part(p_num: int):
            nonlocal current_file, current_xml_path, current_sheet_rows
            if current_file is not None:
                self._write_sheet_footer(current_file)
                current_file.close()
                current_file = None
                s_title = self._sanitize_sheet_title(f"{base_title}_{p_num}")
                self.sheets.append((s_title, current_xml_path, current_sheet_rows))
                created_titles.append(s_title)

        _open_new_part(part_num)
        try:
            for row in rows_iter:
                if current_sheet_rows >= max_rows_per_sheet:
                    _close_current_part(part_num)
                    part_num += 1
                    _open_new_part(part_num)

                self._write_row(current_file, row_num, row, style_id=0)
                row_num += 1
                current_sheet_rows += 1
                total_data_rows += 1
        finally:
            _close_current_part(part_num)

        return {"total_rows": total_data_rows, "sheets_created": created_titles}

    @staticmethod
    def _sanitize_sheet_title(title: str) -> str:
        clean = "".join(c if c not in r"[]:*?/\\" else "_" for c in str(title).strip())
        return clean[:31] or "Hoja"

    @staticmethod
    def _write_sheet_header(f, num_cols: int, col_widths: Sequence[int]):
        f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
        f.write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n')
        f.write("  <cols>\n")
        for c_idx in range(1, num_cols + 1):
            w = col_widths[c_idx - 1] if (c_idx - 1) < len(col_widths) else 45
            f.write(f'    <col min="{c_idx}" max="{c_idx}" width="{w}" customWidth="1"/>\n')
        f.write("  </cols>\n")
        f.write("  <sheetData>\n")

    @staticmethod
    def _write_row(f, row_num: int, values: Sequence[Any], style_id: int = 0):
        f.write(f'    <row r="{row_num}">')
        for c_idx, val in enumerate(values, start=1):
            col_let = _col_letter(c_idx)
            cell_ref = f"{col_let}{row_num}"
            esc_val = _sanitize_xml_text(val)
            s_attr = f' s="{style_id}"' if style_id > 0 else ""
            # preserve="1" conserva espacios iniciales/finales si los hubiera
            f.write(f'<c r="{cell_ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{esc_val}</t></is></c>')
        f.write("</row>\n")

    @staticmethod
    def _write_sheet_footer(f):
        f.write("  </sheetData>\n")
        f.write("</worksheet>\n")

    def save(self) -> Path:
        """Empaqueta todos los archivos XML temporales en el archivo .xlsx final y limpia temporales."""
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(str(self.output_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("[Content_Types].xml", self._build_content_types_xml())
                zf.writestr("_rels/.rels", self._build_root_rels_xml())
                zf.writestr("xl/workbook.xml", self._build_workbook_xml())
                zf.writestr("xl/_rels/workbook.xml.rels", self._build_workbook_rels_xml())
                zf.writestr("xl/styles.xml", self._build_styles_xml())

                for idx, (_, xml_path, _) in enumerate(self.sheets, start=1):
                    zf.write(str(xml_path), arcname=f"xl/worksheets/sheet{idx}.xml")
            return self.output_path
        finally:
            self.cleanup()

    def cleanup(self):
        if self.tmp_dir.exists():
            shutil.rmtree(str(self.tmp_dir), ignore_errors=True)

    def _build_content_types_xml(self) -> str:
        overrides = [
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
            '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        ]
        for idx in range(1, len(self.sheets) + 1):
            overrides.append(
                f'  <Override PartName="/xl/worksheets/sheet{idx}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        ov_str = "\n".join(overrides)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            f"{ov_str}\n"
            "</Types>\n"
        )

    @staticmethod
    def _build_root_rels_xml() -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
            "</Relationships>\n"
        )

    def _build_workbook_xml(self) -> str:
        sheet_tags = []
        for idx, (title, _, _) in enumerate(self.sheets, start=1):
            esc_title = _sanitize_xml_text(title)
            sheet_tags.append(f'    <sheet name="{esc_title}" sheetId="{idx}" r:id="rId{idx}"/>')
        sheets_str = "\n".join(sheet_tags)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            "  <sheets>\n"
            f"{sheets_str}\n"
            "  </sheets>\n"
            "</workbook>\n"
        )

    def _build_workbook_rels_xml(self) -> str:
        rels = []
        for idx in range(1, len(self.sheets) + 1):
            rels.append(
                f'  <Relationship Id="rId{idx}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{idx}.xml"/>'
            )
        styles_rid = len(self.sheets) + 1
        rels.append(
            f'  <Relationship Id="rId{styles_rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        rels_str = "\n".join(rels)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            f"{rels_str}\n"
            "</Relationships>\n"
        )

    @staticmethod
    def _build_styles_xml() -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
            '  <fonts count="2">\n'
            '    <font><sz val="10"/><color rgb="FF0F172A"/><name val="Calibri"/></font>\n'
            '    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>\n'
            "  </fonts>\n"
            '  <fills count="3">\n'
            '    <fill><patternFill patternType="none"/></fill>\n'
            '    <fill><patternFill patternType="gray125"/></fill>\n'
            '    <fill><patternFill patternType="solid"><fgColor rgb="FF1E3A8A"/><bgColor indexed="64"/></patternFill></fill>\n'
            "  </fills>\n"
            '  <borders count="1">\n'
            '    <border><left/><right/><top/><bottom/><diagonal/></border>\n'
            "  </borders>\n"
            '  <cellStyleXfs count="1">\n'
            '    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>\n'
            "  </cellStyleXfs>\n"
            '  <cellXfs count="2">\n'
            '    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>\n'
            '    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>\n'
            "  </cellXfs>\n"
            "</styleSheet>\n"
        )
