#!/usr/bin/env python3
"""
NAS Sync Daemon — synchronisation bidirectionnelle silencieuse
~/offline_cache  ↔  ~/NasShare (Cassis.local)

Signaux :
  SIGHUP  → recharger la configuration
  SIGUSR1 → forcer une synchronisation immédiate
"""

import fcntl
import fnmatch
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nas_sync_config import (
    load_config, append_event, write_progress, free_bytes,
    CONFIG_FILE, LOG_FILE, STATE_FILE, PID_FILE, LOCK_FILE, BACKUP_DIR,
    LOCAL_BASE, NAS_MOUNT,
)

DIALOG_PY = Path(__file__).parent / "conflict_dialog.py"

SPACE_MARGIN = 1_073_741_824  # 1 Go de marge de sécurité


def _fmt_sz(n: int) -> str:
    for unit, div in (("Go", 1_000_000_000), ("Mo", 1_000_000), ("Ko", 1_000)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} o"

# ── logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    filename=str(LOG_FILE), level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger()

# ── état global rechargeable ──────────────────────────────────────────────────

cfg          = load_config()
_force_sync  = False
_reload_cfg  = False
_clock_offset = 0.0   # fix 7 : décalage horloge NAS vs local (secondes)


def on_sighup(*_):
    global _reload_cfg
    _reload_cfg = True


def on_sigusr1(*_):
    global _force_sync
    _force_sync = True


signal.signal(signal.SIGHUP,  on_sighup)
signal.signal(signal.SIGUSR1, on_sigusr1)


# ── fix 2 : verrou instance unique ───────────────────────────────────────────

_lock_fd = None


def acquire_lock():
    global _lock_fd
    _lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log.error("nas-sync est déjà en cours d'exécution (verrou actif)")
        sys.exit("nas-sync déjà en cours d'exécution.")


# ── détection NAS ─────────────────────────────────────────────────────────────

def nas_reachable() -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        ok = s.connect_ex((cfg["nas_host"], cfg["nas_port"])) == 0
        s.close()
        return ok
    except Exception:
        return False


def nas_mounted() -> bool:
    try:
        # Lance mountpoint avec un timeout de 2 secondes pour éviter les blocages de stat()
        r = subprocess.run(
            ["timeout", "2", "mountpoint", "-q", cfg["nas_mount"]],
            capture_output=True,
            timeout=3
        )
        return r.returncode == 0
    except Exception:
        return False


def _try_mount_main_nas() -> bool:
    """Tente de monter le NAS principal si le réseau est disponible."""
    mount_pt = Path(cfg["nas_mount"]).expanduser()
    if nas_mounted():
        return True

    # Vérification réseau rapide avant de lancer mount
    if not nas_reachable():
        return False

    log.info(f"NAS principal accessible mais non monté. Tentative de montage sur {mount_pt}...")
    try:
        mount_pt.mkdir(parents=True, exist_ok=True)
        # Exécute mount (réussira si l'option 'user' est présente dans /etc/fstab)
        r = subprocess.run(["mount", str(mount_pt)], capture_output=True, timeout=10)
        if r.returncode == 0:
            log.info("NAS principal monté avec succès via mount")
            return True
        else:
            stderr = r.stderr.decode().strip()
            log.warning(f"Échec de la commande mount (code {r.returncode}) : {stderr}")
    except Exception as e:
        log.warning(f"Erreur lors du montage automatique du NAS : {e}")
    return False


# ── fix 9 : pause intelligente ────────────────────────────────────────────────

def is_on_battery() -> bool:
    try:
        for ps in Path("/sys/class/power_supply").iterdir():
            t = (ps / "type")
            s = (ps / "status")
            if t.exists() and s.exists() and t.read_text().strip() == "Battery":
                return s.read_text().strip() == "Discharging"
    except Exception:
        pass
    return False


def is_metered() -> bool:
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.NetworkManager",
            "/org/freedesktop/NetworkManager",
            "org.freedesktop.NetworkManager",
            None
        )
        metered = proxy.get_cached_property("Metered")
        if metered:
            val = metered.unpack()
            return val in (1, 3)
    except Exception:
        pass
    return False


