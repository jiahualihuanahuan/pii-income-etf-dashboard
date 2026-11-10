"""Configuration: config/settings.yaml (defaults, editable) + DATA_DIR/settings.override.yaml (written by the UI)."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

from .scoring import DEFAULT_STABILITY_WEIGHTS, DEFAULT_WEIGHTS, TR_WINDOWS, normalize

DEFAULT_SETTINGS = {
    "weights": dict(DEFAULT_WEIGHTS),
    "stability_weights": dict(DEFAULT_STABILITY_WEIGHTS),
    "window": "1y",
    "scope": "all",  # all | category
    "half_life_days": 180,
    "views_transform": "log",  # log | linear | none
    "cut_threshold": 0.10,
    "missing_return": "neutral",  # neutral | renormalize | exclude: funds too young for the chosen window
    "stale_days": 10,  # funds whose last price is older than this vs the newest are left unranked (delisted / halted)
    "timezone": "America/Toronto",
    "schedule": {
        "prices": {"day_of_week": "mon-fri", "hour": "9-16", "minute": "0,30"},
        "dividends": {"hour": 2, "minute": 30},
        "videos": {"day_of_week": "sun", "hour": 3, "minute": 0},
    },
    "videos": {
        "channel_url": "https://www.youtube.com/@passiveincomeinvesting",
        "max_new_per_run": 25,
        "fetch_transcripts": True,
    },
    "fetch": {"chunk_size": 40, "pause_seconds": 2.0, "retries": 3},
    "bootstrap_on_start": True,
    "scheduler_enabled": True,
}


def _paths() -> dict:
    root = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parents[2]))
    return {
        "config_dir": Path(os.environ.get("CONFIG_DIR", root / "config")),
        "seed_dir": Path(os.environ.get("SEED_DIR", root / "seed")),
        "data_dir": Path(os.environ.get("DATA_DIR", root / "data")),
    }


def paths() -> dict:
    return _paths()


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_yaml(p: Path) -> dict:
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def load_settings() -> dict:
    p = _paths()
    s = _merge(DEFAULT_SETTINGS, _read_yaml(p["config_dir"] / "settings.yaml"))
    s = _merge(s, _read_yaml(p["data_dir"] / "settings.override.yaml"))
    return validate_settings(s)


def validate_settings(s: dict) -> dict:
    s["weights"] = normalize(s.get("weights", {}), DEFAULT_WEIGHTS)
    s["stability_weights"] = normalize(s.get("stability_weights", {}), DEFAULT_STABILITY_WEIGHTS)
    if s.get("window") not in TR_WINDOWS:
        s["window"] = "1y"
    if s.get("scope") not in ("all", "category"):
        s["scope"] = "all"
    s["half_life_days"] = min(max(float(s.get("half_life_days", 180)), 1.0), 3650.0)
    if s.get("views_transform") not in ("log", "linear", "none"):
        s["views_transform"] = "log"
    if s.get("missing_return") not in ("neutral", "renormalize", "exclude"):
        s["missing_return"] = "neutral"
    s["cut_threshold"] = min(max(float(s.get("cut_threshold", 0.10)), 0.0), 0.9)
    return s


UI_EDITABLE = ("weights", "stability_weights", "window", "scope", "half_life_days", "views_transform", "cut_threshold")


def save_override(update: dict) -> dict:
    """Persist UI-editable keys only; returns the new effective settings."""
    p = _paths()
    p["data_dir"].mkdir(parents=True, exist_ok=True)
    f = p["data_dir"] / "settings.override.yaml"
    cur = _read_yaml(f)
    for k in UI_EDITABLE:
        if k in update:
            cur[k] = update[k]
    f.write_text(yaml.safe_dump(cur, sort_keys=True), encoding="utf-8")
    return load_settings()


def reset_override() -> dict:
    f = _paths()["data_dir"] / "settings.override.yaml"
    if f.exists():
        f.unlink()
    return load_settings()


def load_universe() -> dict:
    """config/tickers.yaml -> {'funds': [...], 'categories': {...}, 'unresolved': [...]}"""
    y = _read_yaml(_paths()["config_dir"] / "tickers.yaml")
    y.setdefault("funds", [])
    y.setdefault("categories", {})
    y.setdefault("unresolved", [])
    for f in y["funds"]:
        f.setdefault("role", "fund")
        f.setdefault("aliases", [])
        f.setdefault("stances", [])
        f["stances"] = [{**s, "date": str(s["date"])} for s in f["stances"]]
    return y


def load_seed(name: str):
    import json
    f = _paths()["seed_dir"] / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
