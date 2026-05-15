import os
import posixpath
import re
from typing import Optional


SCRIPT_DIR = "/home/ubuntu/.lca/scripts"
SCRIPT_SUFFIX = ".sh"


def normalize_script_path(path: str) -> Optional[str]:
    """Return a canonical sandbox script path, or None when the path is unsafe."""
    raw = (path or "").strip()
    if not raw:
        return None

    if not raw.startswith("/"):
        raw = posixpath.join(SCRIPT_DIR, raw)

    normalized = posixpath.normpath(raw)
    if not normalized.startswith(f"{SCRIPT_DIR}/"):
        return None
    if not normalized.endswith(SCRIPT_SUFFIX):
        return None
    path_parts = [part for part in normalized.split("/") if part]
    if any(part in {".", ".."} for part in path_parts):
        return None

    filename = posixpath.basename(normalized)
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.sh", filename):
        return None
    return normalized


def is_valid_script_path(path: str) -> bool:
    return normalize_script_path(path) == (path or "").strip()


def script_name_from_path(path: str) -> str:
    return os.path.basename(path or "").removesuffix(SCRIPT_SUFFIX) or "shell_script_task"


def render_script_command(script_path: str) -> str:
    return f"bash {script_path}"
