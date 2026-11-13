"""
Interfaz Gráfica (GUI) para Diagnóstico General, Autocorrección de Carátula, Renombrado,
Auditoría 100% MySQL y Herramienta de Rollback Atómico con Claude Sonnet 5.
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



class ProgressModalDialog(tk.Toplevel):
    """
    Ventana modal de bloqueo estricto con barra de progreso en tiempo real.
    Inhabilita la ventana principal e impide cualquier clic o acción concurrente
    mientras se ejecutan tareas críticas (Paso 1, Paso 2, Rollback).
    """
    def __init__(self, parent: tk.Tk, title: str = "Operación en Proceso", initial_msg: str = "Iniciando operación..."):
        super().__init__(parent)
        self.parent_win = parent
        self.title(title)
        self.geometry("540x190")
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # Impedir cierre accidental con 'X'
        
        container = ttk.Frame(self, padding=20)
        container.pack(fill=tk.BOTH, expand=True)

        self.lbl_title = ttk.Label(container, text=title, font=("Segoe UI", 11, "bold"), foreground="#0369a1")
        self.lbl_title.pack(anchor=tk.W, pady=(0, 6))

        self.lbl_status = ttk.Label(container, text=initial_msg, font=("Segoe UI", 9), wraplength=500)
        self.lbl_status.pack(anchor=tk.W, pady=(0, 10))

        self.progress_bar = ttk.Progressbar(container, orient=tk.HORIZONTAL, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=(0, 8))

        self.lbl_counter = ttk.Label(container, text="0%", font=("Segoe UI", 9, "bold"), foreground="#475569")
        self.lbl_counter.pack(anchor=tk.E)

        try:
            self.grab_set()
            self.focus_set()
        except Exception:
            pass
        self.update_idletasks()

    def update_progress(self, current: int, total: int, message: str = ""):
        if total > 0:
            pct = max(0, min(100, int((current / total) * 100)))
            self.progress_bar["maximum"] = total
            self.progress_bar["value"] = current
            self.lbl_counter.config(text=f"{current:,} de {total:,} ({pct}%)")
        else:
            self.progress_bar["mode"] = "indeterminate"
            self.progress_bar.start(10)
            self.lbl_counter.config(text="Procesando...")

        if message:
            self.lbl_status.config(text=message)
        self.update_idletasks()

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class MySQLHistoryAndRollbackDialog(tk.Toplevel):
    """
    Centro de Historial de Lotes y Rollback LIFO 100% MySQL (trazabilidad_selloslibros).
    Permite auditar cada ejecución (Paso 1, Diagnóstico, Apartados) y revertir
    atómicamente en orden cronológico inverso exacto (LIFO).
    """

    def __init__(self, parent: "OCRLibrosApp"):
        super().__init__(parent)
        self.parent_app = parent
        self.service = parent.service
        self.title("⏪ Historial de Lotes y Rollback Atómico MySQL (trazabilidad_selloslibros)")
        self.geometry("1240x720")
        self.minsize(1050, 620)
        self.transient(parent)
        self.grab_set()

        self._lotes_data: Dict[int, Dict[str, Any]] = {}
        self._build_ui()
        self.refresh_all_tabs()

    def _build_ui(self):
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(container)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        db_name = self.service.mysql.database if self.service.mysql else "trazabilidad_selloslibros"
        self.lbl_server = ttk.Label(
            top_bar,
            text=f"🐬 Servidor MySQL: 127.0.0.1:3306 | Base: {db_name} | Modo: Rollback Atómico LIFO",
            font=("Segoe UI", 9, "bold"),
            foreground="#0369a1"
        )
        self.lbl_server.pack(side=tk.LEFT)

        ttk.Button(top_bar, text="🔄 Actualizar Historial", command=self.refresh_all_tabs).pack(side=tk.RIGHT)
        ttk.Button(
            top_bar,
            text="📊 Abrir Dashboard Ejecutivo",
            style="Primary.TButton",
            command=self._open_stats_dashboard
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Pestaña 1: Lotes de Ejecución
        tab_lotes = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_lotes, text=" 📦 1. Lotes de Ejecución (ejecuciones_lotes) ")

        info_lbl = ttk.Label(
            tab_lotes,
            text=(
                "Cada acción sobre el lote de 950 carpetas genera un lote numerado e inmutable.\n"
                "La reversión se realiza de forma atómica en orden cronológico inverso (LIFO) "
                "garantizando la integridad absoluta del sistema de archivos y la invariante de 950 carpetas."
            ),
            foreground="#334155"
        )
        info_lbl.pack(anchor=tk.W, pady=(0, 8))

        btn_bar = ttk.Frame(tab_lotes)
        btn_bar.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(
            btn_bar,
            text="⏪ Revertir Último Lote (Rollback LIFO Atómico)",
            style="Primary.TButton",
            command=self._revertir_ultimo_lote
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            btn_bar,
            text="🚨 Revertir Todos los Lotes Activos (Restaurar todo al Punto Inicial)",
            style="Danger.TButton",
            command=self._revertir_todos_lotes
        ).pack(side=tk.RIGHT)

        paned_lotes = ttk.PanedWindow(tab_lotes, orient=tk.VERTICAL)
        paned_lotes.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(paned_lotes)
        paned_lotes.add(tree_frame, weight=3)

        lote_cols = ("id", "tipo", "estado", "carpetas", "archivos", "inicio", "fin")
        self.tree_lotes = ttk.Treeview(tree_frame, columns=lote_cols, show="headings", selectmode="browse")
        self.tree_lotes.heading("id", text="# Lote")
        self.tree_lotes.heading("tipo", text="Tipo de Ejecución")
        self.tree_lotes.heading("estado", text="Estado del Lote")
        self.tree_lotes.heading("carpetas", text="Carpetas Afectadas")
        self.tree_lotes.heading("archivos", text="Archivos Afectados")
        self.tree_lotes.heading("inicio", text="Fecha Inicio")
        self.tree_lotes.heading("fin", text="Fecha Fin")

        self.tree_lotes.column("id", width=65, anchor=tk.CENTER)
        self.tree_lotes.column("tipo", width=260, anchor=tk.W)
        self.tree_lotes.column("estado", width=140, anchor=tk.CENTER)
        self.tree_lotes.column("carpetas", width=140, anchor=tk.CENTER)
        self.tree_lotes.column("archivos", width=140, anchor=tk.CENTER)
        self.tree_lotes.column("inicio", width=160, anchor=tk.CENTER)
        self.tree_lotes.column("fin", width=160, anchor=tk.CENTER)

        self.tree_lotes.tag_configure("completado", background="#f0fdf4", foreground="#166534")
        self.tree_lotes.tag_configure("revertido", background="#fef2f2", foreground="#991b1b")
        self.tree_lotes.tag_configure("en_proceso", background="#fefce8", foreground="#854d0e")

        sc_l = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree_lotes.yview)
        self.tree_lotes.configure(yscrollcommand=sc_l.set)
        self.tree_lotes.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_l.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_lotes.bind("<<TreeviewSelect>>", self._on_lote_selected)

        det_frame = ttk.LabelFrame(paned_lotes, text=" Detalle Forense del Lote Seleccionado ", padding=6)
        paned_lotes.add(det_frame, weight=1)

        self.txt_lote_detail = tk.Text(det_frame, font=("Consolas", 9), height=5, wrap=tk.WORD, bg="#f8fafc")
        sc_det = ttk.Scrollbar(det_frame, orient=tk.VERTICAL, command=self.txt_lote_detail.yview)
        self.txt_lote_detail.configure(yscrollcommand=sc_det.set)
        self.txt_lote_detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_det.pack(side=tk.RIGHT, fill=tk.Y)

        # Pestaña 2: Auditoría de Movimientos de Carpetas
        tab_mc = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_mc, text=" 📁 2. Movimientos de Carpetas (auditoria_movimientos_carpetas) ")

        mc_cols = ("id", "lote", "tipo", "nombre_ant", "nombre_nvo", "fecha", "estado")
        self.tree_mc = ttk.Treeview(tab_mc, columns=mc_cols, show="headings")
        self.tree_mc.heading("id", text="ID")
        self.tree_mc.heading("lote", text="Lote")
        self.tree_mc.heading("tipo", text="Tipo Acción")
        self.tree_mc.heading("nombre_ant", text="Nombre Anterior")
        self.tree_mc.heading("nombre_nvo", text="Nombre Nuevo")
        self.tree_mc.heading("fecha", text="Fecha de Movimiento")
        self.tree_mc.heading("estado", text="Estado")

        self.tree_mc.column("id", width=55, anchor=tk.CENTER)
        self.tree_mc.column("lote", width=65, anchor=tk.CENTER)
        self.tree_mc.column("tipo", width=180, anchor=tk.W)
        self.tree_mc.column("nombre_ant", width=280, anchor=tk.W)
        self.tree_mc.column("nombre_nvo", width=280, anchor=tk.W)
        self.tree_mc.column("fecha", width=160, anchor=tk.CENTER)
        self.tree_mc.column("estado", width=110, anchor=tk.CENTER)

        sc_mc = ttk.Scrollbar(tab_mc, orient=tk.VERTICAL, command=self.tree_mc.yview)
        self.tree_mc.configure(yscrollcommand=sc_mc.set)
        self.tree_mc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_mc.pack(side=tk.RIGHT, fill=tk.Y)

        # Pestaña 3: Auditoría de Movimientos de Archivos
        tab_ma = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_ma, text=" 📄 3. Movimientos de Archivos (auditoria_movimientos_archivos) ")

        ma_cols = ("id", "lote", "tipo", "nombre_ant", "nombre_nvo", "paginas", "fecha", "estado")
        self.tree_ma = ttk.Treeview(tab_ma, columns=ma_cols, show="headings")
        self.tree_ma.heading("id", text="ID")
        self.tree_ma.heading("lote", text="Lote")
        self.tree_ma.heading("tipo", text="Tipo Acción")
        self.tree_ma.heading("nombre_ant", text="Nombre Anterior")
        self.tree_ma.heading("nombre_nvo", text="Nombre Nuevo")
        self.tree_ma.heading("paginas", text="Págs")
        self.tree_ma.heading("fecha", text="Fecha")
        self.tree_ma.heading("estado", text="Estado")

        self.tree_ma.column("id", width=55, anchor=tk.CENTER)
        self.tree_ma.column("lote", width=65, anchor=tk.CENTER)
        self.tree_ma.column("tipo", width=180, anchor=tk.W)
        self.tree_ma.column("nombre_ant", width=280, anchor=tk.W)
        self.tree_ma.column("nombre_nvo", width=280, anchor=tk.W)
        self.tree_ma.column("paginas", width=55, anchor=tk.CENTER)
        self.tree_ma.column("fecha", width=160, anchor=tk.CENTER)
        self.tree_ma.column("estado", width=110, anchor=tk.CENTER)

        sc_ma = ttk.Scrollbar(tab_ma, orient=tk.VERTICAL, command=self.tree_ma.yview)
        self.tree_ma.configure(yscrollcommand=sc_ma.set)
        self.tree_ma.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_ma.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh_all_tabs(self):
        # 1. Lotes
        for item in self.tree_lotes.get_children():
            self.tree_lotes.delete(item)
        self.txt_lote_detail.delete("1.0", tk.END)

        historial = self.service.mysql.obtener_historial_ejecuciones() if self.service.mysql else []
        self._lotes_data = {r["id"]: r for r in historial}
        for r in historial:
            st = str(r.get("estado", "DESCONOCIDO"))
            tag = "completado" if "COMPLETADO" in st else ("revertido" if "REVERTIDO" in st else "en_proceso")
            self.tree_lotes.insert(
                "", tk.END,
                values=(
                    r.get("id"),
                    r.get("tipo_ejecucion", ""),
                    st,
                    r.get("total_carpetas_afectadas", 0),
                    r.get("total_archivos_afectados", 0),
                    str(r.get("fecha_inicio") or ""),
                    str(r.get("fecha_fin") or ""),
                ),
                tags=(tag,)
            )

        # 2. Movimientos Carpetas
        for item in self.tree_mc.get_children():
            self.tree_mc.delete(item)
        mov_c = self.service.mysql.obtener_todos_movimientos_carpetas(limit=1000) if self.service.mysql else []
        for m in mov_c:
            st_l = str(m.get("estado_lote") or "")
            st_text = "REVERTIDO" if st_l == "REVERTIDO" else "ACTIVO"
            self.tree_mc.insert(
                "", tk.END,
                values=(
                    m.get("id"),
                    m.get("ejecucion_id"),
                    m.get("tipo_accion", ""),
                    m.get("nombre_anterior", ""),
                    m.get("nombre_nuevo", ""),
                    str(m.get("fecha_accion") or ""),
                    st_text,
                )
            )

        # 3. Movimientos Archivos
        for item in self.tree_ma.get_children():
            self.tree_ma.delete(item)
        mov_a = self.service.mysql.obtener_todos_movimientos_archivos(limit=1000) if self.service.mysql else []
        for m in mov_a:
            st_l = str(m.get("estado_lote") or "")
            st_text = "REVERTIDO" if st_l == "REVERTIDO" else "ACTIVO"
            self.tree_ma.insert(
                "", tk.END,
                values=(
                    m.get("id"),
                    m.get("ejecucion_id"),
                    m.get("tipo_accion", ""),
                    m.get("nombre_anterior", ""),
                    m.get("nombre_nuevo", ""),
                    m.get("cantidad_paginas", 0),
                    str(m.get("fecha_accion") or ""),
                    st_text,
                )
            )

    def _on_lote_selected(self, event=None):
        sel = self.tree_lotes.selection()
        if not sel:
            return
        lote_id = self.tree_lotes.item(sel[0], "values")[0]
        lote_data = self._lotes_data.get(int(lote_id))
        if not lote_data:
            return

        detail = (
            f"=== DETALLE FORENSE DEL LOTE #{lote_id} ===\n"
            f"Tipo de Ejecución:      {lote_data.get('tipo_ejecucion')}\n"
            f"Estado Actual:          {lote_data.get('estado')}\n"
            f"Fecha de Inicio:        {lote_data.get('fecha_inicio')}\n"
            f"Fecha de Fin:           {lote_data.get('fecha_fin')}\n"
            f"Carpetas Afectadas:     {lote_data.get('total_carpetas_afectadas')}\n"
            f"Archivos Afectados:     {lote_data.get('total_archivos_afectados')}\n"
            f"Detalles Técnicos JSON:\n{lote_data.get('detalles_json')}\n"
        )
        self.txt_lote_detail.delete("1.0", tk.END)
        self.txt_lote_detail.insert(tk.END, detail)

    def _revertir_ultimo_lote(self):
        ultimo = self.service.mysql.obtener_ultima_ejecucion_revertible() if self.service.mysql else None
        if not ultimo:
            messagebox.showinfo("Sin Lotes Pendientes", "No hay ejecuciones activas pendientes por revertir en MySQL.", parent=self)
            return

        lote_id = ultimo["id"]
        tipo = ultimo["tipo_ejecucion"]
        carps = ultimo.get("total_carpetas_afectadas", 0)
        archs = ultimo.get("total_archivos_afectados", 0)

        if not messagebox.askyesno(
            "Confirmar Rollback LIFO MySQL",
            f"¿Deseas revertir atómicamente el último lote ejecutado?\n\n"
            f"• Lote ID: #{lote_id}\n"
            f"• Tipo: {tipo}\n"
            f"• Carpetas a restaurar: {carps}\n"
            f"• Archivos a restaurar: {archs}\n\n"
            "Se restaurarán los nombres y ubicaciones previas de forma atómica en disco y se marcará como REVERTIDO en MySQL.",
            parent=self
        ):
            return

        res = self.service.ejecutar_rollback_ultima_ejecucion()
        self.refresh_all_tabs()
        self.parent_app.reload_root_preview()
        if res.get("success"):
            messagebox.showinfo(
                "Rollback Exitoso",
                f"Lote #{lote_id} ({tipo}) revertido exitosamente.\n"
                f"Carpetas restauradas: {res.get('folders_reverted', 0)}\n"
                f"Archivos restaurados: {res.get('files_reverted', 0)}",
                parent=self
            )
        else:
            errs = res.get("errors", [])
            messagebox.showerror(
                "Rollback con Errores",
                f"El lote #{lote_id} presentó problemas durante la reversión:\n\n" + "\n".join(errs[:8]),
                parent=self
            )

    def _revertir_todos_lotes(self):
        if not messagebox.askyesno(
            "Confirmar Rollback TOTAL",
            "ADVERTENCIA CRÍTICA:\n\n"
            "Esta operación revertirá TODOS los lotes activos en orden cronológico inverso "
            "hasta dejar todas las carpetas en su nombre y estado original inicial.\n\n"
            "¿Deseas proceder?",
            parent=self
        ):
            return

        revertidos = 0
        while self.service.mysql and self.service.mysql.obtener_ultima_ejecucion_revertible():
            rb = self.service.mysql.revertir_ultima_ejecucion()
            if rb.get("success"):
                revertidos += 1
            else:
                messagebox.showerror("Error en Rollback Total", f"Se detuvo en lote: {rb.get('message')}", parent=self)
                break

        self.refresh_all_tabs()
        self.parent_app.reload_root_preview()
        messagebox.showinfo(
            "Rollback Total Concluido",
            f"Se revirtieron exitosamente {revertidos} lotes de ejecución en MySQL.",
            parent=self
        )

    def _open_stats_dashboard(self):
        ExecutiveTraceabilityDashboardDialog(self.parent_app)


RollbackAndDatabaseDialog = MySQLHistoryAndRollbackDialog

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

        self.comparison_view_mode = "carpetas"
        self.source_var = tk.StringVar(value="MYSQL")
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

        r_my = ttk.Radiobutton(
            top_bar,
            text="🐬 Servidor MySQL (trazabilidad_selloslibros)",
            variable=self.source_var,
            value="MYSQL",
            command=self.refresh_data
        )
        r_my.pack(side=tk.LEFT, padx=(0, 6))

        r_mem = ttk.Radiobutton(
            top_bar,
            text="📌 Sesión en Vivo (Lote en Pantalla)",
            variable=self.source_var,
            value="MEMORIA",
            command=self.refresh_data
        )
        r_mem.pack(side=tk.LEFT, padx=(0, 12))

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

        ttk.Label(filter_bar, text="Modo Vista:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_v_carp = ttk.Button(filter_bar, text="📁 Vista por Carpetas (950)", command=lambda: self._set_view_mode("carpetas"))
        self.btn_v_carp.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_v_arch = ttk.Button(filter_bar, text="📄 Vista por Archivos PDF", command=lambda: self._set_view_mode("archivos"))
        self.btn_v_arch.pack(side=tk.LEFT, padx=(0, 10))

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

    def _set_view_mode(self, mode: str):
        self.comparison_view_mode = mode
        if mode == "carpetas":
            self.tree_comp.heading("orig_name", text="Carpeta Original")
            self.tree_comp.heading("orig_folder", text="Ruta Original")
            self.tree_comp.heading("dest_name", text="Carpeta Final / Actual")
            self.tree_comp.heading("dest_folder", text="Ruta Final / Actual")
            self.tree_comp.heading("pages", text="PDFs")
        else:
            self.tree_comp.heading("orig_name", text="Archivo Original")
            self.tree_comp.heading("orig_folder", text="Carpeta Original")
            self.tree_comp.heading("dest_name", text="Archivo Final / Actual")
            self.tree_comp.heading("dest_folder", text="Carpeta Final / Actual")
            self.tree_comp.heading("pages", text="Págs")
        self.refresh_data()

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
            has_insp = bool(self.parent_app.all_folder_items and any(r.get("veredicto_global") and r.get("veredicto_global") != "PENDIENTE_DIAGNOSTICO" for r in self.parent_app.all_folder_items))
            if not has_insp:
                messagebox.showinfo(
                    "Sesión en Vivo no disponible",
                    "La 'Sesión en Vivo' requiere haber ejecutado el Paso 2 (Diagnóstico).\n\n"
                    "Conmutando automáticamente a '🐬 Servidor MySQL' para consultar el estado real auditado del lote.",
                    parent=self
                )
                self.source_var.set("MYSQL")
                source = "MYSQL"

        if source == "MYSQL":
            self.stats_data = self.service.mysql.get_executive_statistics() if self.service.mysql else {}
            self.comparison_items = self.service.mysql.obtener_matriz_trazabilidad(self.comparison_view_mode) if self.service.mysql else []
            db_label = f"MySQL Server: {self.service.mysql.database}" if self.service.mysql else "MySQL"
        else:
            self.stats_data, self.comparison_items = self._load_memory_data()
            db_label = f"Sesión en Vivo: {len(self.parent_app.all_folder_items)} carpetas"

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

    def _apply_manual_fields_to_res(self, new_code: str):
        sec = self.sec_var.get().strip()
        lib = self.lib_var.get().strip()
        tom = self.tom_var.get().strip()
        vol = self.vol_var.get().strip()
        fracc = self.fracc_var.get().strip()
        anio = self.anio_var.get().strip()

        self.res["codigo_base_sugerido"] = new_code
        self.parent_app.suggested_code_var.set(new_code)

        ocr = self.res.setdefault("ocr_consolidado", {})
        ocr["seccion"] = sec
        ocr["libro"] = lib
        ocr["tomo"] = tom
        ocr["volumen"] = vol
        ocr["fraccionamiento"] = fracc
        ocr["anio"] = anio
        ev = ocr.setdefault("evidencias_globales", [])
        ev.append(f"✏️ Corrección manual de carátula por el usuario: {new_code}")

        self.res["comparacion"] = FolderComparator.compare(self.res.get("folder_data", {}), ocr)
        self.res["needs_rename"] = True

    def _save_code(self):
        new_code = self.preview_code_var.get().strip()
        self._apply_manual_fields_to_res(new_code)
        self.parent_app._refresh_master_table()
        self.parent_app._render_single_folder_detail(self.res)
        self.destroy()

    def _rename_now(self):
        new_code = self.preview_code_var.get().strip()
        self._apply_manual_fields_to_res(new_code)
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


class DryRunPaso1Dialog(tk.Toplevel):
    """
    Simulación / Dry-Run del Paso 1:
    - 100% solo lectura (0 escrituras en disco, 0 tokens de IA).
    - Simula el renombrado por 1er PDF hasta el año (conserva '0000' tal cual; agrega _2 si hay colisión).
    - Permite buscar y filtrar (todos / a renombrar / ya correctas / omitidas / colisiones).
    - Incluye botón para aplicar el Paso 1 directamente con registro en lote MySQL y Rollback 100% atómico.
    """
    def __init__(self, parent: "OCRLibrosApp", initial_path: str = ""):
        super().__init__(parent)
        self.parent_app = parent
        self.title("🧪 Dry-Run Paso 1: Simulación de Renombrado por 1er PDF (0 Escrituras en Disco)")
        self.geometry("1100x680")
        self.minsize(900, 520)
        self.transient(parent)
        self.grab_set()

        self.plan_result: Dict[str, Any] = {}
        self.all_items: List[Dict[str, Any]] = []
        self._build_ui(initial_path)
        if self.path_var.get().strip():
            self._scan_preview()

    def _build_ui(self, initial_path: str):
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        # 1. Selector de Ruta
        top_frame = ttk.LabelFrame(container, text=" Carpeta Raíz para Simulación del Paso 1 ", padding=8)
        top_frame.pack(fill=tk.X, pady=(0, 6))

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X)
        self.path_var = tk.StringVar(value=initial_path or self.parent_app.path_var.get())
        ttk.Label(row1, text="Ruta Raíz:").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(row1, textvariable=self.path_var, font=("Consolas", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(row1, text="Examinar...", command=self._browse_dir).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row1, text="🔍 Simular Ahora (Dry-Run)", style="Primary.TButton", command=self._scan_preview).pack(side=tk.LEFT)

        # 2. Banner de Regla
        rule_banner = tk.Frame(container, bg="#eff6ff", padx=8, pady=6, relief=tk.SOLID, bd=1)
        rule_banner.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            rule_banner,
            text=(
                "💡 REGLA DEL PASO 1 (Solo Carpetas, 0 Tokens): El nombre se toma directamente del 1er PDF regular hasta el año. "
                "La delegación '01' se lee del propio PDF (no de la carpeta). Si el PDF tiene año '0000', se conserva '0000'. "
                "Colisiones se resuelven con _2, _3... Los PDFs internos NO se tocan."
            ),
            font=("Segoe UI", 9, "bold"),
            bg="#eff6ff",
            fg="#1e40af",
            justify=tk.LEFT,
            wraplength=1040
        ).pack(anchor=tk.W)

        # 3. Badges de Métricas
        metrics_frame = ttk.Frame(container)
        metrics_frame.pack(fill=tk.X, pady=(0, 6))

        self.lbl_total = tk.Label(metrics_frame, text="Total: 0", font=("Segoe UI", 9, "bold"), bg="#f1f5f9", fg="#334155", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_total.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_renombrar = tk.Label(metrics_frame, text="✏ Por Renombrar: 0", font=("Segoe UI", 9, "bold"), bg="#eff6ff", fg="#1d4ed8", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_renombrar.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_correctas = tk.Label(metrics_frame, text="✔ Ya Correctas: 0", font=("Segoe UI", 9, "bold"), bg="#f0fdf4", fg="#15803d", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_correctas.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_omitir = tk.Label(metrics_frame, text="⚠ Omitidas: 0", font=("Segoe UI", 9, "bold"), bg="#fef2f2", fg="#b91c1c", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_omitir.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_colisiones = tk.Label(metrics_frame, text="⚡ Colisiones (_N): 0", font=("Segoe UI", 9, "bold"), bg="#fffbeb", fg="#b45309", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_colisiones.pack(side=tk.LEFT)

        # 4. Filtro y Búsqueda
        filter_frame = ttk.Frame(container)
        filter_frame.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(filter_frame, text="Filtrar:").pack(side=tk.LEFT, padx=(0, 6))
        self.filter_var = tk.StringVar(value="TODOS")
        cb_filter = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=["TODOS", "SOLO_RENOMBRAR", "SOLO_CORRECTAS", "SOLO_OMITIDAS", "SOLO_COLISIONES"],
            state="readonly",
            width=20
        )
        cb_filter.pack(side=tk.LEFT, padx=(0, 14))
        cb_filter.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        ttk.Label(filter_frame, text="Buscar:").pack(side=tk.LEFT, padx=(0, 6))
        self.search_var = tk.StringVar()
        ent_search = ttk.Entry(filter_frame, textvariable=self.search_var, width=28)
        ent_search.pack(side=tk.LEFT)
        ent_search.bind("<KeyRelease>", lambda e: self._apply_filter())

        # 5. Tabla de Vista Previa (Treeview)
        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("num", "orig_name", "first_pdf", "sugg_name", "action", "motivo")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("num", text="#")
        self.tree.heading("orig_name", text="Carpeta Original")
        self.tree.heading("first_pdf", text="1er PDF Base")
        self.tree.heading("sugg_name", text="Nombre Destino (Paso 1)")
        self.tree.heading("action", text="Acción")
        self.tree.heading("motivo", text="Motivo / Detalle")

        self.tree.column("num", width=45, anchor=tk.CENTER, stretch=False)
        self.tree.column("orig_name", width=240, anchor=tk.W)
        self.tree.column("first_pdf", width=250, anchor=tk.W)
        self.tree.column("sugg_name", width=240, anchor=tk.W)
        self.tree.column("action", width=120, anchor=tk.CENTER, stretch=False)
        self.tree.column("motivo", width=220, anchor=tk.W)

        self.tree.tag_configure("RENOMBRAR", background="#eff6ff", foreground="#1e40af")
        self.tree.tag_configure("SIN_CAMBIO", background="#f0fdf4", foreground="#15803d")
        self.tree.tag_configure("OMITIR", background="#fef2f2", foreground="#b91c1c")
        self.tree.tag_configure("COLISION", background="#fffbeb", foreground="#b45309")

        scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        # 6. Barra de Botones Inferior
        bot_bar = ttk.Frame(container)
        bot_bar.pack(fill=tk.X)

        self.btn_apply = ttk.Button(
            bot_bar,
            text="▶ Aplicar Renombrado Ahora (Paso 1 en Lote con MySQL y Rollback)",
            style="Primary.TButton",
            command=self._apply_renames,
            state=tk.DISABLED
        )
        self.btn_apply.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bot_bar, text="Cerrar Vista Previa", command=self.destroy).pack(side=tk.RIGHT)

    def _browse_dir(self):
        sel = filedialog.askdirectory(title="Seleccionar Carpeta Raíz", initialdir=self.path_var.get())
        if sel:
            self.path_var.set(sel)
            self._scan_preview()

    def _scan_preview(self):
        root_path = self.path_var.get().strip()
        if not root_path or not Path(root_path).is_dir():
            messagebox.showerror("Error", "La ruta especificada no existe o no es un directorio.", parent=self)
            return

        try:
            self.plan_result = self.parent_app.service.planificar_paso1(root_path)
        except Exception as exc:
            messagebox.showerror("Error en Simulación", f"No se pudo simular el Paso 1: {exc}", parent=self)
            return

        self.all_items = self.plan_result.get("items", [])
        self.lbl_total.config(text=f"Total: {self.plan_result.get('total', 0)}")
        self.lbl_renombrar.config(text=f"✏ Por Renombrar: {self.plan_result.get('renombrar', 0)}")
        self.lbl_correctas.config(text=f"✔ Ya Correctas: {self.plan_result.get('sin_cambio', 0)}")
        self.lbl_omitir.config(text=f"⚠ Omitidas: {self.plan_result.get('omitir', 0)}")
        self.lbl_colisiones.config(text=f"⚡ Colisiones (_N): {self.plan_result.get('colisiones', 0)}")

        self._apply_filter()

        to_ren = self.plan_result.get("renombrar", 0)
        self.btn_apply.config(state=tk.NORMAL if to_ren > 0 else tk.DISABLED)

    def _apply_filter(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        f_val = self.filter_var.get()
        s_val = self.search_var.get().strip().lower()

        idx = 1
        for it in self.all_items:
            acc = it.get("accion", "")
            motivo = it.get("motivo", "")
            is_col = (acc == "RENOMBRAR" and motivo != "OK")

            if f_val == "SOLO_RENOMBRAR" and acc != "RENOMBRAR":
                continue
            if f_val == "SOLO_CORRECTAS" and acc != "SIN_CAMBIO":
                continue
            if f_val == "SOLO_OMITIDAS" and acc != "OMITIR":
                continue
            if f_val == "SOLO_COLISIONES" and not is_col:
                continue

            if s_val:
                search_blob = f"{it.get('carpeta', '')} {it.get('nuevo', '')} {it.get('pdf_base', '')} {motivo}".lower()
                if s_val not in search_blob:
                    continue

            tag = "COLISION" if is_col else acc
            self.tree.insert(
                "",
                tk.END,
                values=(
                    idx,
                    it.get("carpeta", ""),
                    it.get("pdf_base", "-"),
                    it.get("nuevo", "-") or "-",
                    acc,
                    motivo
                ),
                tags=(tag,)
            )
            idx += 1

    def _apply_renames(self):
        root_path = self.path_var.get().strip()
        to_ren = self.plan_result.get("renombrar", 0)
        if to_ren == 0:
            messagebox.showinfo("Información", "No hay carpetas por renombrar.", parent=self)
            return

        if not messagebox.askyesno(
            "Confirmar Paso 1",
            f"¿Deseas aplicar el renombrado físico a las {to_ren} carpetas?\n\n"
            f"• Se tomará el nombre del 1er PDF alfabético hasta la posición del año.\n"
            f"• Si el 1er PDF tiene año '0000', se conservará tal cual '0000'.\n"
            f"• Todo se registrará en un lote de MySQL con Rollback 100% atómico.\n"
            f"• Los PDFs internos NO se modificarán.",
            parent=self
        ):
            return

        self.destroy()
        self.parent_app.path_var.set(root_path)
        self.parent_app._execute_step1(root_path)


FolderRenamerDialog = DryRunPaso1Dialog


class DryRunPaso2Dialog(tk.Toplevel):
    """
    Simulación / Dry-Run del Paso 2 (Chequeo de Estructura Pre-IA):
    - 100% solo lectura (0 tokens de IA gastados, 0 modificaciones en disco).
    - Clasifica las carpetas en:
      1. APARTADOS: Estructura discrepante / archivos REG / diferente patrón de 7 posiciones (Paso 2A).
      2. CORRECTOS: Homogénea 100% sin archivo de carátula externa -> Carátula embebida (Paso 2B).
      3. REQUIERE_IA: Homogénea 100% con archivo de carátula externa -> Requiere análisis con Claude (Paso 2C).
    - Permite filtrar y buscar.
    - Incluye botón para iniciar el Diagnóstico General con IA (Paso 2).
    """
    def __init__(self, parent: "OCRLibrosApp", initial_path: str = ""):
        super().__init__(parent)
        self.parent_app = parent
        self.title("🧪 Dry-Run Paso 2: Chequeo de Estructura Pre-IA (0 Tokens Gastados)")
        self.geometry("1100x680")
        self.minsize(900, 520)
        self.transient(parent)
        self.grab_set()

        self.plan_result: Dict[str, Any] = {}
        self.all_items: List[Dict[str, Any]] = []
        self._build_ui(initial_path)
        if self.path_var.get().strip():
            self._scan_preview()

    def _build_ui(self, initial_path: str):
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        # 1. Selector de Ruta
        top_frame = ttk.LabelFrame(container, text=" Carpeta Raíz para Chequeo de Estructura ", padding=8)
        top_frame.pack(fill=tk.X, pady=(0, 6))

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X)
        self.path_var = tk.StringVar(value=initial_path or self.parent_app.path_var.get())
        ttk.Label(row1, text="Ruta Raíz:").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(row1, textvariable=self.path_var, font=("Consolas", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(row1, text="Examinar...", command=self._browse_dir).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row1, text="🔍 Simular Estructura (0 Tokens)", style="Primary.TButton", command=self._scan_preview).pack(side=tk.LEFT)

        # 2. Banner Informativo
        rule_banner = tk.Frame(container, bg="#eff6ff", padx=8, pady=6, relief=tk.SOLID, bd=1)
        rule_banner.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            rule_banner,
            text=(
                "💡 CHEQUEO PRE-IA (0 Tokens): "
                "Paso 2A: Si algún PDF difiere en las 7 posiciones o es REG -> Directo a Apartados. "
                "Paso 2B: Si todos coinciden y no hay archivo carátula -> Directo a Correctos (Carátula embebida). "
                "Paso 2C: Si todos coinciden y hay archivo carátula -> Requiere cotejo visual con IA."
            ),
            font=("Segoe UI", 9, "bold"),
            bg="#eff6ff",
            fg="#1e40af",
            justify=tk.LEFT,
            wraplength=1040
        ).pack(anchor=tk.W)

        # 3. Badges de Métricas
        metrics_frame = ttk.Frame(container)
        metrics_frame.pack(fill=tk.X, pady=(0, 6))

        self.lbl_total = tk.Label(metrics_frame, text="Total: 0", font=("Segoe UI", 9, "bold"), bg="#f1f5f9", fg="#334155", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_total.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_apartados = tk.Label(metrics_frame, text="📦 Directo a Apartados: 0", font=("Segoe UI", 9, "bold"), bg="#fef2f2", fg="#b91c1c", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_apartados.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_correctos = tk.Label(metrics_frame, text="✅ Directo a Correctos: 0", font=("Segoe UI", 9, "bold"), bg="#f0fdf4", fg="#15803d", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_correctos.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_requiere_ia = tk.Label(metrics_frame, text="🔍 Requiere Cotejo con IA: 0", font=("Segoe UI", 9, "bold"), bg="#eff6ff", fg="#1d4ed8", padx=8, pady=4, relief=tk.RIDGE)
        self.lbl_requiere_ia.pack(side=tk.LEFT)

        # 4. Filtro y Búsqueda
        filter_frame = ttk.Frame(container)
        filter_frame.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(filter_frame, text="Filtrar:").pack(side=tk.LEFT, padx=(0, 6))
        self.filter_var = tk.StringVar(value="TODOS")
        cb_filter = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=["TODOS", "SOLO_APARTADOS", "SOLO_CORRECTOS", "SOLO_REQUIERE_IA"],
            state="readonly",
            width=22
        )
        cb_filter.pack(side=tk.LEFT, padx=(0, 14))
        cb_filter.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        ttk.Label(filter_frame, text="Buscar:").pack(side=tk.LEFT, padx=(0, 6))
        self.search_var = tk.StringVar()
        ent_search = ttk.Entry(filter_frame, textvariable=self.search_var, width=28)
        ent_search.pack(side=tk.LEFT)
        ent_search.bind("<KeyRelease>", lambda e: self._apply_filter())

        # 5. Tabla de Vista Previa (Treeview)
        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("num", "carpeta", "total_pdfs", "caratula", "clasificacion", "accion", "motivo")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("num", text="#")
        self.tree.heading("carpeta", text="Carpeta")
        self.tree.heading("total_pdfs", text="Total PDFs")
        self.tree.heading("caratula", text="Carátula Externa")
        self.tree.heading("clasificacion", text="Clasificación")
        self.tree.heading("accion", text="Acción Recomendada")
        self.tree.heading("motivo", text="Motivo / Detalle")

        self.tree.column("num", width=45, anchor=tk.CENTER, stretch=False)
        self.tree.column("carpeta", width=250, anchor=tk.W)
        self.tree.column("total_pdfs", width=80, anchor=tk.CENTER, stretch=False)
        self.tree.column("caratula", width=110, anchor=tk.CENTER, stretch=False)
        self.tree.column("clasificacion", width=130, anchor=tk.CENTER, stretch=False)
        self.tree.column("accion", width=220, anchor=tk.W)
        self.tree.column("motivo", width=280, anchor=tk.W)

        self.tree.tag_configure("APARTADOS", background="#fef2f2", foreground="#b91c1c")
        self.tree.tag_configure("CORRECTOS", background="#f0fdf4", foreground="#15803d")
        self.tree.tag_configure("REQUIERE_IA", background="#eff6ff", foreground="#1d4ed8")

        scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        # 6. Barra de Acciones Pre-IA (0 Tokens Gastados, 100% MySQL)
        action_bar = ttk.LabelFrame(container, text=" ⚡ Acciones Pre-IA Inmediatas (0 Tokens Gastados, 100% MySQL & Rollback) ", padding=6)
        action_bar.pack(fill=tk.X, pady=(0, 6))

        ttk.Button(
            action_bar,
            text="📦 Apartar Discrepantes Ahora (0 Tokens)",
            style="Warning.TButton",
            command=self._action_apartar_discrepantes
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            action_bar,
            text="✅ Auditar Correctas en MySQL (0 Tokens)",
            style="Success.TButton",
            command=self._action_auditar_correctos
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            action_bar,
            text="📁 Mover Correctas a Validados",
            command=self._action_mover_validados
        ).pack(side=tk.LEFT, padx=(0, 6))

        # 7. Barra de Botones Inferior
        bot_bar = ttk.Frame(container)
        bot_bar.pack(fill=tk.X)

        ttk.Button(
            bot_bar,
            text="🔍 Iniciar Diagnóstico General con IA (Paso 2)",
            style="Action.TButton",
            command=self._launch_step2_analysis
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bot_bar, text="Cerrar Vista Previa", command=self.destroy).pack(side=tk.RIGHT)

    def _browse_dir(self):
        sel = filedialog.askdirectory(title="Seleccionar Carpeta Raíz", initialdir=self.path_var.get())
        if sel:
            self.path_var.set(sel)
            self._scan_preview()

    def _scan_preview(self):
        root_path = self.path_var.get().strip()
        if not root_path or not Path(root_path).is_dir():
            messagebox.showerror("Error", "La ruta especificada no existe o no es un directorio.", parent=self)
            return

        try:
            self.plan_result = self.parent_app.service.planificar_paso2_estructura(root_path)
        except Exception as exc:
            messagebox.showerror("Error en Chequeo de Estructura", f"No se pudo analizar la estructura: {exc}", parent=self)
            return

        self.all_items = self.plan_result.get("items", [])
        self.lbl_total.config(text=f"Total: {self.plan_result.get('total', 0)}")
        self.lbl_apartados.config(text=f"📦 Directo a Apartados: {self.plan_result.get('apartados', 0)}")
        self.lbl_correctos.config(text=f"✅ Directo a Correctos: {self.plan_result.get('correctos', 0)}")
        self.lbl_requiere_ia.config(text=f"🔍 Requiere Cotejo con IA: {self.plan_result.get('requiere_ia', 0)}")

        self._apply_filter()

    def _apply_filter(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        f_val = self.filter_var.get()
        s_val = self.search_var.get().strip().lower()

        idx = 1
        for it in self.all_items:
            clas = it.get("clasificacion", "")

            if f_val == "SOLO_APARTADOS" and clas != "APARTADOS":
                continue
            if f_val == "SOLO_CORRECTOS" and clas != "CORRECTOS":
                continue
            if f_val == "SOLO_REQUIERE_IA" and clas != "REQUIERE_IA":
                continue

            if s_val:
                search_blob = f"{it.get('carpeta', '')} {it.get('motivo', '')} {it.get('accion', '')}".lower()
                if s_val not in search_blob:
                    continue

            car_str = "Sí" if it.get("tiene_caratula") else "No"
            self.tree.insert(
                "",
                tk.END,
                values=(
                    idx,
                    it.get("carpeta", ""),
                    it.get("total_pdfs", 0),
                    car_str,
                    clas,
                    it.get("accion", ""),
                    it.get("motivo", "")
                ),
                tags=(clas,)
            )
            idx += 1

    def _launch_step2_analysis(self):
        root_path = self.path_var.get().strip()
        self.destroy()
        self.parent_app.path_var.set(root_path)
        self.parent_app.start_analysis()

    def _action_apartar_discrepantes(self):
        root_path = self.path_var.get().strip()
        if not root_path or not os.path.isdir(root_path):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        cant_apartados = self.plan_result.get("apartados", 0)
        if cant_apartados == 0:
            messagebox.showinfo("Paso 2A", "No hay carpetas con estructura discrepante para apartar.", parent=self)
            return

        apartados_dir = self.parent_app.apartados_var.get().strip()
        if not apartados_dir:
            self.parent_app.browse_apartados_dir()
            apartados_dir = self.parent_app.apartados_var.get().strip()
            if not apartados_dir:
                messagebox.showwarning("Falta Directorio", "Configura primero el directorio de 'Apartados'.", parent=self)
                return

        dest_preview = os.path.join(apartados_dir, "ESTRUCTURA_DISCREPANTE")
        if not messagebox.askyesno(
            "Confirmar Paso 2A: Apartar Estructura Discrepante",
            f"Se moverán {cant_apartados} carpeta(s) con estructura discrepante o archivos REG a:\n"
            f"{dest_preview}\n\n"
            "• 0 Tokens de IA consumidos (análisis de patrón de nombres)\n"
            "• Registrado en MySQL con soporte de Rollback total\n\n"
            "¿Deseas mover las carpetas ahora?",
            parent=self
        ):
            return

        try:
            self.grab_release()
        except Exception:
            pass

        def _work():
            return self.parent_app.service.ejecutar_paso2a_apartar_estructura(
                root_path, apartados_dir,
                progress_cb=lambda i, t, msg: self.parent_app.after(0, self.parent_app._update_async_status, i, t, msg)
            )

        def _done(res, err):
            try:
                self.grab_set()
            except Exception:
                pass
            if err:
                messagebox.showerror("Error en Paso 2A", f"Ocurrió un error: {err}", parent=self)
            else:
                messagebox.showinfo(
                    "Paso 2A Completado",
                    f"Se apartaron {res.get('movidas', 0)} carpeta(s) con éxito.\nLote #{res.get('ejecucion_id')} registrado en MySQL.",
                    parent=self
                )
            self._scan_preview()
            self.parent_app.reload_root_preview()
            self.parent_app.refresh_execution_history()

        self.parent_app._run_exclusive_in_thread("Paso 2A: Apartando estructura discrepante", _work, _done)

    def _action_auditar_correctos(self):
        root_path = self.path_var.get().strip()
        if not root_path or not os.path.isdir(root_path):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        cant_correctos = self.plan_result.get("correctos", 0)
        if cant_correctos == 0:
            messagebox.showinfo("Paso 2B", "No hay carpetas correctas para auditar.", parent=self)
            return

        if not messagebox.askyesno(
            "Confirmar Paso 2B: Auditar Correctas en MySQL",
            f"Se convalidarán y auditarán en MySQL {cant_correctos} carpeta(s) correctas (con carátula embebida).\n\n"
            "• Estado asignado: CONVALIDADO_CARATULA_EMBEBIDA\n"
            "• 0 Tokens de IA consumidos\n"
            "• No se mueven archivos físicamente en este paso\n\n"
            "¿Deseas registrar la convalidación en MySQL?",
            parent=self
        ):
            return

        try:
            self.grab_release()
        except Exception:
            pass

        def _work():
            return self.parent_app.service.ejecutar_paso2b_auditar_correctos(
                root_path,
                progress_cb=lambda i, t, msg: self.parent_app.after(0, self.parent_app._update_async_status, i, t, msg)
            )

        def _done(res, err):
            try:
                self.grab_set()
            except Exception:
                pass
            if err:
                messagebox.showerror("Error en Paso 2B", f"Ocurrió un error: {err}", parent=self)
            else:
                messagebox.showinfo(
                    "Paso 2B Completado",
                    f"Se auditaron y convalidaron {res.get('auditadas', 0)} carpeta(s) en MySQL.\nLote #{res.get('ejecucion_id')} registrado.",
                    parent=self
                )
            self._scan_preview()
            self.parent_app.refresh_execution_history()

        self.parent_app._run_exclusive_in_thread("Paso 2B: Auditando carpetas correctas", _work, _done)

    def _action_mover_validados(self):
        root_path = self.path_var.get().strip()
        if not root_path or not os.path.isdir(root_path):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        validados_dir = self.parent_app.validados_var.get().strip()
        if not validados_dir:
            self.parent_app.browse_validados_dir()
            validados_dir = self.parent_app.validados_var.get().strip()
            if not validados_dir:
                messagebox.showwarning("Falta Directorio", "Configura primero el directorio de 'Correctos'.", parent=self)
                return

        cant_correctos = self.plan_result.get("correctos", 0)
        if cant_correctos == 0:
            messagebox.showinfo("Paso 2C", "No hay carpetas correctas en la raíz para trasladar a Validados.", parent=self)
            return

        if not messagebox.askyesno(
            "Confirmar Paso 2C: Trasladar a Validados",
            f"Se moverán físicamente {cant_correctos} carpeta(s) correctas a:\n{validados_dir}\n\n"
            "• Deja en la raíz ÚNICAMENTE las carpetas que requieren análisis con IA\n"
            "• 0 Tokens de IA consumidos\n"
            "• Registrado en MySQL con soporte de Rollback total\n\n"
            "¿Deseas mover las carpetas ahora?",
            parent=self
        ):
            return

        try:
            self.grab_release()
        except Exception:
            pass

        def _work():
            return self.parent_app.service.ejecutar_traslado_validados(
                root_path, validados_dir,
                progress_cb=lambda i, t, msg: self.parent_app.after(0, self.parent_app._update_async_status, i, t, msg)
            )

        def _done(res, err):
            try:
                self.grab_set()
            except Exception:
                pass
            if err:
                messagebox.showerror("Error en Paso 2C", f"Ocurrió un error: {err}", parent=self)
            else:
                messagebox.showinfo(
                    "Paso 2C Completado",
                    f"Se trasladaron {res.get('movidas', 0)} carpeta(s) a Validados con éxito.\nLote #{res.get('ejecucion_id')} registrado en MySQL.",
                    parent=self
                )
            self._scan_preview()
            self.parent_app.reload_root_preview()
            self.parent_app.refresh_execution_history()

        self.parent_app._run_exclusive_in_thread("Paso 2C: Trasladando validadas a destino", _work, _done)


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
        self.title("Auditoría, Diagnóstico, Autocorrección, MySQL y Rollback de Libros Registrales - Claude Sonnet 5")
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
        self.is_busy = False
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
        tools_menu.add_command(label="🧪 Simular Paso 1 (Dry-Run 0 Escrituras)...", command=self.open_step1_dry_run_dialog)
        tools_menu.add_command(label="📁 Paso 1: Indexar en MySQL y Renombrar por 1er PDF", command=self.run_step1_index_and_rename_async)
        tools_menu.add_separator()
        tools_menu.add_command(label="🧪 Simular Estructura Pre-IA (Dry-Run 0 Tokens)...", command=self.open_step2_dry_run_dialog)
        tools_menu.add_command(label="📦 Paso 2A: Apartar Discrepantes (0 Tokens)", command=self.run_step2a_apartar_async)
        tools_menu.add_command(label="✅ Paso 2B: Auditar Correctas en MySQL (0 Tokens)", command=self.run_step2b_auditar_async)
        tools_menu.add_command(label="📁 Paso 2C: Mover Correctas a Validados", command=self.run_step2c_trasladar_validadas_async)
        tools_menu.add_command(label="🔍 Paso 2D: Diagnóstico General con IA (Restantes)", command=self.start_analysis)
        tools_menu.add_command(label="✏️ Paso 2E: Corregir y Renombrar Discrepancias (según Carátula/IA)", command=self.run_batch_rename_discrepancies_async)
        tools_menu.add_separator()
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
            text="Diagnóstico General, Autocorrección de Carátula, Renombrado, MySQL y Rollback (Claude Sonnet 5)",
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
            text="⏪ Historial y Rollback MySQL",
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
        self.top_status_frame = top_status_frame

        self.lbl_veredicto = tk.Label(
            top_status_frame,
            text="Selecciona una carpeta raíz para auto-detectar las carátulas e iniciar el Diagnóstico General...",
            font=("Segoe UI", 10, "bold"),
            bg="#f1f5f9",
            fg="#334155"
        )
        self.lbl_veredicto.pack(side=tk.LEFT)
        self.lbl_status = self.lbl_veredicto

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

        # Fila 1: Paso 1
        row_b1 = ttk.Frame(card_batch)
        row_b1.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(
            row_b1,
            text="🧪 Simular Paso 1 (Dry-Run)",
            command=self.open_step1_dry_run_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b1,
            text="📁 1. Ejecutar Renombrado (Paso 1)",
            style="Primary.TButton",
            command=self.run_step1_index_and_rename_async
        ).pack(side=tk.LEFT)

        # Fila 2: Paso 2 (Pre-IA y Diagnóstico)
        row_b2 = ttk.Frame(card_batch)
        row_b2.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(
            row_b2,
            text="🧪 Simular Estructura Pre-IA (Dry-Run 0 Tokens)",
            command=self.open_step2_dry_run_dialog
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b2,
            text="📦 2A. Apartar Discrepantes (0 Tokens)",
            style="Warning.TButton",
            command=self.run_step2a_apartar_async
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b2,
            text="✅ 2B. Auditar Correctas (0 Tokens)",
            style="Success.TButton",
            command=self.run_step2b_auditar_async
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b2,
            text="📁 2C. Mover Correctas a Validados",
            command=self.run_step2c_trasladar_validadas_async
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b2,
            text="🔍 2D. Diagnóstico IA (Restantes)",
            style="Action.TButton",
            command=self.start_analysis
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b2,
            text="✏️ 2E. Corregir Discrepancias",
            style="Primary.TButton",
            command=self.run_batch_rename_discrepancies_async
        ).pack(side=tk.LEFT)

        # Fila 3: Paso 3 (Traslados Finales)
        row_b3 = ttk.Frame(card_batch)
        row_b3.pack(fill=tk.X)
        ttk.Button(
            row_b3,
            text="✅ 3A. Mover TODAS las Correctas (COINCIDE TOTAL) a Dir. Correctos",
            style="Success.TButton",
            command=self.run_step3_move_correctos_async
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            row_b3,
            text="📦 3B. Mover TODAS las Apartadas (por Subcarpeta de Error)",
            style="Warning.TButton",
            command=self.run_step3_move_apartados_async
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
            text="⏪ Revertir Último Lote (Rollback)",
            style="Danger.TButton",
            command=self.trigger_rollback_last_batch
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
        ttk.Button(
            audit_toolbar,
            text="🧪 Verificar BD vs Disco",
            style="Info.TButton",
            command=self.verify_integrity
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
        self.open_step1_dry_run_dialog()

    def open_step1_dry_run_dialog(self):
        DryRunPaso1Dialog(self, initial_path=self.path_var.get())

    def open_step2_dry_run_dialog(self):
        DryRunPaso2Dialog(self, initial_path=self.path_var.get())

    def open_folder_separator_dialog(self):
        FolderSeparatorDialog(self, initial_path=self.path_var.get())

    def open_rollback_dialog(self):
        MySQLHistoryAndRollbackDialog(self)

    def open_manual_edit_dialog(self):
        res = self._get_selected_result()
        if not res:
            messagebox.showwarning("Atención", "Selecciona primero una carpeta de la tabla.")
            return
        ManualEditDialog(self, res)

    # ------------------------------------------------------------------
    # BLOQUEO GLOBAL MIENTRAS SE EJECUTA UNA OPERACIÓN
    # ------------------------------------------------------------------
    def _iter_widgets(self, w):
        for c in w.winfo_children():
            yield c
            yield from self._iter_widgets(c)

    def _set_busy(self, busy: bool, label: str = ""):
        """
        Bloquea de forma visible e inequívoca TODOS los controles, botones, menús y pestañas
        mientras se ejecuta un proceso en lote para evitar cualquier conflicto o condición de carrera.
        """
        if busy:
            self.is_busy = True
            try:
                self.config(cursor="watch")
            except Exception:
                pass

            # 1. Alerta visual prominente en el banner superior
            if hasattr(self, "banner_frame"):
                try:
                    self.banner_frame.config(bg="#fef3c7", relief=tk.SOLID, bd=2)
                    if hasattr(self, "top_status_frame"):
                        self.top_status_frame.config(bg="#fef3c7")
                    op_title = label.upper() if label else "PROCESO EN CURSO"
                    self.lbl_veredicto.config(
                        bg="#fef3c7",
                        fg="#92400e",
                        font=("Segoe UI", 11, "bold"),
                        text=f"⛔ PROCESO EN EJECUCIÓN: {op_title} — CONTROLES BLOQUEADOS"
                    )
                    self.lbl_info.config(
                        bg="#fef3c7",
                        fg="#b45309",
                        font=("Segoe UI", 9, "bold"),
                        text="⏳ Operación en curso. Por favor no cierres la aplicación."
                    )
                except Exception:
                    pass

            # 2. Bloquear cambio de pestañas durante la ejecución
            if hasattr(self, "notebook_tabs"):
                try:
                    self.notebook_tabs.bind("<Button-1>", lambda e: "break")
                except Exception:
                    pass

            # 3. Bloquear todos los botones (ttk y tk) excepto el de detener
            self._busy_saved_buttons = []
            for w in self._iter_widgets(self):
                if w is getattr(self, "btn_stop", None):
                    continue
                if isinstance(w, ttk.Button):
                    try:
                        if not w.instate(["disabled"]):
                            w.state(["disabled"])
                            self._busy_saved_buttons.append((w, "ttk"))
                    except Exception:
                        pass
                elif isinstance(w, tk.Button):
                    try:
                        if str(w.cget("state")) != "disabled":
                            w.config(state="disabled")
                            self._busy_saved_buttons.append((w, "tk"))
                    except Exception:
                        pass

            # 4. Deshabilitar elementos del menú
            self._busy_saved_menu = []
            try:
                menubar = self.nametowidget(self.cget("menu"))
                last = menubar.index("end")
                for i in range(0, (last or -1) + 1):
                    if menubar.type(i) != "cascade":
                        continue
                    sub = self.nametowidget(menubar.entrycget(i, "menu"))
                    sub_last = sub.index("end")
                    for j in range(0, (sub_last or -1) + 1):
                        if sub.type(j) == "command" and str(sub.entrycget(j, "state")) != "disabled":
                            sub.entryconfig(j, state="disabled")
                            self._busy_saved_menu.append((sub, j))
            except Exception:
                pass

            try:
                self.update_idletasks()
            except Exception:
                pass
        else:
            # Restaurar cursor
            try:
                self.config(cursor="")
            except Exception:
                pass

            # Restaurar banner superior
            if hasattr(self, "banner_frame"):
                try:
                    self.banner_frame.config(bg="#f1f5f9", relief=tk.GROOVE, bd=1)
                    if hasattr(self, "top_status_frame"):
                        self.top_status_frame.config(bg="#f1f5f9")
                    self.lbl_veredicto.config(
                        bg="#f1f5f9",
                        fg="#15803d",
                        font=("Segoe UI", 10, "bold"),
                        text="🟢 Sistema listo — Sin procesos en ejecución"
                    )
                    self.lbl_info.config(
                        bg="#f1f5f9",
                        fg="#475569",
                        font=("Segoe UI", 9, "bold"),
                        text=""
                    )
                except Exception:
                    pass

            # Desbloquear cambio de pestañas
            if hasattr(self, "notebook_tabs"):
                try:
                    self.notebook_tabs.unbind("<Button-1>")
                except Exception:
                    pass

            # Restaurar estado previo de los botones
            for item in getattr(self, "_busy_saved_buttons", []):
                try:
                    if isinstance(item, tuple):
                        w, btype = item
                        if btype == "ttk":
                            w.state(["!disabled"])
                        else:
                            w.config(state="normal")
                    elif isinstance(item, ttk.Button):
                        item.state(["!disabled"])
                    elif isinstance(item, tk.Button):
                        item.config(state="normal")
                except Exception:
                    pass

            # Restaurar menús
            for sub, j in getattr(self, "_busy_saved_menu", []):
                try:
                    sub.entryconfig(j, state="normal")
                except Exception:
                    pass

            self._busy_saved_buttons = []
            self._busy_saved_menu = []
            self.is_busy = False

            try:
                self.update_idletasks()
            except Exception:
                pass

    def _run_exclusive_in_thread(self, label: str, work, on_done):
        """
        Ejecuta `work()` en un hilo con la ventana bloqueada. `on_done(result, error)` corre en el hilo de la UI.
        Si ya hay otra operación en curso se rechaza (no se encola).
        """
        if getattr(self, "is_busy", False):
            messagebox.showwarning("Operación en curso", f"Ya hay una operación en ejecución. Espera a que termine.", parent=self)
            return
        self._set_busy(True, label)
        try:
            self._current_progress_modal = ProgressModalDialog(self, title=label, initial_msg=f"{label} en curso...")
        except Exception:
            self._current_progress_modal = None

        def _runner():
            res, err = None, None
            try:
                res = work()
            except Exception as exc:
                err = exc
            self.after(0, lambda: self._finish_exclusive(on_done, res, err))

        threading.Thread(target=_runner, daemon=True).start()

    def _finish_exclusive(self, on_done, res, err):
        if getattr(self, "_current_progress_modal", None):
            try:
                self._current_progress_modal.close()
            except Exception:
                pass
            self._current_progress_modal = None
        self._set_busy(False)
        on_done(res, err)

    def report_callback_exception(self, exc, val, tb):
        import traceback
        traceback.print_exception(exc, val, tb)
        try:
            messagebox.showerror("Error", f"{exc.__name__}: {val}", parent=self)
        except Exception:
            pass

    def verify_integrity(self):
        """Compara la BD contra el disco real (950 = 950) y reporta faltantes/desconocidas."""
        scan = []
        if self.path_var.get().strip():
            scan.append((self.path_var.get().strip(), 1))
        if self.validados_var.get().strip():
            scan.append((self.validados_var.get().strip(), 1))
        if self.apartados_var.get().strip():
            scan.append((self.apartados_var.get().strip(), 2))

        def _done(res, err):
            if err:
                messagebox.showerror("Verificación", f"No se pudo verificar: {err}", parent=self)
                return
            lines = [
                f"Carpetas indexadas en BD: {res['total_indexadas']}",
                f"Carpetas físicas encontradas (raíz + correctos + apartados): {res['fisicas_escaneadas']}",
                f"Esperadas por BD pero NO existen en disco: {len(res['faltantes'])}",
                f"Existen en disco pero la BD no las conoce: {len(res['desconocidas'])}",
            ]
            if res["faltantes"]:
                lines += ["", "Faltantes (máx 8):"] + res["faltantes"][:8]
            if res["desconocidas"]:
                lines += ["", "Desconocidas (máx 8):"] + res["desconocidas"][:8]
            (messagebox.showinfo if res["congruente"] else messagebox.showwarning)(
                "✔ BD y disco congruentes" if res["congruente"] else "⚠ Hay incongruencias", "\n".join(lines), parent=self
            )

        self._run_exclusive_in_thread("Verificando BD vs disco", lambda: self.service.mysql.verificar_invariante(scan), _done)

    def run_step1_index_and_rename_async(self):
        """
        Paso 1: primero una VISTA PREVIA (solo lectura) con la regla y lo que se va a renombrar;
        solo si el usuario confirma se indexa en MySQL y se renombran las CARPETAS (los PDFs no se tocan).
        """
        root_p = self.path_var.get().strip()
        if not root_p or not os.path.isdir(root_p):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        def _plan_done(plan, err):
            if err:
                messagebox.showerror("Paso 1", f"No se pudo planificar: {err}", parent=self)
                return
            sample = [i for i in plan["items"] if i["accion"] == "RENOMBRAR"][:10]
            lines = [f"  {i['carpeta']}\n     → {i['nuevo']}   (PDF base: {i['pdf_base']})" for i in sample]
            omit = [i for i in plan["items"] if i["accion"] == "OMITIR"][:5]
            msg = (
                "REGLA DEL PASO 1\n"
                "• Se toma el 1er PDF regular (orden alfabético) hasta la posición del año.\n"
                "• Delegación (posición 1): se lee directamente del 1er PDF (conservando '01').\n"
                "• Año: se toma del 1er PDF (si tiene '0000', se conserva '0000' tal cual).\n"
                "• Si ya existe ese nombre se agrega _2, _3... • Solo se renombran CARPETAS; PDFs quedan intactos.\n\n"
                f"Carpetas: {plan['total']}  |  A renombrar: {plan['renombrar']}  |  Ya correctas: {plan['sin_cambio']}  "
                f"|  Omitidas: {plan['omitir']}  |  Con sufijo _N por colisión: {plan['colisiones']}\n\n"
                + ("Ejemplos:\n" + "\n".join(lines) if lines else "No hay carpetas por renombrar.")
                + ("\n\nOmitidas (ejemplos):\n" + "\n".join(f"  {o['carpeta']}: {o['motivo']}" for o in omit) if omit else "")
                + "\n\n¿Ejecutar el Paso 1?"
            )
            if plan["renombrar"] == 0 and plan["omitir"] == 0:
                messagebox.showinfo("Paso 1", msg, parent=self)
                return
            if not messagebox.askyesno("Paso 1: Vista previa", msg, parent=self):
                return
            self._execute_step1(root_p)

        self._run_exclusive_in_thread("Calculando vista previa del Paso 1", lambda: self.service.planificar_paso1(root_p), _plan_done)

    def _execute_step1(self, root_p: str):
        def _work():
            return self.service.ejecutar_paso1_indexar_y_renombrar(
                root_p,
                progress_cb=lambda i, t, msg: self.after(0, self._update_async_status, i, t, msg),
            )

        def _done(res, err):
            if err:
                self.reload_root_preview()
                self.refresh_execution_history()
                messagebox.showerror("Error Paso 1", f"{err}\n\nRevisa la pestaña de Historial: el lote quedó registrado y puede revertirse.", parent=self)
                return
            self._on_step1_finished(res)

        self._run_exclusive_in_thread("Paso 1: indexando y renombrando", _work, _done)

    def _on_step1_finished(self, res: Dict[str, Any]):
        self.reload_root_preview()
        self.refresh_execution_history()
        self._refresh_header_badge()
        errs = res.get("errores", [])
        omit = res.get("omitidas", [])
        msg = (
            f"Paso 1 completado (Lote #{res.get('ejecucion_id')}):\n"
            f"• Carpetas totales: {res.get('total_carpetas', 0)}\n"
            f"• Nuevas indexadas en MySQL: {res.get('nuevas_indexadas', 0)}\n"
            f"• Archivos PDF indexados: {res.get('archivos_indexados', 0)}\n"
            f"• Carpetas renombradas: {res.get('renombradas', 0)}\n"
            f"• Omitidas (sin cambios): {len(omit)}"
        )
        if errs:
            msg += f"\n\nAvisos ({len(errs)}):\n" + "\n".join(errs[:5]) + "\n\nEl lote se puede revertir desde el Historial."
            messagebox.showwarning("Paso 1 con Avisos", msg, parent=self)
        else:
            messagebox.showinfo("Paso 1 Completado", msg, parent=self)

    def run_step2a_apartar_async(self, on_success=None):
        """
        Paso 2A (Pre-IA - 0 Tokens): Mueve carpetas con estructura discrepante o archivos REG
        a apartados_dir / 'ESTRUCTURA_DISCREPANTE', registrando todo en MySQL con soporte de Rollback.
        """
        root_p = self.path_var.get().strip()
        if not root_p or not os.path.isdir(root_p):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        apartados_dir = self.apartados_var.get().strip()
        if not apartados_dir:
            self.browse_apartados_dir()
            apartados_dir = self.apartados_var.get().strip()
            if not apartados_dir:
                messagebox.showwarning("Falta Directorio", "Configura primero el directorio de 'Apartados'.", parent=self)
                return

        def _plan_done(plan, err):
            if err:
                messagebox.showerror("Paso 2A", f"No se pudo analizar la estructura: {err}", parent=self)
                return

            apartados_count = plan.get("apartados", 0)
            if apartados_count == 0:
                messagebox.showinfo("Paso 2A: Apartar Estructura", "No se detectaron carpetas con estructura discrepante ni archivos REG para apartar.", parent=self)
                return

            sample = [i for i in plan.get("items", []) if i.get("clasificacion") == "APARTADOS"][:8]
            lines = [f"  • {i['carpeta']} -> {i['motivo']}" for i in sample]
            dest_preview = os.path.join(apartados_dir, "ESTRUCTURA_DISCREPANTE")
            msg = (
                "PASO 2A: APARTAR ESTRUCTURA DISCREPANTE (0 TOKENS)\n\n"
                f"• Carpetas discrepantes detectadas: {apartados_count}\n"
                f"• Destino: {dest_preview}\n"
                "• 0 Tokens de IA consumidos (filtro estructural puro).\n"
                "• Se registrará en MySQL con soporte de Rollback total.\n\n"
                + ("Ejemplos:\n" + "\n".join(lines) if lines else "")
                + "\n\n¿Deseas mover las carpetas ahora?"
            )
            if not messagebox.askyesno("Confirmar Paso 2A", msg, parent=self):
                return

            def _work():
                return self.service.ejecutar_paso2a_apartar_estructura(
                    root_p, apartados_dir,
                    progress_cb=lambda i, t, m: self.after(0, self._update_async_status, i, t, m)
                )

            def _done(res, w_err):
                if w_err:
                    self.reload_root_preview()
                    self.refresh_execution_history()
                    messagebox.showerror("Error Paso 2A", f"{w_err}\n\nSi se movieron carpetas, el lote está registrado y puede revertirse.", parent=self)
                    return
                self.reload_root_preview()
                self.refresh_execution_history()
                self._refresh_header_badge()
                messagebox.showinfo(
                    "Paso 2A Completado",
                    f"Paso 2A finalizado (Lote #{res.get('ejecucion_id')}):\n"
                    f"• Carpetas movidas a ESTRUCTURA_DISCREPANTE: {res.get('movidas', 0)}\n\n"
                    "Puedes revertir esta operación en cualquier momento desde el Historial.",
                    parent=self
                )
                if on_success:
                    try:
                        on_success()
                    except Exception:
                        pass

            self._run_exclusive_in_thread("Paso 2A: Apartando estructura discrepante", _work, _done)

        self._run_exclusive_in_thread("Analizando estructura de carpetas (Paso 2A)", lambda: self.service.planificar_paso2_estructura(root_p), _plan_done)

    def run_step2b_auditar_async(self, on_success=None):
        """
        Paso 2B (Pre-IA - 0 Tokens): Convalida y audita en MySQL carpetas homogéneas con carátula embebida
        sin moverlas físicamente aún.
        """
        root_p = self.path_var.get().strip()
        if not root_p or not os.path.isdir(root_p):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        def _plan_done(plan, err):
            if err:
                messagebox.showerror("Paso 2B", f"No se pudo analizar la estructura: {err}", parent=self)
                return

            correctos_count = plan.get("correctos", 0)
            if correctos_count == 0:
                messagebox.showinfo("Paso 2B: Auditar Correctas", "No se detectaron carpetas correctas homogéneas para auditar.", parent=self)
                return

            sample = [i for i in plan.get("items", []) if i.get("clasificacion") == "CORRECTOS"][:8]
            lines = [f"  • {i['carpeta']} ({i['total_pdfs']} PDFs)" for i in sample]
            msg = (
                "PASO 2B: AUDITAR CORRECTAS EN MYSQL (0 TOKENS)\n\n"
                f"• Carpetas homogéneas detectadas: {correctos_count}\n"
                "• Estado a registrar: CONVALIDADO_CARATULA_EMBEBIDA\n"
                "• 0 Tokens de IA consumidos.\n"
                "• No se moverán físicamente en este paso (auditoría en MySQL).\n\n"
                + ("Ejemplos:\n" + "\n".join(lines) if lines else "")
                + "\n\n¿Deseas registrar la auditoría en MySQL?"
            )
            if not messagebox.askyesno("Confirmar Paso 2B", msg, parent=self):
                return

            def _work():
                return self.service.ejecutar_paso2b_auditar_correctos(
                    root_p,
                    progress_cb=lambda i, t, m: self.after(0, self._update_async_status, i, t, m)
                )

            def _done(res, w_err):
                if w_err:
                    self.refresh_execution_history()
                    messagebox.showerror("Error Paso 2B", f"{w_err}", parent=self)
                    return
                self.refresh_execution_history()
                self._refresh_header_badge()
                messagebox.showinfo(
                    "Paso 2B Completado",
                    f"Paso 2B finalizado (Lote #{res.get('ejecucion_id')}):\n"
                    f"• Carpetas convalidadas en MySQL: {res.get('auditadas', 0)}\n"
                    "• Estado: CONVALIDADO_CARATULA_EMBEBIDA",
                    parent=self
                )
                if on_success:
                    try:
                        on_success()
                    except Exception:
                        pass

            self._run_exclusive_in_thread("Paso 2B: Auditando carpetas correctas", _work, _done)

        self._run_exclusive_in_thread("Analizando estructura de carpetas (Paso 2B)", lambda: self.service.planificar_paso2_estructura(root_p), _plan_done)

    def run_step2c_trasladar_validadas_async(self, on_success=None):
        """
        Paso 2C (0 Tokens): Mueve físicamente las carpetas correctas (homogéneas) a validados_dir,
        dejando en la raíz ÚNICAMENTE las carpetas que requieren análisis con IA.
        """
        root_p = self.path_var.get().strip()
        if not root_p or not os.path.isdir(root_p):
            messagebox.showwarning("Atención", "Selecciona una carpeta raíz válida primero.", parent=self)
            return

        validados_dir = self.validados_var.get().strip()
        if not validados_dir:
            self.browse_validados_dir()
            validados_dir = self.validados_var.get().strip()
            if not validados_dir:
                messagebox.showwarning("Falta Directorio", "Configura primero el directorio de 'Correctos'.", parent=self)
                return

        def _plan_done(plan, err):
            if err:
                messagebox.showerror("Paso 2C", f"No se pudo analizar la estructura: {err}", parent=self)
                return

            correctos_count = plan.get("correctos", 0)
            if correctos_count == 0:
                messagebox.showinfo("Paso 2C: Trasladar Validadas", "No se detectaron carpetas correctas en la raíz para trasladar.", parent=self)
                return

            sample = [i for i in plan.get("items", []) if i.get("clasificacion") == "CORRECTOS"][:8]
            lines = [f"  • {i['carpeta']} ({i['total_pdfs']} PDFs)" for i in sample]
            msg = (
                "PASO 2C: TRASLADAR CARPETAS CORRECTAS A VALIDADOS\n\n"
                f"• Carpetas correctas a mover: {correctos_count}\n"
                f"• Destino: {validados_dir}\n"
                "• Al moverlas, en la raíz quedarán ÚNICAMENTE las carpetas que requieren IA.\n"
                "• 0 Tokens de IA consumidos.\n"
                "• Registrado en MySQL con soporte de Rollback total.\n\n"
                + ("Ejemplos:\n" + "\n".join(lines) if lines else "")
                + "\n\n¿Deseas trasladar las carpetas ahora?"
            )
            if not messagebox.askyesno("Confirmar Paso 2C", msg, parent=self):
                return

            def _work():
                return self.service.ejecutar_traslado_validados(
                    root_p, validados_dir,
                    progress_cb=lambda i, t, m: self.after(0, self._update_async_status, i, t, m)
                )

            def _done(res, w_err):
                if w_err:
                    self.reload_root_preview()
                    self.refresh_execution_history()
                    messagebox.showerror("Error Paso 2C", f"{w_err}\n\nSi se movieron carpetas, el lote está registrado y puede revertirse.", parent=self)
                    return
                self.reload_root_preview()
                self.refresh_execution_history()
                self._refresh_header_badge()
                messagebox.showinfo(
                    "Paso 2C Completado",
                    f"Paso 2C finalizado (Lote #{res.get('ejecucion_id')}):\n"
                    f"• Carpetas trasladadas a Validados: {res.get('movidas', 0)}\n\n"
                    "Puedes revertir este traslado en cualquier momento desde el Historial.",
                    parent=self
                )
                if on_success:
                    try:
                        on_success()
                    except Exception:
                        pass

            self._run_exclusive_in_thread("Paso 2C: Trasladando validadas a destino", _work, _done)

        self._run_exclusive_in_thread("Analizando estructura de carpetas (Paso 2C)", lambda: self.service.planificar_paso2_estructura(root_p), _plan_done)

    def run_batch_rename_discrepancies_async(self):
        """
        Paso 2E: Renombra en lote todas las carpetas con discrepancia (o ceros)
        cuyos datos fueron detectados válidamente en la carátula por la IA o corregidos por el usuario.
        Al renombrarse, pasan a COINCIDE_TOTAL (Listas/Correctas) para ser trasladadas a Validados.
        Todo queda respaldado en MySQL con soporte de Rollback.
        """
        targets = [
            r for r in self.all_folder_items
            if r.get("needs_rename", False)
            and r.get("codigo_base_sugerido")
            and r.get("veredicto_global") not in (
                "PENDIENTE_DIAGNOSTICO", "APARTAR_CARATULA_BLANCA", "SIN_ARCHIVO_CARATULA",
                "DISCREPANCIA_SIN_CARATULA", "ERROR", "MOVIDO_A_APARTADOS", "MOVIDO_A_CORRECTOS"
            )
        ]
        if not targets:
            messagebox.showinfo(
                "Sin Discrepancias para Corregir",
                "No hay carpetas diagnosticadas con cambio de nombre sugerido pendiente de aplicar.",
                parent=self
            )
            return

        sample = targets[:8]
        lines = [f"  • {Path(r.get('folder_path', '')).name} -> {r.get('codigo_base_sugerido')}" for r in sample]
        msg = (
            "PASO 2E: CORREGIR Y RENOMBRAR DISCREPANCIAS\n\n"
            f"Se aplicará el código validado de la carátula a {len(targets)} carpeta(s) y sus PDFs internos.\n\n"
            "• Al renombrarse, pasarán a '✔ Listas / Correctas (COINCIDE TOTAL)' para ir a Validados.\n"
            "• Las carpetas con archivos DUP o REG seguirán requiriendo revisión.\n"
            "• 100% Auditado en MySQL con soporte de Rollback total.\n\n"
            + ("Ejemplos:\n" + "\n".join(lines) if lines else "")
            + ("\n  ... y más." if len(targets) > 8 else "")
            + "\n\n¿Deseas aplicar las correcciones ahora?"
        )
        if not messagebox.askyesno("Confirmar Corrección de Discrepancias", msg, parent=self):
            return

        def _work():
            ejec_id = self.mysql_manager.crear_ejecucion_lote(
                tipo_ejecucion="CORRECCION_DISCREPANCIAS_RENOMBRADO",
                detalles={"total_candidatos": len(targets)}
            ) if (self.mysql_manager and self.mysql_manager.enabled) else None

            renombradas = 0
            archivos_totales = 0
            errores: List[str] = []

            for idx, r in enumerate(targets, start=1):
                fname = Path(r.get("folder_path", "")).name
                self.after(0, self._update_async_status, idx, len(targets), f"Corrigiendo [{idx}/{len(targets)}]: {fname}")
                try:
                    res = self.service.execute_folder_and_pdfs_rename(r, ejecucion_id=ejec_id)
                    renombradas += 1
                    archivos_totales += res.get("files_renamed_count", 0)
                except Exception as exc:
                    errores.append(f"{fname}: {exc}")

            if ejec_id is not None:
                self.mysql_manager.finalizar_ejecucion_lote(
                    ejecucion_id=ejec_id,
                    total_carpetas=renombradas,
                    total_archivos=archivos_totales,
                    estado="COMPLETADO" if not errores else "COMPLETADO_CON_ERRORES",
                    detalles={"errores": errores[:20]} if errores else None
                )

            return {
                "ejecucion_id": ejec_id,
                "renombradas": renombradas,
                "archivos": archivos_totales,
                "errores": errores
            }

        def _done(res, err):
            self._refresh_master_table()
            self.refresh_execution_history()
            self._refresh_header_badge()
            if err:
                messagebox.showerror("Error al Corregir Discrepancias", f"{err}", parent=self)
                return
            msg_ok = (
                f"Corrección de Discrepancias Finalizada (Lote #{res.get('ejecucion_id')}):\n\n"
                f"• Carpetas corregidas y renombradas: {res.get('renombradas', 0)} de {len(targets)}\n"
                f"• Archivos PDF actualizados: {res.get('archivos', 0)}\n\n"
                "Las carpetas corregidas han pasado a '✔ Listas / Correctas (COINCIDE TOTAL)' "
                "y están listas para ser trasladadas a Dir. Correctos con el Paso 3."
            )
            if res.get("errores"):
                msg_ok += f"\n\nAvisos ({len(res['errores'])}):\n" + "\n".join(res["errores"][:5])
                messagebox.showwarning("Finalizado con Avisos", msg_ok, parent=self)
            else:
                messagebox.showinfo("Discrepancias Corregidas", msg_ok, parent=self)

        self._run_exclusive_in_thread("Corrigiendo y renombrando discrepancias", _work, _done)

    def _run_step3(self, mode: str, target_dir: str, label: str):
        def _work():
            return self.service.ejecutar_paso3_trasladar_lote(
                self.all_folder_items, target_dir, mode=mode,
                progress_cb=lambda i, t, msg: self.after(0, self._update_async_status, i, t, msg),
            )

        def _done(res, err):
            if err:
                self.reload_root_preview()
                self.refresh_execution_history()
                messagebox.showerror("Error Paso 3", f"{err}\n\nSi se movió algo, el lote está en el Historial y puede revertirse.", parent=self)
                return
            self._on_step3_finished(label, res)

        self._run_exclusive_in_thread(f"Paso 3: moviendo a {label}", _work, _done)

    def run_step3_move_correctos_async(self):
        """Paso 3B: mueve las carpetas COINCIDE_TOTAL a Dir. Correctos en un lote MySQL (con rollback)."""
        validados_dir = self.validados_var.get().strip()
        if not validados_dir:
            messagebox.showwarning("Atención", "Configura primero el directorio de 'Correctos'.", parent=self)
            return
        targets = [r for r in self.all_folder_items if r.get("veredicto_global") == "COINCIDE_TOTAL" and not r.get("needs_apartar")]
        if not targets:
            messagebox.showinfo("Sin Pendientes", "No hay carpetas listas (COINCIDE_TOTAL) para mover a Correctos.", parent=self)
            return
        if not messagebox.askyesno("Paso 3B: Mover Correctas", f"Se moverán {len(targets)} carpeta(s) a:\n{validados_dir}\n\nQuedará un lote revertible. ¿Continuar?", parent=self):
            return
        self._run_step3("CORRECTOS", validados_dir, "Correctos")

    def run_step3_move_apartados_async(self):
        """Paso 3A: mueve las carpetas marcadas para apartar a Dir. Apartados en un lote MySQL (con rollback)."""
        apartados_dir = self.apartados_var.get().strip()
        if not apartados_dir:
            messagebox.showwarning("Atención", "Configura primero el directorio de 'Apartados'.", parent=self)
            return
        targets = [r for r in self.all_folder_items if r.get("needs_apartar")]
        if not targets:
            messagebox.showinfo("Sin Pendientes", "No hay carpetas marcadas para Apartados.", parent=self)
            return
        if not messagebox.askyesno("Paso 3A: Mover Apartadas", f"Se moverán {len(targets)} carpeta(s) a:\n{apartados_dir}\n\nQuedará un lote revertible. ¿Continuar?", parent=self):
            return
        self._run_step3("APARTADOS", apartados_dir, "Apartados")

    def _on_step3_finished(self, dest_label: str, res: Dict[str, Any]):
        self.reload_root_preview()
        self._refresh_master_table()
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

    def _refresh_all_tabs(self):
        """Refresca la tabla maestra y los contadores de todas las pestañas."""
        self._refresh_master_table()

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
        """Revierte estrictamente el último lote pendiente (orden cronológico inverso), con la ventana bloqueada."""
        try:
            last_exec = self.service.mysql.obtener_ultima_ejecucion_revertible()
        except Exception as exc:
            messagebox.showerror("Rollback", f"No se pudo consultar MySQL: {exc}", parent=self)
            return
        if not last_exec:
            messagebox.showinfo("Sin Lotes Revertibles", "No hay ejecuciones pendientes por revertir en MySQL.", parent=self)
            return

        if not messagebox.askyesno(
            "⏪ Confirmar Rollback de Último Lote",
            f"Se revertirá ÚNICAMENTE el último lote pendiente:\n\n"
            f"• Lote ID: #{last_exec['id']}\n"
            f"• Tipo: {last_exec['tipo_ejecucion']}\n"
            f"• Estado: {last_exec['estado']}\n"
            f"• Carpetas afectadas: {last_exec.get('total_carpetas_afectadas', 0)}\n"
            f"• Archivos afectados: {last_exec.get('total_archivos_afectados', 0)}\n\n"
            "Se restauran nombres y ubicaciones a como estaban justo antes de ese lote.",
            icon="warning",
            parent=self,
        ):
            return

        def _done(res, err):
            self.reload_root_preview()
            self.refresh_execution_history()
            self._refresh_header_badge()
            if err:
                messagebox.showerror("Rollback", f"{err}", parent=self)
                return
            if res.get("success"):
                messagebox.showinfo(
                    "✔ Rollback Completado",
                    f"Lote #{res.get('ejecucion_id')} ({res.get('tipo_ejecucion')}) revertido:\n"
                    f"• Carpetas restauradas: {res.get('folders_reverted', 0)}\n"
                    f"• Archivos restaurados: {res.get('files_reverted', 0)}",
                    parent=self,
                )
            else:
                messagebox.showwarning(
                    "⚠ Rollback INCOMPLETO",
                    f"{res.get('message', '')}\n\nProblemas:\n" + "\n".join(res.get("errors", [])[:10]) +
                    "\n\nEl lote sigue pendiente (REVERSION_PARCIAL). Corrige el conflicto y vuelve a pulsar Revertir: "
                    "es seguro repetirlo, lo ya restaurado se omite.",
                    parent=self,
                )

        self._run_exclusive_in_thread("Rollback de lote", self.service.ejecutar_rollback_ultima_ejecucion, _done)

    def trigger_full_system_reset(self):
        """Revierte TODOS los lotes en orden inverso y, solo si todo salió bien, limpia las tablas de MySQL."""
        msg1 = (
            "🚨 RESET MAESTRO\n\n"
            "1. Revierte en orden inverso TODOS los lotes pendientes.\n"
            "2. SOLO si todo se revirtió sin problemas, vacía las 5 tablas de MySQL.\n"
            "   (Si algo falla, la BD se conserva para poder reintentar.)\n\n¿Continuar?"
        )
        if not messagebox.askyesno("🚨 Confirmar Reset Maestro (1 de 2)", msg1, icon="warning", parent=self):
            return
        clear_cache = messagebox.askyesno("Caché de IA (2 de 2)", "¿Vaciar también la caché local de análisis de Claude?", parent=self)

        def _done(res, err):
            self.reload_root_preview()
            self.refresh_execution_history()
            self._refresh_header_badge()
            if err:
                messagebox.showerror("Reset Maestro", f"{err}", parent=self)
                return
            txt = (
                f"• Lotes revertidos: {res.get('reverted_batches', 0)}\n"
                f"• Base de datos MySQL: {res['mysql_reset']['msg']}\n"
                f"• Caché local de Claude: {'Vaciada' if res['cache_cleared'] else 'Conservada'}"
            )
            if res.get("rollback_errors"):
                txt += "\n\nProblemas en rollback:\n" + "\n".join(res["rollback_errors"][:8])
                messagebox.showwarning("Reset Maestro INCOMPLETO", txt, parent=self)
            else:
                messagebox.showinfo("✔ Reset Maestro Completado", txt, parent=self)

        self._run_exclusive_in_thread("Reset maestro", lambda: self.service.full_system_reset(clear_cache=clear_cache), _done)

    def rollback_selected_folder_direct(self):
        """Obsoleto: el rollback es SOLO por lote (orden cronológico inverso)."""
        self.trigger_rollback_last_batch()

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

        if getattr(self, "is_busy", False):
            messagebox.showwarning("Operación en curso", "Espera a que termine la operación actual.")
            return
        self._set_busy(True, "Paso 2: diagnóstico")
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
                        "• Todas las carpetas procesadas hasta este punto han quedado guardadas en MySQL y en la caché local.\n"
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
        self.is_busy = False
        self._set_busy(False)
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
                    f"✔ Diagnóstico General finalizado ({processed} carpetas registradas en MySQL). "
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
        """Obsoleto: el rollback es SOLO por lote (orden cronológico inverso)."""
        self.trigger_rollback_last_batch()

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
            "Se guardará respaldo automático para Rollback y se registrará en MySQL. ¿Continuar?"
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
            "¿Deseas aplicar el renombre y registrarlo en MySQL con Rollback?"
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
            "(Se limpiará el nombre hasta el año y se registrará en MySQL con soporte de Rollback)."
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
        if getattr(self, "is_busy", False):
            messagebox.showwarning("Operación en curso", "Espera a que termine la operación actual.")
            return
        self._set_busy(True, op_title)

        self.stop_requested = False
        self.btn_stop.config(state=tk.NORMAL)

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
        if getattr(self, "_current_progress_modal", None):
            try:
                self._current_progress_modal.update_progress(current, total, msg)
            except Exception:
                pass

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
        self._set_busy(False)
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
            "Todos los cambios quedarán respaldados en MySQL con soporte de Rollback total.\n"
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
        self.run_batch_rename_discrepancies_async()

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
            "Todos los movimientos quedarán registrados en MySQL con Rollback.\n"
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
