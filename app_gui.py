"""
Interfaz Gráfica (GUI) para Diagnóstico General, Autocorrección de Carátula, Renombrado,
Auditoría en SQLite ('auditoria_libros.db') y Herramienta de Rollback Total con Claude Sonnet 5.
"""
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

if getattr(sys, "frozen", False):
    CURRENT_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    CURRENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_DIR))

from src.service import FolderOCRService
from src.core.settings_manager import SettingsManager, AVAILABLE_MODELS
from src.core.claude_engine import ClaudeVisionEngine
from src.parsers.folder_parser import FolderParser
from src.core.file_operations import FileOperations
from src.database.mysql_manager import MySQLManager
from src.core.folder_renamer_by_pdf import FolderRenamerByFirstPdf
from src.core.folder_file_separator import FolderFileSeparator


class SettingsDialog(tk.Toplevel):
    """Ventana modal de configuración de API Key de Claude y opciones de ahorro de Caché."""

    def __init__(self, parent: "OCRLibrosApp"):
        super().__init__(parent)
        self.parent_app = parent
        self.title("⚙ Configuración de API Key (Claude Sonnet 5) y Caché")
        self.geometry("650x480")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.current_settings = SettingsManager.load()
        self._build_ui()

    def _build_ui(self):
        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        api_frame = ttk.LabelFrame(container, text=" Credenciales de Anthropic Claude API ", padding=12)
        api_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(api_frame, text="API Key de Claude:").grid(row=0, column=0, sticky=tk.W, pady=(0, 6))

        self.api_key_var = tk.StringVar(value=self.current_settings.get("api_key", ""))
        self.entry_key = ttk.Entry(api_frame, textvariable=self.api_key_var, font=("Consolas", 9), width=58, show="•")
        self.entry_key.grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=(0, 8))

        self.show_key_var = tk.BooleanVar(value=False)
        chk_show = ttk.Checkbutton(
            api_frame,
            text="Mostrar Key",
            variable=self.show_key_var,
            command=self._toggle_key_visibility
        )
        chk_show.grid(row=1, column=2, sticky=tk.W)

        ttk.Label(api_frame, text="Modelo Claude:").grid(row=2, column=0, sticky=tk.W, pady=(10, 4))
        self.model_var = tk.StringVar(value=self.current_settings.get("model", "claude-sonnet-5"))
        cmb_model = ttk.Combobox(api_frame, textvariable=self.model_var, values=AVAILABLE_MODELS, width=32)
        cmb_model.grid(row=2, column=1, sticky=tk.W, pady=(10, 4))

        self.btn_test = ttk.Button(api_frame, text="🔌 Probar API Key", command=self._test_api_connection)
        self.btn_test.grid(row=2, column=2, sticky=tk.E, pady=(10, 4))

        self.lbl_test_status = ttk.Label(api_frame, text="", font=("Segoe UI", 9))
        self.lbl_test_status.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(4, 0))

        api_frame.columnconfigure(1, weight=1)

        cache_frame = ttk.LabelFrame(container, text=" Optimización de Caché y Ahorro de Tokens ", padding=12)
        cache_frame.pack(fill=tk.X, pady=(0, 12))

        self.use_prompt_cache_var = tk.BooleanVar(value=bool(self.current_settings.get("use_prompt_cache", True)))
        ttk.Checkbutton(
            cache_frame,
            text="Activar Prompt Caching de Anthropic (cache_control ephemeral: 90% ahorro en prompt del sistema)",
            variable=self.use_prompt_cache_var
        ).pack(anchor=tk.W, pady=3)

        self.use_local_cache_var = tk.BooleanVar(value=bool(self.current_settings.get("use_local_cache", True)))
        ttk.Checkbutton(
            cache_frame,
            text="Activar Caché Local Persistente en Disco (0 tokens al volver a revisar carátulas ya procesadas)",
            variable=self.use_local_cache_var
        ).pack(anchor=tk.W, pady=3)

        stats = self.parent_app.service.cache.get_summary()
        stats_frame = ttk.Frame(cache_frame)
        stats_frame.pack(fill=tk.X, pady=(10, 0))

        self.lbl_cache_stats = ttk.Label(
            stats_frame,
            text=(
                f"Carátulas en caché local: {stats['cached_documents_on_disk']}   |   "
                f"Hits locales (0 tok): {stats['local_cache_hits']}   |   "
                f"Tokens leídos de caché Anthropic: {stats['cache_read_tokens']}"
            ),
            foreground="#0369a1",
            font=("Segoe UI", 9, "bold")
        )
        self.lbl_cache_stats.pack(side=tk.LEFT)

        ttk.Button(stats_frame, text="🗑 Limpiar Caché Local", command=self._clear_local_cache).pack(side=tk.RIGHT)

        btn_bar = ttk.Frame(container)
        btn_bar.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(btn_bar, text="Cancelar", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            btn_bar,
            text="💾 Guardar Configuración",
            style="Primary.TButton",
            command=self._save_and_close
        ).pack(side=tk.RIGHT)

    def _toggle_key_visibility(self):
        self.entry_key.config(show="" if self.show_key_var.get() else "•")

    def _test_api_connection(self):
        key = self.api_key_var.get().strip()
        model = self.model_var.get().strip() or "claude-sonnet-5"
        self.lbl_test_status.config(text="Probando conexión con Anthropic API...", foreground="#0284c7")
        self.update_idletasks()

        ok, msg = ClaudeVisionEngine.test_connection(api_key=key, model=model)
        if ok:
            self.lbl_test_status.config(text=f"✔ {msg}", foreground="#15803d")
        else:
            self.lbl_test_status.config(text=f"✖ {msg}", foreground="#b91c1c")

    def _clear_local_cache(self):
        removed = self.parent_app.service.cache.clear()
        stats = self.parent_app.service.cache.get_summary()
        self.lbl_cache_stats.config(
            text=(
                f"Carátulas en caché local: {stats['cached_documents_on_disk']}   |   "
                f"Hits locales (0 tok): {stats['local_cache_hits']}   |   "
                f"Tokens leídos de caché Anthropic: {stats['cache_read_tokens']}"
            )
        )
        self.parent_app._refresh_header_badge()
        messagebox.showinfo("Caché Limpiada", f"Se eliminaron {removed} registros de la caché local en disco.")

    def _save_and_close(self):
        new_settings = dict(self.current_settings)
        new_settings["api_key"] = self.api_key_var.get().strip()
        new_settings["model"] = self.model_var.get().strip() or "claude-sonnet-5"
        new_settings["use_prompt_cache"] = bool(self.use_prompt_cache_var.get())
        new_settings["use_local_cache"] = bool(self.use_local_cache_var.get())

        SettingsManager.save(new_settings)
        self.parent_app.service.reload_settings()
        self.parent_app._refresh_header_badge()
        self.destroy()


class RollbackAndDatabaseDialog(tk.Toplevel):
    """
    Centro de Rollback Total y Visor de las 2 Tablas SQLite ('detecciones' y 'movimientos_archivos').
    Permite regresar cualquier archivo individual, carpeta completa o todos los archivos al estado
    exacto (ruta, nombre y contenido binario SHA-256) que tenían antes del primer cambio.
    """

    def __init__(self, parent: "OCRLibrosApp"):
        super().__init__(parent)
        self.parent_app = parent
        self.service = parent.service
        self.title("⏪ Centro de Rollback al Punto Inicial y Base de Datos SQLite (auditoria_libros.db)")
        self.geometry("1220x700")
        self.transient(parent)
        self.grab_set()

        self.candidates_by_iid: Dict[str, Dict[str, Any]] = {}
        self._build_ui()
        self.refresh_all_tabs()

    def _build_ui(self):
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(container)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(
            top_bar,
            text=f"Base de Datos SQLite: {self.service.db.db_path}",
            font=("Consolas", 9, "bold"),
            foreground="#0369a1"
        ).pack(side=tk.LEFT)

        ttk.Button(top_bar, text="🔄 Actualizar Tablas", command=self.refresh_all_tabs).pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Pestaña 1: Rollback al Estado Inicial
        tab_rb = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_rb, text=" ⏪ 1. Herramienta de Rollback (Restaurar antes del 1er Cambio) ")

        info_lbl = ttk.Label(
            tab_rb,
            text=(
                "Aquí se listan todos los archivos que han sufrido algún cambio (reemplazo de hoja en blanco, "
                "renombrado o envío a apartados).\nAl hacer Rollback, el archivo vuelve exactamente a la ruta, "
                "nombre y contenido binario (hash SHA-256) que tenía ANTES de su primer cambio."
            ),
            foreground="#334155"
        )
        info_lbl.pack(anchor=tk.W, pady=(0, 8))

        rb_btn_frame = ttk.Frame(tab_rb)
        rb_btn_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(
            rb_btn_frame,
            text="⏪ Revertir Archivo(s) Seleccionado(s) al Origen Inicial",
            style="Primary.TButton",
            command=self._rollback_selected_files
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            rb_btn_frame,
            text="📁 Revertir Carpeta Completa del Archivo Seleccionado",
            command=self._rollback_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            rb_btn_frame,
            text="🚨 Revertir TODOS los Cambios Activos (Rollback Total)",
            style="Danger.TButton",
            command=self._rollback_all
        ).pack(side=tk.RIGHT)

        rb_cols = (
            "carpeta_ini",
            "archivo_ini",
            "ruta_actual",
            "cambios",
            "paginas",
            "hash_ini",
            "backup",
            "fecha"
        )
        self.tree_rb = ttk.Treeview(tab_rb, columns=rb_cols, show="headings", selectmode="extended")
        self.tree_rb.heading("carpeta_ini", text="Carpeta Original (Antes 1er Cambio)")
        self.tree_rb.heading("archivo_ini", text="Nombre Original")
        self.tree_rb.heading("ruta_actual", text="Ubicación / Nombre Actual")
        self.tree_rb.heading("cambios", text="Cambios Aplicados")
        self.tree_rb.heading("paginas", text="Págs")
        self.tree_rb.heading("hash_ini", text="Hash SHA-256 Inicial")
        self.tree_rb.heading("backup", text="Respaldo Binario")
        self.tree_rb.heading("fecha", text="Último Movimiento")

        self.tree_rb.column("carpeta_ini", width=210, anchor=tk.W)
        self.tree_rb.column("archivo_ini", width=220, anchor=tk.W)
        self.tree_rb.column("ruta_actual", width=310, anchor=tk.W)
        self.tree_rb.column("cambios", width=180, anchor=tk.CENTER)
        self.tree_rb.column("paginas", width=55, anchor=tk.CENTER, stretch=False)
        self.tree_rb.column("hash_ini", width=120, anchor=tk.CENTER)
        self.tree_rb.column("backup", width=110, anchor=tk.CENTER)
        self.tree_rb.column("fecha", width=130, anchor=tk.CENTER)

        scroll_rb = ttk.Scrollbar(tab_rb, orient=tk.VERTICAL, command=self.tree_rb.yview)
        self.tree_rb.configure(yscrollcommand=scroll_rb.set)
        self.tree_rb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_rb.pack(side=tk.RIGHT, fill=tk.Y)

        # Pestaña 2: Tabla SQLite 'movimientos_archivos'
        tab_mov = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_mov, text=" 📋 2. Tabla SQLite: movimientos_archivos ")

        mov_cols = (
            "id", "fecha", "tipo", "nombre_ant", "nombre_nvo",
            "ruta_ant", "ruta_nva", "hash", "paginas", "revertido"
        )
        self.tree_mov = ttk.Treeview(tab_mov, columns=mov_cols, show="headings")
        self.tree_mov.heading("id", text="ID")
        self.tree_mov.heading("fecha", text="Fecha")
        self.tree_mov.heading("tipo", text="Tipo Movimiento")
        self.tree_mov.heading("nombre_ant", text="Nombre Anterior")
        self.tree_mov.heading("nombre_nvo", text="Nombre Nuevo")
        self.tree_mov.heading("ruta_ant", text="Ruta Anterior")
        self.tree_mov.heading("ruta_nva", text="Ruta Nueva")
        self.tree_mov.heading("hash", text="Hash SHA-256")
        self.tree_mov.heading("paginas", text="Págs")
        self.tree_mov.heading("revertido", text="Estado")

        self.tree_mov.column("id", width=45, anchor=tk.CENTER, stretch=False)
        self.tree_mov.column("fecha", width=130, anchor=tk.CENTER)
        self.tree_mov.column("tipo", width=160, anchor=tk.CENTER)
        self.tree_mov.column("nombre_ant", width=210, anchor=tk.W)
        self.tree_mov.column("nombre_nvo", width=210, anchor=tk.W)
        self.tree_mov.column("ruta_ant", width=240, anchor=tk.W)
        self.tree_mov.column("ruta_nva", width=240, anchor=tk.W)
        self.tree_mov.column("hash", width=110, anchor=tk.CENTER)
        self.tree_mov.column("paginas", width=55, anchor=tk.CENTER, stretch=False)
        self.tree_mov.column("revertido", width=95, anchor=tk.CENTER)

        scroll_mov = ttk.Scrollbar(tab_mov, orient=tk.VERTICAL, command=self.tree_mov.yview)
        self.tree_mov.configure(yscrollcommand=scroll_mov.set)
        self.tree_mov.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_mov.pack(side=tk.RIGHT, fill=tk.Y)

        # Pestaña 3: Tabla SQLite 'detecciones'
        tab_det = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_det, text=" 📊 3. Tabla SQLite: detecciones ")

        det_cols = (
            "id", "fecha", "carpeta", "caratula", "estado_car",
            "todos_con_car", "esperado", "detectado", "veredicto", "sugerido"
        )
        self.tree_det = ttk.Treeview(tab_det, columns=det_cols, show="headings")
        self.tree_det.heading("id", text="ID")
        self.tree_det.heading("fecha", text="Fecha")
        self.tree_det.heading("carpeta", text="Carpeta")
        self.tree_det.heading("caratula", text="PDF Carátula")
        self.tree_det.heading("estado_car", text="Estado Carátula")
        self.tree_det.heading("todos_con_car", text="¿Todos c/Carátula?")
        self.tree_det.heading("esperado", text="Carpeta [S|L|T|V|A]")
        self.tree_det.heading("detectado", text="Detectado [S|L|T|V|A]")
        self.tree_det.heading("veredicto", text="Veredicto")
        self.tree_det.heading("sugerido", text="Código Sugerido")

        self.tree_det.column("id", width=45, anchor=tk.CENTER, stretch=False)
        self.tree_det.column("fecha", width=130, anchor=tk.CENTER)
        self.tree_det.column("carpeta", width=210, anchor=tk.W)
        self.tree_det.column("caratula", width=200, anchor=tk.W)
        self.tree_det.column("estado_car", width=155, anchor=tk.CENTER)
        self.tree_det.column("todos_con_car", width=115, anchor=tk.CENTER)
        self.tree_det.column("esperado", width=130, anchor=tk.CENTER)
        self.tree_det.column("detectado", width=130, anchor=tk.CENTER)
        self.tree_det.column("veredicto", width=155, anchor=tk.CENTER)
        self.tree_det.column("sugerido", width=185, anchor=tk.W)

        scroll_det = ttk.Scrollbar(tab_det, orient=tk.VERTICAL, command=self.tree_det.yview)
        self.tree_det.configure(yscrollcommand=scroll_det.set)
        self.tree_det.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_det.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh_all_tabs(self):
        # 1. Candidatos de Rollback
        for item in self.tree_rb.get_children():
            self.tree_rb.delete(item)
        self.candidates_by_iid.clear()

        candidates = self.service.db.get_rollback_candidates()
        for idx, cand in enumerate(candidates, start=1):
            iid = f"rb_{idx}"
            self.candidates_by_iid[iid] = cand
            has_bkp = "Sí (Pág Original)" if cand.get("backup_contenido_path") else "No requerido"
            h_ini = (cand.get("hash_origen_inicial") or "")[:14] + "..."
            self.tree_rb.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    cand.get("nombre_carpeta_inicial", ""),
                    cand.get("nombre_origen_inicial", ""),
                    cand.get("ruta_actual", ""),
                    f"{cand.get('tipos_movimiento_str', '')} ({cand.get('cantidad_cambios', 1)})",
                    cand.get("numero_paginas", 0),
                    h_ini,
                    has_bkp,
                    cand.get("ultima_fecha", ""),
                )
            )

        # 2. Historial de movimientos_archivos
        for item in self.tree_mov.get_children():
            self.tree_mov.delete(item)
        movements = self.service.db.get_movements(include_reverted=True, limit=1000)
        for m in movements:
            rev_str = "⏪ REVERTIDO" if m.get("revertido") else "✔ ACTIVO"
            h_short = (m.get("hash") or "")[:14] + "..."
            self.tree_mov.insert(
                "",
                tk.END,
                values=(
                    m.get("id"),
                    m.get("fecha_movimiento"),
                    m.get("tipo_movimiento"),
                    m.get("nombre_anterior"),
                    m.get("nombre_nuevo"),
                    m.get("ruta_anterior"),
                    m.get("ruta_nueva"),
                    h_short,
                    m.get("numero_paginas"),
                    rev_str,
                )
            )

        # 3. Historial de detecciones
        for item in self.tree_det.get_children():
            self.tree_det.delete(item)
        dets = self.service.db.get_detections(limit=500)
        for d in dets:
            esp = f"{d.get('carpeta_seccion')}|{d.get('carpeta_libro')}|{d.get('carpeta_tomo')}|{d.get('carpeta_volumen')}|{d.get('carpeta_anio')}"
            det = f"{d.get('detectado_seccion')}|{d.get('detectado_libro')}|{d.get('detectado_tomo')}|{d.get('detectado_volumen')}|{d.get('detectado_anio')}"
            todos_ok = "Sí" if d.get("todos_pdfs_con_caratula") else "No"
            self.tree_det.insert(
                "",
                tk.END,
                values=(
                    d.get("id"),
                    d.get("fecha_registro"),
                    d.get("nombre_carpeta"),
                    d.get("pdf_caratula"),
                    d.get("estado_caratula"),
                    todos_ok,
                    esp,
                    det,
                    d.get("veredicto"),
                    d.get("codigo_base_sugerido"),
                )
            )

    def _rollback_selected_files(self):
        sel = self.tree_rb.selection()
        if not sel:
            messagebox.showwarning("Atención", "Selecciona uno o más archivos de la lista para revertir.", parent=self)
            return

        if not messagebox.askyesno(
            "Confirmar Rollback de Archivo(s)",
            f"¿Deseas restaurar {len(sel)} archivo(s) seleccionado(s) exactamente a como estaban antes del primer cambio?",
            parent=self
        ):
            return

        restored = 0
        errors: List[str] = []
        for iid in sel:
            cand = self.candidates_by_iid.get(iid)
            if not cand:
                continue
            ok, msg = self.service.rollback_single_file(cand)
            if ok:
                restored += 1
            else:
                errors.append(msg)

        self.refresh_all_tabs()
        self.parent_app.reload_root_preview()
        if errors:
            messagebox.showwarning(
                "Rollback Parcial",
                f"Archivos restaurados: {restored}\nErrores ({len(errors)}):\n" + "\n".join(errors[:10]),
                parent=self
            )
        else:
            messagebox.showinfo(
                "Rollback Completado",
                f"Se restauraron exitosamente {restored} archivo(s) a su ruta, nombre y contenido original.",
                parent=self
            )

    def _rollback_selected_folder(self):
        sel = self.tree_rb.selection()
        if not sel:
            messagebox.showwarning(
                "Atención",
                "Selecciona cualquier archivo de la carpeta que deseas revertir por completo.",
                parent=self
            )
            return

        cand = self.candidates_by_iid.get(sel[0])
        if not cand:
            return
        init_folder = cand.get("carpeta_origen_inicial", "")
        folder_name = cand.get("nombre_carpeta_inicial", "")

        if not messagebox.askyesno(
            "Confirmar Rollback de Carpeta Completa",
            f"¿Deseas revertir TODOS los archivos de la carpeta original:\n'{folder_name}'\n"
            f"a su estado exacto antes del primer cambio?",
            parent=self
        ):
            return

        res = self.service.rollback_folder(init_folder)
        self.refresh_all_tabs()
        self.parent_app.reload_root_preview()
        if res["errors"]:
            messagebox.showwarning(
                "Rollback con Avisos",
                f"Restaurados: {res['restored_count']}/{res['total_candidates']}\n"
                + "\n".join(res["errors"][:10]),
                parent=self
            )
        else:
            messagebox.showinfo(
                "Carpeta Restaurada",
                f"La carpeta '{folder_name}' ({res['restored_count']} archivos) fue restaurada intacta a su origen inicial.",
                parent=self
            )

    def _rollback_all(self):
        candidates = self.service.db.get_rollback_candidates()
        if not candidates:
            messagebox.showinfo("Sin Cambios Activos", "No hay archivos pendientes por revertir.", parent=self)
            return

        if not messagebox.askyesno(
            "Confirmar Rollback Total",
            f"¿Estás seguro de revertir TODOS los {len(candidates)} archivos modificados en la base de datos "
            "a su estado original antes del primer cambio?",
            parent=self
        ):
            return

        res = self.service.rollback_all()
        self.refresh_all_tabs()
        self.parent_app.reload_root_preview()
        if res["errors"]:
            messagebox.showwarning(
                "Rollback Total con Avisos",
                f"Restaurados: {res['restored_count']}/{res['total_candidates']}\n"
                + "\n".join(res["errors"][:10]),
                parent=self
            )
        else:
            messagebox.showinfo(
                "Rollback Total Completado",
                f"Se restauraron intactos los {res['restored_count']} archivos a su ubicación y estado inicial.",
                parent=self
            )


