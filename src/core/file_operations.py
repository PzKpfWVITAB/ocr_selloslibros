import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pymupdf as fitz

from src.core.settings_manager import SettingsManager
from src.database.db_manager import DatabaseManager


class FileOperations:
    """
    Motor de Operaciones Físicas y Rollback Total:
    1. Construcción del código base limpio de 7 posiciones hasta el Año (conservando siempre
       intacta la posición 1 de Delegación).
    2. Resolución de colisiones de directorios agregando _2, _3, etc. ÚNICAMENTE al nombre
       de la carpeta, nunca a los archivos PDF internos.
    3. Reemplazo de la página en blanco inicial de cada PDF por la página con información de
       la CARÁTULA (guardando antes respaldo binario en 'backups_rollback/' para Rollback exacto).
    4. Renombrado de carpeta y PDFs internos registrando cada archivo en SQLite.
    5. Movimiento de carpetas con carátula en blanco al directorio de Apartados.
    6. Herramienta de Rollback Total: restaura cualquier archivo o carpeta completa al punto
       exacto anterior a su primer cambio (misma ruta inicial, mismo nombre inicial y mismo hash SHA-256).
    """

    @staticmethod
    def get_backup_dir() -> Path:
        d = SettingsManager.get_base_dir() / "backups_rollback"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(131072)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def get_pdf_page_count(file_path: Path) -> int:
        try:
            doc = fitz.open(str(file_path))
            try:
                return len(doc)
            finally:
                doc.close()
        except Exception:
            return 0

    @staticmethod
    def _format_two_digit_or_str(val: Any, default: str = "00") -> str:
        if val is None:
            return default
        s = str(val).strip().upper()
        if not s or s in ["NONE", "NULL", "-"]:
            return default
        if s.isdigit():
            n = int(s)
            return f"{n:02d}" if n < 100 else str(n)
        # Limpiar espacios por guiones bajos
        s = re.sub(r"\s+", "_", s)
        return s

    @staticmethod
    def _format_year(val: Any) -> str:
        if val is None:
            return "0000"
        s = str(val).strip()
        m = re.search(r"(19\d{2}|20\d{2})", s)
        if m:
            return m.group(1)
        if s in ["0", "00", "000", "0000"]:
            return "0000"
        return "0000"

    @classmethod
    def build_clean_folder_code(
        cls,
        delegacion: Any,
        seccion: Any,
        libro: Any,
        tomo: Any,
        volumen: Any,
        fraccionamiento: Any,
        anio: Any,
    ) -> str:
        """
        Construye el nombre base de 7 posiciones hasta el Año:
        [Delegación]_[Sección]_[Libro]_[Tomo]_[Volumen]_[Fraccionamiento]_[Año]
        Regla estricta:
        - La posición 1 (Delegación) NUNCA se modifica.
        - No incluye sufijos posteriores al año (_REVISAR, _REVISAR_SEPARADO, ni _2/_3).
        """
        del_str = str(delegacion).strip() if delegacion is not None and str(delegacion).strip() else "01"
        sec_str = cls._format_two_digit_or_str(seccion, "00")
        lib_str = cls._format_two_digit_or_str(libro, "00")
        tom_str = cls._format_two_digit_or_str(tomo, "00")
        vol_str = cls._format_two_digit_or_str(volumen, "00")
        frac_str = cls._format_two_digit_or_str(fraccionamiento, "00")
        anio_str = cls._format_year(anio)

        return f"{del_str}_{sec_str}_{lib_str}_{tom_str}_{vol_str}_{frac_str}_{anio_str}"

    @classmethod
    def strip_suffix_after_year(cls, folder_name: str, folder_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Conserva el nombre de la carpeta tal cual hasta la posición del año (4 dígitos),
        eliminando únicamente sufijos posteriores al año (como _REVISAR, _REVISAR_SEPARADO, _2, etc.).
        """
        parts = str(folder_name).strip().split("_")
        year_pattern = re.compile(r"^(19\d{2}|20\d{2}|0000)$")
        if len(parts) >= 7:
            for idx in range(6, len(parts)):
                if year_pattern.match(parts[idx]):
                    return "_".join(parts[: idx + 1])

        if folder_data:
            return cls.build_clean_folder_code(
                delegacion=folder_data.get("delegacion") or "01",
                seccion=folder_data.get("seccion_raw"),
                libro=folder_data.get("libro_raw"),
                tomo=folder_data.get("tomo_raw"),
                volumen=folder_data.get("volumen_raw"),
                fraccionamiento=folder_data.get("fraccionamiento_raw"),
                anio=folder_data.get("anio_raw"),
            )
        return str(folder_name).strip()

    @staticmethod
    def resolve_directory_collision(
        parent_dir: Path,
        base_folder_name: str,
        current_folder_path: Optional[Path] = None,
    ) -> Path:
        """
        Determina la ruta destino para la carpeta dentro de 'parent_dir'.
        Si ya existe otro directorio con 'base_folder_name', agrega _2, _3, _4, etc.
        al nombre de la carpeta.
        """
        candidate = parent_dir / base_folder_name
        if not candidate.exists():
            return candidate

        if current_folder_path is not None:
            try:
                if candidate.resolve() == current_folder_path.resolve():
                    return candidate
            except Exception:
                pass

        counter = 2
        while True:
            suffixed_candidate = parent_dir / f"{base_folder_name}_{counter}"
            if not suffixed_candidate.exists():
                return suffixed_candidate
            if current_folder_path is not None:
                try:
                    if suffixed_candidate.resolve() == current_folder_path.resolve():
                        return suffixed_candidate
                except Exception:
                    pass
            counter += 1

    @staticmethod
    def build_new_pdf_name(
        old_pdf_name: str,
        clean_base_code: str,
        current_folder_name: str = "",
    ) -> str:
        """
        Construye el nuevo nombre para un archivo PDF interno usando el 'clean_base_code'
        hasta el año (SIN incluir nunca el sufijo de colisión _2, _3 de la carpeta).
        Conserva intacto el folio o identificador final del PDF (ej. '0001_00.pdf' o 'CARATULA_00.pdf').
        """
        remainder: Optional[str] = None

        # 1. Intentar separar a partir del patrón de 7 posiciones hasta el año (19XX, 20XX o 0000)
        pattern = re.compile(
            r"^\d{2}_[^_]+_[^_]+_[^_]+_[^_]+_.*?_(?:19\d{2}|20\d{2}|0000)_(.+\.pdf)$",
            re.IGNORECASE,
        )
        m = pattern.match(old_pdf_name)
        if m:
            remainder = m.group(1)

        # 2. Si no hizo match por año pero empieza con el nombre actual de la carpeta + '_'
        if remainder is None and current_folder_name and old_pdf_name.startswith(current_folder_name + "_"):
            remainder = old_pdf_name[len(current_folder_name) + 1:]

        # 3. Fallback si tiene al menos 7 guiones bajos
        if remainder is None:
            parts = old_pdf_name.split("_")
            if len(parts) > 7:
                remainder = "_".join(parts[7:])

        if remainder:
            # Limpiar si el remainder arrastraba etiquetas de revisión o sufijos de colisión previos
            remainder = re.sub(r"^(?:REVISAR(?:_SEPARADO)?_)+", "", remainder, flags=re.IGNORECASE)
            return f"{clean_base_code}_{remainder}"

        return old_pdf_name

    @classmethod
    def replace_blank_caratula_in_folder_pdfs(
        cls,
        folder_path: Any,
        caratula_path: Any,
        info_page_num: int,
        replaceable_pdfs: List[Dict[str, Any]],
        db_manager: DatabaseManager,
        mysql_manager: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Reemplaza la página en blanco adjunta en cada PDF listado en 'replaceable_pdfs'
        por la página 'info_page_num' (1-indexed) del archivo 'caratula_path'.
        Antes de modificar un archivo por primera vez, guarda una copia intacta en
        'backups_rollback/' para garantizar que el Rollback pueda restaurar el binario original.
        """
        folder = Path(folder_path)
        caratula = Path(caratula_path)
        if not caratula.exists():
            raise FileNotFoundError(f"No se encontró el archivo de carátula: {caratula}")

        src_page_idx = max(0, int(info_page_num) - 1)
        backup_dir = cls.get_backup_dir()

        replaced_count = 0
        errors: List[str] = []

        caratula_doc = fitz.open(str(caratula))
        try:
            if src_page_idx >= len(caratula_doc):
                raise ValueError(
                    f"La página con información ({info_page_num}) excede las páginas de la carátula ({len(caratula_doc)})."
                )

            for item in replaceable_pdfs:
                pdf_file = Path(item["pdf_path"])
                # Si la carpeta ya fue movida/renombrada pero el nombre sigue en folder, resolver:
                if not pdf_file.exists() and (folder / item.get("pdf_name", "")).exists():
                    pdf_file = folder / item["pdf_name"]

                if not pdf_file.exists():
                    errors.append(f"No existe: {pdf_file.name}")
                    continue

                blank_idx = int(item.get("blank_page_index", 0))

                try:
                    orig_hash = cls.compute_sha256(pdf_file)
                    prior = db_manager.get_prior_active_origin(pdf_file)
                    if prior and prior.get("backup_contenido_path"):
                        backup_path_str = prior["backup_contenido_path"]
                    else:
                        backup_file = backup_dir / f"{orig_hash[:24]}_{pdf_file.name}"
                        if not backup_file.exists():
                            shutil.copy2(str(pdf_file), str(backup_file))
                        backup_path_str = str(backup_file.resolve())

                    # Modificar el PDF reemplazando la hoja en blanco por la hoja con información de la carátula
                    tmp_file = pdf_file.with_suffix(".tmp_replace.pdf")
                    target_doc = fitz.open(str(pdf_file))
                    try:
                        if 0 <= blank_idx < len(target_doc):
                            target_doc.delete_page(blank_idx)
                            target_doc.insert_pdf(
                                caratula_doc,
                                from_page=src_page_idx,
                                to_page=src_page_idx,
                                start_at=blank_idx,
                            )
                        else:
                            target_doc.insert_pdf(
                                caratula_doc,
                                from_page=src_page_idx,
                                to_page=src_page_idx,
                                start_at=0,
                            )
                        num_pages = len(target_doc)
                        target_doc.save(str(tmp_file), deflate=True)
                    finally:
                        target_doc.close()

                    os.replace(str(tmp_file), str(pdf_file))
                    new_hash = cls.compute_sha256(pdf_file)

                    db_manager.log_file_movement(
                        tipo_movimiento="REEMPLAZO_HOJA_BLANCA",
                        ruta_anterior=pdf_file,
                        ruta_nueva=pdf_file,
                        nombre_anterior=pdf_file.name,
                        nombre_nuevo=pdf_file.name,
                        hash_actual=new_hash,
                        numero_paginas=num_pages,
                        hash_origen_inicial=orig_hash,
                        backup_contenido_path=backup_path_str,
                    )
                    if mysql_manager and getattr(mysql_manager, "enabled", False):
                        mysql_manager.registrar_trazabilidad_archivo(
                            tipo_operacion="REEMPLAZO_HOJA_BLANCA",
                            archivo_nombre_origen=pdf_file.name,
                            archivo_nombre_destino=pdf_file.name,
                            carpeta_origen=str(folder),
                            carpeta_destino=str(folder),
                            ruta_completa_origen=str(pdf_file.resolve()),
                            ruta_completa_destino=str(pdf_file.resolve()),
                            hash_sha256=new_hash,
                            numero_paginas=num_pages,
                        )
                    replaced_count += 1
                except Exception as exc:
                    errors.append(f"{pdf_file.name}: {exc}")
        finally:
            caratula_doc.close()

        return {
            "replaced_count": replaced_count,
            "errors": errors,
        }

    @classmethod
    def rename_folder_and_internal_pdfs(
        cls,
        folder_path: Any,
        clean_base_code: str,
        db_manager: DatabaseManager,
        mysql_manager: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Renombra los PDFs internos con 'clean_base_code' (hasta el año, SIN _2/_3) y
        renombra la carpeta con 'clean_base_code' (agregando _2, _3 si hay colisión de directorios).
        Registra cada movimiento en la tabla 'movimientos_archivos' de SQLite.
        """
        folder = Path(folder_path).resolve()
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"La carpeta no existe: {folder}")

        old_folder_name = folder.name
        parent_dir = folder.parent

        # 1. Resolver el nombre destino de la carpeta (con _2, _3 si choca con otra carpeta)
        target_folder_path = cls.resolve_directory_collision(
            parent_dir=parent_dir,
            base_folder_name=clean_base_code,
            current_folder_path=folder,
        ).resolve()

        # 2. Preparar estado anterior de todos los archivos PDF antes de renombrar
        pdf_files = sorted(
            [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"],
            key=lambda x: x.name,
        )

        file_tracking: List[Dict[str, Any]] = []
        for pdf in pdf_files:
            old_abs_path = pdf.resolve()
            old_name = pdf.name
            new_pdf_name = cls.build_new_pdf_name(old_name, clean_base_code, old_folder_name)

            # Evitar colisión interna si dos PDFs generaran el mismo nombre
            dest_in_current_folder = folder / new_pdf_name
            if dest_in_current_folder.exists() and dest_in_current_folder.resolve() != old_abs_path:
                stem = Path(new_pdf_name).stem
                ext = Path(new_pdf_name).suffix
                c = 2
                while (folder / f"{stem}_DUP{c}{ext}").exists():
                    c += 1
                new_pdf_name = f"{stem}_DUP{c}{ext}"
                dest_in_current_folder = folder / new_pdf_name

            if dest_in_current_folder.resolve() != old_abs_path:
                os.replace(str(old_abs_path), str(dest_in_current_folder))

            file_tracking.append({
                "old_abs_path": old_abs_path,
                "old_name": old_name,
                "new_name": new_pdf_name,
                "temp_path_in_folder": dest_in_current_folder,
            })

        # 3. Renombrar la carpeta si su ruta destino cambia
        if target_folder_path != folder:
            os.replace(str(folder), str(target_folder_path))

        # 4. Registrar cada archivo en SQLite con su ruta anterior y su ruta nueva final
        logged_files = 0
        for item in file_tracking:
            final_pdf_path = (target_folder_path / item["new_name"]).resolve()
            if final_pdf_path == item["old_abs_path"] and item["new_name"] == item["old_name"]:
                continue

            file_hash = cls.compute_sha256(final_pdf_path)
            pages_cnt = cls.get_pdf_page_count(final_pdf_path)

            db_manager.log_file_movement(
                tipo_movimiento="RENOMBRADO",
                ruta_anterior=item["old_abs_path"],
                ruta_nueva=final_pdf_path,
                nombre_anterior=item["old_name"],
                nombre_nuevo=item["new_name"],
                hash_actual=file_hash,
                numero_paginas=pages_cnt,
                hash_origen_inicial=file_hash,
            )
            if mysql_manager and getattr(mysql_manager, "enabled", False):
                mysql_manager.registrar_trazabilidad_archivo(
                    tipo_operacion="RENOMBRADO",
                    archivo_nombre_origen=item["old_name"],
                    archivo_nombre_destino=item["new_name"],
                    carpeta_origen=str(folder),
                    carpeta_destino=str(target_folder_path),
                    ruta_completa_origen=str(item["old_abs_path"]),
                    ruta_completa_destino=str(final_pdf_path),
                    hash_sha256=file_hash,
                    numero_paginas=pages_cnt,
                )
            logged_files += 1

        collision_applied = target_folder_path.name != clean_base_code
        return {
            "old_folder_path": str(folder),
            "new_folder_path": str(target_folder_path),
            "old_folder_name": old_folder_name,
            "new_folder_name": target_folder_path.name,
            "clean_base_code": clean_base_code,
            "collision_applied": collision_applied,
            "files_renamed_count": logged_files,
        }

    @staticmethod
    def determine_review_subfolder(
        caratula_status: str,
        veredicto_global: str,
        found_any_ocr_info: bool = True,
    ) -> str:
        """
        Devuelve el nombre corto, intuitivo y descriptivo de la subcarpeta de error
        donde se colocará la carpeta dentro del directorio de Apartados / Revisión.
        """
        c_st = (caratula_status or "").upper()
        ver = (veredicto_global or "").upper()

        if c_st == "TOTALMENTE_BLANCA" or ver == "APARTAR_CARATULA_BLANCA":
            return "CARATULA EN BLANCO"
        if c_st == "SIN_ARCHIVO_CARATULA" or ver == "SIN_ARCHIVO_CARATULA":
            return "SIN ARCHIVO CARATULA"
        if c_st == "PDFS_SIN_CARATULA" or ver == "DISCREPANCIA_SIN_CARATULA":
            return "PDFS SIN CARATULA"
        if not found_any_ocr_info or ver in ("SIN_INFO_MANTENER_NOMBRE", "SIN_INFO_LIMPIAR_NOMBRE"):
            return "CARATULA SIN DATOS"
        if c_st == "REEMPLAZABLE_HOJA_BLANCA" or "REEMPLAZAR_HOJA" in ver:
            return "HOJA BLANCA EN PDFS"
        if "DISCREPANCIA" in ver or "CEROS" in ver:
            return "DISCREPANCIA DE DATOS"
        if c_st == "ERROR" or ver == "ERROR":
            return "ERROR EN ARCHIVOS"
        return "REVISION MANUAL"

    @classmethod
    def move_folder_to_apartados(
        cls,
        folder_path: Any,
        apartados_dir: Any,
        db_manager: DatabaseManager,
        tipo_movimiento: str = "APARTADO_CARATULA_BLANCA",
        error_subfolder: Optional[str] = "CARATULA EN BLANCO",
        mysql_manager: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Mueve una carpeta completa al directorio destino especificado.
        Si se especifica 'error_subfolder' (ej. 'CARATULA EN BLANCO', 'PDFS SIN CARATULA'),
        la carpeta se coloca dentro de esa subcarpeta descriptiva del error.
        Limpia el nombre hasta el año, adapta colisiones (_2, _3) y registra en SQLite para Rollback.
        """
        folder = Path(folder_path).resolve()
        base_dest = Path(apartados_dir).resolve()
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"La carpeta a mover no existe: {folder}")

        if error_subfolder and str(error_subfolder).strip():
            clean_sub = re.sub(r'[<>:"/\\|?*]+', "", str(error_subfolder).strip())
            dest_parent = (base_dest / clean_sub).resolve()
        else:
            clean_sub = ""
            dest_parent = base_dest

        dest_parent.mkdir(parents=True, exist_ok=True)
        clean_base_name = cls.strip_suffix_after_year(folder.name)
        target_folder_path = cls.resolve_directory_collision(
            parent_dir=dest_parent,
            base_folder_name=clean_base_name,
            current_folder_path=folder,
        ).resolve()

        all_files = sorted([f for f in folder.iterdir() if f.is_file()], key=lambda x: x.name)
        pre_move_info = []
        for f in all_files:
            old_p = f.resolve()
            old_n = f.name
            if f.suffix.lower() == ".pdf" and clean_base_name != folder.name:
                new_n = cls.build_new_pdf_name(old_n, clean_base_name, folder.name)
            else:
                new_n = old_n
            pre_move_info.append({"old_path": old_p, "old_name": old_n, "new_name": new_n})

        shutil.move(str(folder), str(target_folder_path))

        moved_count = 0
        for item in pre_move_info:
            moved_file_path = (target_folder_path / item["old_name"]).resolve()
            if not moved_file_path.exists():
                continue
            final_file_path = (target_folder_path / item["new_name"]).resolve()
            if final_file_path != moved_file_path:
                os.replace(str(moved_file_path), str(final_file_path))

            f_hash = cls.compute_sha256(final_file_path)
            n_pages = cls.get_pdf_page_count(final_file_path) if final_file_path.suffix.lower() == ".pdf" else 0
            db_manager.log_file_movement(
                tipo_movimiento=tipo_movimiento,
                ruta_anterior=item["old_path"],
                ruta_nueva=final_file_path,
                nombre_anterior=item["old_name"],
                nombre_nuevo=item["new_name"],
                hash_actual=f_hash,
                numero_paginas=n_pages,
                hash_origen_inicial=f_hash,
            )
            if mysql_manager and getattr(mysql_manager, "enabled", False):
                mysql_manager.registrar_trazabilidad_archivo(
                    tipo_operacion=tipo_movimiento,
                    archivo_nombre_origen=item["old_name"],
                    archivo_nombre_destino=item["new_name"],
                    carpeta_origen=str(folder),
                    carpeta_destino=str(target_folder_path),
                    ruta_completa_origen=str(item["old_path"]),
                    ruta_completa_destino=str(final_file_path),
                    hash_sha256=f_hash,
                    numero_paginas=n_pages,
                )
            moved_count += 1

        return {
            "old_folder_path": str(folder),
            "new_folder_path": str(target_folder_path),
            "error_subfolder": clean_sub,
            "files_moved_count": moved_count,
        }

    @classmethod
    def move_folder_to_validados(
        cls,
        folder_path: Any,
        validados_dir: Any,
        db_manager: DatabaseManager,
    ) -> Dict[str, Any]:
        """
        Mueve una carpeta que ya está totalmente bien (COINCIDE_TOTAL) al directorio
        de carpetas correctas/validadas especificado por el usuario (sin subcarpeta de error),
        registrando el movimiento en SQLite.
        """
        return cls.move_folder_to_apartados(
            folder_path=folder_path,
            apartados_dir=validados_dir,
            db_manager=db_manager,
            tipo_movimiento="MOVIDO_A_CORRECTOS",
            error_subfolder=None,
        )

    @classmethod
    def rollback_single_candidate(
        cls,
        candidate: Dict[str, Any],
        db_manager: DatabaseManager,
        mysql_manager: Optional[Any] = None,
    ) -> Tuple[bool, str]:
        """
        Restaura un archivo individual exactamente a como estaba antes del primer cambio:
        - Misma ruta inicial ('ruta_origen_inicial')
        - Mismo nombre inicial ('nombre_origen_inicial')
        - Mismo contenido binario y hash SHA-256 ('hash_origen_inicial') desde 'backup_contenido_path' si aplica.
        """
        init_path = Path(candidate["ruta_origen_inicial"])
        curr_path = Path(candidate["ruta_actual"])
        backup_str = candidate.get("backup_contenido_path")
        backup_path = Path(backup_str) if backup_str else None

        init_dir = init_path.parent
        init_dir.mkdir(parents=True, exist_ok=True)
        curr_dir = curr_path.parent

        try:
            # 1. Si existe un respaldo binario (por reemplazo de hoja en blanco), restaurar esos bytes exactos
            if backup_path is not None and backup_path.exists():
                shutil.copy2(str(backup_path), str(init_path))
                if curr_path.exists() and curr_path.resolve() != init_path.resolve():
                    os.remove(str(curr_path))
                # Eliminar el archivo de backup una vez restaurado
                try:
                    os.remove(str(backup_path))
                except Exception:
                    pass
            else:
                if not curr_path.exists():
                    if init_path.exists():
                        db_manager.mark_file_reverted(init_path)
                        if mysql_manager and getattr(mysql_manager, "enabled", False):
                            mysql_manager.registrar_rollback_archivo(
                                archivo_nombre_revertido=init_path.name,
                                ruta_origen_restaurada=str(init_path),
                                hash_sha256=candidate.get("hash_origen_inicial", ""),
                                numero_paginas=candidate.get("numero_paginas", 0)
                            )
                        return True, f"Ya se encontraba en su ruta inicial: {init_path.name}"
                    return False, f"No se encontró el archivo actual en: {curr_path}"

                if curr_path.resolve() != init_path.resolve():
                    os.replace(str(curr_path), str(init_path))

            # 2. Limpiar el directorio actual (y su subcarpeta de error si aplica) si quedaron vacíos
            try:
                if curr_dir.exists() and curr_dir.resolve() != init_dir.resolve():
                    remaining = list(curr_dir.iterdir())
                    if not remaining:
                        parent_sub = curr_dir.parent
                        curr_dir.rmdir()
                        if (
                            parent_sub.exists()
                            and parent_sub.resolve() != init_dir.resolve()
                            and parent_sub.resolve() != init_dir.parent.resolve()
                            and not list(parent_sub.iterdir())
                        ):
                            parent_sub.rmdir()
            except Exception:
                pass

            # 3. Verificar integridad SHA-256 contra hash_origen_inicial
            restored_hash = cls.compute_sha256(init_path)
            expected_hash = candidate.get("hash_origen_inicial", "")
            db_manager.mark_file_reverted(init_path)

            if mysql_manager and getattr(mysql_manager, "enabled", False):
                mysql_manager.registrar_rollback_archivo(
                    archivo_nombre_revertido=init_path.name,
                    ruta_origen_restaurada=str(init_path),
                    hash_sha256=restored_hash,
                    numero_paginas=candidate.get("numero_paginas", 0)
                )

            if expected_hash and restored_hash != expected_hash:
                return (
                    True,
                    f"Restaurado en '{init_path}' (Aviso: hash actual {restored_hash[:10]} != inicial {expected_hash[:10]}).",
                )
            return True, f"Restaurado intacto a su origen: {init_path}"
        except Exception as exc:
            return False, f"Error al revertir '{init_path.name}': {exc}"

    @classmethod
    def rollback_folder_to_initial_state(
        cls,
        initial_folder_path: Any,
        db_manager: DatabaseManager,
        mysql_manager: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Revierte todos los archivos pertenecientes a una carpeta original a su punto inicial
        previo al primer cambio.
        """
        candidates = db_manager.get_rollback_candidates(filter_initial_folder=str(initial_folder_path))
        restored = 0
        errors: List[str] = []
        details: List[str] = []

        for cand in candidates:
            ok, msg = cls.rollback_single_candidate(cand, db_manager, mysql_manager)
            if ok:
                restored += 1
                details.append(msg)
            else:
                errors.append(msg)

        return {
            "initial_folder": str(initial_folder_path),
            "total_candidates": len(candidates),
            "restored_count": restored,
            "errors": errors,
            "details": details,
        }

    @classmethod
    def rollback_all_to_initial_state(
        cls,
        db_manager: DatabaseManager,
        mysql_manager: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Revierte absolutamente todos los archivos con cambios activos en la base de datos
        a su estado y ubicación original antes del primer cambio.
        """
        candidates = db_manager.get_rollback_candidates()
        restored = 0
        errors: List[str] = []

        for cand in candidates:
            ok, msg = cls.rollback_single_candidate(cand, db_manager, mysql_manager)
            if ok:
                restored += 1
            else:
                errors.append(msg)

        return {
            "total_candidates": len(candidates),
            "restored_count": restored,
            "errors": errors,
        }
