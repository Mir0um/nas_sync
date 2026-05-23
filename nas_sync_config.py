#!/usr/bin/env python3
"""Module de configuration partagé entre le démon et l'interface."""

import json
import shutil
import time
from pathlib import Path

HOME          = Path.home()
CONFIG_FILE   = HOME / ".nas_sync_config.json"
EVENTS_FILE   = HOME / ".nas_sync_events.jsonl"
PID_FILE      = HOME / ".nas_sync.pid"
LOG_FILE      = HOME / ".nas_sync.log"
STATE_FILE    = HOME / ".nas_sync_state.json"
LOCK_FILE     = HOME / ".nas_sync.lock"          # verrou instance unique (fix 2)
PROGRESS_FILE = HOME / ".nas_sync_progress.json" # progression en cours (fix 6)
BACKUP_DIR    = HOME / ".nas_sync_backups"        # sauvegardes avant écrasement (fix 3)
LOCAL_BASE    = HOME / "offline_cache"
NAS_MOUNT     = HOME / "NasShare"

MAX_EVENTS   = 1000
_TRIM_EVERY  = 50    # fix 13 : élagage tous les N appels seulement
_write_count = 0

DEFAULT_CONFIG = {
    "mode":            "portable",   # "portable" (sync) ou "fixe" (accès direct NAS)
    "nas_host":        "Cassis.local",
    "nas_port":        445,
    "nas_mount":       str(HOME / "NasShare"),
    "local_base":      str(HOME / "offline_cache"),
    "check_interval":  30,
    "sync_interval":   300,
    "mtime_eps":       2.0,
    "notifications":   True,
    "notif_min_files": 1,
    "conflict_mode":   "ask",
    # fix 5 — filtres d'exclusion
    "exclude_patterns": [
        "*.tmp", "*.lock", "~$*", ".DS_Store", "Thumbs.db",
        "desktop.ini", "*.part", "*.crdownload", "*.nastmp",
        ".Trash*", "*.swp", "*.swo", "*.pyc",
    ],
    # fix 3 — sauvegarde avant écrasement
    "backup_before_overwrite": True,
    "backup_max_days": 30,
    # fix 4 — synchronisation des suppressions
    "deletion_sync": False,
    # fix 9 — pause intelligente
    "pause_on_battery": False,
    "pause_on_metered": False,
    "dirs": [
        {"local_sub": "Desktop",   "nas_sub": "Desktop",   "enabled": True,  "max_age_days": 0,   "max_size_mb": 0},
        {"local_sub": "Downloads", "nas_sub": "Downloads", "enabled": True,  "max_age_days": 90,  "max_size_mb": 0},
        {"local_sub": "Documents", "nas_sub": "Documents", "enabled": True,  "max_age_days": 0,   "max_size_mb": 0},
        {"local_sub": "Music",     "nas_sub": "Music",     "enabled": True,  "max_age_days": 180, "max_size_mb": 0},
        {"local_sub": "Pictures",  "nas_sub": "Pictures",  "enabled": True,  "max_age_days": 0,   "max_size_mb": 0},
        {"local_sub": "video",     "nas_sub": "video",     "enabled": True,  "max_age_days": 90,  "max_size_mb": 0},
    ],
    # NAS supplémentaires à monter automatiquement
    # Chaque entrée : {name, host, share, mount_point, credentials_file, enabled, auto_mount}
    "extra_nas": [],
}


def free_bytes(path) -> int:
    """Espace disque disponible en octets sur la partition contenant path."""
    try:
        return shutil.disk_usage(str(path)).free
    except Exception:
        return 0


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except Exception:
            pass
    cfg = DEFAULT_CONFIG.copy()
    save_config(cfg)
    return cfg


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


# ── événements (fix 13 : élagage peu fréquent) ───────────────────────────────

def append_event(action: str, key: str, detail: str = ""):
    global _write_count
    ev = {"ts": time.time(), "action": action, "key": key}
    if detail:
        ev["detail"] = detail
    try:
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        _write_count += 1
        if _write_count % _TRIM_EVERY == 0:
            _trim_events()
    except Exception:
        pass


def _trim_events():
    try:
        lines = EVENTS_FILE.read_text().splitlines()
        if len(lines) > MAX_EVENTS:
            EVENTS_FILE.write_text("\n".join(lines[-MAX_EVENTS:]) + "\n")
    except Exception:
        pass


def read_events(n: int = 200) -> list:
    try:
        events = []
        for line in reversed(EVENTS_FILE.read_text().splitlines()):
            try:
                events.append(json.loads(line))
                if len(events) >= n:
                    break
            except Exception:
                pass
        return events
    except Exception:
        return []


# ── progression (fix 6) ───────────────────────────────────────────────────────

def write_progress(status: str, current: str = "", done: int = 0, total: int = 0,
                   bytes_done: int = 0, bytes_total: int = 0):
    try:
        PROGRESS_FILE.write_text(json.dumps({
            "status": status, "current": current,
            "done": done, "total": total,
            "bytes_done": bytes_done, "bytes_total": bytes_total,
            "ts": time.time(),
        }))
    except Exception:
        pass


def read_progress() -> dict:
    try:
        return json.loads(PROGRESS_FILE.read_text())
    except Exception:
        return {"status": "unknown", "current": "", "done": 0, "total": 0}


# ── PID / démon ───────────────────────────────────────────────────────────────

def get_daemon_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def is_daemon_running() -> bool:
    pid = get_daemon_pid()
    if pid is None:
        return False
    try:
        import os
        os.kill(pid, 0)
        return True
    except OSError:
        return False