class ManualEditDialog(tk.Toplevel):
    """
    Ventana modal para editar manualmente los 6 campos de la carpeta y generar el código limpio:
    [Delegación (Fija)]_[Sección]_[Libro]_[Tomo]_[Volumen]_[Fraccionamiento]_[Año]
    Permite corregir cualquier discrepancia o error de Claude antes de aplicar el renombre físico.
    """

    def __init__(self, parent: "OCRLibrosApp", res: Dict[str, Any]):
        super().__init__(parent)
        self.parent_app = parent
        self.res = res
        self.title("✏️ Corrección Manual de Campos antes de Aplicar")
        self.geometry("640x540")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        curr_folder_name = Path(res.get("folder_path", "")).name
        curr_code = self.parent_app.suggested_code_var.get().strip() or res.get("codigo_base_sugerido", "")
        p_data = FolderParser.parse(curr_code) if curr_code else FolderParser.parse(curr_folder_name)

        self.del_val = p_data.get("delegacion") or "01"
        self.sec_var = tk.StringVar(value=str(p_data.get("seccion_raw") or "00"))
        self.lib_var = tk.StringVar(value=str(p_data.get("libro_raw") or "00"))
        self.tom_var = tk.StringVar(value=str(p_data.get("tomo_raw") or "00"))
        self.vol_var = tk.StringVar(value=str(p_data.get("volumen_raw") or "00"))
        self.fracc_var = tk.StringVar(value=str(p_data.get("fraccionamiento_raw") or "00"))
        self.anio_var = tk.StringVar(value=str(p_data.get("anio_raw") or "0000"))
        self.preview_code_var = tk.StringVar(value=curr_code or curr_folder_name)

        for v in (self.sec_var, self.lib_var, self.tom_var, self.vol_var, self.fracc_var, self.anio_var):
            v.trace_add("write", self._update_preview)

        self._build_ui(curr_folder_name)
        self._update_preview()

    def _build_ui(self, curr_folder_name: str):
        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.LabelFrame(container, text=" Carpeta Seleccionada ", padding=10)
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            header,
            text=f"📁 {curr_folder_name}",
            font=("Segoe UI", 9, "bold"),
            foreground="#1e293b",
            wraplength=580
        ).pack(anchor=tk.W)

        fields_frame = ttk.LabelFrame(container, text=" Campos para el Código Base (7 Posiciones) ", padding=12)
        fields_frame.pack(fill=tk.X, pady=(0, 12))

        rows_def = [
            ("1. Delegación (Pos. 1 - Fija):", self.del_val, True, None),
            ("2. Sección (Pos. 2 - ej. 00, 01, 02...):", self.sec_var, False, None),
            ("3. Libro (Pos. 3 - ej. 01, 02, 100):", self.lib_var, False, None),
            ("4. Tomo (Pos. 4 - ej. 01, 03, o CREDITO_AGRICOLA, AUXILIAR):", self.tom_var, False, None),
            ("5. Volumen (Pos. 5 - ej. 00, 01, 02):", self.vol_var, False, None),
            ("6. Fracc. / Materia (Pos. 6 - ej. 00, ACTOS_Y_CONTRATOS, COLONIAS):", self.fracc_var, False, None),
            ("7. Año (Pos. 7 - 4 dígitos, ej. 1992):", self.anio_var, False, None),
        ]

        for i, (label_t, val_or_var, is_fixed, combo_vals) in enumerate(rows_def):
            ttk.Label(fields_frame, text=label_t, font=("Segoe UI", 9)).grid(row=i, column=0, sticky=tk.W, pady=3)
            if is_fixed:
                lbl_f = ttk.Label(fields_frame, text=f"{val_or_var} 🔒 (Inmutable)", font=("Consolas", 9, "bold"), foreground="#64748b")
                lbl_f.grid(row=i, column=1, sticky=tk.W, padx=(8, 0), pady=3)
            elif combo_vals:
                cmb = ttk.Combobox(fields_frame, textvariable=val_or_var, values=combo_vals, width=22, font=("Consolas", 9))
                cmb.grid(row=i, column=1, sticky=tk.W, padx=(8, 0), pady=3)
            else:
                ent = ttk.Entry(fields_frame, textvariable=val_or_var, width=24, font=("Consolas", 9))
                ent.grid(row=i, column=1, sticky=tk.W, padx=(8, 0), pady=3)

        fields_frame.columnconfigure(1, weight=1)

        preview_frame = ttk.LabelFrame(container, text=" Vista Previa en Tiempo Real del Código Sugerido ", padding=10)
        preview_frame.pack(fill=tk.X, pady=(0, 14))

        lbl_prev = ttk.Label(
            preview_frame,
            textvariable=self.preview_code_var,
            font=("Consolas", 12, "bold"),
            foreground="#2563eb",
            anchor=tk.CENTER
        )
        lbl_prev.pack(fill=tk.X)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X, pady=(4, 0))

        ttk.Button(
            btn_box,
            text="✔ Guardar en Código Sugerido",
            style="Primary.TButton",
            command=self._save_code
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            btn_box,
            text="✏️ Renombrar Ahora (Carpeta y PDFs)",
            command=self._rename_now
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            btn_box,
            text="Cancelar",
            command=self.destroy
        ).pack(side=tk.RIGHT)

    def _update_preview(self, *args):
        sec = self.sec_var.get().strip()
        lib = self.lib_var.get().strip()
        tom = self.tom_var.get().strip()
        vol = self.vol_var.get().strip()
        fracc = self.fracc_var.get().strip()
        anio = self.anio_var.get().strip()

        code = FileOperations.build_clean_folder_code(
            delegacion=self.del_val,
            seccion=sec,
            libro=lib,
            tomo=tom,
            volumen=vol,
            fraccionamiento=fracc,
            anio=anio
        )
        self.preview_code_var.set(code)

    def _save_code(self):
        new_code = self.preview_code_var.get().strip()
        self.res["codigo_base_sugerido"] = new_code
        self.parent_app.suggested_code_var.set(new_code)
        self.parent_app._refresh_master_table()
        self.parent_app._render_single_folder_detail(self.res)
        self.destroy()

    def _rename_now(self):
        new_code = self.preview_code_var.get().strip()
        self.res["codigo_base_sugerido"] = new_code
        self.parent_app.suggested_code_var.set(new_code)
        self.destroy()
        self.parent_app.rename_selected_folder()