def is_paused() -> bool:
    if cfg.get("pause_on_battery", False) and is_on_battery():
        return True
    if cfg.get("pause_on_metered", False) and is_metered():
        return True
    return False


def nas_available() -> bool:
    if not nas_reachable():
        return False
    if not nas_mounted():
        # Essayer de monter à la volée si joignable
        return _try_mount_main_nas()
    return True


# ── commutation des liens symboliques (mode fixe) ─────────────────────────────

_FR_LINKS = {
    "Desktop":   "Bureau",
    "Downloads": "Téléchargements",
    "Documents": "Documents",
    "Pictures":  "Images",
    "Music":     "Musique",
    "video":     "Vidéos",
}


def _switch_symlinks(to_nas: bool) -> None:
    """Redirige les liens symboliques XDG vers le NAS ou le cache local."""
    if cfg.get("mode", "portable") != "fixe":
        return
    active_subs = {d["local_sub"] for d in cfg["dirs"] if d.get("enabled", True)}
    nas_mount   = Path(cfg["nas_mount"])
    cache_base  = Path(cfg.get("local_base", str(Path.home() / "offline_cache")))
    home        = Path.home()
    for local_sub, fr_name in _FR_LINKS.items():
        if local_sub not in active_subs:
            continue
        target = (nas_mount if to_nas else cache_base) / local_sub
        link   = home / fr_name
        try:
            if link.is_symlink():
                link.unlink()
            if not link.exists():
                link.symlink_to(target)
                log.info(f"Lien ~/{fr_name} → {target}")
        except OSError as exc:
            log.warning(f"Impossible de mettre à jour le lien ~/{fr_name} : {exc}")


# ── fix 7 : décalage horloge NAS ─────────────────────────────────────────────

def measure_clock_offset() -> float:
    """Mesure l'écart entre l'horloge du NAS et l'horloge locale."""
    try:
        test = Path(cfg["nas_mount"]) / ".nas_sync_clocktest"
        t_before = time.time()
        test.write_text("t")
        nas_mt = test.stat().st_mtime
        t_after = time.time()
        test.unlink(missing_ok=True)
        offset = nas_mt - (t_before + t_after) / 2
        log.info(f"Décalage horloge NAS : {offset:+.2f}s")
        return offset
    except Exception as e:
        log.warning(f"Mesure décalage horloge échouée : {e}")
        return 0.0


# ── utilitaires fichiers ──────────────────────────────────────────────────────

def get_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


def get_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except Exception:
        return 0


def notify(summary: str, body: str = ""):
    if not cfg.get("notifications", True):
        return
    try:
        import gi
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify
        Notify.init("NAS Sync")
        n = Notify.Notification.new(summary, body, "network-server")
        n.show()
    except Exception as e:
        log.warning(f"Notification native échouée : {e}")


def local_path(key: str) -> Path:
    return Path(cfg["local_base"]) / key


def nas_path(key: str) -> Path:
    local_sub = key.split("/")[0]
    rel       = key[len(local_sub) + 1:]
    nas_sub   = next(
        (d["nas_sub"] for d in cfg["dirs"] if d["local_sub"] == local_sub),
        local_sub,
    )
    return Path(cfg["nas_mount"]) / nas_sub / rel


# ── fix 3 : sauvegarde avant écrasement ──────────────────────────────────────

def backup_file(p: Path):
    """Sauvegarde un fichier avant qu'il soit écrasé."""
    if not p.exists():
        return
    try:
        day_dir = BACKUP_DIR / datetime.now().strftime("%Y-%m-%d")
        rel = p.relative_to(Path.home())
        dst = day_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    except Exception as e:
        log.warning(f"Backup échoué pour {p}: {e}")


def cleanup_old_backups():
    """Supprime les sauvegardes plus anciennes que backup_max_days."""
    max_days = cfg.get("backup_max_days", 30)
    cutoff   = time.time() - max_days * 86400
    if not BACKUP_DIR.exists():
        return
    for day_dir in BACKUP_DIR.iterdir():
        if day_dir.is_dir() and day_dir.stat().st_mtime < cutoff:
            try:
                shutil.rmtree(day_dir)
                log.info(f"Backup expiré supprimé : {day_dir.name}")
            except Exception:
                pass


