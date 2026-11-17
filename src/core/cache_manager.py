import json
import hashlib
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from src.core.settings_manager import SettingsManager
from src.core.claude_engine import ClaudeVisionEngine

class CacheManager:
    """
    Sistema de Caché de Doble Nivel para minimizar el gasto de tokens en Claude API:
    1. Caché Local en Disco (SHA-256 de imágenes/documentos): Si un PDF o página ya fue
       analizado previamente, devuelve el resultado de inmediato con costo $0 (0 tokens).
    2. Registro de métricas de Prompt Caching de Anthropic (cache_read_input_tokens vs
       cache_creation_input_tokens) para visualizar el ahorro en la interfaz.
    """
    _lock = threading.Lock()

    def __init__(self):
        self.cache_dir = SettingsManager.get_base_dir() / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "claude_vision_cache.json"
        self._data: Dict[str, Any] = self._load_disk_cache()

        self.folder_cache_file = self.cache_dir / "folder_diagnoses_cache.json"
        self._folder_cache: Dict[str, Any] = self._load_folder_disk_cache()

        # Estadísticas de la sesión y acumuladas
        self.stats = {
            "local_cache_hits": 0,
            "api_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0
        }

    def _load_disk_cache(self) -> Dict[str, Any]:
        if self.cache_file.exists():
            try:
                raw_cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if isinstance(raw_cache, dict):
                    any_repaired = False
                    for k, v in list(raw_cache.items()):
                        repaired_v, changed = ClaudeVisionEngine.sanitize_cached_doc(v)
                        if changed:
                            raw_cache[k] = repaired_v
                            any_repaired = True
                    if any_repaired:
                        try:
                            self.cache_file.write_text(
                                json.dumps(raw_cache, indent=2, ensure_ascii=False),
                                encoding="utf-8"
                            )
                        except Exception:
                            pass
                    return raw_cache
            except Exception:
                return {}
        return {}

    def _save_disk_cache(self) -> None:
        try:
            self.cache_file.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception:
            pass

    def _load_folder_disk_cache(self) -> Dict[str, Any]:
        if self.folder_cache_file.exists():
            try:
                raw_cache = json.loads(self.folder_cache_file.read_text(encoding="utf-8"))
                if isinstance(raw_cache, dict):
                    return raw_cache
            except Exception:
                return {}
        return {}

    def _save_folder_disk_cache(self) -> None:
        try:
            self.folder_cache_file.write_text(
                json.dumps(self._folder_cache, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception:
            pass

    @staticmethod
    def compute_folder_signature(folder_path: Path) -> str:
        """
        Calcula una firma ultrarrápida del contenido de la carpeta:
        nombres de archivo, tamaños y timestamps de modificación.
        """
        try:
            p = Path(folder_path)
            if not p.exists() or not p.is_dir():
                return ""
            items = []
            for f in sorted(p.iterdir(), key=lambda x: x.name):
                if f.is_file():
                    st = f.stat()
                    items.append(f"{f.name}:{st.st_size}:{st.st_mtime_ns}")
            sig_raw = "|".join(items)
            return hashlib.sha256(sig_raw.encode("utf-8")).hexdigest()
        except Exception:
            return ""

    def get_folder_diagnosis(self, folder_path: Path) -> Optional[Dict[str, Any]]:
        with self._lock:
            p_str = str(Path(folder_path).resolve())
            entry = self._folder_cache.get(p_str)
            if not entry or not isinstance(entry, dict):
                return None
            current_sig = self.compute_folder_signature(folder_path)
            if entry.get("signature") == current_sig and entry.get("diagnosis"):
                self.stats["local_cache_hits"] += 1
                return dict(entry["diagnosis"])
            return None

    def save_folder_diagnosis(self, folder_path: Path, diagnosis: Dict[str, Any]) -> None:
        with self._lock:
            p_str = str(Path(folder_path).resolve())
            current_sig = self.compute_folder_signature(folder_path)
            # Guardar una copia limpia del diagnóstico
            diag_copy = dict(diagnosis)
            self._folder_cache[p_str] = {
                "signature": current_sig,
                "folder_name": Path(folder_path).name,
                "saved_at": diag_copy.get("saved_at") or "",
                "diagnosis": diag_copy
            }
            self._save_folder_disk_cache()

    def invalidate_folder_diagnosis(self, folder_path: Path) -> None:
        with self._lock:
            p_str = str(Path(folder_path).resolve())
            if p_str in self._folder_cache:
                del self._folder_cache[p_str]
                self._save_folder_disk_cache()

    def get_all_folder_diagnoses(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._folder_cache)

    @staticmethod
    def compute_pdf_key(pdf_path: Path, max_pages: int = 3) -> str:
        """
        Calcula una clave única basada en el contenido binario del PDF y el número de páginas.
        """
        h = hashlib.sha256()
        h.update(f"v3_pages={max_pages}_".encode("utf-8"))
        try:
            # Leer los primeros y últimos 64KB + tamaño del archivo para hash ultrarrápido en red
            size = pdf_path.stat().st_size
            h.update(f"size={size}_".encode("utf-8"))
            with open(pdf_path, "rb") as f:
                h.update(f.read(65536))
                if size > 65536:
                    f.seek(max(0, size - 65536))
                    h.update(f.read(65536))
        except Exception:
            h.update(str(pdf_path).encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            val = self._data.get(key)
            if val is not None:
                self.stats["local_cache_hits"] += 1
                return dict(val)
            return None

    def set(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = value
            self._save_disk_cache()

    def record_api_usage(self, usage: Dict[str, Any]) -> None:
        with self._lock:
            self.stats["api_calls"] += 1
            self.stats["input_tokens"] += int(usage.get("input_tokens", 0))
            self.stats["output_tokens"] += int(usage.get("output_tokens", 0))
            self.stats["cache_read_tokens"] += int(usage.get("cache_read_input_tokens", 0))
            self.stats["cache_creation_tokens"] += int(usage.get("cache_creation_input_tokens", 0))

    def clear(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            self._save_disk_cache()
            self._folder_cache.clear()
            self._save_folder_disk_cache()
            return count

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "cached_documents_on_disk": len(self._data),
                "cached_folders_on_disk": len(self._folder_cache),
                **self.stats
            }
