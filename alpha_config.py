"""Reads config/project_alpha.yaml (or $PROJECT_ALPHA_CONFIG). Shared by the app, branch report and CQI agent."""
from __future__ import annotations

import os

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("PROJECT_ALPHA_CONFIG", os.path.join(ROOT, "config", "project_alpha.yaml"))
_cache = {"mtime": None, "cfg": {}}


def cfg() -> dict:
    """The parsed config; re-read automatically when the file changes (edit it while the app runs)."""
    m = os.path.getmtime(CONFIG_PATH)
    if m != _cache["mtime"]:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            _cache["cfg"] = yaml.safe_load(fh) or {}
        _cache["mtime"] = m
    return _cache["cfg"]


def path(value: str) -> str:
    """Resolve a config path: absolute paths (/Volumes/..., C:\\...) as-is, others relative to project_alpha/."""
    if not value:
        return value
    return value if os.path.isabs(value) or value.startswith("/") else os.path.join(ROOT, value)


def data(key: str, default=None):
    return (cfg().get("data") or {}).get(key, default)