# ── fix 1 : copie atomique ────────────────────────────────────────────────────

def safe_copy(src: Path, dst: Path, do_backup: bool = False) -> bool:
    """
    Copie atomique via fichier temporaire.
    Évite les fichiers corrompus si le processus est interrompu.
    """
    tmp = dst.with_suffix(dst.suffix + ".nastmp")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if do_backup and dst.exists():
            backup_file(dst)
        shutil.copy2(src, tmp)   # copie dans .nastmp
        tmp.rename(dst)          # renommage atomique
        log.info(f"  copié {src.name} → {dst.parent}")
        return True
    except Exception as e:
        log.error(f"  copie échouée {src}: {e}")
        tmp.unlink(missing_ok=True)
        return False


# ── fix 5 : filtres d'exclusion ───────────────────────────────────────────────

def is_excluded(filename: str) -> bool:
    patterns = cfg.get("exclude_patterns", [])
    return any(fnmatch.fnmatch(filename, p) for p in patterns)


# ── état ──────────────────────────────────────────────────────────────────────

import json


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except Exception as e:
        log.error(f"save_state : {e}")


# ── scan ──────────────────────────────────────────────────────────────────────

def _is_too_old(mtime: float, max_age_days: int) -> bool:
    return max_age_days > 0 and (time.time() - mtime) > max_age_days * 86400


def scan_local() -> dict:
    """Retourne {clé: (mtime, size)} pour tous les fichiers du cache local."""
    result = {}
    base = Path(cfg["local_base"])
    for d in cfg["dirs"]:
        if not d.get("enabled", True):
            continue
        sub = base / d["local_sub"]
        if not sub.exists():
            continue
        max_age = d.get("max_age_days", 0)
        for f in sub.rglob("*"):
            if not f.is_file():
                continue
            if is_excluded(f.name):
                continue
            try:
                st = f.stat()
                mt, sz = st.st_mtime, st.st_size
            except Exception:
                continue
            if _is_too_old(mt, max_age):
                continue
            result[f"{d['local_sub']}/{f.relative_to(sub)}"] = (mt, sz)
    return result


