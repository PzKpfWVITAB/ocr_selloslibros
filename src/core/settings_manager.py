import json
import os
import sys
from pathlib import Path
from typing import Dict, Any


def load_env_file(env_path: Path) -> Dict[str, str]:
    """Carga variables clave=valor desde un archivo .env sin dependencias externas."""
    loaded: Dict[str, str] = {}
    if not env_path.exists():
        return loaded
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            loaded[k] = v
            if k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass
    return loaded


def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


_ENV_VARS = load_env_file(_resolve_base_dir() / ".env")
DEFAULT_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "") or _ENV_VARS.get("ANTHROPIC_API_KEY", "")

AVAILABLE_MODELS = [
    "claude-sonnet-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet-latest",
]


class SettingsManager:
    """
    Administra la configuración persistente de la aplicación (API Key de Claude,
    modelo seleccionado y opciones de ahorro de caché).
    Prioridad de carga de la API Key:
    1. settings.json (si el usuario la configuró en la interfaz)
    2. Archivo local .env (ANTHROPIC_API_KEY=...)
    3. Variable de entorno del sistema (ANTHROPIC_API_KEY)
    """

    @staticmethod
    def get_base_dir() -> Path:
        return _resolve_base_dir()

    @classmethod
    def get_settings_path(cls) -> Path:
        return cls.get_base_dir() / "settings.json"

    @classmethod
    def load(cls) -> Dict[str, Any]:
        env_vars = load_env_file(cls.get_base_dir() / ".env")
        env_key = os.environ.get("ANTHROPIC_API_KEY", "") or env_vars.get("ANTHROPIC_API_KEY", "")
        env_model = os.environ.get("CLAUDE_MODEL", "") or env_vars.get("CLAUDE_MODEL", "claude-sonnet-5")

        defaults: Dict[str, Any] = {
            "api_key": env_key,
            "model": env_model or "claude-sonnet-5",
            "use_local_cache": True,
            "use_prompt_cache": True,
            "early_stop_caratula": True,
            "max_pages_per_doc": 3,
            "image_max_dim": 1400,
            "jpeg_quality": 82,
        }

        path = cls.get_settings_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    defaults.update(data)
                    # Si settings.json no tiene key pero .env sí, usar la de .env
                    if not defaults.get("api_key") and env_key:
                        defaults["api_key"] = env_key
            except Exception:
                pass
        else:
            cls.save(defaults)
        return defaults

    @classmethod
    def save(cls, settings: Dict[str, Any]) -> None:
        path = cls.get_settings_path()
        try:
            path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
            path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
