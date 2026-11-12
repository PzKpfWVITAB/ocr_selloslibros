"""
Interfaz Gráfica (GUI) para Diagnóstico General, Autocorrección de Carátula, Renombrado,
Auditoría en SQLite ('auditoria_libros.db') y Herramienta de Rollback Total con Claude Sonnet 5.
"""
import os
import subprocess
import sys
import threading
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set
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
from src.core.pdf_selector import PDFSelector


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
        ttk.Button(
            top_bar,
            text="📊 Dashboard Estadístico & Trazabilidad",
            style="Primary.TButton",
            command=self._open_stats_dashboard
        ).pack(side=tk.RIGHT, padx=(0, 8))

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

    def _open_stats_dashboard(self):
        ExecutiveTraceabilityDashboardDialog(self.parent_app)


class ExecutiveTraceabilityDashboardDialog(tk.Toplevel):
    """
    Dashboard Ejecutivo y Centro de Estadísticas Registrales:
    - Permite dimensionar y cruzar el estado inicial vs final de carpetas y PDFs.
    - Cuantifica el volumen de digitalización en Carpetas, Archivos PDF y su equivalencia en IMÁGENES (páginas).
    - Permite comparar ruta original, nombre original, ruta final y nombre final.
    - Exporta reportes ejecutivos consolidados a CSV / Excel para Finanzas y supervisión.
    """

    def __init__(self, parent: "OCRLibrosApp"):
        super().__init__(parent)
        self.parent_app = parent
        self.service = parent.service
        self.title("📊 Dashboard Ejecutivo: Estadísticas y Trazabilidad (Original vs Final)")
        self.geometry("1260x780")
        self.minsize(1050, 650)
        self.transient(parent)

        self.source_var = tk.StringVar(value="MEMORIA" if parent.all_folder_items else "SQLITE")
        self.search_var = tk.StringVar(value="")
        self.active_filter = "TODOS"

        self.comparison_items: List[Dict[str, Any]] = []
        self.filtered_items: List[Dict[str, Any]] = []
        self.stats_data: Dict[str, Any] = {}

        self._build_ui()
        self.refresh_data()

    def _build_ui(self):
        container = ttk.Frame(self, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        # 1. Barra Superior de Control y Fuente de Datos
        top_bar = ttk.Frame(container)
        top_bar.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(
            top_bar,
            text="Fuente de Datos:",
            font=("Segoe UI", 9, "bold")
        ).pack(side=tk.LEFT, padx=(0, 6))

        r_mem = ttk.Radiobutton(
            top_bar,
            text="📌 Sesión en Vivo (Lote en Pantalla)",
            variable=self.source_var,
            value="MEMORIA",
            command=self.refresh_data
        )
        r_mem.pack(side=tk.LEFT, padx=(0, 6))

        r_sql = ttk.Radiobutton(
            top_bar,
            text="🗄️ SQLite Local (auditoria_libros.db)",
            variable=self.source_var,
            value="SQLITE",
            command=self.refresh_data
        )
        r_sql.pack(side=tk.LEFT, padx=(0, 6))

        mysql_on = bool(self.service.mysql and self.service.mysql.enabled)
        r_my = ttk.Radiobutton(
            top_bar,
            text="🐬 Servidor MySQL (trazabilidad_selloslibros)" if mysql_on else "🐬 MySQL (Inactivo)",
            variable=self.source_var,
            value="MYSQL",
            command=self.refresh_data,
            state=tk.NORMAL if mysql_on else tk.DISABLED
        )
        r_my.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(
            top_bar,
            text="🔄 Actualizar Estadísticas",
            command=self.refresh_data
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            top_bar,
            text="📥 Exportar Reporte Ejecutivo (CSV / Excel)",
            style="Primary.TButton",
            command=self.export_csv
        ).pack(side=tk.RIGHT)

        # 2. Panel Superior de Tarjetas KPI / Dimensionamiento
        kpi_frame = ttk.Frame(container)
        kpi_frame.pack(fill=tk.X, pady=(0, 10))

        # Tarjeta 1: Carpetas
        card1 = tk.Frame(kpi_frame, bg="#eff6ff", bd=1, relief=tk.SOLID, padx=10, pady=8)
        card1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        tk.Label(card1, text="📁 CARPETAS REGISTRALES", font=("Segoe UI", 9, "bold"), fg="#1e40af", bg="#eff6ff").pack(anchor=tk.W)
        self.lbl_carp_total = tk.Label(card1, text="Total: 0", font=("Segoe UI", 12, "bold"), fg="#1e293b", bg="#eff6ff")
        self.lbl_carp_total.pack(anchor=tk.W, pady=(2, 0))
        self.lbl_carp_bien = tk.Label(card1, text="✔ Concluidas: 0 (0.0%)", font=("Segoe UI", 9, "bold"), fg="#16a34a", bg="#eff6ff")
        self.lbl_carp_bien.pack(anchor=tk.W)
        self.lbl_carp_mal = tk.Label(card1, text="⚠ En Revisión / Discrepancia: 0 (0.0%)", font=("Segoe UI", 9), fg="#dc2626", bg="#eff6ff")
        self.lbl_carp_mal.pack(anchor=tk.W)

        # Tarjeta 2: Archivos PDF
        card2 = tk.Frame(kpi_frame, bg="#f0fdf4", bd=1, relief=tk.SOLID, padx=10, pady=8)
        card2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        tk.Label(card2, text="📄 ARCHIVOS PDF", font=("Segoe UI", 9, "bold"), fg="#166534", bg="#f0fdf4").pack(anchor=tk.W)
        self.lbl_pdf_total = tk.Label(card2, text="Total PDFs: 0", font=("Segoe UI", 12, "bold"), fg="#1e293b", bg="#f0fdf4")
        self.lbl_pdf_total.pack(anchor=tk.W, pady=(2, 0))
        self.lbl_pdf_bien = tk.Label(card2, text="✔ En Expedientes Correctos: 0", font=("Segoe UI", 9, "bold"), fg="#16a34a", bg="#f0fdf4")
        self.lbl_pdf_bien.pack(anchor=tk.W)
        self.lbl_pdf_mal = tk.Label(card2, text="⚠ Con Incidencias / Apartados: 0", font=("Segoe UI", 9), fg="#dc2626", bg="#f0fdf4")
        self.lbl_pdf_mal.pack(anchor=tk.W)
        self.lbl_pdf_mod = tk.Label(card2, text="🔄 Con Movimiento Registrado: 0", font=("Segoe UI", 8), fg="#0284c7", bg="#f0fdf4")
        self.lbl_pdf_mod.pack(anchor=tk.W)

        # Tarjeta 3: Imágenes / Fojas (Páginas Digitalizadas)
        card3 = tk.Frame(kpi_frame, bg="#fefce8", bd=1, relief=tk.SOLID, padx=10, pady=8)
        card3.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        tk.Label(card3, text="🖼️ IMÁGENES / FOJAS (PÁGINAS)", font=("Segoe UI", 9, "bold"), fg="#854d0e", bg="#fefce8").pack(anchor=tk.W)
        self.lbl_img_total = tk.Label(card3, text="Total Imágenes: 0", font=("Segoe UI", 12, "bold"), fg="#1e293b", bg="#fefce8")
        self.lbl_img_total.pack(anchor=tk.W, pady=(2, 0))
        self.lbl_img_bien = tk.Label(card3, text="✔ Validadas (Bien): 0 (0.0%)", font=("Segoe UI", 9, "bold"), fg="#16a34a", bg="#fefce8")
        self.lbl_img_bien.pack(anchor=tk.W)
        self.lbl_img_mal = tk.Label(card3, text="⚠ En Revisión (Mal): 0 (0.0%)", font=("Segoe UI", 9), fg="#dc2626", bg="#fefce8")
        self.lbl_img_mal.pack(anchor=tk.W)
        self.lbl_img_prom = tk.Label(card3, text="📐 Promedio: 0.0 págs / carpeta", font=("Segoe UI", 8), fg="#475569", bg="#fefce8")
        self.lbl_img_prom.pack(anchor=tk.W)

        # Tarjeta 4: Rendimiento y Salud Registral
        card4 = tk.Frame(kpi_frame, bg="#f5f3ff", bd=1, relief=tk.SOLID, padx=10, pady=8)
        card4.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(card4, text="🎯 SALUD REGISTRAL", font=("Segoe UI", 9, "bold"), fg="#5b21b6", bg="#f5f3ff").pack(anchor=tk.W)
        self.lbl_salud_pct = tk.Label(card4, text="Salud Carpetas: 0.0%", font=("Segoe UI", 12, "bold"), fg="#1e293b", bg="#f5f3ff")
        self.lbl_salud_pct.pack(anchor=tk.W, pady=(2, 0))
        self.lbl_vol_pct = tk.Label(card4, text="Volumen Fojas OK: 0.0%", font=("Segoe UI", 9, "bold"), fg="#16a34a", bg="#f5f3ff")
        self.lbl_vol_pct.pack(anchor=tk.W)
        self.lbl_db_status = tk.Label(card4, text="Trazabilidad Activa", font=("Segoe UI", 8), fg="#475569", bg="#f5f3ff")
        self.lbl_db_status.pack(anchor=tk.W)

        # 3. Pestañas Principales: 1. Desglose de Volumetría | 2. Comparativa Cruzada (Original vs Final)
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        tab_vol = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab_vol, text=" 📊 1. Desglose de Volumetría y Categorías (Carpetas, PDFs e Imágenes) ")

        tab_comp = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab_comp, text=" 🔄 2. Matriz de Trazabilidad Cruzada: Original vs Final (Archivo por Archivo) ")

        # --- CONTENIDO PESTAÑA 1 (VOLUMETRÍA) ---
        paned_vol = ttk.PanedWindow(tab_vol, orient=tk.VERTICAL)
        paned_vol.pack(fill=tk.BOTH, expand=True)

        top_vol_frame = ttk.Frame(paned_vol)
        paned_vol.add(top_vol_frame, weight=3)

        # Desglose Veredictos (Izquierda)
        f_ver = ttk.LabelFrame(top_vol_frame, text=" Desglose por Diagnóstico / Veredicto Registral ", padding=6)
        f_ver.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        ver_cols = ("veredicto", "carpetas", "pdfs", "imagenes", "pct", "estado")
        self.tree_ver = ttk.Treeview(f_ver, columns=ver_cols, show="headings", height=8)
        self.tree_ver.heading("veredicto", text="Veredicto / Diagnóstico")
        self.tree_ver.heading("carpetas", text="Carpetas")
        self.tree_ver.heading("pdfs", text="PDFs")
        self.tree_ver.heading("imagenes", text="Imágenes (Fojas)")
        self.tree_ver.heading("pct", text="% Lote")
        self.tree_ver.heading("estado", text="Condición")

        self.tree_ver.column("veredicto", width=200, anchor=tk.W)
        self.tree_ver.column("carpetas", width=75, anchor=tk.CENTER)
        self.tree_ver.column("pdfs", width=75, anchor=tk.CENTER)
        self.tree_ver.column("imagenes", width=110, anchor=tk.CENTER)
        self.tree_ver.column("pct", width=65, anchor=tk.CENTER)
        self.tree_ver.column("estado", width=115, anchor=tk.CENTER)

        sc_ver = ttk.Scrollbar(f_ver, orient=tk.VERTICAL, command=self.tree_ver.yview)
        self.tree_ver.configure(yscrollcommand=sc_ver.set)
        self.tree_ver.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_ver.pack(side=tk.RIGHT, fill=tk.Y)

        # Desglose Carátula (Derecha)
        f_car = ttk.LabelFrame(top_vol_frame, text=" Desglose por Estado de Carátula y Sellos ", padding=6)
        f_car.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        car_cols = ("estado_car", "carpetas", "pdfs", "imagenes", "accion")
        self.tree_car = ttk.Treeview(f_car, columns=car_cols, show="headings", height=8)
        self.tree_car.heading("estado_car", text="Estado Carátula")
        self.tree_car.heading("carpetas", text="Carpetas")
        self.tree_car.heading("pdfs", text="PDFs")
        self.tree_car.heading("imagenes", text="Imágenes (Fojas)")
        self.tree_car.heading("accion", text="Acción Registral")

        self.tree_car.column("estado_car", width=190, anchor=tk.W)
        self.tree_car.column("carpetas", width=75, anchor=tk.CENTER)
        self.tree_car.column("pdfs", width=75, anchor=tk.CENTER)
        self.tree_car.column("imagenes", width=110, anchor=tk.CENTER)
        self.tree_car.column("accion", width=130, anchor=tk.CENTER)

        sc_car = ttk.Scrollbar(f_car, orient=tk.VERTICAL, command=self.tree_car.yview)
        self.tree_car.configure(yscrollcommand=sc_car.set)
        self.tree_car.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_car.pack(side=tk.RIGHT, fill=tk.Y)

        # Desglose Movimientos Físicos (Abajo)
        bot_vol_frame = ttk.LabelFrame(paned_vol, text=" Registro de Operaciones y Movimientos Físicos Aplicados ", padding=6)
        paned_vol.add(bot_vol_frame, weight=2)

        mov_cols = ("tipo", "archivos", "paginas", "descripcion")
        self.tree_mov_stat = ttk.Treeview(bot_vol_frame, columns=mov_cols, show="headings", height=5)
        self.tree_mov_stat.heading("tipo", text="Tipo de Operación")
        self.tree_mov_stat.heading("archivos", text="Archivos Involucrados")
        self.tree_mov_stat.heading("paginas", text="Imágenes / Fojas Modificadas")
        self.tree_mov_stat.heading("descripcion", text="Impacto Forense")

        self.tree_mov_stat.column("tipo", width=220, anchor=tk.W)
        self.tree_mov_stat.column("archivos", width=150, anchor=tk.CENTER)
        self.tree_mov_stat.column("paginas", width=190, anchor=tk.CENTER)
        self.tree_mov_stat.column("descripcion", width=340, anchor=tk.W)

        sc_mov = ttk.Scrollbar(bot_vol_frame, orient=tk.VERTICAL, command=self.tree_mov_stat.yview)
        self.tree_mov_stat.configure(yscrollcommand=sc_mov.set)
        self.tree_mov_stat.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_mov.pack(side=tk.RIGHT, fill=tk.Y)

        # --- CONTENIDO PESTAÑA 2 (COMPARATIVA ORIGINAL VS FINAL) ---
        filter_bar = ttk.Frame(tab_comp)
        filter_bar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(filter_bar, text="🔍 Buscar:").pack(side=tk.LEFT, padx=(0, 4))
        ent_search = ttk.Entry(filter_bar, textvariable=self.search_var, width=28)
        ent_search.pack(side=tk.LEFT, padx=(0, 10))
        self.search_var.trace_add("write", lambda *args: self._apply_filter())

        ttk.Label(filter_bar, text="Filtro Rápido:").pack(side=tk.LEFT, padx=(0, 4))
        self.btn_f_all = ttk.Button(filter_bar, text="Todos", command=lambda: self._set_filter("TODOS"))
        self.btn_f_all.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_f_mod = ttk.Button(filter_bar, text="Solo Modificados", command=lambda: self._set_filter("MODIFICADOS"))
        self.btn_f_mod.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_f_disc = ttk.Button(filter_bar, text="Solo con Discrepancia", command=lambda: self._set_filter("DISCREPANCIA"))
        self.btn_f_disc.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_f_ok = ttk.Button(filter_bar, text="Solo Concluidos", command=lambda: self._set_filter("CONCLUIDOS"))
        self.btn_f_ok.pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_filter_count = ttk.Label(filter_bar, text="Cargando registros...", font=("Segoe UI", 9, "bold"), foreground="#0369a1")
        self.lbl_filter_count.pack(side=tk.RIGHT)

        paned_comp = ttk.PanedWindow(tab_comp, orient=tk.VERTICAL)
        paned_comp.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(paned_comp)
        paned_comp.add(tree_frame, weight=4)

        comp_cols = (
            "num", "orig_name", "orig_folder", "dest_name", "dest_folder",
            "pages", "estado", "operacion", "hash_orig", "hash_dest", "fecha"
        )
        self.tree_comp = ttk.Treeview(tree_frame, columns=comp_cols, show="headings", selectmode="browse")
        self.tree_comp.heading("num", text="#")
        self.tree_comp.heading("orig_name", text="Archivo Original")
        self.tree_comp.heading("orig_folder", text="Carpeta Original")
        self.tree_comp.heading("dest_name", text="Archivo Final / Actual")
        self.tree_comp.heading("dest_folder", text="Carpeta Final / Actual")
        self.tree_comp.heading("pages", text="Págs")
        self.tree_comp.heading("estado", text="Estado")
        self.tree_comp.heading("operacion", text="Operación Aplicada")
        self.tree_comp.heading("hash_orig", text="Hash SHA-256 Inicial")
        self.tree_comp.heading("hash_dest", text="Hash Actual")
        self.tree_comp.heading("fecha", text="Fecha")

        self.tree_comp.column("num", width=42, anchor=tk.CENTER, stretch=False)
        self.tree_comp.column("orig_name", width=220, anchor=tk.W)
        self.tree_comp.column("orig_folder", width=190, anchor=tk.W)
        self.tree_comp.column("dest_name", width=220, anchor=tk.W)
        self.tree_comp.column("dest_folder", width=190, anchor=tk.W)
        self.tree_comp.column("pages", width=55, anchor=tk.CENTER, stretch=False)
        self.tree_comp.column("estado", width=110, anchor=tk.CENTER)
        self.tree_comp.column("operacion", width=180, anchor=tk.W)
        self.tree_comp.column("hash_orig", width=110, anchor=tk.CENTER)
        self.tree_comp.column("hash_dest", width=110, anchor=tk.CENTER)
        self.tree_comp.column("fecha", width=120, anchor=tk.CENTER)

        self.tree_comp.tag_configure("modificado", background="#f0fdf4", foreground="#166534")
        self.tree_comp.tag_configure("discrepancia", background="#fef2f2", foreground="#991b1b")
        self.tree_comp.tag_configure("intacto", background="#ffffff", foreground="#334155")

        sc_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree_comp.yview)
        sc_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree_comp.xview)
        self.tree_comp.configure(yscrollcommand=sc_y.set, xscrollcommand=sc_x.set)

        self.tree_comp.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_y.pack(side=tk.RIGHT, fill=tk.Y)
        sc_x.pack(side=tk.BOTTOM, fill=tk.X)

        self.tree_comp.bind("<<TreeviewSelect>>", self._on_comparison_row_selected)

        # Panel de Detalle Inferior
        det_frame = ttk.LabelFrame(paned_comp, text=" Detalle Forense del Archivo Seleccionado (Contraste Original vs Final) ", padding=6)
        paned_comp.add(det_frame, weight=1)

        self.txt_comp_detail = tk.Text(det_frame, font=("Consolas", 9), height=5, wrap=tk.WORD, bg="#f8fafc")
        sc_det = ttk.Scrollbar(det_frame, orient=tk.VERTICAL, command=self.txt_comp_detail.yview)
        self.txt_comp_detail.configure(yscrollcommand=sc_det.set)
        self.txt_comp_detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_det.pack(side=tk.RIGHT, fill=tk.Y)

    def _set_filter(self, filter_name: str):
        self.active_filter = filter_name
        self._apply_filter()

    def _on_comparison_row_selected(self, event=None):
        sel = self.tree_comp.selection()
        if not sel:
            return
        item_data = None
        try:
            row_idx = int(self.tree_comp.item(sel[0], "values")[0]) - 1
            if 0 <= row_idx < len(self.filtered_items):
                item_data = self.filtered_items[row_idx]
        except Exception:
            pass

        if not item_data:
            return

        detail_text = (
            f"=== CONTRASTE DE TRAZABILIDAD REGISTRAL ===\n"
            f"[ORIGINAL INICIAL]  Nombre: {item_data.get('nombre_origen')} | Carpeta: {item_data.get('carpeta_origen')}\n"
            f"                    Ruta:   {item_data.get('ruta_origen')}\n"
            f"                    Hash SHA-256 Inicial: {item_data.get('hash_inicial')}\n"
            f"--------------------------------------------------------------------------------------------------------\n"
            f"[DESTINO / ACTUAL]  Nombre: {item_data.get('nombre_actual')} | Carpeta: {item_data.get('carpeta_actual')}\n"
            f"                    Ruta:   {item_data.get('ruta_actual')}\n"
            f"                    Hash SHA-256 Actual:  {item_data.get('hash_actual')}\n"
            f"--------------------------------------------------------------------------------------------------------\n"
            f"[VOLUMETRÍA]        Páginas / Imágenes Equivalentes: {item_data.get('numero_paginas')} foja(s)\n"
            f"[OPERACIÓN]         {item_data.get('tipos_movimiento')} | Fecha: {item_data.get('fecha_ultimo_movimiento')} | Estado: {item_data.get('estado_str')}\n"
        )
        self.txt_comp_detail.delete("1.0", tk.END)
        self.txt_comp_detail.insert(tk.END, detail_text)

    def refresh_data(self):
        source = self.source_var.get()
        if source == "MEMORIA":
            self.stats_data, self.comparison_items = self._load_memory_data()
            db_label = f"Sesión en Vivo: {len(self.parent_app.all_folder_items)} carpetas"
        else:
            self.stats_data = self.service.mysql.get_executive_statistics() if self.service.mysql else {}
            self.comparison_items = []
            db_label = f"MySQL Server: {self.service.mysql.database}" if self.service.mysql else "MySQL"

        # 1. Actualizar Tarjetas KPI
        t_carp = self.stats_data.get("total_carpetas", 0)
        c_bien = self.stats_data.get("carpetas_bien", 0)
        c_mal = self.stats_data.get("carpetas_mal", 0)
        p_carp_bien = self.stats_data.get("pct_carpetas_bien", 0.0)
        p_carp_mal = round(100.0 - p_carp_bien, 1) if t_carp > 0 else 0.0

        self.lbl_carp_total.config(text=f"Total: {t_carp:,}")
        self.lbl_carp_bien.config(text=f"✔ Concluidas: {c_bien:,} ({p_carp_bien}%)")
        self.lbl_carp_mal.config(text=f"⚠ En Revisión / Discrepancia: {c_mal:,} ({p_carp_mal}%)")

        t_pdf = self.stats_data.get("total_pdfs", 0)
        pdf_bien = self.stats_data.get("pdfs_bien", 0)
        pdf_mal = self.stats_data.get("pdfs_mal", 0)
        pdf_mod = self.stats_data.get("archivos_modificados", 0)

        self.lbl_pdf_total.config(text=f"Total PDFs: {t_pdf:,}")
        self.lbl_pdf_bien.config(text=f"✔ En Expedientes Correctos: {pdf_bien:,}")
        self.lbl_pdf_mal.config(text=f"⚠ Con Incidencias / Apartados: {pdf_mal:,}")
        self.lbl_pdf_mod.config(text=f"🔄 Con Movimiento Registrado: {pdf_mod:,}")

        t_img = self.stats_data.get("total_imagenes", 0)
        img_bien = self.stats_data.get("imagenes_bien", 0)
        img_mal = self.stats_data.get("imagenes_mal", 0)
        p_img_bien = self.stats_data.get("pct_imagenes_bien", 0.0)
        p_img_mal = round(100.0 - p_img_bien, 1) if t_img > 0 else 0.0
        prom_img = self.stats_data.get("promedio_img_carpeta", 0.0)

        self.lbl_img_total.config(text=f"Total Imágenes: {t_img:,}")
        self.lbl_img_bien.config(text=f"✔ Validadas (Bien): {img_bien:,} ({p_img_bien}%)")
        self.lbl_img_mal.config(text=f"⚠ En Revisión (Mal): {img_mal:,} ({p_img_mal}%)")
        self.lbl_img_prom.config(text=f"📐 Promedio: {prom_img} imágenes / carpeta")

        self.lbl_salud_pct.config(text=f"Salud Carpetas: {p_carp_bien}%")
        self.lbl_vol_pct.config(text=f"Volumen Fojas OK: {p_img_bien}%")
        self.lbl_db_status.config(text=db_label)

        # 2. Actualizar Tabla de Veredictos (Pestaña 1)
        for item in self.tree_ver.get_children():
            self.tree_ver.delete(item)
        desglose_v = self.stats_data.get("desglose_veredicto", {})
        for v_name, counts in sorted(desglose_v.items(), key=lambda x: x[1].get("carpetas", 0), reverse=True):
            carp = counts.get("carpetas", 0)
            pdfs = counts.get("pdfs", 0)
            imgs = counts.get("imagenes", 0)
            pct = round(carp / t_carp * 100, 1) if t_carp > 0 else 0.0
            cond = "✔ Concluido" if "COINCIDE" in v_name or "CORRECTOS" in v_name else "⚠ Requiere Revisión"
            self.tree_ver.insert("", tk.END, values=(v_name, carp, pdfs, imgs, f"{pct}%", cond))

        # 3. Actualizar Tabla de Carátulas (Pestaña 1)
        for item in self.tree_car.get_children():
            self.tree_car.delete(item)
        desglose_c = self.stats_data.get("desglose_caratula", {})
        for c_name, counts in sorted(desglose_c.items(), key=lambda x: x[1].get("carpetas", 0), reverse=True):
            carp = counts.get("carpetas", 0)
            pdfs = counts.get("pdfs", 0)
            imgs = counts.get("imagenes", 0)
            if c_name == "OK_CON_INFO":
                act = "✔ Sello con Datos"
            elif c_name == "REEMPLAZABLE_HOJA_BLANCA":
                act = "🛠 Hoja Blanca Sustituible"
            elif c_name == "TOTALMENTE_BLANCA":
                act = "📦 Apartar (Sin Datos)"
            elif c_name == "PDFS_SIN_CARATULA":
                act = "⚠ Apartar (Incompletos)"
            else:
                act = "Diagnóstico Pendiente"
            self.tree_car.insert("", tk.END, values=(c_name, carp, pdfs, imgs, act))

        # 4. Actualizar Tabla de Movimientos Físicos (Pestaña 1)
        for item in self.tree_mov_stat.get_children():
            self.tree_mov_stat.delete(item)
        mov_types = self.stats_data.get("desglose_movimientos", [])
        for m in mov_types:
            t = m.get("tipo_movimiento") or "DESCONOCIDO"
            archs = m.get("cantidad", 0)
            pags = m.get("paginas", 0)
            if "RENOMBRADO" in t:
                desc = "Actualización de nombre físico de carpetas y PDFs"
            elif "REEMPLAZO" in t:
                desc = "Sustitución física de hoja en blanco por carátula oficial"
            elif "APARTADO" in t:
                desc = "Traslado a subcarpeta de error para auditoría"
            elif "SEPARACION" in t:
                desc = "Separación de libros mezclados en nuevas carpetas"
            else:
                desc = "Movimiento físico registrado con hash SHA-256"
            self.tree_mov_stat.insert("", tk.END, values=(t, f"{archs} archivo(s)", f"{pags} foja(s) / imagen(es)", desc))

        # 5. Filtrar y Popular Matriz de Comparativa (Pestaña 2)
        self._apply_filter()

    def _load_memory_data(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Calcula estadísticas y comparativa proyectada del lote actualmente cargado en memoria."""
        total_carpetas = len(self.parent_app.all_folder_items)
        carpetas_bien = 0
        carpetas_mal = 0
        total_pdfs = 0
        pdfs_bien = 0
        pdfs_mal = 0
        total_imagenes = 0
        imagenes_bien = 0
        imagenes_mal = 0

        veredicto_counts: Dict[str, Dict[str, int]] = {}
        caratula_counts: Dict[str, Dict[str, int]] = {}
        comparison_list: List[Dict[str, Any]] = []

        for idx, res in enumerate(self.parent_app.all_folder_items, start=1):
            f_path_str = res.get("folder_path", "")
            f_p = Path(f_path_str) if f_path_str else Path(".")
            f_data = res.get("folder_data", {})
            f_name = f_data.get("folder_name") or f_p.name
            insp = res.get("caratula_inspection", {})
            v_global = str(res.get("veredicto_global", "PENDIENTE")).upper()
            c_status = str(insp.get("status", "PENDIENTE")).upper()
            sugg_code = res.get("codigo_base_sugerido") or f_name
            applied = res.get("applied_actions", [])
            has_disc = res.get("has_discrepancy", False)

            p_cnt = int(insp.get("total_regular_pdfs", 0)) + (1 if insp.get("caratula_path") else 0)
            img_cnt = int(insp.get("total_pages_in_folder", 0))
            if img_cnt <= 0:
                img_cnt = p_cnt

            total_pdfs += p_cnt
            total_imagenes += img_cnt

            es_bien = ("COINCIDE_TOTAL" in v_global or "CORRECTOS" in v_global or "REEMPLAZAR_HOJA_BLANCA" in v_global) and not has_disc and "DISCREPANCIA" not in v_global and "APARTAR" not in v_global

            if es_bien:
                carpetas_bien += 1
                pdfs_bien += p_cnt
                imagenes_bien += img_cnt
            else:
                carpetas_mal += 1
                pdfs_mal += p_cnt
                imagenes_mal += img_cnt

            # Veredictos agrupados
            if v_global not in veredicto_counts:
                veredicto_counts[v_global] = {"carpetas": 0, "pdfs": 0, "imagenes": 0}
            veredicto_counts[v_global]["carpetas"] += 1
            veredicto_counts[v_global]["pdfs"] += p_cnt
            veredicto_counts[v_global]["imagenes"] += img_cnt

            # Carátulas agrupadas
            if c_status not in caratula_counts:
                caratula_counts[c_status] = {"carpetas": 0, "pdfs": 0, "imagenes": 0}
            caratula_counts[c_status]["carpetas"] += 1
            caratula_counts[c_status]["pdfs"] += p_cnt
            caratula_counts[c_status]["imagenes"] += img_cnt

            # Lista de archivos para la comparativa
            page_counts = insp.get("pdf_page_counts", {})
            pdf_files: List[Path] = []
            if f_p.exists() and f_p.is_dir():
                try:
                    pdf_files = sorted([f for f in f_p.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"], key=lambda x: x.name)
                except Exception:
                    pdf_files = []

            for pf in pdf_files:
                orig_n = pf.name
                sugg_pdf_n = FileOperations.build_new_pdf_name(orig_n, sugg_code, f_name)
                dest_f = sugg_code if "COINCIDE" in v_global or "CORREGIDA" in v_global else (res.get("subcarpeta_revision") or f_name)
                pgs = page_counts.get(orig_n, 1)
                changed = (orig_n != sugg_pdf_n or f_name != dest_f)
                is_dup_pf = bool(re.search(r"[_+-]DUP\d*(\.pdf)?$", orig_n, re.IGNORECASE))
                if is_dup_pf:
                    estado_str = "ℹ REVISIÓN MANUAL (DUP)"
                else:
                    estado_str = "✔ CONCLUIDO" if es_bien else ("🛠 REEMPLAZABLE" if c_status == "REEMPLAZABLE_HOJA_BLANCA" else "⚠ DISCREPANCIA")
                oper_str = " -> ".join(applied) if applied else ("Renombre Proyectado" if changed else "Sin Cambios")

                comparison_list.append({
                    "nombre_origen": orig_n,
                    "carpeta_origen": f_name,
                    "ruta_origen": str(pf),
                    "nombre_actual": sugg_pdf_n,
                    "carpeta_actual": dest_f,
                    "ruta_actual": str(pf.parent / sugg_pdf_n),
                    "numero_paginas": pgs,
                    "tipos_movimiento": oper_str,
                    "cantidad_cambios": len(applied),
                    "hash_inicial": "-",
                    "hash_actual": "-",
                    "fecha_ultimo_movimiento": "Sesión Activa",
                    "ha_cambiado": changed,
                    "estado_str": estado_str,
                })

        pct_carpetas_bien = round((carpetas_bien / total_carpetas * 100), 1) if total_carpetas > 0 else 0.0
        pct_imagenes_bien = round((imagenes_bien / total_imagenes * 100), 1) if total_imagenes > 0 else 0.0
        promedio_img_carpeta = round(total_imagenes / total_carpetas, 1) if total_carpetas > 0 else 0.0

        stats = {
            "total_carpetas": total_carpetas,
            "carpetas_bien": carpetas_bien,
            "carpetas_mal": carpetas_mal,
            "pct_carpetas_bien": pct_carpetas_bien,
            "total_pdfs": total_pdfs,
            "pdfs_bien": pdfs_bien,
            "pdfs_mal": pdfs_mal,
            "total_imagenes": total_imagenes,
            "imagenes_bien": imagenes_bien,
            "imagenes_mal": imagenes_mal,
            "pct_imagenes_bien": pct_imagenes_bien,
            "promedio_img_carpeta": promedio_img_carpeta,
            "archivos_modificados": sum(1 for c in comparison_list if c["ha_cambiado"]),
            "paginas_modificadas": sum(c["numero_paginas"] for c in comparison_list if c["ha_cambiado"]),
            "desglose_veredicto": veredicto_counts,
            "desglose_caratula": caratula_counts,
            "desglose_movimientos": [
                {"tipo_movimiento": "Sesión Activa: Análisis", "cantidad": len(comparison_list), "paginas": total_imagenes}
            ],
        }
        return stats, comparison_list

    def _apply_filter(self):
        q = self.search_var.get().strip().lower()
        flt = self.active_filter

        self.filtered_items = []
        for item in self.comparison_items:
            orig_n = item.get("nombre_origen", "")
            orig_f = item.get("carpeta_origen", "")
            curr_n = item.get("nombre_actual", "")
            curr_f = item.get("carpeta_actual", "")
            oper = item.get("tipos_movimiento", "")
            estado = item.get("estado_str", "")
            changed = item.get("ha_cambiado", False)

            if flt == "MODIFICADOS" and not changed:
                continue
            elif flt == "DISCREPANCIA" and "DISCREPANCIA" not in estado and "REVISIÓN" not in estado:
                continue
            elif flt == "CONCLUIDOS" and "CONCLUIDO" not in estado and "✔" not in estado:
                continue

            if q:
                searchable = f"{orig_n} {orig_f} {curr_n} {curr_f} {oper} {estado}".lower()
                if q not in searchable:
                    continue

            self.filtered_items.append(item)

        for row in self.tree_comp.get_children():
            self.tree_comp.delete(row)

        for idx, item in enumerate(self.filtered_items, start=1):
            changed = item.get("ha_cambiado", False)
            estado = item.get("estado_str", "")
            tag = "modificado" if changed else ("discrepancia" if "DISCREPANCIA" in estado else "intacto")

            h_orig_s = (item.get("hash_inicial") or "")[:12] + ("..." if len(item.get("hash_inicial", "")) > 12 else "")
            h_dest_s = (item.get("hash_actual") or "")[:12] + ("..." if len(item.get("hash_actual", "")) > 12 else "")

            self.tree_comp.insert(
                "",
                tk.END,
                values=(
                    idx,
                    item.get("nombre_origen", ""),
                    item.get("carpeta_origen", ""),
                    item.get("nombre_actual", ""),
                    item.get("carpeta_actual", ""),
                    item.get("numero_paginas", 0),
                    estado,
                    item.get("tipos_movimiento", ""),
                    h_orig_s,
                    h_dest_s,
                    item.get("fecha_ultimo_movimiento", ""),
                ),
                tags=(tag,)
            )

        self.lbl_filter_count.config(text=f"Mostrando {len(self.filtered_items):,} de {len(self.comparison_items):,} archivos")

    def export_csv(self):
        """Exporta el reporte ejecutivo completo a formato CSV (con BOM UTF-8 para Excel)."""
        if not self.comparison_items and not self.stats_data:
            messagebox.showwarning("Atención", "No hay datos para exportar en este momento.", parent=self)
            return

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        def_name = f"Reporte_Ejecutivo_Trazabilidad_{now_str}.csv"
        file_path = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar Reporte Ejecutivo de Estadísticas y Trazabilidad",
            defaultextension=".csv",
            initialfile=def_name,
            filetypes=[("Archivos CSV (Excel)", "*.csv"), ("Todos los archivos", "*.*")]
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)

                # 1. ENCABEZADO EJECUTIVO
                writer.writerow(["========================================================================================="])
                writer.writerow(["REPORTE EJECUTIVO DE AUDITORÍA REGISTRAL, DIMENSIONAMIENTO Y TRAZABILIDAD TOTAL"])
                writer.writerow(["REGISTRO PÚBLICO DE LA PROPIEDAD Y DE COMERCIO DEL ESTADO DE CHIAPAS"])
                writer.writerow([f"Fecha y Hora de Generación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
                writer.writerow([f"Fuente de Datos Consultada: {self.source_var.get()}"])
                writer.writerow(["========================================================================================="])
                writer.writerow([])

                # 2. RESUMEN DE VOLUMETRÍA Y ESTADÍSTICA
                writer.writerow(["--- RESUMEN EJECUTIVO DE VOLUMETRÍA REGISTRAL ---"])
                writer.writerow(["Concepto", "Total", "Concluidos / Validados (Bien)", "En Revisión / Incidencia (Mal)", "% Efectividad"])
                writer.writerow([
                    "Carpetas Registrales",
                    self.stats_data.get("total_carpetas", 0),
                    self.stats_data.get("carpetas_bien", 0),
                    self.stats_data.get("carpetas_mal", 0),
                    f"{self.stats_data.get('pct_carpetas_bien', 0.0)}%"
                ])
                writer.writerow([
                    "Archivos PDF",
                    self.stats_data.get("total_pdfs", 0),
                    self.stats_data.get("pdfs_bien", 0),
                    self.stats_data.get("pdfs_mal", 0),
                    f"{round(self.stats_data.get('pdfs_bien', 0) / max(1, self.stats_data.get('total_pdfs', 1)) * 100, 1)}%"
                ])
                writer.writerow([
                    "Imágenes Digitalizadas (Fojas/Páginas)",
                    self.stats_data.get("total_imagenes", 0),
                    self.stats_data.get("imagenes_bien", 0),
                    self.stats_data.get("imagenes_mal", 0),
                    f"{self.stats_data.get('pct_imagenes_bien', 0.0)}%"
                ])
                writer.writerow(["Promedio de Imágenes por Carpeta", f"{self.stats_data.get('promedio_img_carpeta', 0.0)} imágenes/carpeta", "", "", ""])
                writer.writerow(["Archivos con Movimiento Físico / Modificados", self.stats_data.get("archivos_modificados", 0), "", "", ""])
                writer.writerow([])

                # 3. DESGLOSE POR VEREDICTO
                writer.writerow(["--- DESGLOSE POR VEREDICTO Y DIAGNÓSTICO ---"])
                writer.writerow(["Veredicto Registral", "Carpetas", "PDFs", "Imágenes (Fojas)", "% del Lote Total"])
                t_c = max(1, self.stats_data.get("total_carpetas", 1))
                for v_name, counts in self.stats_data.get("desglose_veredicto", {}).items():
                    carp = counts.get("carpetas", 0)
                    pdfs = counts.get("pdfs", 0)
                    imgs = counts.get("imagenes", 0)
                    pct = round(carp / t_c * 100, 1)
                    writer.writerow([v_name, carp, pdfs, imgs, f"{pct}%"])
                writer.writerow([])

                # 4. TABLA DE TRAZABILIDAD DETALLADA ORIGINAL VS FINAL
                writer.writerow(["--- MATRIZ COMPLETA DE TRAZABILIDAD CRUZADA (ORIGINAL VS FINAL) ---"])
                writer.writerow([
                    "#", "Archivo Original", "Carpeta Original", "Ruta Completa Original",
                    "Archivo Final / Actual", "Carpeta Final / Actual", "Ruta Completa Final",
                    "Páginas (Imágenes)", "Estado Registral", "Operaciones Aplicadas",
                    "Hash SHA-256 Inicial", "Hash SHA-256 Actual", "Último Movimiento"
                ])

                for idx, row in enumerate(self.comparison_items, start=1):
                    writer.writerow([
                        idx,
                        row.get("nombre_origen", ""),
                        row.get("carpeta_origen", ""),
                        row.get("ruta_origen", ""),
                        row.get("nombre_actual", ""),
                        row.get("carpeta_actual", ""),
                        row.get("ruta_actual", ""),
                        row.get("numero_paginas", 0),
                        row.get("estado_str", ""),
                        row.get("tipos_movimiento", ""),
                        row.get("hash_inicial", ""),
                        row.get("hash_actual", ""),
                        row.get("fecha_ultimo_movimiento", "")
                    ])

            messagebox.showinfo(
                "Exportación Exitosa",
                f"El reporte ejecutivo fue exportado exitosamente a:\n{file_path}\n\n"
                f"✔ Carpetas registradas: {self.stats_data.get('total_carpetas', 0)}\n"
                f"✔ Total PDFs: {self.stats_data.get('total_pdfs', 0)}\n"
                f"✔ Total Imágenes / Fojas: {self.stats_data.get('total_imagenes', 0):,}\n"
                f"✔ Registros de trazabilidad: {len(self.comparison_items):,}",
                parent=self
            )
        except Exception as e:
            messagebox.showerror("Error al Exportar", f"Ocurrió un error al guardar el archivo CSV:\n{e}", parent=self)


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

        try:
            subfolders = PDFSelector.get_valid_subfolders(str(root_path))
        except Exception:
            subfolders = sorted([f for f in root_path.iterdir() if f.is_dir() and PDFSelector.is_book_folder_name(f.name)])
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

        total_dup_files = sum(item.get("dup_files_count", 0) for item in self.preview_data)
        summary_txt = f"Total: {total_sub} carpetas analizadas  |  {to_rename_cnt} por renombrar  |  {already_ok_cnt} ya correctas"
        if total_dup_files > 0:
            summary_txt += f"  |  ℹ {total_dup_files} archivos DUP detectados (conservan _DUP para revisión)"
        self.lbl_summary.config(text=summary_txt)
        self.btn_apply.config(state=tk.NORMAL if to_rename_cnt > 0 else tk.DISABLED)

    def _apply_renames(self):
        items_to_rename = [item for item in self.preview_data if item.get("needs_rename")]
        if not items_to_rename:
            messagebox.showinfo("Información", "No hay carpetas pendientes de renombrar.")
            return

        if not messagebox.askyesno(
            "Confirmar Renombrado Físico",
            f"¿Deseas aplicar el renombrado físico a las {len(items_to_rename)} carpetas?\n\n"
            f"• Se tomará el nombre del 1er PDF estándar hasta el año (omitiendo _DUP para el nombre de la carpeta).\n"
            f"• Si el PDF tiene año '0000', se conservará el año de la carpeta original.\n"
            f"• Los archivos con _DUP mantendrán su sufijo intacto y serán marcados para revisión manual.\n"
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
        self.tree_folders.tag_configure("review", background="#fffbeb", foreground="#b45309")
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

        try:
            subfolders = PDFSelector.get_valid_subfolders(str(root_path))
        except Exception:
            subfolders = sorted([f for f in root_path.iterdir() if f.is_dir() and PDFSelector.is_book_folder_name(f.name)])
        mixed_count = 0
        review_count = 0

        for idx, f_path in enumerate(subfolders, start=1):
            insp = self.separator.preview_folder_split(f_path)
            self.inspected_folders.append(insp)

            is_m = insp.get("is_mixed", False)
            irreg_cnt = insp.get("irregular_files_count", 0)

            if is_m:
                mixed_count += 1
                tag = "mixed"
                st = f"⚠ MEZCLADA ({insp.get('total_groups')} libros)"
            elif irreg_cnt > 0:
                review_count += 1
                tag = "review"
                st = f"ℹ Revisión manual ({irreg_cnt} fuera de formato)"
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
            text=f"Total: {len(subfolders)} carpetas  |  {mixed_count} mezcladas  |  {review_count} con archivos para revisión manual"
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
                self.txt_detail.insert(tk.END, "Todos los archivos regulares de esta carpeta pertenecen al mismo Libro y Sección.\n")

            irreg_items = insp.get("irregular_files", [])
            if irreg_items:
                self.txt_detail.insert(tk.END, f"\n=== ARCHIVOS PARA REVISIÓN MANUAL ({len(irreg_items)}) ===\n")
                self.txt_detail.insert(tk.END, "(Permanecen seguros en la carpeta origen; no se separan en carpetas extrañas):\n")
                for it in irreg_items:
                    self.txt_detail.insert(tk.END, f"  • {it['pdf_name']} -> {it.get('irregular_reason', 'Formato no estándar')}\n")

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
        self.geometry("1440x920")
        self.minsize(1180, 750)
        try:
            self.state("zoomed")
        except Exception:
            pass

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
        self.style.configure("Success.TButton", font=("Segoe UI", 9, "bold"), padding=5, foreground="#15803d")
        self.style.configure("Warning.TButton", font=("Segoe UI", 9, "bold"), padding=5, foreground="#b45309")
        self.style.configure("Info.TButton", font=("Segoe UI", 9, "bold"), padding=5, foreground="#0369a1")
        self.style.configure("Config.TButton", font=("Segoe UI", 9, "bold"), padding=5)
        self.style.configure("Danger.TButton", font=("Segoe UI", 9, "bold"), padding=5, foreground="#b91c1c")
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.style.configure("Treeview", rowheight=25)
        self.style.configure("Card.TLabelframe", padding=6)
        self.style.configure("Card.TLabelframe.Label", font=("Segoe UI", 9, "bold"), foreground="#1e293b")

    def _create_menu(self):
        menubar = tk.Menu(self)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="📊 Dashboard Ejecutivo: Estadísticas MySQL (Original vs Final)...", command=self.open_statistics_dashboard)
        tools_menu.add_separator()
        tools_menu.add_command(label="📁 Paso 1: Indexar en MySQL y Renombrar por 1er PDF", command=self.run_step1_index_and_rename_async)
        tools_menu.add_command(label="🔍 Paso 2: Chequeo de Estructura + Diagnóstico General", command=self.start_analysis)
        tools_menu.add_command(label="✅ Paso 3A: Mover Correctas a Dir. Correctos", command=self.run_step3_move_correctos_async)
        tools_menu.add_command(label="📦 Paso 3B: Mover Apartadas a Dir. Apartados", command=self.run_step3_move_apartados_async)
        tools_menu.add_separator()
        tools_menu.add_command(label="🗄 Conexión MySQL & Crear Tablas (5 Tablas)...", command=self.open_mysql_dialog)
        tools_menu.add_separator()
        tools_menu.add_command(label="⏪ Revertir Última Ejecución (Rollback Lote)...", command=self.trigger_rollback_last_batch)
        tools_menu.add_command(label="🧹 Reset Maestro de Sistema (Revertir Todo + Limpiar MySQL)...", command=self.trigger_full_system_reset)
        menubar.add_cascade(label="🛠 Flujo MySQL y Rollback", menu=tools_menu)

        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label="⚙ Configurar API Key y Caché...", command=self.open_settings_dialog)
        config_menu.add_command(label="🗄 Configurar MySQL...", command=self.open_mysql_dialog)
        config_menu.add_separator()
        config_menu.add_command(label="🗑 Limpiar Caché Local en Disco", command=self._clear_cache_from_menu)
        config_menu.add_separator()
        config_menu.add_command(label="Salir", command=self.quit)
        menubar.add_cascade(label="⚙ Configuración", menu=config_menu)
        self.config(menu=menubar)

    def _create_widgets(self):
        # Contenedor raíz con Canvas y Scrollbar Vertical para desplazamiento suave de toda la ventana
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.v_scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        main_frame = ttk.Frame(self.canvas, padding=10)

        main_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window_id = self.canvas.create_window((0, 0), window=main_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.v_scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Ajustar ancho del frame interior al ancho del canvas dinámicamente
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_window_id, width=e.width)
        )

        # Scroll contextual inteligente con la rueda del ratón:
        def _on_mousewheel(event):
            widget = event.widget
            is_tree = False
            w = widget
            while w is not None and w != self:
                if isinstance(w, ttk.Treeview):
                    is_tree = True
                    break
                w = getattr(w, "master", None)

            if not is_tree:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.bind_all("<MouseWheel>", _on_mousewheel)

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

        ttk.Button(
            row_root,
            text="📊 Estadísticas & Trazabilidad (Original vs Final)",
            command=self.open_statistics_dashboard
        ).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Button(
            row_root,
            text="⏪ Revertir Último Lote (Rollback)",
            command=self.trigger_rollback_last_batch
        ).pack(side=tk.LEFT, padx=(6, 0))

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

        # 4. Panel de Herramientas Clasificadas y Separadas Visualmente
        tools_container = ttk.Frame(main_frame)
        tools_container.pack(fill=tk.X, pady=(0, 8))

        # --- TARJETA 1: Acciones sobre la Carpeta Seleccionada ---
        card_single = ttk.LabelFrame(
            tools_container,
            text=" 📦 1. Carpeta Seleccionada (Herramientas Individuales) ",
            padding=6,
            style="Card.TLabelframe"
        )
        card_single.pack(fill=tk.X, pady=(0, 5))

        row_s1 = ttk.Frame(card_single)
        row_s1.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(
            row_s1,
            text="📂 Abrir en Explorador",
            command=self.open_selected_folder_in_explorer
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_s1,
            text="📄 Cambiar PDF Carátula...",
            command=self.change_selected_folder_caratula
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_s1,
            text="📝 Editar Campos Manualmente...",
            command=self.open_manual_edit_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_s1,
            text="🔍 Re-analizar Carpeta",
            style="Info.TButton",
            command=self.reanalyze_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 6))

        row_s2 = ttk.Frame(card_single)
        row_s2.pack(fill=tk.X)
        ttk.Label(row_s2, text="Código Base (hasta Año):", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(0, 4))
        self.suggested_code_var = tk.StringVar(value="")
        ttk.Entry(row_s2, textvariable=self.suggested_code_var, font=("Consolas", 10, "bold"), width=24).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(
            row_s2,
            text="⚡ Aplicar Todo a esta Carpeta",
            style="Success.TButton",
            command=self.apply_all_single_folder
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_s2,
            text="✏️ Renombrar Carpeta y PDFs",
            style="Primary.TButton",
            command=self.rename_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_s2,
            text="🛠 Reemplazar Hoja Blanca",
            style="Warning.TButton",
            command=self.replace_blank_pages_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_s2,
            text="📦 Apartar Seleccionada",
            style="Warning.TButton",
            command=self.apartar_selected_folder
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_s2,
            text="✅ Mover a Correctos",
            style="Success.TButton",
            command=self.move_selected_folder_to_validados
        ).pack(side=tk.LEFT)

        # --- TARJETA 2: Flujo Guiado en 3 Pasos (Ejecución Asíncrona por Lotes) ---
        card_batch = ttk.LabelFrame(
            tools_container,
            text=" 🚀 2. Flujo Guiado por Lotes (950 Carpetas = 950 Carpetas, 100% MySQL) ",
            padding=6,
            style="Card.TLabelframe"
        )
        card_batch.pack(fill=tk.X, pady=(0, 5))

        row_b1 = ttk.Frame(card_batch)
        row_b1.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(
            row_b1,
            text="📁 1. Indexar Estado Inicial en MySQL y Renombrar por 1er PDF",
            style="Primary.TButton",
            command=self.run_step1_index_and_rename_async
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b1,
            text="🔍 2. Chequeo de Estructura + Diagnóstico General (IA)",
            style="Action.TButton",
            command=self.start_analysis
        ).pack(side=tk.LEFT, padx=(0, 6))

        row_b2 = ttk.Frame(card_batch)
        row_b2.pack(fill=tk.X)
        ttk.Button(
            row_b2,
            text="📦 3A. Mover TODAS las Apartadas (por Subcarpeta de Error)",
            style="Warning.TButton",
            command=self.run_step3_move_apartados_async
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b2,
            text="✅ 3B. Mover TODAS las Correctas (COINCIDE TOTAL) a Dir. Correctos",
            style="Success.TButton",
            command=self.run_step3_move_correctos_async
        ).pack(side=tk.LEFT)

        # --- TARJETA 3: Trazabilidad MySQL, Estadísticas y Rollback por Lote ---
        card_audit = ttk.LabelFrame(
            tools_container,
            text=" 🛡️ 3. Trazabilidad MySQL y Rollback por Lote ",
            padding=6,
            style="Card.TLabelframe"
        )
        card_audit.pack(fill=tk.X, pady=(0, 4))

        row_a2 = ttk.Frame(card_audit)
        row_a2.pack(fill=tk.X)
        ttk.Button(
            row_a2,
            text="🗄 Conexión MySQL & Tablas",
            command=self.open_mysql_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_a2,
            text="📊 Estadísticas MySQL (Original vs Final)",
            command=self.open_statistics_dashboard
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_a2,
            text="⏪ Revertir Última Ejecución (Rollback Lote)",
            style="Action.TButton",
            command=self.trigger_rollback_last_batch
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_a2,
            text="🧹 Reset Maestro (Revertir Todo + Limpiar MySQL)",
            style="Danger.TButton",
            command=self.trigger_full_system_reset
        ).pack(side=tk.LEFT)

        # 5. Panel Dividido (Pestañas en Tiempo Real + Detalle)
        paned = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        master_frame = ttk.LabelFrame(
            paned,
            text=" Listas en Tiempo Real (Al aplicar cambios, cada carpeta pasa automáticamente a su pestaña correspondiente) ",
            padding=4
        )
        paned.add(master_frame, weight=5)

        self.notebook_tabs = ttk.Notebook(master_frame)
        self.notebook_tabs.pack(fill=tk.BOTH, expand=True)

        self.tab_all = ttk.Frame(self.notebook_tabs, padding=2)
        self.tab_pending = ttk.Frame(self.notebook_tabs, padding=2)
        self.tab_ready = ttk.Frame(self.notebook_tabs, padding=2)
        self.tab_done = ttk.Frame(self.notebook_tabs, padding=2)
        self.tab_audit = ttk.Frame(self.notebook_tabs, padding=2)

        self.notebook_tabs.add(self.tab_all, text="📋 Todas en Raíz (0)")
        self.notebook_tabs.add(self.tab_pending, text="⚠ Pendientes por Corregir / Apartar (0)")
        self.notebook_tabs.add(self.tab_ready, text="✔ Listas / Correctas (0)")
        self.notebook_tabs.add(self.tab_done, text="✅ Ya Hecho / Cambios Aplicados (0)")
        self.notebook_tabs.add(self.tab_audit, text="⏪ Historial de Ejecuciones y Rollback")

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
        self.tree_done = ttk.Treeview(done_table_frame, columns=done_cols, show="headings", height=20)
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

        # Pestaña 5: Historial de Ejecuciones y Rollback por Lote (MySQL)
        audit_toolbar = ttk.Frame(self.tab_audit)
        audit_toolbar.pack(fill=tk.X, pady=(0, 4))
        self.lbl_audit_summary = ttk.Label(
            audit_toolbar,
            text="Historial de ejecuciones por lotes en MySQL (Rollback estricto en orden cronológico inverso).",
            font=("Segoe UI", 9, "bold"),
            foreground="#0369a1"
        )
        self.lbl_audit_summary.pack(side=tk.LEFT, padx=(4, 10))
        ttk.Button(
            audit_toolbar,
            text="🔄 Actualizar Historial",
            style="Primary.TButton",
            command=self.refresh_execution_history
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            audit_toolbar,
            text="⏪ Revertir Última Ejecución",
            style="Danger.TButton",
            command=self.trigger_rollback_last_batch
        ).pack(side=tk.LEFT, padx=(0, 6))

        audit_table_frame = ttk.Frame(self.tab_audit)
        audit_table_frame.pack(fill=tk.BOTH, expand=True)
        history_cols = (
            "id",
            "tipo_ejecucion",
            "fecha_inicio",
            "total_carpetas",
            "total_archivos",
            "estado",
        )
        self.tree_history = ttk.Treeview(audit_table_frame, columns=history_cols, show="headings", height=20)
        self.tree_history.heading("id", text="ID Lote")
        self.tree_history.heading("tipo_ejecucion", text="Tipo de Ejecución")
        self.tree_history.heading("fecha_inicio", text="Fecha / Hora")
        self.tree_history.heading("total_carpetas", text="Carpetas")
        self.tree_history.heading("total_archivos", text="Archivos")
        self.tree_history.heading("estado", text="Estado")

        self.tree_history.column("id", width=70, anchor=tk.CENTER, stretch=False)
        self.tree_history.column("tipo_ejecucion", width=260, anchor=tk.W)
        self.tree_history.column("fecha_inicio", width=180, anchor=tk.CENTER)
        self.tree_history.column("total_carpetas", width=110, anchor=tk.CENTER)
        self.tree_history.column("total_archivos", width=110, anchor=tk.CENTER)
        self.tree_history.column("estado", width=180, anchor=tk.CENTER)

        self._configure_tree_tags(self.tree_history)
        scroll_history = ttk.Scrollbar(audit_table_frame, orient=tk.VERTICAL, command=self.tree_history.yview)
        self.tree_history.configure(yscrollcommand=scroll_history.set)
        self.tree_history.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_history.pack(side=tk.RIGHT, fill=tk.Y)

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

    def open_statistics_dashboard(self):
        ExecutiveTraceabilityDashboardDialog(self)

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

    def run_step1_index_and_rename_async(self):
        """
        Paso 1 (Asíncrono): Indexa las 950 carpetas y sus PDFs en MySQL (estado original inicial)
        y renombra cada carpeta por el nombre del 1er PDF hasta el año.
        """
        root_p = self.path_var.get().strip()
        if not root_p or not os.path.isdir(root_p):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        if not messagebox.askyesno(
            "Paso 1: Indexar en MySQL y Renombrar",
            f"Se indexarán todas las carpetas y PDFs de:\n{root_p}\n\n"
            "en MySQL (tablas `carpetas_iniciales` y `archivos_iniciales`) y posteriormente "
            "se renombrarán por su 1er PDF hasta el año.\n\n¿Continuar?",
            parent=self
        ):
            return

        def _worker():
            self.after(0, lambda: self.lbl_status.config(text="Paso 1: Indexando en MySQL y renombrando..."))
            try:
                res = self.service.ejecutar_paso1_indexar_y_renombrar(
                    root_p,
                    progress_cb=lambda i, t, msg: self.after(0, self._update_async_status, i, t, msg),
                )
                self.after(0, lambda r=res: self._on_step1_finished(r))
            except Exception as exc:
                self.after(0, lambda e=str(exc): messagebox.showerror("Error Paso 1", e, parent=self))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_step1_finished(self, res: Dict[str, Any]):
        self.reload_root_preview()
        self.refresh_execution_history()
        self._refresh_header_badge()
        errs = res.get("errores", [])
        msg = (
            f"Paso 1 completado (Lote #{res.get('ejecucion_id')}):\n"
            f"• Carpetas totales: {res.get('total_carpetas', 0)}\n"
            f"• Nuevas indexadas en MySQL: {res.get('nuevas_indexadas', 0)}\n"
            f"• Archivos PDF indexados: {res.get('archivos_indexados', 0)}\n"
            f"• Carpetas renombradas: {res.get('renombradas', 0)}\n"
            f"• PDFs renombrados: {res.get('archivos_renombrados', 0)}"
        )
        if errs:
            msg += f"\n\nAvisos ({len(errs)}):\n" + "\n".join(errs[:5])
            messagebox.showwarning("Paso 1 con Avisos", msg, parent=self)
        else:
            messagebox.showinfo("Paso 1 Completado", msg, parent=self)

    def run_step3_move_correctos_async(self):
        """Paso 3B (Asíncrono): Mueve todas las carpetas COINCIDE_TOTAL a Dir. Correctos en un lote MySQL."""
        validados_dir = self.validados_var.get().strip()
        if not validados_dir:
            messagebox.showwarning("Atención", "Configura primero el directorio de 'Correctos'.", parent=self)
            return
        targets = [r for r in self.all_folder_items if r.get("veredicto_global") == "COINCIDE_TOTAL" and not r.get("needs_apartar")]
        if not targets:
            messagebox.showinfo("Sin Pendientes", "No hay carpetas listas (COINCIDE_TOTAL) para mover a Correctos.", parent=self)
            return
        if not messagebox.askyesno("Paso 3B: Mover Correctas", f"Se moverán {len(targets)} carpeta(s) a:\n{validados_dir}\n\n¿Continuar?", parent=self):
            return

        def _worker():
            res = self.service.ejecutar_paso3_trasladar_lote(
                self.all_folder_items,
                validados_dir,
                mode="CORRECTOS",
                progress_cb=lambda i, t, msg: self.after(0, self._update_async_status, i, t, msg),
            )
            self.after(0, lambda r=res: self._on_step3_finished("Correctos", r))

        threading.Thread(target=_worker, daemon=True).start()

    def run_step3_move_apartados_async(self):
        """Paso 3A (Asíncrono): Mueve todas las carpetas con discrepancias/apartar a Dir. Apartados en un lote MySQL."""
        apartados_dir = self.apartados_var.get().strip()
        if not apartados_dir:
            messagebox.showwarning("Atención", "Configura primero el directorio de 'Apartados'.", parent=self)
            return
        targets = [r for r in self.all_folder_items if r.get("needs_apartar")]
        if not targets:
            messagebox.showinfo("Sin Pendientes", "No hay carpetas marcadas para Apartados.", parent=self)
            return
        if not messagebox.askyesno("Paso 3A: Mover Apartadas", f"Se moverán {len(targets)} carpeta(s) a:\n{apartados_dir}\n\n¿Continuar?", parent=self):
            return

        def _worker():
            res = self.service.ejecutar_paso3_trasladar_lote(
                self.all_folder_items,
                apartados_dir,
                mode="APARTADOS",
                progress_cb=lambda i, t, msg: self.after(0, self._update_async_status, i, t, msg),
            )
            self.after(0, lambda r=res: self._on_step3_finished("Apartados", r))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_step3_finished(self, dest_label: str, res: Dict[str, Any]):
        self._refresh_all_tabs()
        self.refresh_execution_history()
        self._refresh_header_badge()
        messagebox.showinfo(
            f"Paso 3 ({dest_label}) Completado",
            f"Lote #{res.get('ejecucion_id')} finalizado:\n"
            f"• Carpetas movidas: {res.get('movidas', 0)}\n"
            f"• Archivos movidos: {res.get('archivos_movidos', 0)}\n"
            f"• Errores: {len(res.get('errores', []))}",
            parent=self
        )

    def refresh_execution_history(self):
        """Recarga la tabla del Historial de Ejecuciones desde MySQL."""
        if not hasattr(self, "tree_history"):
            return
        for item in self.tree_history.get_children():
            self.tree_history.delete(item)
        try:
            rows = self.service.obtener_historial_ejecuciones()
        except Exception:
            rows = []
        for row in rows:
            tag = "ok" if row.get("estado") == "COMPLETADO" else ("diff" if row.get("estado") == "REVERTIDO" else "")
            self.tree_history.insert(
                "",
                tk.END,
                values=(
                    row.get("id"),
                    row.get("tipo_ejecucion"),
                    str(row.get("fecha_inicio") or ""),
                    row.get("total_carpetas", 0),
                    row.get("total_archivos", 0),
                    row.get("estado"),
                ),
                tags=(tag,) if tag else (),
            )

    def trigger_rollback_last_batch(self):
        """Revierte estrictamente la última ejecución completada en orden cronológico inverso."""
        last_exec = self.service.mysql.obtener_ultima_ejecucion_revertible() if self.service.mysql else None
        if not last_exec:
            messagebox.showinfo("Sin Lotes Revertibles", "No hay ejecuciones activas pendientes por revertir en MySQL.", parent=self)
            return

        if not messagebox.askyesno(
            "⏪ Confirmar Rollback de Último Lote",
            f"Se revertirá únicamente la última ejecución:\n\n"
            f"• Lote ID: #{last_exec['id']}\n"
            f"• Tipo: {last_exec['tipo_ejecucion']}\n"
            f"• Carpetas afectadas: {last_exec.get('total_carpetas', 0)}\n"
            f"• Archivos afectados: {last_exec.get('total_archivos', 0)}\n\n"
            "¿Deseas restaurar los nombres y rutas exactamente al estado previo a esta ejecución?",
            icon="warning",
            parent=self,
        ):
            return

        res = self.service.ejecutar_rollback_ultima_ejecucion()
        self.reload_root_preview()
        self.refresh_execution_history()
        self._refresh_header_badge()
        if res.get("success"):
            messagebox.showinfo(
                "✔ Rollback Completado",
                f"Lote #{res.get('ejecucion_id')} revertido:\n"
                f"• Carpetas restauradas: {res.get('folders_reverted', 0)}\n"
                f"• Archivos restaurados: {res.get('files_reverted', 0)}",
                parent=self,
            )
        else:
            messagebox.showwarning(
                "Rollback con Avisos",
                f"Errores:\n" + "\n".join(res.get("errors", [res.get("error", "Error desconocido")])[:8]),
                parent=self,
            )

    def trigger_full_system_reset(self):
        """
        Revierte todos los lotes en orden cronológico inverso y limpia las 5 tablas de MySQL.
        """
        msg1 = (
            "🚨 ATENCIÓN: RESET MAESTRO DE SISTEMA Y MYSQL\n\n"
            "Esta acción realizará un restablecimiento TOTAL:\n"
            "1. Rollback por Lotes: Revertirá en orden inverso todas las ejecuciones activas en MySQL.\n"
            "2. Reinicio de MySQL: Vaciará las 5 tablas de trazabilidad e índices en MySQL.\n\n"
            "¿Estás seguro de que deseas continuar?"
        )
        if not messagebox.askyesno("🚨 Confirmar Reset Maestro (Paso 1 de 2)", msg1, icon="warning", parent=self):
            return

        clear_cache = messagebox.askyesno(
            "Caché de Inteligencia Artificial (Paso 2 de 2)",
            "¿Deseas también vaciar la caché local de análisis de Claude?",
            parent=self
        )

        res = self.service.full_system_reset(clear_cache=clear_cache)
        self.reload_root_preview()
        self.refresh_execution_history()
        self._refresh_header_badge()

        messagebox.showinfo(
            "✔ Reset Maestro Completado",
            f"El sistema ha vuelto a su estado inicial:\n\n"
            f"• Lotes revertidos físicamente: {res.get('reverted_batches', 0)}\n"
            f"• Base de datos MySQL: {res['mysql_reset']['msg']}\n"
            f"• Caché local de Claude: {'Vaciada' if res['cache_cleared'] else 'Conservada'}",
            parent=self
        )

    def rollback_selected_folder_direct(self):
        """Revierte únicamente la carpeta actualmente seleccionada en la tabla."""
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla para revertir.", parent=self)
            return

        init_folder = res.get("initial_folder_path") or res.get("folder_path")
        f_name = Path(init_folder).name

        if not messagebox.askyesno(
            "Revertir Carpeta Seleccionada",
            f"¿Deseas revertir todos los archivos de la carpeta original:\n'{f_name}'\n\n"
            "a su estado exacto antes del primer cambio?",
            parent=self
        ):
            return

        rb = self.service.rollback_folder(init_folder)
        self.reload_root_preview()
        if rb["errors"]:
            messagebox.showwarning(
                "Reversión con Avisos",
                f"Archivos restaurados: {rb['restored_count']}/{rb['total_candidates']}\n\n"
                + "\n".join(rb["errors"][:5]),
                parent=self
            )
        else:
            messagebox.showinfo(
                "Carpeta Restaurada",
                f"La carpeta '{f_name}' ({rb['restored_count']} archivos) fue restaurada intacta a su origen inicial.",
                parent=self
            )

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
        my_status = f"🐬 MySQL: {self.service.mysql.database}" if (self.service.mysql and self.service.mysql.enabled) else "MySQL: Off"
        self.lbl_api_badge.config(
            text=f"Modelo: {model} | Key: {masked} | Caché: {stats['cached_documents_on_disk']} | {my_status}"
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
            if p.exists() and p.is_dir() and PDFSelector.is_book_folder_name(p.name):
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
                    f"Pre-carga lista: {len(subfolders)} carpetas de libro detectadas. "
                    "Verifica las carátulas auto-detectadas y pulsa '🔍 Ejecutar Diagnóstico General'."
                ),
                fg="#0284c7"
            )
            self.lbl_info.config(text="(Incluye carpetas con '_' o '-'; ignora subcarpetas y BUENOS/MALOS)")
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
        tree = ttk.Treeview(parent_frame, columns=master_cols, show="headings", height=20)
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
        if tab_idx == 4:
            self.refresh_execution_history()
            return
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
            caratula_name = insp.get("caratula_filename")
            if not caratula_name:
                if c_status == "CARATULA_EMBEBIDA_VALIDA" or res.get("embedded_caratula_to_create"):
                    caratula_name = "(Auto-generar de Pág. 1)"
                elif insp.get("embedded_caratula_info"):
                    caratula_name = "(Pág. 1 compartida no válida)"
                else:
                    caratula_name = "SIN CARÁTULA"

            if c_status == "OK_CON_INFO":
                info_pgs = insp.get("info_pages", [])
                if len(info_pgs) > 1:
                    pgs_str = ", ".join(str(p) for p in info_pgs)
                    estado_hojas = f"✔ OK (Págs. {pgs_str} c/info)"
                else:
                    estado_hojas = f"✔ OK (Pág. {insp.get('info_page_num', 1)} c/info)"
            elif c_status == "CARATULA_EMBEBIDA_VALIDA":
                estado_hojas = "✔ Embebida (Pág.1 compartida)"
            elif c_status == "CARATULA_EMBEBIDA_CANDIDATA":
                estado_hojas = "🔍 Embebida (Pág.1 candidata)"
            elif c_status == "REEMPLAZABLE_HOJA_BLANCA":
                estado_hojas = f"🛠 {insp.get('pdfs_with_blank_page', 0)} PDFs c/Hoja Blanca (Info Pág. {insp.get('info_page_num')})"
            elif c_status == "TOTALMENTE_BLANCA":
                estado_hojas = "⚪ Carátula 100% en Blanco"
            elif c_status == "PDFS_SIN_CARATULA":
                estado_hojas = f"⚠ {len(insp.get('pdfs_missing_caratula', []))} PDFs sin Carátula"
            elif c_status == "SIN_ARCHIVO_CARATULA":
                if insp.get("embedded_caratula_info"):
                    estado_hojas = "✖ Sin Carátula (Pág.1 sin datos)"
                else:
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
            elif v_global == "REVISION_ARCHIVOS_DUP":
                tag = "diff"
                v_label = "📦 APARTAR (CON DUP)"
            elif v_global == "REVISION_ARCHIVOS_REG":
                tag = "diff"
                v_label = "📦 APARTAR (IRREGULARES REG)"
            elif v_global == "REVISION_DUP_Y_REG":
                tag = "diff"
                v_label = "📦 APARTAR (DUP Y REG)"
            elif v_global == "PENDIENTE_DIAGNOSTICO":
                tag = "pending"
                v_label = "⏳ Pendiente Diagnóstico"
            else:
                tag = "diff"
                v_label = f"✖ {v_global}"

            if applied_actions and v_global in (
                "REVISION_ARCHIVOS_DUP",
                "REVISION_ARCHIVOS_REG",
                "REVISION_DUP_Y_REG",
                "SIN_INFO_LIMPIAR_NOMBRE",
                "APARTAR_CARATULA_BLANCA"
            ):
                v_label += " (CORREGIDA)"

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
            elif estado == "AMBOS_EN_CEROS":
                tag = "diff"
                estado_str = "⚠ Ambos en 00 (Sin Libro)"
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

    def apply_all_single_folder(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return

        if not res.get("needs_page_replacement") and not res.get("needs_rename"):
            messagebox.showinfo(
                "Sin Correcciones Pendientes",
                "Esta carpeta no tiene correcciones físicas pendientes (hojas o renombrado)."
            )
            return

        folder_name = Path(res.get("folder_path", "")).name
        if not messagebox.askyesno(
            "Confirmar Corrección Integral de Carpeta",
            f"Se aplicarán todas las correcciones físicas pendientes a la carpeta:\n'{folder_name}'\n\n"
            "• Inserción/reemplazo de carátulas en PDFs regulares y DUP (archivos REG intactos).\n"
            "• Extracción de carátula embebida si aplica.\n"
            "• Renombrado de carpeta y PDFs (DUP preservados, REG intactos).\n\n"
            "¿Deseas continuar?"
        ):
            return

        try:
            op = self.service.execute_all_pending_corrections(res)
            self._refresh_master_table()
            self._render_single_folder_detail(res)
            self._refresh_header_badge()
            actions_str = "\n".join(f"  • {a}" for a in op.get("applied_actions", [])) or "Sin acciones adicionales."
            messagebox.showinfo(
                "Correcciones Aplicadas",
                f"Se corrigió la carpeta con éxito.\n"
                f"Archivos físicos modificados / renombrados: {op.get('modified_count', 0)}\n\n"
                f"Detalle:\n{actions_str}"
            )
        except Exception as exc:
            messagebox.showerror("Error al aplicar correcciones", str(exc))

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

    def reanalyze_selected_folder(self):
        """
        Re-analiza en tiempo real la carpeta seleccionada leyendo el estado físico actual del disco.
        """
        sel_res = self._get_selected_result()
        if not sel_res:
            messagebox.showwarning("Selección Requerida", "Selecciona una carpeta en la tabla para re-analizar.")
            return

        folder_path = sel_res.get("folder_path")
        if not folder_path or not Path(folder_path).exists():
            messagebox.showerror("Error", f"La carpeta no existe en disco: {folder_path}")
            return

        def _reanalyze_worker():
            self.lbl_status.config(text=f"Re-analizando: {Path(folder_path).name}...")
            try:
                new_diag = self.service.process_folder(folder_path, save_to_db=True)
                sel_res.clear()
                sel_res.update(new_diag)
                self.after(0, self._on_reanalyze_done, Path(folder_path).name)
            except Exception as exc:
                self.after(0, lambda e=exc: messagebox.showerror("Error al Re-analizar", str(e)))

        thread = threading.Thread(target=_reanalyze_worker, daemon=True)
        thread.start()

    def _on_reanalyze_done(self, folder_name: str):
        self._refresh_master_table()
        self._refresh_header_badge()
        self.lbl_status.config(text=f"✔ Carpeta '{folder_name}' re-analizada con éxito.")
        messagebox.showinfo("Re-análisis Completado", f"La carpeta '{folder_name}' ha sido diagnosticada nuevamente con los datos reales en disco.")

    def _run_async_batch_operation(
        self,
        op_title: str,
        targets: List[Dict[str, Any]],
        worker_func: Any,
        on_complete: Optional[Any] = None
    ):
        """
        Ejecuta cualquier lote masivo de forma 100% asíncrona en un hilo de fondo.
        Mantiene la ventana de Tkinter completamente fluida y responsiva,
        actualiza el progreso en tiempo real y permite cancelación limpia con Detener.
        """
        if not targets:
            return

        self.stop_requested = False
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_run.config(state=tk.DISABLED)

        total = len(targets)
        self.progressbar["maximum"] = total
        self.progressbar["value"] = 0

        def _worker_thread():
            processed_count = 0
            errors: List[str] = []
            results_payload = []

            for idx, r in enumerate(targets, start=1):
                if self.stop_requested:
                    break

                folder_name = Path(r.get("folder_path", "")).name
                self.after(
                    0,
                    self._update_async_status,
                    idx,
                    total,
                    f"[{idx}/{total}] Procesando: {folder_name} ({op_title})..."
                )

                try:
                    res = worker_func(r)
                    results_payload.append(res)
                    processed_count += 1
                except Exception as exc:
                    errors.append(f"{folder_name}: {exc}")

                self.after(0, self._on_async_item_finished, r, idx, total)

            self.after(
                0,
                self._on_async_batch_completed,
                op_title,
                processed_count,
                total,
                errors,
                on_complete,
                results_payload
            )

        thread = threading.Thread(target=_worker_thread, daemon=True)
        thread.start()

    def _update_async_status(self, current: int, total: int, msg: str):
        self.progressbar["value"] = current
        self.lbl_status.config(text=msg)

    def _on_async_item_finished(self, r: Dict[str, Any], current: int, total: int):
        self.progressbar["value"] = current
        self._refresh_master_table()
        self._refresh_header_badge()

    def _on_async_batch_completed(
        self,
        op_title: str,
        processed: int,
        total: int,
        errors: List[str],
        on_complete: Optional[Any],
        results_payload: List[Any]
    ):
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_run.config(state=tk.NORMAL)
        self.progressbar["value"] = total
        status_text = f"✔ {op_title} finalizada ({processed}/{total} carpetas)."
        if self.stop_requested:
            status_text += " [Detenido por el usuario]"
        if errors:
            status_text += f" ({len(errors)} error(es))"
        self.lbl_status.config(text=status_text)
        self._refresh_master_table()
        self._refresh_header_badge()

        if on_complete:
            try:
                on_complete(results_payload, errors)
            except Exception:
                pass

        summary_msg = f"{op_title} Finalizada:\n\nCarpetas procesadas: {processed} de {total}\n"
        if self.stop_requested:
            summary_msg += "⚠ Operación detenida antes de completar todo el lote.\n"
        summary_msg += f"Errores: {len(errors)}" if errors else "Sin errores."
        messagebox.showinfo(op_title, summary_msg)

    def batch_apply_all_corrections(self):
        targets = [
            r for r in self.all_folder_items
            if r.get("needs_page_replacement", False)
            or r.get("needs_rename", False)
            or r.get("embedded_caratula_to_create")
        ]
        if not targets:
            messagebox.showinfo(
                "Sin Correcciones Pendientes",
                "No hay carpetas con correcciones físicas pendientes (hojas, carátulas o renombres)."
            )
            return

        if not messagebox.askyesno(
            "Confirmar Aplicación Masiva de Correcciones",
            f"Se aplicarán en disco todas las correcciones físicas pendientes en {len(targets)} carpeta(s):\n\n"
            "  • Inserción o reemplazo de carátulas en PDFs regulares y DUP (archivos REG intactos).\n"
            "  • Extracción y creación física de carátulas embebidas.\n"
            "  • Renombrado de carpetas y PDFs (DUP preservados, REG intactos).\n\n"
            "Todos los cambios quedarán respaldados en SQLite con soporte de Rollback total.\n"
            "El proceso se ejecutará de forma asíncrona sin congelar la ventana.\n\n"
            "¿Deseas continuar?"
        ):
            return

        self._run_async_batch_operation(
            op_title="Aplicar Correcciones Físicas",
            targets=targets,
            worker_func=lambda r: self.service.execute_all_pending_corrections(r)
        )

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
            "El proceso se ejecutará de forma asíncrona sin congelar la ventana. ¿Continuar?"
        ):
            return

        self._run_async_batch_operation(
            op_title="Reemplazo Masivo de Hojas",
            targets=targets,
            worker_func=lambda r: self.service.execute_page_replacement(r)
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
            "(agregando _2, _3 solo a la carpeta en caso de colisión).\n\n"
            "El proceso se ejecutará de forma asíncrona sin congelar la ventana. ¿Continuar?"
        ):
            return

        self._run_async_batch_operation(
            op_title="Renombrado Masivo",
            targets=targets,
            worker_func=lambda r: self.service.execute_folder_and_pdfs_rename(r)
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
            "El proceso se ejecutará de forma asíncrona sin congelar la ventana. ¿Continuar?"
        ):
            return

        self._run_async_batch_operation(
            op_title="Apartado Masivo a Revisión",
            targets=targets,
            worker_func=lambda r: self.service.execute_move_to_apartados(r, apartados_dir)
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
            "Todos los movimientos quedarán registrados en SQLite con Rollback.\n"
            "El proceso se ejecutará de forma asíncrona sin congelar la ventana. ¿Continuar?"
        ):
            return

        self._run_async_batch_operation(
            op_title="Mover Carpetas Correctas",
            targets=filtered_targets,
            worker_func=lambda r: self.service.execute_move_to_validados(r, validados_dir)
        )

    def audit_congruence_async(self):
        """
        Ejecuta la auditoría de congruencia entre la memoria/BD y el disco físico en red de forma asíncrona.
        """
        if not self.all_folder_items:
            messagebox.showinfo("Sin Carpetas Cargadas", "Primero selecciona una carpeta raíz o ejecuta el diagnóstico.")
            return

        for item in self.tree_audit.get_children():
            self.tree_audit.delete(item)

        self.notebook_tabs.select(self.tab_audit)
        targets = list(self.all_folder_items)

        def _worker_audit():
            self.stop_requested = False
            self.btn_stop.config(state=tk.NORMAL)
            self.progressbar["maximum"] = len(targets)
            self.progressbar["value"] = 0

            discrepant_count = 0
            for idx, r in enumerate(targets, start=1):
                if self.stop_requested:
                    break

                res = self.service.audit_folder_congruence(r)
                if not res.get("is_congruent", True):
                    discrepant_count += 1
                    disc_str = "; ".join(res.get("discrepancies", []))
                    row_vals = (
                        idx,
                        res.get("folder_name", ""),
                        r.get("veredicto_global", ""),
                        res.get("physical_caratula") or "❌ No encontrada",
                        res.get("physical_pdfs_count", 0),
                        disc_str,
                        res.get("suggested_action", "")
                    )
                    self.after(0, self._insert_audit_row, f"audit_{idx}", row_vals)

                self.after(0, self._update_async_status, idx, len(targets), f"Auditando [{idx}/{len(targets)}]: {res.get('folder_name', '')}")

            self.after(0, self._on_audit_finished, discrepant_count, len(targets))

        thread = threading.Thread(target=_worker_audit, daemon=True)
        thread.start()

    def _insert_audit_row(self, iid: str, row_vals: tuple):
        self.tree_audit.insert("", tk.END, iid=iid, values=row_vals, tags=("diff",))

    def _on_audit_finished(self, discrepant_count: int, total: int):
        self.btn_stop.config(state=tk.DISABLED)
        self.progressbar["value"] = total
        self.notebook_tabs.tab(self.tab_audit, text=f"🔍 Auditoría de Congruencia ({discrepant_count})")
        msg = f"Auditoría Finalizada: {discrepant_count} discrepancia(s) detectada(s) de {total} carpetas."
        self.lbl_status.config(text=msg)
        self.lbl_audit_summary.config(
            text=f"Total Carpetas Auditadas: {total} | Discrepancias Físicas / Memoria: {discrepant_count}"
        )
        if discrepant_count > 0:
            messagebox.showwarning(
                "Discrepancias Detectadas",
                f"Se detectaron {discrepant_count} carpetas con discrepancias entre la memoria/BD y el disco físico.\n\n"
                "Revisa la tabla y presiona '⚡ Resincronizar Todo con Disco' para alinearlas con la realidad física."
            )
        else:
            messagebox.showinfo("Congruencia Total", "¡Excelente! Todas las carpetas coinciden al 100% con el disco físico.")

    def resync_with_disk_confirmed(self):
        """
        Muestra confirmación previa con el desglose de cambios antes de resincronizar la memoria y BD con el disco.
        """
        if not self.all_folder_items:
            messagebox.showinfo("Sin Carpetas", "No hay carpetas cargadas para resincronizar.")
            return

        targets_to_resync = []
        for r in self.all_folder_items:
            audit_res = self.service.audit_folder_congruence(r)
            if not audit_res.get("is_congruent", True):
                targets_to_resync.append((r, audit_res))

        if not targets_to_resync:
            messagebox.showinfo("Todo en Orden", "No se detectaron discrepancias que requieran resincronización.")
            return

        preview_lines = []
        for r, a_res in targets_to_resync[:8]:
            preview_lines.append(f" • {a_res['folder_name']}: {a_res['suggested_action']}")
        if len(targets_to_resync) > 8:
            preview_lines.append(f" • ... y {len(targets_to_resync) - 8} carpeta(s) más.")

        preview_text = "\n".join(preview_lines)

        if not messagebox.askyesno(
            "Confirmar Resincronización con Disco Físico",
            f"Se detectaron {len(targets_to_resync)} carpetas desincronizadas.\n\n"
            f"Resumen de acciones a aplicar en memoria y BD:\n"
            f"{preview_text}\n\n"
            "¿Deseas aplicar esta resincronización ahora mismo?"
        ):
            return

        targets = [item[0] for item in targets_to_resync]
        self._run_async_batch_operation(
            op_title="Resincronizar con Disco",
            targets=targets,
            worker_func=lambda r: self.service.resync_folder_with_physical_disk(r),
            on_complete=lambda res, err: self.audit_congruence_async()
        )


def launch_gui():
    if "--self-test" in sys.argv:
        svc = FolderOCRService()
        out_check = Path(sys.executable).resolve().parent / "self_test_ok.txt"
        out_check.write_text(
            f"OK: ClaudeVisionEngine + CacheManager + MySQLManager + CaratulaInspector + FileOperations "
            f"initialized (model={svc.settings.get('model')}, mysql={svc.mysql.database}).",
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