def scan_nas() -> dict:
    """Retourne {clé: (mtime, size)} pour tous les fichiers du NAS.

    Utilise ``find -printf`` en priorité (une seule passe réseau) avec un
    fallback sur rglob+stat si find échoue.
    """
    result = {}
    mount = Path(cfg["nas_mount"])
    for d in cfg["dirs"]:
        if not d.get("enabled", True):
            continue
        sub = mount / d["nas_sub"]
        if not sub.exists():
            continue
        max_age  = d.get("max_age_days", 0)
        local_sub = d["local_sub"]

        # Passe unique via find : évite un stat() réseau par fichier
        try:
            r = subprocess.run(
                ["find", str(sub), "-type", "f", "-printf", r"%T@ %s %P\n"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split(" ", 2)
                    if len(parts) != 3:
                        continue
                    mt_raw = float(parts[0])
                    sz     = int(parts[1])
                    rel    = parts[2]
                    if is_excluded(os.path.basename(rel)):
                        continue
                    if _is_too_old(mt_raw, max_age):
                        continue
                    result[f"{local_sub}/{rel}"] = (mt_raw - _clock_offset, sz)
                continue
        except Exception:
            pass

        # Fallback : rglob + stat (un appel réseau par fichier)
        for f in sub.rglob("*"):
            if not f.is_file():
                continue
            if is_excluded(f.name):
                continue
            try:
                st = f.stat()
                mt, sz = st.st_mtime, st.st_size
            except Exception:
                continue
            if _is_too_old(mt, max_age):
                continue
            result[f"{local_sub}/{f.relative_to(sub)}"] = (mt - _clock_offset, sz)
    return result


# ── résolution de conflit ─────────────────────────────────────────────────────

def resolve_conflict(key: str) -> str | None:
    mode = cfg.get("conflict_mode", "ask")
    if mode == "keep_local":
        return "local"
    if mode == "keep_nas":
        return "nas"
    try:
        lp = local_path(key)
        np = nas_path(key)
        res = subprocess.run(
            [sys.executable, str(DIALOG_PY), key, str(lp), str(np)],
            capture_output=True, text=True, timeout=600, env=os.environ.copy(),
        )
        out = res.stdout.strip()
        if out in ("local", "nas") or out.startswith("rename:"):
            return out
        return None
    except Exception as e:
        log.error(f"dialogue conflit {key}: {e}")
        return None


def apply_rename(key: str, side: str, new_name: str,
                 new_state: dict, state: dict) -> bool:
    lp = local_path(key)
    np = nas_path(key)
    sub     = key.split("/")[0]
    rel_dir = "/".join(key.split("/")[1:-1])
    new_key = f"{sub}/{rel_dir}/{new_name}" if rel_dir else f"{sub}/{new_name}"
    new_lp  = local_path(new_key)
    new_np  = nas_path(new_key)
    try:
        if side == "local":
            new_lp.parent.mkdir(parents=True, exist_ok=True)
            lp.rename(new_lp)
            safe_copy(np, lp)
            safe_copy(new_lp, new_np)
        else:
            new_np.parent.mkdir(parents=True, exist_ok=True)
            safe_copy(np, new_np)
            safe_copy(lp, np)
            safe_copy(new_np, new_lp)
        new_state[key]     = get_mtime(lp)
        new_state[new_key] = get_mtime(new_lp)
        append_event(f"renommé ({side})", key, new_name)
        return True
    except Exception as e:
        log.error(f"Rename error {key}: {e}")
        new_state[key] = state.get(key, 0)
        return False


# ── synchronisation ───────────────────────────────────────────────────────────

def do_sync() -> int:
    global _clock_offset
    eps   = cfg.get("mtime_eps", 2.0)
    do_bk = cfg.get("backup_before_overwrite", True)
    state = load_state()
    l_f   = scan_local()
    n_f   = scan_nas()

    scan_keys  = set(l_f) | set(n_f)
    state_keys = set(state)
    all_keys   = scan_keys | state_keys

    to_nas        = []
    to_local      = []
    del_from_nas  = []   # fix 4
    del_from_local= []   # fix 4
    conflicts     = []
    new_state     = {}

    total = len(all_keys)
    done  = 0
    write_progress("analyse", "", 0, total)  # fix 6

    for key in all_keys:
        l_info = l_f.get(key)
        n_info = n_f.get(key)
        l_mt = l_info[0] if l_info is not None else None
        n_mt = n_info[0] if n_info is not None else None
        s_mt = state.get(key)

        if l_mt is not None and n_mt is not None:
            # Les deux côtés sont visibles dans le scan
            if s_mt is None:
                if l_mt > n_mt + eps:
                    to_nas.append(key)
                elif n_mt > l_mt + eps:
                    to_local.append(key)
                else:
                    new_state[key] = max(l_mt, n_mt)
            else:
                lc = l_mt > s_mt + eps
                nc = n_mt > s_mt + eps
                if lc and nc:
                    conflicts.append(key)
                elif lc:
                    to_nas.append(key)
                elif nc:
                    to_local.append(key)
                else:
                    new_state[key] = s_mt

        elif l_mt is not None:
            # Local visible, NAS absent du scan
            if s_mt is not None and not nas_path(key).exists():
                # NAS vraiment supprimé (pas juste filtré par âge ou motif)
                if l_mt > s_mt + eps:
                    conflicts.append(key)           # local modifié + NAS supprimé
                elif cfg.get("deletion_sync", False):
                    del_from_local.append(key)
                else:
                    new_state[key] = s_mt           # deletion_sync off → conserver
            else:
                # Nouveau fichier local, ou NAS présent mais filtré
                to_nas.append(key)

        elif n_mt is not None:
            # NAS visible, local absent du scan
            if s_mt is not None and not local_path(key).exists():
                # Local vraiment supprimé (pas juste filtré par âge ou motif)
                if n_mt > s_mt + eps:
                    conflicts.append(key)           # NAS modifié + local supprimé
                elif cfg.get("deletion_sync", False):
                    del_from_nas.append(key)
                else:
                    new_state[key] = s_mt           # deletion_sync off → conserver
            else:
                # Nouveau fichier NAS, ou local présent mais filtré
                to_local.append(key)

        else:
            # Ni local ni NAS dans le scan — clé en state uniquement
            lp = local_path(key)
            np = nas_path(key)
            l_exists = lp.exists()
            n_exists = np.exists()
            if not l_exists and not n_exists:
                append_event("supprimé partout", key)
            elif not l_exists and n_exists and cfg.get("deletion_sync", False):
                del_from_nas.append(key)
            elif l_exists and not n_exists and cfg.get("deletion_sync", False):
                del_from_local.append(key)
            else:
                new_state[key] = state[key]         # filtré (âge/motif) ou deletion_sync off

    # ── filtrage par quota de taille (max_size_mb) ───────────────────────────
    # Pour chaque dossier avec max_size_mb > 0, seuls les fichiers NAS les plus
    # récents qui rentrent dans le budget sont autorisés à rejoindre le cache local.
    _quota_sets: dict[str, set] = {}
    for d in cfg["dirs"]:
        max_mb = d.get("max_size_mb", 0)
        if not d.get("enabled", True) or max_mb <= 0:
            continue
        sub    = d["local_sub"]
        budget = max_mb * 1_048_576
        # Tous les fichiers NAS de ce dossier, triés du plus récent au plus ancien
        # Taille lue directement depuis le scan — aucun stat() réseau supplémentaire
        dir_files = sorted(
            [(k, n_f[k][0], n_f[k][1]) for k in n_f if k.startswith(sub + "/")],
            key=lambda x: x[1], reverse=True,
        )
        cumul, in_q = 0, set()
        for key, _mt, sz in dir_files:
            if cumul + sz <= budget:
                in_q.add(key)
                cumul += sz
        _quota_sets[sub] = in_q

    if _quota_sets:
        # Ne copier depuis le NAS que les fichiers dans le quota
        to_local = [
            k for k in to_local
            if k.split("/")[0] not in _quota_sets
            or k in _quota_sets.get(k.split("/")[0], set())
        ]

    changes   = 0
    total_ops = len(to_nas) + len(to_local) + len(del_from_nas) + len(del_from_local) + len(conflicts)
    op        = 0

    bytes_total = (sum(l_f[k][1] for k in to_nas) +
                   sum(n_f[k][1] for k in to_local))
    bytes_done  = 0

    # ── vérification espace disque avant copie NAS → local ───────────────────
    needed_local = sum(n_f[k][1] for k in to_local)
    available    = free_bytes(cfg["local_base"])
    if needed_local > 0 and available < needed_local + SPACE_MARGIN:
        short = needed_local + SPACE_MARGIN - available
        msg   = (f"Espace disque insuffisant — manque {_fmt_sz(short)} "
                 f"({_fmt_sz(needed_local)} à copier, {_fmt_sz(available)} libres, "
                 f"marge de sécurité 1 Go)")
        log.error(msg)
        notify("NAS Sync — Disque plein", msg)
        write_progress("erreur_espace", "", 0, 0)
        append_event("erreur_espace", "disque", msg[:120])
        save_state(new_state)
        return 0

    # ── copier local → NAS
    for key in to_nas:
        op += 1
        src        = local_path(key)
        file_bytes = l_f[key][1]
        write_progress("synchro", key, op, total_ops, bytes_done, bytes_total)
        if safe_copy(src, nas_path(key), do_backup=do_bk):
            new_state[key] = get_mtime(nas_path(key))
            append_event("→NAS", key)
            changes    += 1
            bytes_done += file_bytes
        else:
            new_state[key] = state.get(key, 0)

    # ── copier NAS → local
    for key in to_local:
        op += 1
        src        = nas_path(key)
        file_bytes = n_f[key][1]
        write_progress("synchro", key, op, total_ops, bytes_done, bytes_total)
        if safe_copy(src, local_path(key), do_backup=do_bk):
            new_state[key] = get_mtime(local_path(key))
            append_event("←NAS", key)
            changes    += 1
            bytes_done += file_bytes
        else:
            new_state[key] = state.get(key, 0)

    # ── fix 4 : suppressions
    for key in del_from_nas:
        op += 1
        np = nas_path(key)
        try:
            if do_bk:
                backup_file(np)
            np.unlink()
            append_event("supprimé NAS", key)
            changes += 1
        except Exception as e:
            log.error(f"Suppression NAS échouée {key}: {e}")
            new_state[key] = state.get(key, 0)

    for key in del_from_local:
        op += 1
        lp = local_path(key)
        try:
            if do_bk:
                backup_file(lp)
            lp.unlink()
            append_event("supprimé local", key)
            changes += 1
        except Exception as e:
            log.error(f"Suppression locale échouée {key}: {e}")
            new_state[key] = state.get(key, 0)

    # ── conflits
    if conflicts:
        notify("NAS Sync — Conflits", f"{len(conflicts)} fichier(s) en conflit")

    for key in conflicts:
        op += 1
        write_progress("conflit", key, op, total_ops)
        choice = resolve_conflict(key)
        lp, np = local_path(key), nas_path(key)

        if choice == "local":
            if do_bk:
                backup_file(np)
            if safe_copy(lp, np):
                new_state[key] = get_mtime(np)
                append_event("conflit→NAS", key, "local retenu")
                changes += 1
            else:
                new_state[key] = state.get(key, 0)

        elif choice == "nas":
            if do_bk:
                backup_file(lp)
            if safe_copy(np, lp):
                new_state[key] = get_mtime(lp)
                append_event("conflit←NAS", key, "NAS retenu")
                changes += 1
            else:
                new_state[key] = state.get(key, 0)

        elif choice and choice.startswith("rename:"):
            parts = choice.split(":", 2)
            if len(parts) == 3:
                _, side, new_name = parts
                if apply_rename(key, side, new_name, new_state, state):
                    changes += 2
            else:
                new_state[key] = state.get(key, 0)

        else:
            new_state[key] = state.get(key, 0)
            append_event("conflit ignoré", key)

    # ── nettoyage cache local dépassant le quota ─────────────────────────────
    # Pour chaque dossier avec max_size_mb, supprimer les fichiers locaux les plus
    # anciens jusqu'à ce que le total soit ≤ budget.
    for d in cfg["dirs"]:
        max_mb = d.get("max_size_mb", 0)
        if not d.get("enabled", True) or max_mb <= 0:
            continue
        sub       = d["local_sub"]
        local_dir = Path(cfg["local_base"]) / sub
        if not local_dir.exists():
            continue
        budget = max_mb * 1_048_576
        local_files = [
            (f, f.stat().st_mtime, f.stat().st_size)
            for f in local_dir.rglob("*")
            if f.is_file() and not is_excluded(f.name)
        ]
        total_size = sum(sz for _, _, sz in local_files)
        if total_size <= budget:
            continue
        # Trier du plus ancien au plus récent — on supprime les anciens en premier
        local_files.sort(key=lambda x: x[1])
        for f, _mt, sz in local_files:
            if total_size <= budget:
                break
            key = f"{sub}/{f.relative_to(local_dir)}"
            try:
                if do_bk:
                    backup_file(f)
                f.unlink()
                total_size -= sz
                new_state.pop(key, None)
                append_event("quota_trim", key, f"hors quota {max_mb} Mo — {_fmt_sz(sz)}")
                log.info(f"  quota: supprimé {key} ({_fmt_sz(sz)})")
                changes += 1
            except Exception as e:
                log.error(f"quota trim {key}: {e}")

    save_state(new_state)
    write_progress("idle", "", changes, total_ops, bytes_done, bytes_total)

    min_notif = cfg.get("notif_min_files", 1)
    if changes >= min_notif:
        log.info(
            f"Synchro : →NAS={len(to_nas)} ←NAS={len(to_local)} "
            f"suppr={len(del_from_nas)+len(del_from_local)} "
            f"conflits={len(conflicts)} total={changes}"
        )
    return changes


# ── montage automatique des NAS supplémentaires ───────────────────────────────

def _try_mount_extra_nas():
    for nas in cfg.get("extra_nas", []):
        if not nas.get("enabled", True) or not nas.get("auto_mount", True):
            continue
        host     = nas.get("host", "")
        mount_pt = Path(nas.get("mount_point", "")).expanduser()
        if not host or not str(mount_pt):
            continue
        if mount_pt.is_mount():
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            if s.connect_ex((host, 445)) != 0:
                s.close()
                continue
            s.close()
        except Exception:
            continue
        try:
            r = subprocess.run(["mount", str(mount_pt)],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                log.info(f"NAS supplémentaire monté : {mount_pt}")
                continue
        except Exception as e:
            log.warning(f"La commande 'mount' a échoué ou est absente : {e}")

        try:
            share = nas.get("share", "")
            r2 = subprocess.run(
                ["gio", "mount", f"smb://{host}/{share}"],
                capture_output=True, timeout=10,
                env=os.environ.copy(),
            )
            if r2.returncode == 0:
                log.info(f"NAS supplémentaire monté via gio : {host}/{share}")
            else:
                log.debug(f"Montage NAS supplémentaire échoué : {host}/{share} — "
                          f"ajoutez une entrée dans /etc/fstab avec l'option 'user'")
        except Exception as e:
            log.warning(f"La commande 'gio mount' a échoué ou est absente : {e}")


# ── boucle principale ─────────────────────────────────────────────────────────

def main():
    global cfg, _force_sync, _reload_cfg, _clock_offset

    acquire_lock()                             # fix 2
    PID_FILE.write_text(str(os.getpid()))
    log.info(f"=== nas-sync démarré (PID {os.getpid()}) ===")
    notify("NAS Sync", "Démon de synchronisation actif")
    write_progress("démarrage")

    for d in cfg["dirs"]:
        (Path(cfg["local_base"]) / d["local_sub"]).mkdir(parents=True, exist_ok=True)

    cleanup_old_backups()                      # fix 3

    was_up      = False
    last_sync_t = 0.0

    # Vérification initiale : état réseau connu dès le démarrage
    _initial_up = nas_available()
    _switch_symlinks(to_nas=_initial_up)
    if not _initial_up:
        write_progress("hors ligne")

    try:
        while True:
            if _reload_cfg:
                cfg = load_config()
                _reload_cfg = False
                log.info("Configuration rechargée")
                for d in cfg["dirs"]:
                    (Path(cfg["local_base"]) / d["local_sub"]).mkdir(parents=True, exist_ok=True)

            up = nas_available()
            _try_mount_extra_nas()

            if is_paused():                    # fix 9
                if up and not was_up:
                    log.info("NAS disponible mais synchro en pause (batterie/réseau limité)")
                    write_progress("pause")
                was_up = up
                time.sleep(cfg.get("check_interval", 30))
                continue

            if (up and not was_up) or _force_sync:
                _clock_offset = measure_clock_offset()  # fix 7
                if not was_up and up:
                    log.info("NAS connecté → synchronisation")
                    notify("NAS Sync", "NAS connecté — synchronisation en cours…")
                    _switch_symlinks(to_nas=True)
                do_sync()
                last_sync_t = time.time()
                _force_sync = False

            elif up and (time.time() - last_sync_t > cfg.get("sync_interval", 300)):
                do_sync()
                last_sync_t = time.time()

            elif not up and was_up:
                log.info("NAS hors ligne — mode hors connexion")
                notify("NAS Sync", "NAS hors ligne — mode local actif")
                write_progress("hors ligne")
                _switch_symlinks(to_nas=False)

            was_up = up
            time.sleep(cfg.get("check_interval", 30))

    finally:
        PID_FILE.unlink(missing_ok=True)
        LOCK_FILE.unlink(missing_ok=True)
        write_progress("arrêté")
        log.info("nas-sync arrêté")


if __name__ == "__main__":
    main()