class MySQLSettingsDialog(tk.Toplevel):
    """Ventana de configuración, prueba de conexión y creación de base de datos y tablas MySQL."""

    def __init__(self, parent: "OCRLibrosApp"):
        super().__init__(parent)
        self.parent_app = parent
        self.title("🗄 Conexión MySQL & Trazabilidad Total")
        self.geometry("640x530")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.current_settings = SettingsManager.load()
        self._build_ui()

    def _build_ui(self):
        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        header_lbl = ttk.Label(
            container,
            text="Control y Conexión a Base de Datos MySQL para Trazabilidad Total",
            font=("Segoe UI", 11, "bold")
        )
        header_lbl.pack(anchor=tk.W, pady=(0, 4))

        desc_lbl = ttk.Label(
            container,
            text="Permite probar la conexión y crear automáticamente la base de datos y las tablas de trazabilidad.",
            font=("Segoe UI", 9),
            foreground="#475569"
        )
        desc_lbl.pack(anchor=tk.W, pady=(0, 12))

        form_frame = ttk.LabelFrame(container, text=" Parámetros del Servidor MySQL ", padding=12)
        form_frame.pack(fill=tk.X, pady=(0, 10))

        # Host y Puerto
        ttk.Label(form_frame, text="Servidor / Host:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.host_var = tk.StringVar(value=str(self.current_settings.get("mysql_host", "127.0.0.1")))
        ttk.Entry(form_frame, textvariable=self.host_var, width=28).grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)

        ttk.Label(form_frame, text="Puerto:").grid(row=0, column=2, sticky=tk.W, padx=(12, 0), pady=4)
        self.port_var = tk.StringVar(value=str(self.current_settings.get("mysql_port", 3306)))
        ttk.Entry(form_frame, textvariable=self.port_var, width=10).grid(row=0, column=3, sticky=tk.W, padx=6, pady=4)

        # Usuario y Contraseña
        ttk.Label(form_frame, text="Usuario:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.user_var = tk.StringVar(value=str(self.current_settings.get("mysql_user", "root")))
        ttk.Entry(form_frame, textvariable=self.user_var, width=28).grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)

        ttk.Label(form_frame, text="Contraseña:").grid(row=1, column=2, sticky=tk.W, padx=(12, 0), pady=4)
        self.pass_var = tk.StringVar(value=str(self.current_settings.get("mysql_password", "")))
        self.entry_pass = ttk.Entry(form_frame, textvariable=self.pass_var, width=18, show="•")
        self.entry_pass.grid(row=1, column=3, sticky=tk.W, padx=6, pady=4)

        # Base de Datos
        ttk.Label(form_frame, text="Base de Datos:").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.db_var = tk.StringVar(value=str(self.current_settings.get("mysql_database", "trazabilidad_selloslibros")))
        ttk.Entry(form_frame, textvariable=self.db_var, width=28).grid(row=2, column=1, sticky=tk.W, padx=6, pady=4)

        self.show_pass_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            form_frame,
            text="Ver clave",
            variable=self.show_pass_var,
            command=self._toggle_pass
        ).grid(row=2, column=3, sticky=tk.W, padx=6, pady=4)

        # Checkbox activar MySQL
        self.enabled_var = tk.BooleanVar(value=bool(self.current_settings.get("mysql_enabled", False)))
        chk_act = ttk.Checkbutton(
            container,
            text="Activar registro activo de trazabilidad en MySQL (movimientos físicos, hashes y separaciones)",
            variable=self.enabled_var
        )
        chk_act.pack(anchor=tk.W, pady=(0, 10))

        # Botones de Acción: Probar Conexión y Crear Tablas
        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X, pady=(0, 10))

        self.btn_test = ttk.Button(
            btn_box,
            text="🔌 Probar Conexión",
            command=self._test_connection
        )
        self.btn_test.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_init = ttk.Button(
            btn_box,
            text="🏗 Crear Base de Datos y Tablas",
            style="Primary.TButton",
            command=self._init_database
        )
        self.btn_init.pack(side=tk.LEFT, padx=(0, 8))

        # Estado / Mensajes de diagnóstico
        self.lbl_status = tk.Label(
            container,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg="#f8fafc",
            fg="#334155",
            relief=tk.GROOVE,
            bd=1,
            padx=10,
            pady=8,
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=580
        )
        self.lbl_status.pack(fill=tk.X, pady=(0, 14))

        # Botones inferiores (Guardar / Cerrar)
        bot_bar = ttk.Frame(container)
        bot_bar.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(bot_bar, text="💾 Guardar Configuración", style="Primary.TButton", command=self._save_settings).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bot_bar, text="Cerrar", command=self.destroy).pack(side=tk.RIGHT)

    def _toggle_pass(self):
        self.entry_pass.config(show="" if self.show_pass_var.get() else "•")

    def _test_connection(self):
        h = self.host_var.get().strip()
        p = self.port_var.get().strip()
        u = self.user_var.get().strip()
        pwd = self.pass_var.get()

        try:
            port_int = int(p)
        except ValueError:
            self.lbl_status.config(text="✖ Error: El puerto debe ser un número entero (ej. 3306).", fg="#dc2626")
            return

        self.lbl_status.config(text="⏳ Conectando al servidor MySQL...", fg="#0284c7")
        self.update_idletasks()

        ok, msg = MySQLManager.test_connection_params(h, port_int, u, pwd)
        if ok:
            self.lbl_status.config(text=f"✔ {msg}", fg="#16a34a")
        else:
            self.lbl_status.config(text=f"✖ {msg}", fg="#dc2626")

    def _init_database(self):
        h = self.host_var.get().strip()
        p = self.port_var.get().strip()
        u = self.user_var.get().strip()
        pwd = self.pass_var.get()
        db = self.db_var.get().strip()

        try:
            port_int = int(p)
        except ValueError:
            self.lbl_status.config(text="✖ Error: El puerto debe ser numérico.", fg="#dc2626")
            return

        if not db:
            self.lbl_status.config(text="✖ Error: Especifica un nombre para la base de datos.", fg="#dc2626")
            return

        self.lbl_status.config(text="⏳ Creando base de datos y tablas de trazabilidad...", fg="#0284c7")
        self.update_idletasks()

        mgr = MySQLManager({
            "mysql_host": h,
            "mysql_port": port_int,
            "mysql_user": u,
            "mysql_password": pwd,
            "mysql_database": db,
            "mysql_enabled": True
        })
        ok, msg = mgr.init_database_and_tables()

        if ok:
            self.lbl_status.config(
                text=f"✔ {msg}\nTablas activas: 'movimientos_carpetas', 'trazabilidad_archivos', 'separaciones_carpetas', 'auditoria_diagnosticos'.",
                fg="#16a34a"
            )
            self.enabled_var.set(True)
            messagebox.showinfo("MySQL Inicializado", f"{msg}\n\nLas tablas de trazabilidad total están listas.")
        else:
            self.lbl_status.config(text=f"✖ {msg}", fg="#dc2626")
            messagebox.showerror("Error al crear tablas", msg)

    def _save_settings(self):
        try:
            p_int = int(self.port_var.get().strip())
        except ValueError:
            p_int = 3306

        self.current_settings["mysql_host"] = self.host_var.get().strip()
        self.current_settings["mysql_port"] = p_int
        self.current_settings["mysql_user"] = self.user_var.get().strip()
        self.current_settings["mysql_password"] = self.pass_var.get()
        self.current_settings["mysql_database"] = self.db_var.get().strip()
        self.current_settings["mysql_enabled"] = self.enabled_var.get()

        SettingsManager.save(self.current_settings)
        self.parent_app.mysql_manager.update_config(
            host=self.current_settings["mysql_host"],
            port=p_int,
            user=self.current_settings["mysql_user"],
            password=self.current_settings["mysql_password"],
            database=self.current_settings["mysql_database"],
            enabled=self.current_settings["mysql_enabled"]
        )
        self.parent_app._refresh_header_badge()
        messagebox.showinfo("Configuración Guardada", "Parámetros de MySQL guardados exitosamente.")
        self.destroy()


class FolderRenamerDialog(tk.Toplevel):
    """Ventana para renombrar carpetas según el primer PDF hasta el año (regla 0000 y colisión _2)."""

    def __init__(self, parent: "OCRLibrosApp", initial_path: str = ""):
        super().__init__(parent)
        self.parent_app = parent
        self.title("📁 Renombrar Carpetas por Primer PDF (con Regla 0000 y Colisiones _2)")
        self.geometry("1060x650")
        self.minsize(880, 500)
        self.transient(parent)
        self.grab_set()

        self.renamer = FolderRenamerByFirstPdf(self.parent_app.mysql_manager)
        self.preview_data: List[Dict[str, Any]] = []
        self._build_ui(initial_path)

    def _build_ui(self, initial_path: str):
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.LabelFrame(container, text=" Carpeta Contenedora de Libros ", padding=8)
        top_frame.pack(fill=tk.X, pady=(0, 8))

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X)

        self.path_var = tk.StringVar(value=initial_path or self.parent_app.path_var.get())
        ttk.Label(row1, text="Ruta:").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(row1, textvariable=self.path_var, font=("Consolas", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(row1, text="Examinar...", command=self._browse_dir).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row1, text="🔍 Escanear / Vista Previa", style="Primary.TButton", command=self._scan_preview).pack(side=tk.LEFT)

        # Resumen / Progreso
        summary_frame = ttk.Frame(container)
        summary_frame.pack(fill=tk.X, pady=(0, 6))

        self.lbl_summary = ttk.Label(summary_frame, text="Haz clic en '🔍 Escanear / Vista Previa' para analizar las carpetas.", font=("Segoe UI", 9, "bold"))
        self.lbl_summary.pack(side=tk.LEFT)

        self.mysql_active_var = tk.BooleanVar(value=self.parent_app.mysql_manager.enabled)
        chk_my = ttk.Checkbutton(summary_frame, text="Registrar movimientos en MySQL", variable=self.mysql_active_var)
        chk_my.pack(side=tk.RIGHT)

        # Tabla de Vista Previa
        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("num", "orig_name", "first_pdf", "sugg_name", "col", "status", "detail")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("num", text="#")
        self.tree.heading("orig_name", text="Carpeta Original")
        self.tree.heading("first_pdf", text="Primer Archivo PDF")
        self.tree.heading("sugg_name", text="Nombre Sugerido (hasta Año)")
        self.tree.heading("col", text="Colisión")
        self.tree.heading("status", text="Estado")
        self.tree.heading("detail", text="Detalle / Regla Aplicada")

        self.tree.column("num", width=40, anchor=tk.CENTER, stretch=False)
        self.tree.column("orig_name", width=220, anchor=tk.W)
        self.tree.column("first_pdf", width=250, anchor=tk.W)
        self.tree.column("sugg_name", width=220, anchor=tk.W)
        self.tree.column("col", width=65, anchor=tk.CENTER, stretch=False)
        self.tree.column("status", width=120, anchor=tk.CENTER)
        self.tree.column("detail", width=200, anchor=tk.W)

        self.tree.tag_configure("needs_rename", background="#fffbeb", foreground="#b45309")
        self.tree.tag_configure("match", background="#f0fdf4", foreground="#15803d")
        self.tree.tag_configure("applied", background="#ecfdf5", foreground="#047857")
        self.tree.tag_configure("error", background="#fef2f2", foreground="#b91c1c")

        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Botones inferiores
        bot_bar = ttk.Frame(container)
        bot_bar.pack(fill=tk.X)

        self.btn_apply = ttk.Button(
            bot_bar,
            text="▶ Ejecutar Renombrado Físico",
            style="Primary.TButton",
            command=self._apply_renames,
            state=tk.DISABLED
        )
        self.btn_apply.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bot_bar, text="Cerrar", command=self.destroy).pack(side=tk.RIGHT)

    def _browse_dir(self):
        sel = filedialog.askdirectory(title="Seleccionar Carpeta Contenedora", initialdir=self.path_var.get())
        if sel:
            self.path_var.set(sel)
            self._scan_preview()

    def _scan_preview(self):
        root_path = Path(self.path_var.get().strip())
        if not root_path.is_dir():
            messagebox.showerror("Error", "La ruta especificada no existe o no es un directorio.")
            return

        for item in self.tree.get_children():
            self.tree.delete(item)
        self.preview_data.clear()

        subfolders = sorted([f for f in root_path.iterdir() if f.is_dir()])
        total_sub = len(subfolders)
        to_rename_cnt = 0
        already_ok_cnt = 0

        for idx, f_path in enumerate(subfolders, start=1):
            prev = self.renamer.preview_folder_rename(f_path)
            self.preview_data.append(prev)

            col_str = f"_{prev.get('collision_index')}" if prev.get("collision_index", 0) > 0 else "-"
            st = prev.get("status", "")
            if prev.get("needs_rename"):
                tag = "needs_rename"
                to_rename_cnt += 1
                status_txt = "✏ Requiere Renombre"
            elif st == "YA_TIENE_NOMBRE_CORRECTO":
                tag = "match"
                already_ok_cnt += 1
                status_txt = "✔ Ya Coincide"
            else:
                tag = "error"
                status_txt = f"✖ {st}"

            self.tree.insert(
                "",
                tk.END,
                iid=f"ren_{idx}",
                values=(
                    idx,
                    prev.get("original_name", ""),
                    prev.get("first_pdf", "-"),
                    prev.get("suggested_name", "-"),
                    col_str,
                    status_txt,
                    prev.get("detail", prev.get("message", ""))
                ),
                tags=(tag,)
            )

        self.lbl_summary.config(
            text=f"Total: {total_sub} carpetas analizadas  |  {to_rename_cnt} por renombrar  |  {already_ok_cnt} ya correctas"
        )
        self.btn_apply.config(state=tk.NORMAL if to_rename_cnt > 0 else tk.DISABLED)

    def _apply_renames(self):
        items_to_rename = [item for item in self.preview_data if item.get("needs_rename")]
        if not items_to_rename:
            messagebox.showinfo("Información", "No hay carpetas pendientes de renombrar.")
            return

        if not messagebox.askyesno(
            "Confirmar Renombrado Físico",
            f"¿Deseas aplicar el renombrado físico a las {len(items_to_rename)} carpetas?\n\n"
            f"• Se tomará el nombre del 1er PDF hasta el año.\n"
            f"• Si el PDF tiene año '0000', se conservará el año de la carpeta original.\n"
            f"• Las colisiones se resolverán con _2 solo en la carpeta.\n"
            f"• MySQL activo: {'Sí' if self.mysql_active_var.get() else 'No'}"
        ):
            return

        renamed_count = 0
        error_count = 0

        # Activar/desactivar MySQL temporalmente según el checkbox
        prev_enabled = self.parent_app.mysql_manager.enabled
        self.parent_app.mysql_manager.enabled = self.mysql_active_var.get()

        try:
            for idx, item in enumerate(self.preview_data, start=1):
                if not item.get("needs_rename"):
                    continue
                orig_folder = Path(item["folder_path"])
                res = self.renamer.execute_folder_rename(orig_folder)
                iid = f"ren_{idx}"
                if res.get("applied"):
                    renamed_count += 1
                    self.tree.item(
                        iid,
                        values=(
                            idx,
                            item.get("original_name"),
                            item.get("first_pdf"),
                            res.get("suggested_name"),
                            f"_{res.get('collision_index')}" if res.get("collision_index", 0) > 0 else "-",
                            "✔ Renombrada",
                            res.get("message", "OK")
                        ),
                        tags=("applied",)
                    )
                else:
                    error_count += 1
                    self.tree.item(
                        iid,
                        values=(
                            idx,
                            item.get("original_name"),
                            item.get("first_pdf"),
                            item.get("suggested_name"),
                            "-",
                            "✖ Error",
                            res.get("message", "Fallo")
                        ),
                        tags=("error",)
                    )
        finally:
            self.parent_app.mysql_manager.enabled = prev_enabled

        self.btn_apply.config(state=tk.DISABLED)
        messagebox.showinfo(
            "Renombrado Completado",
            f"Proceso finalizado:\n\n✔ Carpetas renombradas exitosamente: {renamed_count}\n✖ Errores: {error_count}"
        )
        self.parent_app.reload_root_preview()


class FolderSeparatorDialog(tk.Toplevel):
    """Ventana para detectar y separar archivos mezclados pertenecientes a libros/secciones distintos."""

    def __init__(self, parent: "OCRLibrosApp", initial_path: str = ""):
        super().__init__(parent)
        self.parent_app = parent
        self.title("🔀 Separar Archivos Mezclados (Libros y Secciones Distintos)")
        self.geometry("1100x670")
        self.minsize(920, 520)
        self.transient(parent)
        self.grab_set()

        self.separator = FolderFileSeparator(self.parent_app.mysql_manager)
        self.inspected_folders: List[Dict[str, Any]] = []
        self._build_ui(initial_path)

    def _build_ui(self, initial_path: str):
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.LabelFrame(container, text=" Carpeta Raíz con Libros Registrales ", padding=8)
        top_frame.pack(fill=tk.X, pady=(0, 8))

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X)

        self.path_var = tk.StringVar(value=initial_path or self.parent_app.path_var.get())
        ttk.Label(row1, text="Ruta:").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(row1, textvariable=self.path_var, font=("Consolas", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(row1, text="Examinar...", command=self._browse_dir).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row1, text="🔍 Detectar Carpetas Mezcladas", style="Primary.TButton", command=self._scan_mixed).pack(side=tk.LEFT)

        summary_frame = ttk.Frame(container)
        summary_frame.pack(fill=tk.X, pady=(0, 6))

        self.lbl_summary = ttk.Label(summary_frame, text="Haz clic en '🔍 Detectar Carpetas Mezcladas' para escanear.", font=("Segoe UI", 9, "bold"))
        self.lbl_summary.pack(side=tk.LEFT)

        self.mysql_active_var = tk.BooleanVar(value=self.parent_app.mysql_manager.enabled)
        chk_my = ttk.Checkbutton(summary_frame, text="Registrar movimientos y hashes en MySQL", variable=self.mysql_active_var)
        chk_my.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(container, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Tabla superior: Lista de Carpetas
        top_pane = ttk.Frame(paned)
        paned.add(top_pane, weight=2)

        cols = ("num", "folder", "total_pdfs", "groups_count", "primary", "status")
        self.tree_folders = ttk.Treeview(top_pane, columns=cols, show="headings", selectmode="browse")
        self.tree_folders.heading("num", text="#")
        self.tree_folders.heading("folder", text="Carpeta")
        self.tree_folders.heading("total_pdfs", text="Total PDFs")
        self.tree_folders.heading("groups_count", text="Grupos Únicos")
        self.tree_folders.heading("primary", text="Grupo Primario (Permanece)")
        self.tree_folders.heading("status", text="Diagnóstico")

        self.tree_folders.column("num", width=40, anchor=tk.CENTER, stretch=False)
        self.tree_folders.column("folder", width=280, anchor=tk.W)
        self.tree_folders.column("total_pdfs", width=80, anchor=tk.CENTER, stretch=False)
        self.tree_folders.column("groups_count", width=95, anchor=tk.CENTER, stretch=False)
        self.tree_folders.column("primary", width=250, anchor=tk.W)
        self.tree_folders.column("status", width=140, anchor=tk.CENTER)

        self.tree_folders.tag_configure("mixed", background="#fff1f2", foreground="#be123c")
        self.tree_folders.tag_configure("clean", background="#f0fdf4", foreground="#15803d")
        self.tree_folders.tag_configure("done", background="#ecfdf5", foreground="#047857")

        scroll1 = ttk.Scrollbar(top_pane, orient=tk.VERTICAL, command=self.tree_folders.yview)
        self.tree_folders.configure(yscrollcommand=scroll1.set)
        self.tree_folders.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll1.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_folders.bind("<<TreeviewSelect>>", self._on_folder_selected)

        # Panel inferior: Detalle de grupos y archivos a separar
        bot_pane = ttk.LabelFrame(paned, text=" Detalle de Grupos de Archivos y Acciones de Separación ", padding=6)
        paned.add(bot_pane, weight=2)

        self.txt_detail = tk.Text(bot_pane, font=("Consolas", 9), wrap=tk.WORD, height=8)
        scroll2 = ttk.Scrollbar(bot_pane, orient=tk.VERTICAL, command=self.txt_detail.yview)
        self.txt_detail.configure(yscrollcommand=scroll2.set)
        self.txt_detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll2.pack(side=tk.RIGHT, fill=tk.Y)

        bot_bar = ttk.Frame(container)
        bot_bar.pack(fill=tk.X)

        self.btn_apply = ttk.Button(
            bot_bar,
            text="▶ Aplicar Separación Física en Nuevas Carpetas",
            style="Primary.TButton",
            command=self._apply_splits,
            state=tk.DISABLED
        )
        self.btn_apply.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bot_bar, text="Cerrar", command=self.destroy).pack(side=tk.RIGHT)

    def _browse_dir(self):
        sel = filedialog.askdirectory(title="Seleccionar Carpeta", initialdir=self.path_var.get())
        if sel:
            self.path_var.set(sel)
            self._scan_mixed()

    def _scan_mixed(self):
        root_path = Path(self.path_var.get().strip())
        if not root_path.is_dir():
            messagebox.showerror("Error", "La ruta especificada no existe o no es un directorio.")
            return

        for item in self.tree_folders.get_children():
            self.tree_folders.delete(item)
        self.inspected_folders.clear()
        self.txt_detail.delete("1.0", tk.END)

        subfolders = sorted([f for f in root_path.iterdir() if f.is_dir()])
        mixed_count = 0

        for idx, f_path in enumerate(subfolders, start=1):
            insp = self.separator.preview_folder_split(f_path)
            self.inspected_folders.append(insp)

            is_m = insp.get("is_mixed", False)
            if is_m:
                mixed_count += 1
                tag = "mixed"
                st = f"⚠ MEZCLADA ({insp.get('total_groups')} libros)"
            else:
                tag = "clean"
                st = "✔ Homogénea (1 solo libro)"

            self.tree_folders.insert(
                "",
                tk.END,
                iid=f"sep_{idx}",
                values=(
                    idx,
                    insp.get("folder_name"),
                    insp.get("total_pdfs"),
                    insp.get("total_groups"),
                    insp.get("primary_group", "-"),
                    st
                ),
                tags=(tag,)
            )

        self.lbl_summary.config(
            text=f"Total: {len(subfolders)} carpetas  |  {mixed_count} contienen libros/secciones mezclados"
        )
        self.btn_apply.config(state=tk.NORMAL if mixed_count > 0 else tk.DISABLED)

    def _on_folder_selected(self, event=None):
        sel = self.tree_folders.selection()
        if not sel:
            return
        idx = int(sel[0].replace("sep_", "")) - 1
        if 0 <= idx < len(self.inspected_folders):
            insp = self.inspected_folders[idx]
            self.txt_detail.delete("1.0", tk.END)
            self.txt_detail.insert(tk.END, f"CARPETA: {insp.get('folder_path')}\n")
            self.txt_detail.insert(tk.END, f"TOTAL ARCHIVOS PDF: {insp.get('total_pdfs')}  |  GRUPOS IDENTIFICADOS: {insp.get('total_groups')}\n")
            self.txt_detail.insert(tk.END, f"GRUPO PRIMARIO (PERMANECE EN ESTA CARPETA): {insp.get('primary_group')}\n\n")

            if insp.get("is_mixed"):
                self.txt_detail.insert(tk.END, "=== ACCIONES DE SEPARACIÓN (CREAR CARPETAS Y MOVER ARCHIVOS) ===\n")
                for act in insp.get("split_actions", []):
                    self.txt_detail.insert(tk.END, f"\n📁 CREAR CARPETA: {act['target_folder_name']}\n")
                    self.txt_detail.insert(tk.END, f"   Clave de Libro: {act['group_key']}\n")
                    self.txt_detail.insert(tk.END, f"   Archivos a Mover ({act['files_count']}):\n")
                    for fn in act["files"]:
                        self.txt_detail.insert(tk.END, f"     • {fn}\n")
            else:
                self.txt_detail.insert(tk.END, "Todos los archivos de esta carpeta pertenecen al mismo Libro y Sección.\n")

    def _apply_splits(self):
        mixed_items = [item for item in self.inspected_folders if item.get("is_mixed")]
        if not mixed_items:
            messagebox.showinfo("Información", "No hay carpetas mezcladas pendientes de separar.")
            return

        if not messagebox.askyesno(
            "Confirmar Separación Física",
            f"¿Deseas aplicar la separación física en las {len(mixed_items)} carpetas mezcladas?\n\n"
            f"• Se crearán carpetas individuales para cada Libro / Sección diferente.\n"
            f"• Se mantendrán juntos los registros y sus carátulas asociadas.\n"
            f"• Se calculará el hash SHA-256 de cada PDF movido.\n"
            f"• MySQL activo: {'Sí' if self.mysql_active_var.get() else 'No'}"
        ):
            return

        separated_folders_count = 0
        total_files_moved = 0

        prev_enabled = self.parent_app.mysql_manager.enabled
        self.parent_app.mysql_manager.enabled = self.mysql_active_var.get()

        try:
            for idx, item in enumerate(self.inspected_folders, start=1):
                if not item.get("is_mixed"):
                    continue
                f_path = Path(item["folder_path"])
                res = self.separator.execute_folder_split(f_path)
                iid = f"sep_{idx}"
                if res.get("applied"):
                    separated_folders_count += 1
                    acts = res.get("executed_actions", [])
                    files_cnt = sum(a.get("files_moved_count", 0) for a in acts)
                    total_files_moved += files_cnt
                    self.tree_folders.item(
                        iid,
                        values=(
                            idx,
                            item.get("folder_name"),
                            item.get("total_pdfs"),
                            item.get("total_groups"),
                            item.get("primary_group"),
                            f"✔ SEPARADA ({files_cnt} movidos)"
                        ),
                        tags=("done",)
                    )
        finally:
            self.parent_app.mysql_manager.enabled = prev_enabled

        self.btn_apply.config(state=tk.DISABLED)
        messagebox.showinfo(
            "Separación Completada",
            f"Proceso finalizado exitosamente:\n\n"
            f"✔ Carpetas procesadas: {separated_folders_count}\n"
            f"✔ Archivos PDF clasificados y movidos: {total_files_moved}\n"
            f"✔ Todos los movimientos registrados con trazabilidad total."
        )
        self.parent_app.reload_root_preview()


class OCRLibrosApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Auditoría, Diagnóstico, Autocorrección, SQLite y Rollback de Libros Registrales - Claude Sonnet 5")
        self.geometry("1360x850")
        self.minsize(1120, 720)

        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self._configure_styles()
        self.service = FolderOCRService()
        self.mysql_manager = MySQLManager(self.service.settings)
        self.is_processing = False
        self.stop_requested = False

        # Mapa de ruta_carpeta -> ruta_pdf_caratula confirmada o cambiada manualmente por el usuario
        self.custom_caratulas_map: Dict[str, str] = {}
        # Lista completa de resultados de diagnóstico o pre-carga
        self.all_folder_items: List[Dict[str, Any]] = []
        self.results_by_iid: Dict[str, Dict[str, Any]] = {}

        self._create_menu()
        self._create_widgets()
        self._refresh_header_badge()

    def _configure_styles(self):
        self.style.configure(".", font=("Segoe UI", 9))
        self.style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground="#1e293b")
        self.style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#475569")
        self.style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"), padding=5)
        self.style.configure("Action.TButton", font=("Segoe UI", 9, "bold"), padding=5)
        self.style.configure("Config.TButton", font=("Segoe UI", 9, "bold"), padding=5)
        self.style.configure("Danger.TButton", font=("Segoe UI", 9, "bold"), padding=5)
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.style.configure("Treeview", rowheight=25)

    def _create_menu(self):
        menubar = tk.Menu(self)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="📁 Renombrar Carpetas por Primer PDF (con regla 0000)...", command=self.open_folder_renamer_dialog)
        tools_menu.add_command(label="🔀 Separar Archivos Mezclados en Carpetas Nuevas...", command=self.open_folder_separator_dialog)
        tools_menu.add_separator()
        tools_menu.add_command(label="🗄 Conexión MySQL & Crear Tablas de Trazabilidad...", command=self.open_mysql_dialog)
        menubar.add_cascade(label="🛠 Trazabilidad Física y MySQL", menu=tools_menu)

        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label="⚙ Configurar API Key y Caché...", command=self.open_settings_dialog)
        config_menu.add_command(
            label="⏪ Abrir Centro de Rollback y Base de Datos SQLite...",
            command=self.open_rollback_dialog
        )
        config_menu.add_separator()
        config_menu.add_command(label="🗑 Limpiar Caché Local en Disco", command=self._clear_cache_from_menu)
        config_menu.add_separator()
        config_menu.add_command(label="Salir", command=self.quit)
        menubar.add_cascade(label="⚙ Configuración, SQLite y Rollback", menu=config_menu)
        self.config(menu=menubar)

    def _create_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Encabezado + Botones de Configuración y Rollback
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 8))

        title_col = ttk.Frame(header_frame)
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(
            title_col,
            text="Diagnóstico General, Autocorrección de Carátula, Renombrado, SQLite y Rollback (Claude Sonnet 5)",
            style="Header.TLabel"
        ).pack(anchor=tk.W)
        ttk.Label(
            title_col,
            text=(
                "Fase 1: Diagnóstico sin tocar archivos (solo escanea la hoja con info de la Carátula)  |  "
                "Fase 2: Reemplazo de hoja blanca, Renombrado (con _2 solo en carpeta) y Apartados con Rollback."
            ),
            style="SubHeader.TLabel"
        ).pack(anchor=tk.W)

        right_header = ttk.Frame(header_frame)
        right_header.pack(side=tk.RIGHT)

        self.lbl_api_badge = ttk.Label(right_header, text="", font=("Consolas", 9), foreground="#0369a1")
        self.lbl_api_badge.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            right_header,
            text="🗄 Conexión MySQL",
            style="Action.TButton",
            command=self.open_mysql_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            right_header,
            text="⏪ Rollback / Base SQLite",
            style="Action.TButton",
            command=self.open_rollback_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            right_header,
            text="⚙ API Key / Caché",
            style="Config.TButton",
            command=self.open_settings_dialog
        ).pack(side=tk.RIGHT)

        # 2. Panel de Selección de Rutas (Carpeta Raíz + Directorio de Apartados)
        paths_frame = ttk.LabelFrame(
            main_frame,
            text=" 1. Selección de Carpeta Raíz (Solo nivel 1 con '_') y Directorio para Apartar Carátulas en Blanco ",
            padding=8
        )
        paths_frame.pack(fill=tk.X, pady=(0, 8))

        # Fila 1: Carpeta Raíz
        row_root = ttk.Frame(paths_frame)
        row_root.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(row_root, text="Carpeta Raíz:", width=22).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value="")
        ttk.Entry(row_root, textvariable=self.path_var, font=("Consolas", 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6)
        )
        ttk.Button(row_root, text="Examinar Raíz...", command=self.browse_folder).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row_root, text="🔄 Pre-cargar Carátulas", command=self.reload_root_preview).pack(
            side=tk.LEFT, padx=(0, 6)
        )

        self.btn_run = ttk.Button(
            row_root,
            text="🔍 Ejecutar Diagnóstico General (100% Lectura)",
            style="Primary.TButton",
            command=self.start_analysis
        )
        self.btn_run.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = ttk.Button(
            row_root,
            text="⏹ Detener",
            style="Danger.TButton",
            command=self.request_stop,
            state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT)

        # Fila 2: Directorio de Apartados + Directorio de Correctos + Filtro de Discrepancias
        row_apart = ttk.Frame(paths_frame)
        row_apart.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(row_apart, text="Dir. Apartados (Blancos):", width=22).pack(side=tk.LEFT)
        initial_apartados = self.service.settings.get("apartados_dir", "")
        self.apartados_var = tk.StringVar(value=initial_apartados)
        ttk.Entry(row_apart, textvariable=self.apartados_var, font=("Consolas", 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6)
        )
        ttk.Button(row_apart, text="Elegir Dir. Apartados...", command=self.browse_apartados_dir).pack(
            side=tk.LEFT, padx=(0, 14)
        )

        ttk.Label(row_apart, text="Dir. Correctos (Buenos):").pack(side=tk.LEFT, padx=(0, 6))
        initial_validados = self.service.settings.get("validados_dir", "")
        self.validados_var = tk.StringVar(value=initial_validados)
        ttk.Entry(row_apart, textvariable=self.validados_var, font=("Consolas", 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6)
        )
        ttk.Button(row_apart, text="Elegir Dir. Correctos...", command=self.browse_validados_dir).pack(
            side=tk.LEFT, padx=(0, 12)
        )

        self.only_discrepancies_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            row_apart,
            text="🔎 Solo Discrepancias",
            variable=self.only_discrepancies_var,
            command=self._refresh_master_table
        ).pack(side=tk.RIGHT)

        # 3. Barra de Estado y Progreso
        self.banner_frame = tk.Frame(main_frame, bg="#f1f5f9", padx=10, pady=6, relief=tk.GROOVE, bd=1)
        self.banner_frame.pack(fill=tk.X, pady=(0, 8))

        top_status_frame = tk.Frame(self.banner_frame, bg="#f1f5f9")
        top_status_frame.pack(fill=tk.X)

        self.lbl_veredicto = tk.Label(
            top_status_frame,
            text="Selecciona una carpeta raíz para auto-detectar las carátulas e iniciar el Diagnóstico General...",
            font=("Segoe UI", 10, "bold"),
            bg="#f1f5f9",
            fg="#334155"
        )
        self.lbl_veredicto.pack(side=tk.LEFT)

        self.lbl_info = tk.Label(
            top_status_frame,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg="#f1f5f9",
            fg="#475569"
        )
        self.lbl_info.pack(side=tk.RIGHT)

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progressbar = ttk.Progressbar(
            self.banner_frame,
            variable=self.progress_var,
            maximum=100.0,
            mode="determinate"
        )
        self.progressbar.pack(fill=tk.X, pady=(5, 0))

        # 4. Barra de Fase 2: Revisión Manual, Confirmación de Carátula y Acciones Físicas
        action_frame = ttk.LabelFrame(
            main_frame,
            text=" 2. Acciones sobre la Carpeta Seleccionada o en Lote (Registradas en SQLite con Rollback) ",
            padding=8
        )
        action_frame.pack(fill=tk.X, pady=(0, 8))

        row_act1 = ttk.Frame(action_frame)
        row_act1.pack(fill=tk.X, pady=(0, 5))

        ttk.Button(
            row_act1,
            text="📂 Abrir Carpeta en Explorador",
            command=self.open_selected_folder_in_explorer
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act1,
            text="📄 Cambiar PDF Carátula...",
            command=self.change_selected_folder_caratula
        ).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row_act1, text="Código Base (hasta el Año, Pos.1 fija):").pack(side=tk.LEFT, padx=(0, 4))
        self.suggested_code_var = tk.StringVar(value="")
        ttk.Entry(row_act1, textvariable=self.suggested_code_var, font=("Consolas", 10, "bold"), width=28).pack(
            side=tk.LEFT, padx=(0, 6)
        )

        ttk.Button(
            row_act1,
            text="📝 Editar Campos...",
            command=self.open_manual_edit_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act1,
            text="✏️ Renombrar Carpeta Seleccionada y sus PDFs",
            style="Primary.TButton",
            command=self.rename_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act1,
            text="🛠 Reemplazar Hojas Blancas (Carpeta Seleccionada)",
            command=self.replace_blank_pages_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act1,
            text="📦 Apartar Seleccionada",
            command=self.apartar_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act1,
            text="✅ Mover Seleccionada a Correctos",
            command=self.move_selected_folder_to_validados
        ).pack(side=tk.LEFT)

        row_act2 = ttk.Frame(action_frame)
        row_act2.pack(fill=tk.X)

        ttk.Label(
            row_act2,
            text="Acciones Masivas del Diagnóstico:",
            font=("Segoe UI", 9, "bold"),
            foreground="#334155"
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            row_act2,
            text="🛠 Reemplazar Hojas en Blanco (TODAS)",
            command=self.batch_replace_all_blank_pages
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act2,
            text="✏️ Aplicar Renombre (TODAS las Discrepancias)",
            command=self.batch_rename_all_discrepancies
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act2,
            text="📦 Enviar a Revisión / Apartados (por Subcarpeta de Error)",
            command=self.batch_move_all_blank_caratulas
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act2,
            text="✅ Mover TODAS las Carpetas Correctas (COINCIDE TOTAL) a Dir. Correctos",
            style="Primary.TButton",
            command=self.batch_move_all_validados
        ).pack(side=tk.LEFT)

        row_act3 = ttk.Frame(action_frame)
        row_act3.pack(fill=tk.X, pady=(6, 0))

        ttk.Label(
            row_act3,
            text="Herramientas Físicas y Trazabilidad:",
            font=("Segoe UI", 9, "bold"),
            foreground="#0369a1"
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            row_act3,
            text="📁 Renombrar Carpetas por 1er PDF (hasta Año + Regla 0000)",
            style="Primary.TButton",
            command=self.open_folder_renamer_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act3,
            text="🔀 Separar Archivos Mezclados (Libros/Secciones Distintos)",
            style="Action.TButton",
            command=self.open_folder_separator_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            row_act3,
            text="🗄 Conexión MySQL & Trazabilidad",
            command=self.open_mysql_dialog
        ).pack(side=tk.LEFT)

        # 5. Panel Dividido (Pestañas en Tiempo Real + Detalle)
        paned = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        master_frame = ttk.LabelFrame(
            paned,
            text=" Listas en Tiempo Real (Al aplicar cambios, cada carpeta pasa automáticamente a su pestaña correspondiente) ",
            padding=4
        )
        paned.add(master_frame, weight=3)

        self.notebook_tabs = ttk.Notebook(master_frame)
        self.notebook_tabs.pack(fill=tk.BOTH, expand=True)

        self.tab_all = ttk.Frame(self.notebook_tabs, padding=2)
        self.tab_pending = ttk.Frame(self.notebook_tabs, padding=2)
        self.tab_ready = ttk.Frame(self.notebook_tabs, padding=2)
        self.tab_done = ttk.Frame(self.notebook_tabs, padding=2)

        self.notebook_tabs.add(self.tab_all, text="📋 Todas en Raíz (0)")
        self.notebook_tabs.add(self.tab_pending, text="⚠ Pendientes por Corregir / Apartar (0)")
        self.notebook_tabs.add(self.tab_ready, text="✔ Listas / Correctas (0)")
        self.notebook_tabs.add(self.tab_done, text="✅ Ya Hecho / Cambios Aplicados (0)")

        self.tree_folders = self._build_folder_treeview(self.tab_all)
        self.tree_pending = self._build_folder_treeview(self.tab_pending)
        self.tree_ready = self._build_folder_treeview(self.tab_ready)

        # Pestaña 4: Ya Hecho / Cambios Aplicados (con barra de Rollback rápido)
        done_toolbar = ttk.Frame(self.tab_done)
        done_toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(
            done_toolbar,
            text="Carpetas donde ya se aplicó Renombrado, Reemplazo de Hoja, Envío a Correctos o Apartados:",
            font=("Segoe UI", 9, "bold"),
            foreground="#15803d"
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Button(
            done_toolbar,
            text="📂 Abrir Ubicación Actual",
            command=self.open_selected_folder_in_explorer
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            done_toolbar,
            text="⏪ Revertir (Rollback) Seleccionada a su Estado Inicial",
            style="Danger.TButton",
            command=self.rollback_selected_from_done_tab
        ).pack(side=tk.LEFT, padx=(0, 6))

        done_table_frame = ttk.Frame(self.tab_done)
        done_table_frame.pack(fill=tk.BOTH, expand=True)
        done_cols = (
            "num",
            "accion",
            "carpeta_inicial",
            "carpeta_actual",
            "ubicacion",
            "archivos",
            "estado_final",
            "sugerido",
        )
        self.tree_done = ttk.Treeview(done_table_frame, columns=done_cols, show="headings", height=9)
        self.tree_done.heading("num", text="#")
        self.tree_done.heading("accion", text="Cambio / Acción Aplicada")
        self.tree_done.heading("carpeta_inicial", text="Carpeta Inicial (Antes)")
        self.tree_done.heading("carpeta_actual", text="Carpeta Actual / Nueva")
        self.tree_done.heading("ubicacion", text="Ubicación / Subcarpeta Destino")
        self.tree_done.heading("archivos", text="Archivos Tocados")
        self.tree_done.heading("estado_final", text="Estado Actual")
        self.tree_done.heading("sugerido", text="Código Base")

        self.tree_done.column("num", width=42, anchor=tk.CENTER, stretch=False)
        self.tree_done.column("accion", width=215, anchor=tk.W)
        self.tree_done.column("carpeta_inicial", width=220, anchor=tk.W)
        self.tree_done.column("carpeta_actual", width=220, anchor=tk.W)
        self.tree_done.column("ubicacion", width=230, anchor=tk.W)
        self.tree_done.column("archivos", width=110, anchor=tk.CENTER)
        self.tree_done.column("estado_final", width=165, anchor=tk.CENTER)
        self.tree_done.column("sugerido", width=175, anchor=tk.W)

        self._configure_tree_tags(self.tree_done)
        scroll_done = ttk.Scrollbar(done_table_frame, orient=tk.VERTICAL, command=self.tree_done.yview)
        self.tree_done.configure(yscrollcommand=scroll_done.set)
        self.tree_done.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_done.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_done.bind("<<TreeviewSelect>>", lambda e: self._on_folder_selected(e, self.tree_done))

        self.active_tree = self.tree_folders
        self.notebook_tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Panel Inferior: Comparativa Campo por Campo + Ruta Completa y Evidencias
        detail_container = ttk.Frame(paned)
        paned.add(detail_container, weight=2)

        field_frame = ttk.LabelFrame(detail_container, text=" Comparativa Campo por Campo ", padding=6)
        field_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        columns = ("campo", "carpeta", "ocr", "estado")
        self.tree_fields = ttk.Treeview(field_frame, columns=columns, show="headings", height=6)
        self.tree_fields.heading("campo", text="Campo")
        self.tree_fields.heading("carpeta", text="En Carpeta")
        self.tree_fields.heading("ocr", text="En Carátula (Claude)")
        self.tree_fields.heading("estado", text="Estado")

        self.tree_fields.column("campo", width=130, anchor=tk.W)
        self.tree_fields.column("carpeta", width=95, anchor=tk.CENTER)
        self.tree_fields.column("ocr", width=120, anchor=tk.CENTER)
        self.tree_fields.column("estado", width=135, anchor=tk.CENTER)

        self.tree_fields.tag_configure("match", background="#dcfce7", foreground="#166534")
        self.tree_fields.tag_configure("diff", background="#fee2e2", foreground="#991b1b")
        self.tree_fields.tag_configure("zeros", background="#fef9c3", foreground="#854d0e")
        self.tree_fields.tag_configure("normal", background="#ffffff", foreground="#1e293b")
        self.tree_fields.pack(fill=tk.BOTH, expand=True)

        evidence_frame = ttk.LabelFrame(
            detail_container,
            text=" Ruta de la Carpeta, Inspección de Hojas de Carátula y Evidencias ",
            padding=6
        )
        evidence_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.txt_evidence = tk.Text(evidence_frame, height=7, font=("Consolas", 9), wrap=tk.WORD)
        scrollbar_ev = ttk.Scrollbar(evidence_frame, orient=tk.VERTICAL, command=self.txt_evidence.yview)
        self.txt_evidence.configure(yscrollcommand=scrollbar_ev.set)
        self.txt_evidence.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_ev.pack(side=tk.RIGHT, fill=tk.Y)

    # =========================================================================
    # VENTANAS AUXILIARES Y CONFIGURACIÓN
    # =========================================================================

    def open_settings_dialog(self):
        SettingsDialog(self)

    def open_mysql_dialog(self):
        MySQLSettingsDialog(self)

    def open_folder_renamer_dialog(self):
        FolderRenamerDialog(self, initial_path=self.path_var.get())

    def open_folder_separator_dialog(self):
        FolderSeparatorDialog(self, initial_path=self.path_var.get())

    def open_rollback_dialog(self):
        RollbackAndDatabaseDialog(self)

    def open_manual_edit_dialog(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return
        ManualEditDialog(self, res)

    def _clear_cache_from_menu(self):
        removed = self.service.cache.clear()
        self._refresh_header_badge()
        messagebox.showinfo("Caché Limpiada", f"Se eliminaron {removed} documentos de la caché local.")

    def _refresh_header_badge(self):
        cfg = self.service.reload_settings()
        key = cfg.get("api_key", "")
        masked = f"{key[:12]}...{key[-5:]}" if len(key) > 20 else ("Configurada" if key else "SIN KEY")
        model = cfg.get("model", "claude-sonnet-5")
        stats = self.service.cache.get_summary()
        rb_count = len(self.service.db.get_rollback_candidates())
        my_status = "🐬 MySQL: Activo" if (hasattr(self, "mysql_manager") and self.mysql_manager.enabled) else "MySQL: Off"
        self.lbl_api_badge.config(
            text=f"Modelo: {model} | Key: {masked} | Caché: {stats['cached_documents_on_disk']} | {my_status} | Rollback activos: {rb_count}"
        )

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Selecciona la carpeta raíz que contiene las carpetas con '_'")
        if folder:
            self.path_var.set(folder)
            self.reload_root_preview()

    def browse_apartados_dir(self):
        folder = filedialog.askdirectory(
            title="Selecciona el directorio donde se apartarán las carpetas con carátula en blanco"
        )
        if folder:
            self.apartados_var.set(folder)
            cfg = self.service.reload_settings()
            cfg["apartados_dir"] = folder
            SettingsManager.save(cfg)

    def browse_validados_dir(self):
        folder = filedialog.askdirectory(
            title="Selecciona el directorio donde se moverán las carpetas que están totalmente bien (Correctos)"
        )
        if folder:
            self.validados_var.set(folder)
            cfg = self.service.reload_settings()
            cfg["validados_dir"] = folder
            SettingsManager.save(cfg)

    def _resolve_valid_subfolders(self, folder_path: str) -> List[Path]:
        subfolders = self.service.get_valid_subfolders(folder_path)
        if not subfolders:
            p = Path(folder_path)
            if p.exists() and p.is_dir() and "_" in p.name:
                if any(f.suffix.lower() == ".pdf" for f in p.iterdir() if f.is_file()):
                    subfolders = [p]
        return subfolders

    def reload_root_preview(self):
        folder_path = self.path_var.get().strip()
        if not folder_path or not os.path.exists(folder_path):
            return

        try:
            subfolders = self._resolve_valid_subfolders(folder_path)
            previews = self.service.preview_folders_caratulas(subfolders)
            self.all_folder_items.clear()

            for p_item in previews:
                f_path = p_item["folder_path"]
                f_data = p_item["folder_data"]
                custom_c = self.custom_caratulas_map.get(f_path)
                c_path = custom_c if (custom_c and os.path.exists(custom_c)) else p_item["caratula_path"]
                c_name = Path(c_path).name if c_path else "NO DETECTADA"

                self.all_folder_items.append({
                    "folder_path": f_path,
                    "folder_data": f_data,
                    "caratula_inspection": {
                        "status": "PENDIENTE_DIAGNOSTICO",
                        "caratula_path": c_path,
                        "caratula_filename": c_name,
                        "total_regular_pdfs": max(0, p_item["total_pdfs"] - (1 if c_path else 0)),
                        "summary_message": (
                            f"Carátula auto-detectada: {c_name} ({p_item['total_pdfs']} PDFs en carpeta). "
                            "Puedes confirmarla o cambiarla con '📄 Cambiar PDF Carátula...' antes del diagnóstico."
                        ),
                    },
                    "ocr_consolidado": {
                        "seccion": "-", "libro": "-", "tomo": "-",
                        "volumen": "-", "fraccionamiento": "-", "anio": "-",
                        "evidencias_globales": [
                            f"Ruta: {f_path}",
                            f"PDF Carátula seleccionado: {c_name}",
                            "Listo para ejecutar Fase 1: Diagnóstico General (sin modificar archivos)."
                        ],
                    },
                    "comparacion": {"veredicto": "PENDIENTE", "detalles_campos": {}},
                    "veredicto_global": "PENDIENTE_DIAGNOSTICO",
                    "codigo_base_sugerido": f_data.get("folder_name", ""),
                    "needs_page_replacement": False,
                    "needs_rename": False,
                    "needs_apartar": False,
                    "has_discrepancy": False,
                })

            self._refresh_master_table()
            self.lbl_veredicto.config(
                text=(
                    f"Pre-carga lista: {len(subfolders)} carpetas con '_' detectadas. "
                    "Verifica las carátulas auto-detectadas y pulsa '🔍 Ejecutar Diagnóstico General'."
                ),
                fg="#0284c7"
            )
            self.lbl_info.config(text="(Ignorando subcarpetas y carpetas sin '_' como BUENOS/MALOS)")
            self._refresh_header_badge()
        except Exception as exc:
            messagebox.showerror("Error al leer raíz", str(exc))

    def _configure_tree_tags(self, tree: ttk.Treeview):
        tree.tag_configure("match", background="#dcfce7", foreground="#166534")
        tree.tag_configure("diff", background="#fee2e2", foreground="#991b1b")
        tree.tag_configure("zeros", background="#fef9c3", foreground="#854d0e")
        tree.tag_configure("replace", background="#dbeafe", foreground="#1e40af")
        tree.tag_configure("blank", background="#f3e8ff", foreground="#6b21a8")
        tree.tag_configure("pending", background="#ffffff", foreground="#334155")

    def _build_folder_treeview(self, parent_frame: ttk.Frame) -> ttk.Treeview:
        master_cols = (
            "num",
            "carpeta",
            "caratula",
            "estado_hojas",
            "esperado",
            "detectado",
            "veredicto",
            "sugerido",
        )
        tree = ttk.Treeview(parent_frame, columns=master_cols, show="headings", height=9)
        tree.heading("num", text="#")
        tree.heading("carpeta", text="Carpeta Actual")
        tree.heading("caratula", text="PDF Carátula (Confirmable)")
        tree.heading("estado_hojas", text="Estado Carátula y PDFs")
        tree.heading("esperado", text="Carpeta [Sec|Lib|Tom|Vol|Año]")
        tree.heading("detectado", text="Claude [Sec|Lib|Tom|Vol|Año]")
        tree.heading("veredicto", text="Estado / Diagnóstico en Tiempo Real")
        tree.heading("sugerido", text="Código Base (hasta Año)")

        tree.column("num", width=42, anchor=tk.CENTER, stretch=False)
        tree.column("carpeta", width=240, anchor=tk.W)
        tree.column("caratula", width=230, anchor=tk.W)
        tree.column("estado_hojas", width=200, anchor=tk.CENTER)
        tree.column("esperado", width=165, anchor=tk.CENTER)
        tree.column("detectado", width=165, anchor=tk.CENTER)
        tree.column("veredicto", width=205, anchor=tk.CENTER)
        tree.column("sugerido", width=185, anchor=tk.W)

        self._configure_tree_tags(tree)
        scroll = ttk.Scrollbar(parent_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.bind("<<TreeviewSelect>>", lambda e, t=tree: self._on_folder_selected(e, t))
        tree.bind("<Double-1>", lambda e, t=tree: self.open_manual_edit_dialog())
        return tree

    def _on_tab_changed(self, event=None):
        tab_idx = self.notebook_tabs.index(self.notebook_tabs.select())
        trees = [self.tree_folders, self.tree_pending, self.tree_ready, self.tree_done]
        if 0 <= tab_idx < len(trees):
            self.active_tree = trees[tab_idx]
            sel = self.active_tree.selection()
            if not sel:
                children = self.active_tree.get_children()
                if children:
                    self.active_tree.selection_set(children[0])
            self._on_folder_selected(None, self.active_tree)

    def _update_realtime_banner_counts(self):
        in_root = [r for r in self.all_folder_items if not r.get("moved_destination_type")]
        ready_cnt = sum(
            1 for r in in_root
            if r.get("veredicto_global") == "COINCIDE_TOTAL"
            and not r.get("needs_page_replacement")
            and not r.get("needs_rename")
            and not r.get("needs_apartar")
        )
        pending_cnt = sum(
            1 for r in in_root
            if r.get("veredicto_global") != "PENDIENTE_DIAGNOSTICO"
            and (
                r.get("has_discrepancy")
                or r.get("needs_page_replacement")
                or r.get("needs_rename")
                or r.get("needs_apartar")
            )
        )
        replace_cnt = sum(1 for r in in_root if r.get("needs_page_replacement"))
        apartar_cnt = sum(1 for r in in_root if r.get("needs_apartar"))
        done_cnt = sum(1 for r in self.all_folder_items if r.get("applied_actions"))
        stats = self.service.cache.get_summary()
        self.lbl_info.config(
            text=(
                f"✔ Listas: {ready_cnt} | ⚠ Pendientes: {pending_cnt} | 🛠 Hoja Blanca: {replace_cnt} | "
                f"📦 Por Apartar: {apartar_cnt} | ✅ Ya Hecho: {done_cnt} | ⚡ Hits Caché: {stats['local_cache_hits']}"
            )
        )

    def _refresh_master_table(self):
        selected_res = self._get_selected_result()

        for t in (self.tree_folders, self.tree_pending, self.tree_ready, self.tree_done):
            for item in t.get_children():
                t.delete(item)
        self.results_by_iid.clear()

        only_disc = self.only_discrepancies_var.get()
        cnt_all = 0
        cnt_pending = 0
        cnt_ready = 0
        cnt_done = 0

        for idx, res in enumerate(self.all_folder_items, start=1):
            f_data = res.get("folder_data", {})
            ocr = res.get("ocr_consolidado", {})
            insp = res.get("caratula_inspection", {})
            v_global = res.get("veredicto_global", "PENDIENTE")
            c_status = insp.get("status", "PENDIENTE")
            moved_type = res.get("moved_destination_type")
            applied_actions = res.get("applied_actions", [])

            folder_name = f_data.get("folder_name") or Path(res.get("folder_path", "")).name
            caratula_name = insp.get("caratula_filename") or "SIN CARÁTULA"

            if c_status == "OK_CON_INFO":
                estado_hojas = f"✔ OK (Pág.{insp.get('info_page_num', 1)} c/info)"
            elif c_status == "REEMPLAZABLE_HOJA_BLANCA":
                estado_hojas = f"🛠 {insp.get('pdfs_with_blank_page', 0)} PDFs c/Hoja Blanca (Info Pág.{insp.get('info_page_num')})"
            elif c_status == "TOTALMENTE_BLANCA":
                estado_hojas = "⚪ Carátula 100% en Blanco"
            elif c_status == "PDFS_SIN_CARATULA":
                estado_hojas = f"⚠ {len(insp.get('pdfs_missing_caratula', []))} PDFs sin Carátula"
            elif c_status == "SIN_ARCHIVO_CARATULA":
                estado_hojas = "✖ Sin PDF Carátula"
            else:
                estado_hojas = "⏳ Pre-cargada"

            esperado_str = (
                f"{f_data.get('seccion_raw', '-')} | "
                f"{f_data.get('libro_raw', '-')} | "
                f"{f_data.get('tomo_raw', '-')} | "
                f"{f_data.get('volumen_raw', '-')} | "
                f"{f_data.get('anio_raw', '-')}"
            )
            detectado_str = (
                f"{ocr.get('seccion', '-')} | "
                f"{ocr.get('libro', '-')} | "
                f"{ocr.get('tomo', '-')} | "
                f"{ocr.get('volumen', '-')} | "
                f"{ocr.get('anio', '-')}"
            )

            if v_global == "COINCIDE_TOTAL":
                tag = "match"
                v_label = "✔ COINCIDE TOTAL ( CORREGIDA)" if applied_actions else "✔ COINCIDE TOTAL"
            elif v_global == "MOVIDO_A_CORRECTOS":
                tag = "match"
                v_label = "✅ MOVIDA A CORRECTOS"
            elif v_global == "APARTADO_COMPLETADO":
                tag = "blank"
                v_label = f"📦 APARTADA ({res.get('subcarpeta_revision', 'REVISIÓN')})"
            elif v_global == "APARTAR_CARATULA_BLANCA":
                tag = "blank"
                v_label = "📦 APARTAR (CARÁTULA EN BLANCO)"
            elif v_global == "SIN_ARCHIVO_CARATULA":
                tag = "diff"
                v_label = "📦 APARTAR (SIN CARÁTULA)"
            elif v_global == "DISCREPANCIA_SIN_CARATULA":
                tag = "diff"
                v_label = "📦 APARTAR (PDFS SIN CARÁTULA)"
            elif v_global == "REEMPLAZAR_HOJA_BLANCA":
                tag = "replace"
                v_label = "🛠 REEMPLAZAR HOJA BLANCA"
            elif v_global == "REEMPLAZAR_HOJA_Y_RENOMBRAR":
                tag = "diff"
                v_label = "🛠+✏️ REEMPLAZAR Y RENOMBRAR"
            elif "CEROS" in v_global:
                tag = "zeros"
                v_label = "★ RELLENAR CEROS / RENOMBRAR"
            elif v_global == "LIMPIAR_SUFIJO_CARPETA":
                tag = "zeros"
                v_label = "✏️ LIMPIAR SUFIJO HASTA AÑO"
            elif v_global == "SIN_INFO_LIMPIAR_NOMBRE":
                tag = "blank"
                v_label = "📦 SIN DATOS (LIMPIAR Y APARTAR)"
            elif v_global == "SIN_INFO_MANTENER_NOMBRE":
                tag = "blank"
                v_label = "📦 SIN DATOS (APARTAR)"
            elif v_global == "PENDIENTE_DIAGNOSTICO":
                tag = "pending"
                v_label = "⏳ Pendiente Diagnóstico"
            else:
                tag = "diff"
                v_label = f"✖ {v_global}"

            row_vals = (
                idx,
                folder_name,
                caratula_name,
                estado_hojas,
                esperado_str,
                detectado_str,
                v_label,
                res.get("codigo_base_sugerido", ""),
            )

            # 1. Pestaña "Todas en Raíz" (carpetas que siguen en la carpeta raíz)
            if not moved_type:
                if not (only_disc and not res.get("has_discrepancy", False)):
                    cnt_all += 1
                    iid_all = f"all_{idx}"
                    self.results_by_iid[iid_all] = res
                    self.tree_folders.insert("", tk.END, iid=iid_all, values=row_vals, tags=(tag,))

                # 2. Pestaña "Pendientes por Corregir / Apartar"
                is_pending_action = (
                    v_global != "PENDIENTE_DIAGNOSTICO"
                    and (
                        res.get("has_discrepancy", False)
                        or res.get("needs_page_replacement", False)
                        or res.get("needs_rename", False)
                        or res.get("needs_apartar", False)
                    )
                )
                if is_pending_action:
                    cnt_pending += 1
                    iid_pend = f"pend_{idx}"
                    self.results_by_iid[iid_pend] = res
                    self.tree_pending.insert("", tk.END, iid=iid_pend, values=row_vals, tags=(tag,))

                # 3. Pestaña "Listas / Correctas (COINCIDE TOTAL)"
                is_ready_ok = (
                    v_global == "COINCIDE_TOTAL"
                    and not res.get("needs_page_replacement", False)
                    and not res.get("needs_rename", False)
                    and not res.get("needs_apartar", False)
                )
                if is_ready_ok:
                    cnt_ready += 1
                    iid_rdy = f"rdy_{idx}"
                    self.results_by_iid[iid_rdy] = res
                    self.tree_ready.insert("", tk.END, iid=iid_rdy, values=row_vals, tags=("match",))

            # 4. Pestaña "Ya Hecho / Cambios Aplicados"
            if applied_actions:
                cnt_done += 1
                iid_done = f"done_{idx}"
                self.results_by_iid[iid_done] = res
                init_name = res.get("initial_folder_name") or folder_name
                curr_p = Path(res.get("folder_path", ""))
                if moved_type == "APARTADOS":
                    ubic_str = f"📂 Apartados / {res.get('subcarpeta_revision', '')}"
                elif moved_type == "CORRECTOS":
                    ubic_str = "✅ Dir. Correctos (Buenos)"
                else:
                    ubic_str = f"📍 En Raíz ({curr_p.parent.name})"

                done_tag = "match" if v_global in ("COINCIDE_TOTAL", "MOVIDO_A_CORRECTOS") else "blank"
                self.tree_done.insert(
                    "",
                    tk.END,
                    iid=iid_done,
                    values=(
                        idx,
                        res.get("last_action", applied_actions[-1]),
                        init_name,
                        folder_name,
                        ubic_str,
                        f"{res.get('files_modified_count', 0)} PDF(s)",
                        v_label,
                        res.get("codigo_base_sugerido", ""),
                    ),
                    tags=(done_tag,)
                )

        # Actualizar títulos de pestañas con sus contadores en tiempo real
        self.notebook_tabs.tab(self.tab_all, text=f"📋 Todas en Raíz ({cnt_all})")
        self.notebook_tabs.tab(self.tab_pending, text=f"⚠ Pendientes por Corregir / Apartar ({cnt_pending})")
        self.notebook_tabs.tab(self.tab_ready, text=f"✔ Listas / Correctas ({cnt_ready})")
        self.notebook_tabs.tab(self.tab_done, text=f"✅ Ya Hecho / Cambios Aplicados ({cnt_done})")

        self._update_realtime_banner_counts()

        # Mantener selección activa en la pestaña visible
        cur_tree = getattr(self, "active_tree", self.tree_folders)
        restored_sel = False
        if selected_res is not None:
            for iid in cur_tree.get_children():
                if self.results_by_iid.get(iid) is selected_res:
                    cur_tree.selection_set(iid)
                    cur_tree.see(iid)
                    restored_sel = True
                    break

        if not restored_sel:
            children = cur_tree.get_children()
            if children:
                cur_tree.selection_set(children[0])
                self._on_folder_selected(None, cur_tree)

    # =========================================================================
    # FASE 1: EJECUCIÓN DEL DIAGNÓSTICO GENERAL (100% LECTURA)
    # =========================================================================

    def request_stop(self):
        if self.is_processing:
            self.stop_requested = True
            self.btn_stop.config(state=tk.DISABLED, text="Deteniendo...")
            self.lbl_veredicto.config(text="Deteniendo al finalizar la carpeta actual...", fg="#b45309")

    def start_analysis(self):
        folder_path = self.path_var.get().strip()
        if not folder_path or not os.path.exists(folder_path):
            messagebox.showwarning("Atención", "Por favor selecciona una carpeta raíz válida.")
            return

        cfg = self.service.reload_settings()
        if not cfg.get("api_key", "").strip():
            messagebox.showwarning("Falta API Key", "Configura primero tu API Key en '⚙ API Key / Caché'.")
            self.open_settings_dialog()
            return

        subfolders = self._resolve_valid_subfolders(folder_path)
        if not subfolders:
            messagebox.showwarning(
                "Sin carpetas válidas",
                "No se encontraron carpetas con '_' en el primer nivel de la raíz seleccionada."
            )
            return

        self.is_processing = True
        self.stop_requested = False
        self.all_folder_items.clear()
        self.results_by_iid.clear()

        self._refresh_master_table()
        for item in self.tree_fields.get_children():
            self.tree_fields.delete(item)
        self.txt_evidence.delete("1.0", tk.END)

        self.btn_run.config(state=tk.DISABLED, text="⏳ Diagnosticando...")
        self.btn_stop.config(state=tk.NORMAL, text="⏹ Detener")
        self.progress_var.set(0.0)

        thread = threading.Thread(
            target=self._run_diagnosis_worker,
            args=(subfolders,),
            daemon=True
        )
        thread.start()

    def _run_diagnosis_worker(self, subfolders: List[Path]):
        total = len(subfolders)
        coinciden = 0
        discrepancias = 0
        apartar = 0
        reemplazar = 0

        for idx, folder_path in enumerate(subfolders, start=1):
            if self.stop_requested:
                break

            self.after(
                0,
                self._update_progress_start,
                idx,
                total,
                folder_path.name,
                coinciden,
                discrepancias,
                apartar,
                reemplazar
            )

            custom_c = self.custom_caratulas_map.get(str(folder_path))
            try:
                res = self.service.process_folder(
                    str(folder_path),
                    custom_caratula_path=custom_c,
                    save_to_db=True
                )
            except Exception as exc:
                res = self.service._build_error_result(folder_path, str(exc))

            v_global = res.get("veredicto_global", "ERROR")
            if v_global == "COINCIDE_TOTAL":
                coinciden += 1
            elif v_global == "APARTAR_CARATULA_BLANCA":
                apartar += 1
            else:
                discrepancias += 1

            if res.get("needs_page_replacement", False):
                reemplazar += 1

            self.after(
                0,
                self._append_diagnosis_result,
                idx,
                total,
                res,
                coinciden,
                discrepancias,
                apartar,
                reemplazar
            )

            err_msg = str(res.get("error", ""))
            if "401" in err_msg:
                self.stop_requested = True
                self.after(0, lambda msg=err_msg: messagebox.showerror("Error de API Key", msg))
                break

            err_lower = err_msg.lower()
            if any(term in err_lower for term in ["credit balance", "balance is too low", "insufficient_quota", "http 402", "plans & billing", "purchase credits"]):
                self.stop_requested = True
                self.after(
                    0,
                    lambda: messagebox.showwarning(
                        "Créditos de Claude Agotados",
                        "Tu saldo de créditos en Anthropic API se ha agotado.\n\n"
                        "• El procesamiento se detuvo de forma segura.\n"
                        "• Todas las carpetas procesadas hasta este punto han quedado guardadas en SQLite y en la caché local.\n"
                        "• Para continuar: Recarga créditos en console.anthropic.com o ingresa otra API Key en '⚙ Configurar API Key / Caché'.\n"
                        "• Al volver a pulsar '▶ Analizar Directorio Raíz', las carpetas ya analizadas cargarán al instante con 0 tokens sin gastar nada."
                    )
                )
                break

        self.after(0, self._finish_diagnosis, total, coinciden, discrepancias, apartar, reemplazar)

    def _update_progress_start(
        self, idx: int, total: int, folder_name: str,
        coinciden: int, discrepancias: int, apartar: int, reemplazar: int
    ):
        pct = ((idx - 1) / total) * 100.0 if total else 0.0
        self.progress_var.set(pct)
        stats = self.service.cache.get_summary()
        self.lbl_veredicto.config(
            text=f"Diagnóstico General ({idx}/{total}): {folder_name}",
            fg="#0284c7"
        )
        self.lbl_info.config(
            text=(
                f"✔ OK: {coinciden} | ✖ Discrep: {discrepancias} | 🛠 Hoja Blanca: {reemplazar} | "
                f"📦 En Blanco: {apartar} | ⚡ Hits Caché: {stats['local_cache_hits']}"
            )
        )

    def _append_diagnosis_result(
        self, idx: int, total: int, res: Dict[str, Any],
        coinciden: int, discrepancias: int, apartar: int, reemplazar: int
    ):
        pct = (idx / total) * 100.0 if total else 100.0
        self.progress_var.set(pct)

        self.all_folder_items.append(res)
        self._refresh_master_table()

        children = self.tree_folders.get_children()
        if children:
            last_iid = children[-1]
            self.tree_folders.see(last_iid)
            if len(self.tree_folders.selection()) == 0:
                self.tree_folders.selection_set(last_iid)
                self._render_single_folder_detail(res)

        stats = self.service.cache.get_summary()
        self.lbl_info.config(
            text=(
                f"✔ OK: {coinciden} | ✖ Discrep: {discrepancias} | 🛠 Hoja Blanca: {reemplazar} | "
                f"📦 En Blanco: {apartar} | ⚡ Hits Caché: {stats['local_cache_hits']}"
            )
        )
        self._refresh_header_badge()

    def _finish_diagnosis(self, total: int, coinciden: int, discrepancias: int, apartar: int, reemplazar: int):
        self.is_processing = False
        self.btn_run.config(state=tk.NORMAL, text="🔍 Ejecutar Diagnóstico General (100% Lectura)")
        self.btn_stop.config(state=tk.DISABLED, text="⏹ Detener")

        processed = len(self.all_folder_items)
        if self.stop_requested:
            self.lbl_veredicto.config(
                text=f"⏹ Diagnóstico detenido ({processed}/{total} carpetas analizadas).",
                fg="#b45309"
            )
        else:
            self.progress_var.set(100.0)
            self.lbl_veredicto.config(
                text=(
                    f"✔ Diagnóstico General finalizado ({processed} carpetas registradas en SQLite). "
                    "Revisa las discrepancias antes de aplicar cambios físicos."
                ),
                fg="#15803d"
            )
        self._refresh_header_badge()

    # =========================================================================
    # SELECCIÓN Y DETALLE DE CARPETA
    # =========================================================================

    def _get_selected_result(self) -> Optional[Dict[str, Any]]:
        tree = getattr(self, "active_tree", self.tree_folders)
        sel = tree.selection()
        if sel and sel[0] in self.results_by_iid:
            return self.results_by_iid.get(sel[0])
        for fallback_t in (self.tree_folders, self.tree_pending, self.tree_ready, self.tree_done):
            s = fallback_t.selection()
            if s and s[0] in self.results_by_iid:
                return self.results_by_iid.get(s[0])
        return None

    def _on_folder_selected(self, event=None, source_tree: Optional[ttk.Treeview] = None):
        if source_tree is not None:
            self.active_tree = source_tree
        res = self._get_selected_result()
        if res:
            self.suggested_code_var.set(res.get("codigo_base_sugerido", ""))
            self._render_single_folder_detail(res)

    def rollback_selected_from_done_tab(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta en la pestaña '✅ Ya Hecho / Cambios Aplicados'.")
            return

        init_folder = res.get("initial_folder_path") or res.get("folder_path", "")
        if not init_folder or not res.get("applied_actions"):
            messagebox.showinfo("Sin Cambios Aplicados", "Esta carpeta no tiene cambios aplicados pendientes de revertir.")
            return

        init_name = Path(init_folder).name
        curr_name = Path(res.get("folder_path", "")).name
        if not messagebox.askyesno(
            "Confirmar Rollback al Estado Inicial",
            f"¿Deseas revertir la carpeta '{curr_name}' a como estaba exactamente antes del primer cambio?\n\n"
            f"• Carpeta original: {init_name}\n"
            f"• Ruta inicial: {init_folder}\n"
            "• Si se habían reemplazado hojas en blanco o renombrado/movido archivos, volverán intactos a su estado original."
        ):
            return

        try:
            rb_res = self.service.rollback_folder(init_folder)
            # Re-evaluar la carpeta restaurada en tiempo real para regresarla a su pestaña original
            if Path(init_folder).exists():
                restored_diag = self.service.process_folder(init_folder, save_to_db=True)
                idx = self.all_folder_items.index(res)
                self.all_folder_items[idx] = restored_diag
                self._refresh_master_table()
                self._render_single_folder_detail(restored_diag)
            else:
                self._refresh_master_table()
            self._refresh_header_badge()

            if rb_res["errors"]:
                messagebox.showwarning(
                    "Rollback con Avisos",
                    f"Restaurados: {rb_res['restored_count']}/{rb_res['total_candidates']}\n"
                    + "\n".join(rb_res["errors"][:10])
                )
            else:
                messagebox.showinfo(
                    "Rollback Completado en Tiempo Real",
                    f"La carpeta '{init_name}' ({rb_res['restored_count']} archivos) fue restaurada intacta a su origen inicial "
                    "y regresó a las pestañas activas."
                )
        except Exception as exc:
            messagebox.showerror("Error en Rollback", str(exc))

    def _render_single_folder_detail(self, res: Dict[str, Any]):
        for item in self.tree_fields.get_children():
            self.tree_fields.delete(item)
        self.txt_evidence.delete("1.0", tk.END)

        f_data = res.get("folder_data", {})
        ocr = res.get("ocr_consolidado", {})
        comp = res.get("comparacion", {})
        insp = res.get("caratula_inspection", {})

        # Mostrar Delegación fija (Posición 1)
        del_val = f_data.get("delegacion", "01")
        self.tree_fields.insert(
            "",
            tk.END,
            values=("Delegación (Pos. 1)", del_val, del_val, "🔒 Fija (No se toca)"),
            tags=("normal",)
        )

        field_labels = {
            "seccion": "Sección (Pos. 2)",
            "libro": "Libro (Pos. 3)",
            "tomo": "Tomo / Cat. (Pos. 4)",
            "volumen": "Volumen (Pos. 5)",
            "fraccionamiento": "Fracc. (Pos. 6)",
            "anio": "Año (Pos. 7)"
        }

        for campo, det in comp.get("detalles_campos", {}).items():
            label = field_labels.get(campo, campo.capitalize())
            val_folder = str(det["carpeta"]) if det["carpeta"] is not None else "—"
            val_ocr = str(det["ocr"]) if det["ocr"] is not None else "—"
            estado = det["estado"]

            tag = "normal"
            if estado == "COINCIDE":
                tag = "match"
                estado_str = "✔ Coincide"
            elif estado == "DISCREPANCIA":
                tag = "diff"
                estado_str = "✖ Discrepancia"
            elif estado == "CARPETA_EN_CEROS":
                tag = "zeros"
                estado_str = "★ Carpeta en 00"
            else:
                estado_str = estado

            self.tree_fields.insert("", tk.END, values=(label, val_folder, val_ocr, estado_str), tags=(tag,))

        folder_path = res.get("folder_path", "")
        self.txt_evidence.insert(tk.END, f"RUTA DE CARPETA: {folder_path}\n")
        if res.get("initial_folder_path") and res.get("initial_folder_path") != folder_path:
            self.txt_evidence.insert(tk.END, f"RUTA INICIAL:    {res.get('initial_folder_path')}\n")
        self.txt_evidence.insert(
            tk.END,
            f"PDF CARÁTULA:    {insp.get('caratula_filename', 'N/A')}  |  "
            f"ESTADO HOJAS: {insp.get('status', 'N/A')}\n"
        )
        self.txt_evidence.insert(
            tk.END,
            f"CÓDIGO SUGERIDO: {res.get('codigo_base_sugerido', '')}\n"
        )
        sub_rev = res.get("subcarpeta_revision", "")
        if sub_rev and (res.get("has_discrepancy", False) or res.get("moved_destination_type") == "APARTADOS"):
            self.txt_evidence.insert(
                tk.END,
                f"SUBCARPETA DE ERROR (APARTADOS): {sub_rev}\n"
            )
        self.txt_evidence.insert(tk.END, "-" * 78 + "\n")
        if res.get("applied_actions"):
            self.txt_evidence.insert(tk.END, "=== CAMBIOS YA APLICADOS A ESTA CARPETA (TIEMPO REAL) ===\n")
            for act in res.get("applied_actions", []):
                self.txt_evidence.insert(tk.END, f"  ✔ {act}\n")
            self.txt_evidence.insert(tk.END, "\n")

        self.txt_evidence.insert(tk.END, f"{insp.get('summary_message', '')}\n\n")

        missing_pdfs = insp.get("pdfs_missing_caratula", [])
        if missing_pdfs:
            self.txt_evidence.insert(tk.END, f"⚠ PDFs SIN PÁGINA DE CARÁTULA ({len(missing_pdfs)}):\n")
            for mp in missing_pdfs[:25]:
                self.txt_evidence.insert(tk.END, f"  • {mp}\n")
            if len(missing_pdfs) > 25:
                self.txt_evidence.insert(tk.END, f"  ... y {len(missing_pdfs) - 25} más.\n")
            self.txt_evidence.insert(tk.END, "\n")

        self.txt_evidence.insert(tk.END, "=== EVIDENCIAS Y DETECCIÓN CLAUDE ===\n")
        for role, doc_info in res.get("documents_detail", {}).items():
            self.txt_evidence.insert(tk.END, f"[{role}] {doc_info.get('pdf_file', '')}:\n")
            for ev in doc_info.get("evidencias", []):
                self.txt_evidence.insert(tk.END, f"  • {ev}\n")

    # =========================================================================
    # FASE 2: CONFIRMACIÓN DE CARÁTULA Y EJECUCIÓN DE CAMBIOS FÍSICOS
    # =========================================================================

    def open_selected_folder_in_explorer(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return
        folder_path = res.get("folder_path", "")
        if not folder_path or not os.path.exists(folder_path):
            messagebox.showerror("Error", f"La ruta no existe:\n{folder_path}")
            return
        try:
            os.startfile(folder_path)
        except Exception:
            subprocess.Popen(["explorer", folder_path])

    def change_selected_folder_caratula(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return

        folder_path = res.get("folder_path", "")
        if not folder_path or not os.path.exists(folder_path):
            messagebox.showerror("Error", f"La carpeta no existe:\n{folder_path}")
            return

        chosen = filedialog.askopenfilename(
            title=f"Selecciona el archivo PDF de Carátula para '{Path(folder_path).name}'",
            initialdir=folder_path,
            filetypes=[("Archivos PDF", "*.pdf")]
        )
        if not chosen:
            return

        self.custom_caratulas_map[folder_path] = chosen

        if messagebox.askyesno(
            "Carátula Actualizada",
            f"Se asignó '{Path(chosen).name}' como Carátula.\n\n"
            "¿Deseas ejecutar el diagnóstico de esta carpeta ahora mismo con la nueva Carátula?"
        ):
            try:
                new_res = self.service.process_folder(
                    folder_path,
                    custom_caratula_path=chosen,
                    save_to_db=True
                )
                idx = self.all_folder_items.index(res)
                self.all_folder_items[idx] = new_res
                self._refresh_master_table()
                self._render_single_folder_detail(new_res)
                self._refresh_header_badge()
            except Exception as exc:
                messagebox.showerror("Error al diagnosticar", str(exc))
        else:
            res["caratula_inspection"]["caratula_path"] = chosen
            res["caratula_inspection"]["caratula_filename"] = Path(chosen).name
            self._refresh_master_table()
            self._render_single_folder_detail(res)

    def replace_blank_pages_selected_folder(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return

        insp = res.get("caratula_inspection", {})
        replaceable = insp.get("replaceable_pdfs", [])
        info_page = insp.get("info_page_num")

        if not replaceable or not info_page:
            messagebox.showinfo(
                "Sin Hojas Blancas Reemplazables",
                "Esta carpeta no tiene PDFs con hoja de carátula en blanco pendiente de reemplazo."
            )
            return

        folder_name = Path(res.get("folder_path", "")).name
        if not messagebox.askyesno(
            "Confirmar Reemplazo de Hojas en Blanco",
            f"Se reemplazará la hoja en blanco adjunta en {len(replaceable)} PDFs de la carpeta:\n"
            f"'{folder_name}'\n"
            f"por la página {info_page} (con información) de '{insp.get('caratula_filename')}'.\n\n"
            "Se guardará respaldo automático para Rollback y se registrará en SQLite. ¿Continuar?"
        ):
            return

        try:
            op_res = self.service.execute_page_replacement(res)
            self._refresh_master_table()
            self._render_single_folder_detail(res)
            self._refresh_header_badge()

            messagebox.showinfo(
                "Reemplazo Exitoso",
                f"Se reemplazó la hoja en blanco por la hoja con información en {op_res['replaced_count']} PDFs.\n"
                "La lista se actualizó en tiempo real y el cambio quedó registrado en '✅ Ya Hecho / Cambios Aplicados'."
            )
        except Exception as exc:
            messagebox.showerror("Error al reemplazar páginas", str(exc))

    def rename_selected_folder(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return

        custom_code = self.suggested_code_var.get().strip()
        if not custom_code:
            messagebox.showwarning("Código vacío", "Ingresa o verifica el código base hasta el año.")
            return

        old_folder_name = Path(res.get("folder_path", "")).name
        if not messagebox.askyesno(
            "Confirmar Renombrado de Carpeta y PDFs",
            f"Carpeta actual: {old_folder_name}\n"
            f"Nuevo código base (hasta el año): {custom_code}\n\n"
            "Reglas aplicadas:\n"
            "• La posición 1 (Delegación) se mantiene intacta.\n"
            "• Si existe otra carpeta con el mismo nombre, a la CARPETA se le agregará _2, _3, etc.\n"
            "• Los archivos PDF internos se renombrarán con el código base SIN _2.\n\n"
            "¿Deseas aplicar el renombre y registrarlo en SQLite?"
        ):
            return

        try:
            op_res = self.service.execute_folder_and_pdfs_rename(res, custom_clean_base_code=custom_code)
            self._refresh_master_table()
            self._render_single_folder_detail(res)
            self._refresh_header_badge()

            col_note = (
                f"\n(Se detectó colisión de directorio y se nombró la carpeta '{op_res['new_folder_name']}', "
                f"mientras que los PDFs usan '{op_res['clean_base_code']}')"
                if op_res["collision_applied"] else ""
            )
            messagebox.showinfo(
                "Renombrado Completado",
                f"Carpeta renombrada a: {op_res['new_folder_name']}\n"
                f"Archivos PDF actualizados: {op_res['files_renamed_count']}{col_note}\n\n"
                "Las listas y pestañas se actualizaron en tiempo real."
            )
        except Exception as exc:
            messagebox.showerror("Error al renombrar", str(exc))

    def apartar_selected_folder(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return

        apartados_dir = self.apartados_var.get().strip()
        if not apartados_dir:
            messagebox.showwarning(
                "Falta Directorio de Apartados",
                "Selecciona primero el 'Directorio Apartados' en la parte superior."
            )
            self.browse_apartados_dir()
            apartados_dir = self.apartados_var.get().strip()
            if not apartados_dir:
                return

        folder_name = Path(res.get("folder_path", "")).name
        sub_rev = res.get("subcarpeta_revision") or "CARATULA EN BLANCO"
        target_preview = str(Path(apartados_dir) / sub_rev)
        if not messagebox.askyesno(
            "Confirmar Envío a Revisión / Apartados",
            f"¿Deseas mover la carpeta '{folder_name}' dentro de la subcarpeta de error:\n\n"
            f"📂 {sub_rev}\n"
            f"Ruta destino: {target_preview}\n\n"
            "(Se limpiará el nombre hasta el año y se registrará en SQLite con soporte de Rollback)."
        ):
            return

        try:
            op_res = self.service.execute_move_to_apartados(res, apartados_dir)
            self._refresh_master_table()
            self._render_single_folder_detail(res)
            self._refresh_header_badge()
            messagebox.showinfo(
                "Carpeta Apartada por Motivo de Error",
                f"Subcarpeta de error: {op_res.get('error_subfolder', sub_rev)}\n"
                f"Nueva ubicación:\n{op_res['new_folder_path']}\n\n"
                f"({op_res['files_moved_count']} archivos registrados en '✅ Ya Hecho / Cambios Aplicados')."
            )
        except Exception as exc:
            messagebox.showerror("Error al apartar carpeta", str(exc))

    def move_selected_folder_to_validados(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return

        validados_dir = self.validados_var.get().strip()
        if not validados_dir:
            messagebox.showwarning(
                "Falta Directorio de Correctos",
                "Selecciona primero el 'Dir. Correctos (Buenos)' en la parte superior."
            )
            self.browse_validados_dir()
            validados_dir = self.validados_var.get().strip()
            if not validados_dir:
                return

        folder_name = Path(res.get("folder_path", "")).name
        if not messagebox.askyesno(
            "Confirmar Envío a Carpeta de Correctos",
            f"¿Deseas mover la carpeta '{folder_name}' al directorio de Correctos (Buenos):\n{validados_dir}?"
        ):
            return

        try:
            op_res = self.service.execute_move_to_validados(res, validados_dir)
            self._refresh_master_table()
            self._render_single_folder_detail(res)
            self._refresh_header_badge()
            messagebox.showinfo(
                "Carpeta Movida a Correctos",
                f"La carpeta fue movida exitosamente a:\n{op_res['new_folder_path']}\n"
                f"({op_res['files_moved_count']} archivos registrados en '✅ Ya Hecho / Cambios Aplicados')."
            )
        except Exception as exc:
            messagebox.showerror("Error al mover carpeta a correctos", str(exc))

    # =========================================================================
    # OPERACIONES EN LOTE (FASE 2)
    # =========================================================================

    def batch_replace_all_blank_pages(self):
        targets = [r for r in self.all_folder_items if r.get("needs_page_replacement", False)]
        if not targets:
            messagebox.showinfo(
                "Sin Carpetas Pendientes",
                "No hay carpetas diagnosticadas con hojas en blanco reemplazables."
            )
            return

        if not messagebox.askyesno(
            "Confirmar Reemplazo Masivo de Hojas en Blanco",
            f"Se reemplazarán las hojas de carátula en blanco por la página con información en "
            f"{len(targets)} carpeta(s) detectadas.\n\n"
            "Todos los archivos originales serán respaldados para Rollback. ¿Continuar?"
        ):
            return

        total_pdfs = 0
        errors: List[str] = []
        for r in targets:
            try:
                op = self.service.execute_page_replacement(r)
                total_pdfs += op["replaced_count"]
            except Exception as exc:
                errors.append(f"{Path(r.get('folder_path', '')).name}: {exc}")

        self._refresh_master_table()
        self._refresh_header_badge()
        messagebox.showinfo(
            "Reemplazo Masivo Finalizado",
            f"Carpetas procesadas: {len(targets)}\nTotal de PDFs corregidos: {total_pdfs}\n"
            + (f"Errores: {len(errors)}" if errors else "Sin errores.")
        )

    def batch_rename_all_discrepancies(self):
        targets = [
            r for r in self.all_folder_items
            if r.get("needs_rename", False)
            and r.get("veredicto_global") not in ("PENDIENTE_DIAGNOSTICO", "APARTAR_CARATULA_BLANCA", "SIN_ARCHIVO_CARATULA", "DISCREPANCIA_SIN_CARATULA", "ERROR")
        ]
        if not targets:
            messagebox.showinfo(
                "Sin Carpetas para Renombrar",
                "No hay carpetas diagnosticadas con cambio de nombre sugerido listo para aplicar."
            )
            return

        if not messagebox.askyesno(
            "Confirmar Renombrado Masivo",
            f"Se renombrarán {len(targets)} carpeta(s) y sus archivos PDF internos usando el código sugerido hasta el año "
            "(agregando _2, _3 solo a la carpeta en caso de colisión).\n\n¿Continuar?"
        ):
            return

        renamed_folders = 0
        renamed_pdfs = 0
        errors: List[str] = []
        for r in targets:
            try:
                op = self.service.execute_folder_and_pdfs_rename(r)
                renamed_folders += 1
                renamed_pdfs += op["files_renamed_count"]
            except Exception as exc:
                errors.append(f"{Path(r.get('folder_path', '')).name}: {exc}")

        self._refresh_master_table()
        self._refresh_header_badge()
        messagebox.showinfo(
            "Renombrado Masivo Finalizado",
            f"Carpetas renombradas: {renamed_folders}\nArchivos PDF renombrados: {renamed_pdfs}\n"
            + (f"Errores: {len(errors)}" if errors else "Sin errores.")
        )

    def batch_move_all_blank_caratulas(self):
        targets = [r for r in self.all_folder_items if r.get("needs_apartar", False)]
        if not targets:
            messagebox.showinfo(
                "Sin Carpetas para Apartar",
                "No hay carpetas pendientes de enviar a revisión/apartados (Carátula en Blanco, PDFs sin Carátula, Sin Datos, etc.)."
            )
            return

        apartados_dir = self.apartados_var.get().strip()
        if not apartados_dir:
            messagebox.showwarning(
                "Falta Directorio de Apartados",
                "Selecciona primero el 'Directorio Apartados' en la parte superior."
            )
            self.browse_apartados_dir()
            apartados_dir = self.apartados_var.get().strip()
            if not apartados_dir:
                return

        counts_by_sub: Dict[str, int] = {}
        for r in targets:
            sub = r.get("subcarpeta_revision") or "CARATULA EN BLANCO"
            counts_by_sub[sub] = counts_by_sub.get(sub, 0) + 1
        breakdown_lines = "\n".join(f"  • 📂 {sub}: {cnt} carpeta(s)" for sub, cnt in counts_by_sub.items())

        if not messagebox.askyesno(
            "Confirmar Envío Masivo a Revisión por Subcarpeta de Error",
            f"Se moverán {len(targets)} carpeta(s) al directorio:\n{apartados_dir}\n\n"
            f"Clasificadas automáticamente en las siguientes subcarpetas:\n{breakdown_lines}\n\n"
            "¿Continuar?"
        ):
            return

        moved_folders = 0
        moved_files = 0
        errors: List[str] = []
        for r in targets:
            try:
                op = self.service.execute_move_to_apartados(r, apartados_dir)
                moved_folders += 1
                moved_files += op["files_moved_count"]
            except Exception as exc:
                errors.append(f"{Path(r.get('folder_path', '')).name}: {exc}")

        self._refresh_master_table()
        self._refresh_header_badge()
        messagebox.showinfo(
            "Apartado Masivo Finalizado",
            f"Carpetas apartadas: {moved_folders}\n"
            f"Subcarpetas creadas/actualizadas:\n{breakdown_lines}\n\n"
            f"Archivos registrados en SQLite con Rollback: {moved_files}\n"
            + (f"Errores: {len(errors)}" if errors else "Sin errores.")
        )

    def batch_move_all_validados(self):
        targets = [
            r for r in self.all_folder_items
            if r.get("veredicto_global") == "COINCIDE_TOTAL"
            and not r.get("needs_page_replacement", False)
            and not r.get("needs_rename", False)
            and not r.get("needs_apartar", False)
        ]
        if not targets:
            messagebox.showinfo(
                "Sin Carpetas Validadas Pendientes",
                "No hay carpetas diagnosticadas como '✔ COINCIDE TOTAL' listas para mover."
            )
            return

        validados_dir = self.validados_var.get().strip()
        if not validados_dir:
            messagebox.showwarning(
                "Falta Directorio de Correctos",
                "Selecciona primero el 'Dir. Correctos (Buenos)' en la parte superior."
            )
            self.browse_validados_dir()
            validados_dir = self.validados_var.get().strip()
            if not validados_dir:
                return

        # Evitar volver a mover carpetas que ya se encuentran dentro de validados_dir
        val_resolved = Path(validados_dir).resolve()
        filtered_targets = [
            r for r in targets
            if Path(r.get("folder_path", "")).resolve().parent != val_resolved
        ]
        if not filtered_targets:
            messagebox.showinfo(
                "Ya Movidas",
                "Todas las carpetas 'COINCIDE TOTAL' ya se encuentran en el Directorio de Correctos."
            )
            return

        if not messagebox.askyesno(
            "Confirmar Movimiento Masivo de Carpetas Correctas",
            f"Se moverán {len(filtered_targets)} carpeta(s) que están totalmente bien (COINCIDE TOTAL) al directorio:\n"
            f"{validados_dir}\n\n"
            "Todos los movimientos quedarán registrados en SQLite con Rollback. ¿Continuar?"
        ):
            return

        moved_folders = 0
        moved_files = 0
        errors: List[str] = []
        for r in filtered_targets:
            try:
                op = self.service.execute_move_to_validados(r, validados_dir)
                moved_folders += 1
                moved_files += op["files_moved_count"]
            except Exception as exc:
                errors.append(f"{Path(r.get('folder_path', '')).name}: {exc}")

        self._refresh_master_table()
        self._refresh_header_badge()
        messagebox.showinfo(
            "Movimiento de Carpetas Correctas Finalizado",
            f"Carpetas movidas a Correctos: {moved_folders}\n"
            f"Archivos registrados en SQLite: {moved_files}\n"
            + (f"Errores: {len(errors)}" if errors else "Sin errores.")
        )


def launch_gui():
    if "--self-test" in sys.argv:
        svc = FolderOCRService()
        out_check = Path(sys.executable).resolve().parent / "self_test_ok.txt"
        out_check.write_text(
            f"OK: ClaudeVisionEngine + CacheManager + DatabaseManager + CaratulaInspector + FileOperations "
            f"initialized (model={svc.settings.get('model')}, db={svc.db.db_path.name}).",
            encoding="utf-8"
        )
        sys.exit(0)

    app = OCRLibrosApp()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        app.path_var.set(sys.argv[1])
        app.reload_root_preview()
    app.mainloop()


if __name__ == "__main__":
    launch_gui()
