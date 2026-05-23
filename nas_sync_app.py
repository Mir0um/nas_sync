#!/usr/bin/env python3
"""
NAS Sync — Interface de contrôle (barre système)
Icône visible quand la synchro est active, invisible sinon (fix PASSIVE/ACTIVE).
"""

import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nas_sync_config import (
    load_config, save_config, read_events, read_progress,
    is_daemon_running, get_daemon_pid,
    APP_VERSION, APP_VERSION_NAME,
)

import gi
gi.require_version("Gtk",  "3.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gtk, GLib, Pango

# AppIndicator3 est optionnel : nécessite l'extension GNOME appindicatorsupport.
# Sans elle, on bascule sur Gtk.StatusIcon (deprecated mais fonctionnel).
HAS_APPINDICATOR = False
try:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3
    HAS_APPINDICATOR = True
except (ValueError, ImportError):
    pass

DAEMON_PY = Path(__file__).parent / "nas_sync_daemon.py"


def _info_dialog(parent, msg: str):
    dlg = Gtk.MessageDialog(
        parent=parent, modal=True,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text=msg,
    )
    dlg.run()
    dlg.destroy()
ICON_ON   = "network-server"
ICON_OFF  = "network-offline"
REFRESH   = 4000   # ms


def _fmt_size(n: int) -> str:
    for unit, div in (("Go", 1_000_000_000), ("Mo", 1_000_000), ("Ko", 1_000)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} o"


# Contenu standard de user-dirs.dirs — utilise les noms français.
# Ne change JAMAIS entre les modes : seules les cibles des liens changent.
_STANDARD_XDG_DIRS = (
    'XDG_DESKTOP_DIR="$HOME/Bureau"\n'
    'XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n'
    'XDG_TEMPLATES_DIR="$HOME/Modèles"\n'
    'XDG_PUBLICSHARE_DIR="$HOME/Public"\n'
    'XDG_DOCUMENTS_DIR="$HOME/Documents"\n'
    'XDG_MUSIC_DIR="$HOME/Musique"\n'
    'XDG_PICTURES_DIR="$HOME/Images"\n'
    'XDG_VIDEOS_DIR="$HOME/Vidéos"\n'
)

# ── Changement de mode (portable ↔ fixe) ─────────────────────────────────────

def _apply_mode_switch(new_mode: str, cfg: dict):
    """Met à jour les liens symboliques et le service selon le mode.

    user-dirs.dirs pointe toujours vers ~/Bureau, ~/Téléchargements, etc.
    Seule la cible des liens symboliques change (NAS ou cache local).
    Cela évite que xdg-user-dirs-update recrée les vrais dossiers.
    """
    home       = Path.home()
    nas_mount  = Path(cfg.get("nas_mount",  str(home / "NasShare")))
    local_base = Path(cfg.get("local_base", str(home / "offline_cache")))
    base       = nas_mount if new_mode == "fixe" else local_base

    FR_LINKS = {
        "Desktop":   "Bureau",
        "Downloads": "Téléchargements",
        "Documents": "Documents",
        "Pictures":  "Images",
        "Music":     "Musique",
        "video":     "Vidéos",
    }
    enabled = {d["local_sub"] for d in cfg.get("dirs", []) if d.get("enabled", True)}

    for sub, fr_name in FR_LINKS.items():
        if sub not in enabled:
            continue
        target = base / sub
        link   = home / fr_name
        target.mkdir(parents=True, exist_ok=True)

        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            # Dossier réel non-vide : déplacer le contenu vers la cible avant suppression
            try:
                for item in link.iterdir():
                    dst = target / item.name
                    if not dst.exists():
                        shutil.move(str(item), str(dst))
                link.rmdir()
            except OSError:
                pass

        if not link.exists() and not link.is_symlink():
            try:
                link.symlink_to(target)
            except OSError:
                pass

    # Corriger user-dirs.dirs : utiliser les noms français standards ($HOME/Bureau…)
    xdg_file = home / ".config" / "user-dirs.dirs"
    try:
        xdg_file.parent.mkdir(parents=True, exist_ok=True)
        xdg_file.write_text(_STANDARD_XDG_DIRS)
        for std_dir in ("Modèles", "Public"):
            (home / std_dir).mkdir(exist_ok=True)
    except OSError:
        pass

    IS_FLATPAK = Path("/.flatpak-info").exists()
    svc = "nas-sync.service"
    if new_mode == "fixe":
        if not IS_FLATPAK:
            subprocess.run(["systemctl", "--user", "stop",    svc], capture_output=True)
            subprocess.run(["systemctl", "--user", "disable", svc], capture_output=True)
        pid = get_daemon_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    else:
        local_base.mkdir(parents=True, exist_ok=True)
        if not IS_FLATPAK:
            subprocess.run(["systemctl", "--user", "enable", svc], capture_output=True)
            subprocess.run(["systemctl", "--user", "start",  svc], capture_output=True)
        else:
            subprocess.Popen(
                [sys.executable, str(DAEMON_PY)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Fenêtre — Fichiers récents   (fix 8 : auto-rafraîchissement)
# ══════════════════════════════════════════════════════════════════════════════

class RecentWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="NAS Sync — Fichiers récents")
        self.set_default_size(700, 440)
        self.set_border_width(0)
        self.set_position(Gtk.WindowPosition.CENTER)
        # fix 14 : destroy au lieu de hide
        self.connect("delete-event", lambda w, _: w.destroy() or True)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Barre d'outils
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_border_width(8)
        self._count_lbl = Gtk.Label()
        bar.pack_start(self._count_lbl, False, False, 0)
        btn_clear = Gtk.Button(label="Effacer l'historique")
        btn_clear.connect("clicked", self._clear)
        bar.pack_end(btn_clear, False, False, 0)
        vbox.pack_start(bar, False, False, 0)
        vbox.pack_start(Gtk.Separator(), False, False, 0)

        # Liste
        self._store = Gtk.ListStore(str, str, str, str)
        tv = Gtk.TreeView(model=self._store)
        tv.set_rules_hint(True)
        tv.set_enable_search(False)

        for title, col_id, width, expand in [
            ("Heure",     0, 130, False),
            ("Direction", 1,  95, False),
            ("Fichier",   2, 360, True),
            ("Détail",    3, 110, False),
        ]:
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
            col = Gtk.TreeViewColumn(title, r, text=col_id)
            col.set_min_width(width)
            col.set_expand(expand)
            tv.append_column(col)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(tv)
        vbox.pack_start(sw, True, True, 0)

        self.refresh()

        # fix 8 : auto-rafraîchissement toutes les 3 s si fenêtre visible
        self._timer = GLib.timeout_add(3000, self._auto_refresh)

    def _auto_refresh(self) -> bool:
        if self.get_visible():
            self.refresh()
        return True   # continuer le timer

    def refresh(self):
        events = read_events(300)
        self._store.clear()
        LABELS = {
            "→NAS":           "→ NAS",
            "←NAS":           "← Local",
            "conflit→NAS":    "⚠ conf→NAS",
            "conflit←NAS":    "⚠ conf←Local",
            "conflit ignoré": "⚠ ignoré",
            "renommé (local)":"✎ renommé local",
            "renommé (nas)":  "✎ renommé NAS",
            "supprimé NAS":   "🗑 suppr NAS",
            "supprimé local": "🗑 suppr local",
            "supprimé partout":"🗑 suppr partout",
            "quota_trim":     "✂ quota supprimé",
            "erreur_espace":  "⚠ disque plein",
        }
        for ev in events:
            ts     = datetime.fromtimestamp(ev["ts"]).strftime("%d/%m %H:%M:%S")
            action = ev.get("action", "")
            label  = LABELS.get(action, action)
            key    = ev.get("key", "")
            detail = ev.get("detail", "")
            self._store.append([ts, label, key, detail])
        self._count_lbl.set_text(f"{len(events)} événements")

    def _clear(self, _):
        try:
            from nas_sync_config import EVENTS_FILE
            EVENTS_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        self.refresh()

    def do_destroy(self):
        if hasattr(self, "_timer"):
            GLib.source_remove(self._timer)
        Gtk.Window.do_destroy(self)


# ══════════════════════════════════════════════════════════════════════════════
# Fenêtre — Paramètres (5 onglets avec fix 9 + 5 filtres)
# ══════════════════════════════════════════════════════════════════════════════

class SettingsWindow(Gtk.Window):
    def __init__(self, on_saved=None):
        super().__init__(title="NAS Sync — Paramètres")
        self.set_default_size(620, 560)
        self.set_border_width(0)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        # fix 14 : destroy au lieu de hide
        self.connect("delete-event", lambda w, _: w.destroy() or True)
        self._on_saved = on_saved
        self._cfg = load_config()

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        nb = Gtk.Notebook()
        nb.set_border_width(8)
        vbox.pack_start(nb, True, True, 0)

        nb.append_page(self._tab_mode(),       Gtk.Label(label="  Mode  "))
        nb.append_page(self._tab_connexion(),  Gtk.Label(label="  Connexion  "))
        nb.append_page(self._tab_dossiers(),   Gtk.Label(label="  Dossiers  "))
        nb.append_page(self._tab_synchro(),    Gtk.Label(label="  Synchronisation  "))
        nb.append_page(self._tab_notifs(),     Gtk.Label(label="  Notifications  "))
        nb.append_page(self._tab_avance(),     Gtk.Label(label="  Filtres & Avancé  "))
        nb.append_page(self._tab_extra_nas(),  Gtk.Label(label="  NAS supplémentaires  "))

        vbox.pack_start(Gtk.Separator(), False, False, 0)
        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bbox.set_border_width(10)
        btn_cancel = Gtk.Button(label="Annuler")
        btn_cancel.connect("clicked", lambda _: self.destroy())
        btn_save = Gtk.Button(label="Enregistrer et appliquer")
        btn_save.get_style_context().add_class("suggested-action")
        btn_save.connect("clicked", self._save)
        bbox.pack_end(btn_save,   False, False, 0)
        bbox.pack_end(btn_cancel, False, False, 0)
        vbox.pack_start(bbox, False, False, 0)

    # ── Connexion ─────────────────────────────────────────────────────────────

    def _tab_connexion(self):
        grid = Gtk.Grid()
        grid.set_border_width(16)
        grid.set_row_spacing(10)
        grid.set_column_spacing(14)
        fields = [
            ("Hôte NAS :",      "nas_host",   "ex : Cassis.local"),
            ("Port SMB :",      "nas_port",   "445"),
            ("Point montage :", "nas_mount",  str(Path.home() / "NasShare")),
            ("Cache local :",   "local_base", str(Path.home() / "offline_cache")),
        ]
        self._conn_entries = {}
        for row, (lbl, key, ph) in enumerate(fields):
            l = Gtk.Label(label=lbl); l.set_xalign(1)
            e = Gtk.Entry(); e.set_placeholder_text(ph); e.set_hexpand(True)
            e.set_text(str(self._cfg.get(key, "")))
            grid.attach(l, 0, row, 1, 1)
            grid.attach(e, 1, row, 1, 1)
            self._conn_entries[key] = e
        return self._wrap(grid)

    # ── Dossiers ──────────────────────────────────────────────────────────────

    def _tab_dossiers(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_border_width(12)

        info = Gtk.Label()
        info.set_markup(
            "<small>"
            "<b>Âge max (j)</b> = 0 → tous les fichiers. Sinon : ignorer les fichiers "
            "non modifiés depuis N jours.\n"
            "<b>Taille max (Go)</b> = 0 → pas de limite. Sinon : ne garder dans le cache local "
            "que les fichiers les plus récents qui rentrent dans ce volume. "
            "Les plus anciens sont supprimés automatiquement (sauvegardés si l'option est active)."
            "</small>"
        )
        info.set_xalign(0); info.set_line_wrap(True); info.set_margin_bottom(10)
        vbox.pack_start(info, False, False, 0)

        self._dirs_store = Gtk.ListStore(bool, str, str, int, int)
        for d in self._cfg.get("dirs", []):
            self._dirs_store.append([
                d.get("enabled", True),
                d.get("local_sub", ""),
                d.get("nas_sub", ""),
                int(d.get("max_age_days", 0)),
                int(d.get("max_size_mb", 0)) // 1024,
            ])
        tv = Gtk.TreeView(model=self._dirs_store); tv.set_rules_hint(True)

        r_toggle = Gtk.CellRendererToggle()
        r_toggle.connect("toggled", lambda r, p: self._dirs_store.__setitem__(p, [not self._dirs_store[p][0]] + list(self._dirs_store[p])[1:]) or None)
        tv.append_column(Gtk.TreeViewColumn("Actif", r_toggle, active=0))

        for ci, title in [(1, "Dossier local"), (2, "Dossier NAS")]:
            r = Gtk.CellRendererText(); r.set_property("editable", True)
            r.connect("edited", lambda _r, p, t, c=ci: self._dirs_store.__setitem__(p, list(self._dirs_store[p])[:c] + [t] + list(self._dirs_store[p])[c+1:]))
            col = Gtk.TreeViewColumn(title, r, text=ci); col.set_min_width(150); col.set_expand(True)
            tv.append_column(col)

        r_spin = Gtk.CellRendererSpin()
        r_spin.set_property("adjustment", Gtk.Adjustment(value=0, lower=0, upper=3650, step_increment=30))
        r_spin.set_property("editable", True)
        r_spin.connect("edited", lambda _r, p, t: self._dirs_store.__setitem__(
            p, list(self._dirs_store[p])[:3] + [int(t or 0)] + list(self._dirs_store[p])[4:]
        ))
        tv.append_column(Gtk.TreeViewColumn("Âge max (j)", r_spin, text=3))

        r_size = Gtk.CellRendererSpin()
        r_size.set_property("adjustment", Gtk.Adjustment(value=0, lower=0, upper=10_000, step_increment=1))
        r_size.set_property("editable", True)
        r_size.connect("edited", lambda _r, p, t: self._dirs_store.__setitem__(
            p, list(self._dirs_store[p])[:4] + [int(t or 0)]
        ))
        col_size = Gtk.TreeViewColumn("Taille max (Go, 0=∞)", r_size, text=4)
        col_size.set_min_width(130)
        tv.append_column(col_size)

        self._dirs_tv = tv
        sw = Gtk.ScrolledWindow(); sw.set_min_content_height(200); sw.add(tv)
        vbox.pack_start(sw, True, True, 0)

        bbox = Gtk.Box(spacing=6); bbox.set_margin_top(8)
        btn_add = Gtk.Button(label="+ Ajouter")
        btn_add.connect("clicked", lambda _: self._dirs_store.append([True, "nouveau", "nouveau", 0, 0]))
        btn_del = Gtk.Button(label="− Supprimer")
        def del_row(_):
            _, it = tv.get_selection().get_selected()
            if it: self._dirs_store.remove(it)
        btn_del.connect("clicked", del_row)
        bbox.pack_start(btn_add, False, False, 0); bbox.pack_start(btn_del, False, False, 0)
        vbox.pack_start(bbox, False, False, 0)
        return self._wrap(vbox)

    # ── Synchronisation ───────────────────────────────────────────────────────

    def _tab_synchro(self):
        grid = Gtk.Grid(); grid.set_border_width(16); grid.set_row_spacing(14); grid.set_column_spacing(14)

        def spin(lo, hi, step, val):
            s = Gtk.SpinButton()
            s.set_adjustment(Gtk.Adjustment(value=val, lower=lo, upper=hi, step_increment=step))
            s.set_numeric(True); return s

        self._spin_check = spin(5,   3600,  5,   self._cfg.get("check_interval", 30))
        self._spin_sync  = spin(30, 86400,  30,  self._cfg.get("sync_interval",  300))
        self._spin_eps   = spin(0,    60,   0.5, self._cfg.get("mtime_eps",       2.0))

        for i, (lbl, w, suf) in enumerate([
            ("Vérification NAS toutes les :", self._spin_check, "secondes"),
            ("Synchro périodique toutes les :", self._spin_sync, "secondes"),
            ("Tolérance mtime :",               self._spin_eps,  "secondes (SMB = 2 s)"),
        ]):
            l = Gtk.Label(label=lbl); l.set_xalign(1)
            s = Gtk.Label(label=suf); s.set_xalign(0)
            grid.attach(l, 0, i, 1, 1); grid.attach(w, 1, i, 1, 1); grid.attach(s, 2, i, 1, 1)

        sep = Gtk.Separator(); sep.set_margin_top(8); sep.set_margin_bottom(8)
        grid.attach(sep, 0, 3, 3, 1)

        lbl_c = Gtk.Label(label="En cas de conflit :"); lbl_c.set_xalign(1)
        grid.attach(lbl_c, 0, 4, 1, 1)
        self._conflict_combo = Gtk.ComboBoxText()
        for val, label in [("ask","Demander (fenêtre de choix)"),
                            ("keep_local","Toujours garder la version locale"),
                            ("keep_nas","Toujours garder la version NAS")]:
            self._conflict_combo.append(val, label)
        self._conflict_combo.set_active_id(self._cfg.get("conflict_mode", "ask"))
        grid.attach(self._conflict_combo, 1, 4, 2, 1)

        return self._wrap(grid)

    # ── Notifications ─────────────────────────────────────────────────────────

    def _tab_notifs(self):
        grid = Gtk.Grid(); grid.set_border_width(16); grid.set_row_spacing(14); grid.set_column_spacing(14)
        self._chk_notif = Gtk.CheckButton(label="Activer les notifications bureau")
        self._chk_notif.set_active(self._cfg.get("notifications", True))
        grid.attach(self._chk_notif, 0, 0, 3, 1)

        lbl_min = Gtk.Label(label="Notifier seulement si ≥"); lbl_min.set_xalign(1)
        self._spin_min = Gtk.SpinButton()
        self._spin_min.set_adjustment(Gtk.Adjustment(value=self._cfg.get("notif_min_files",1), lower=1, upper=100, step_increment=1))
        lbl_u = Gtk.Label(label="fichiers synchro par cycle"); lbl_u.set_xalign(0)
        grid.attach(lbl_min, 0, 1, 1, 1); grid.attach(self._spin_min, 1, 1, 1, 1); grid.attach(lbl_u, 2, 1, 1, 1)
        return self._wrap(grid)

    # ── Filtres & Avancé (fix 5 + 3 + 4 + 9) ────────────────────────────────

    def _tab_avance(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        vbox.set_border_width(16)

        # fix 5 : exclusions
        lbl_excl = Gtk.Label()
        lbl_excl.set_markup("<b>Fichiers à exclure de la synchronisation</b>")
        lbl_excl.set_xalign(0)
        vbox.pack_start(lbl_excl, False, False, 0)

        info_excl = Gtk.Label(label="Un motif par ligne. Exemples : *.tmp   ~$*   .DS_Store")
        info_excl.set_xalign(0)
        vbox.pack_start(info_excl, False, False, 0)

        self._excl_buffer = Gtk.TextBuffer()
        self._excl_buffer.set_text("\n".join(self._cfg.get("exclude_patterns", [])))
        tv_excl = Gtk.TextView(buffer=self._excl_buffer)
        tv_excl.set_monospace(True)
        sw_excl = Gtk.ScrolledWindow()
        sw_excl.set_min_content_height(90)
        sw_excl.add(tv_excl)
        vbox.pack_start(sw_excl, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 0)

        # fix 3 : sauvegarde
        lbl_bk = Gtk.Label()
        lbl_bk.set_markup("<b>Sauvegarde avant écrasement</b>")
        lbl_bk.set_xalign(0)
        vbox.pack_start(lbl_bk, False, False, 0)

        self._chk_backup = Gtk.CheckButton(label="Sauvegarder les fichiers avant de les écraser (~/.local/share/nas_sync/backups/)")
        self._chk_backup.set_active(self._cfg.get("backup_before_overwrite", True))
        vbox.pack_start(self._chk_backup, False, False, 0)

        box_days = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box_days.pack_start(Gtk.Label(label="Conserver les sauvegardes pendant"), False, False, 0)
        self._spin_bkdays = Gtk.SpinButton()
        self._spin_bkdays.set_adjustment(Gtk.Adjustment(value=self._cfg.get("backup_max_days",30), lower=1, upper=365, step_increment=1))
        box_days.pack_start(self._spin_bkdays, False, False, 0)
        box_days.pack_start(Gtk.Label(label="jours"), False, False, 0)
        vbox.pack_start(box_days, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 0)

        # fix 4 : suppressions
        lbl_del = Gtk.Label()
        lbl_del.set_markup("<b>Synchronisation des suppressions</b>")
        lbl_del.set_xalign(0)
        vbox.pack_start(lbl_del, False, False, 0)

        self._chk_del = Gtk.CheckButton(label="Propager les suppressions (un fichier supprimé d'un côté est supprimé de l'autre)")
        self._chk_del.set_active(self._cfg.get("deletion_sync", False))
        warn_del = Gtk.Label()
        warn_del.set_markup("<small><i>⚠  Activez uniquement si vous faites confiance à vos suppressions — les fichiers effacés vont dans les sauvegardes si l'option ci-dessus est activée.</i></small>")
        warn_del.set_xalign(0); warn_del.set_line_wrap(True)
        vbox.pack_start(self._chk_del, False, False, 0)
        vbox.pack_start(warn_del,      False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 0)

        # fix 9 : pause intelligente
        lbl_pause = Gtk.Label()
        lbl_pause.set_markup("<b>Pause intelligente</b>")
        lbl_pause.set_xalign(0)
        vbox.pack_start(lbl_pause, False, False, 0)

        self._chk_battery = Gtk.CheckButton(label="Mettre en pause si sur batterie")
        self._chk_battery.set_active(self._cfg.get("pause_on_battery", False))
        self._chk_metered = Gtk.CheckButton(label="Mettre en pause si connexion limitée (partage mobile, réseau mesuré)")
        self._chk_metered.set_active(self._cfg.get("pause_on_metered", False))
        vbox.pack_start(self._chk_battery, False, False, 0)
        vbox.pack_start(self._chk_metered, False, False, 0)

        return self._wrap(vbox)

    # ── onglet Mode ───────────────────────────────────────────────────────────

    def _tab_mode(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        vbox.set_border_width(24)

        title = Gtk.Label()
        title.set_markup("<b>Mode de synchronisation</b>")
        title.set_xalign(0)
        vbox.pack_start(title, False, False, 0)

        info = Gtk.Label()
        info.set_markup(
            "La synchronisation est <b>entièrement automatique</b>.\n\n"
            "<b>Réseau local (NAS accessible) :</b>\n"
            "  Les fichiers modifiés sont synchronisés automatiquement.\n\n"
            "<b>Hors réseau local (déconnecté ou autre réseau) :</b>\n"
            "  Le daemon attend le retour sur le réseau local.\n"
            "  Aucune action requise de votre part.\n\n"
            "Vos dossiers (Bureau, Documents, Images…) pointent toujours\n"
            "vers <tt>~/offline_cache/</tt> et restent accessibles en toutes\n"
            "circonstances, avec ou sans NAS."
        )
        info.set_xalign(0)
        info.set_line_wrap(True)
        vbox.pack_start(info, False, False, 0)

        return self._wrap(vbox)

    # ── NAS supplémentaires ───────────────────────────────────────────────────

    def _tab_extra_nas(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(12)

        info = Gtk.Label()
        info.set_markup(
            "<small>Configurez des NAS supplémentaires montés automatiquement "
            "dès qu'ils sont joignables.\n"
            "Double-cliquez sur une cellule pour modifier. "
            "Pour un montage persistant, ajoutez une entrée dans <tt>/etc/fstab</tt>.</small>"
        )
        info.set_xalign(0)
        info.set_line_wrap(True)
        vbox.pack_start(info, False, False, 0)

        # Colonnes : enabled, name, host, share, mount_point, credentials_file
        self._extra_nas_store = Gtk.ListStore(bool, str, str, str, str, str)
        for n in self._cfg.get("extra_nas", []):
            self._extra_nas_store.append([
                n.get("enabled",          True),
                n.get("name",             ""),
                n.get("host",             ""),
                n.get("share",            "home"),
                n.get("mount_point",      str(Path.home() / ("Nas" + n.get("name", "Extra").replace(" ", "")))),
                n.get("credentials_file", str(Path.home() / ".smbcredentials")),
            ])

        tv = Gtk.TreeView(model=self._extra_nas_store)
        tv.set_rules_hint(True)

        r_toggle = Gtk.CellRendererToggle()
        r_toggle.connect("toggled", lambda r, p: self._extra_nas_store.__setitem__(
            p, [not self._extra_nas_store[p][0]] + list(self._extra_nas_store[p])[1:]
        ))
        tv.append_column(Gtk.TreeViewColumn("✓", r_toggle, active=0))

        for ci, (title, w, expand) in enumerate([
            ("Nom",              100, False),
            ("Hôte NAS",         130, False),
            ("Partage",           80, False),
            ("Point de montage", 180, True),
            ("Credentials",      150, False),
        ], 1):
            r = Gtk.CellRendererText()
            r.set_property("editable", True)
            r.connect("edited", lambda _r, p, t, c=ci: self._extra_nas_store.__setitem__(
                p, list(self._extra_nas_store[p])[:c] + [t] + list(self._extra_nas_store[p])[c+1:]
            ))
            col = Gtk.TreeViewColumn(title, r, text=ci)
            col.set_min_width(w)
            col.set_expand(expand)
            tv.append_column(col)

        self._extra_nas_tv = tv
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(130)
        sw.add(tv)
        vbox.pack_start(sw, True, True, 0)

        bbox = Gtk.Box(spacing=6)
        bbox.set_margin_top(6)

        def _add(_):
            self._extra_nas_store.append([
                True, "Nouveau NAS", "nas.local", "home",
                str(Path.home() / "NasExtra"),
                str(Path.home() / ".smbcredentials"),
            ])
        btn_add = Gtk.Button(label="+ Ajouter")
        btn_add.connect("clicked", _add)

        def _del(_):
            _, it = tv.get_selection().get_selected()
            if it:
                self._extra_nas_store.remove(it)
        btn_del = Gtk.Button(label="− Supprimer")
        btn_del.connect("clicked", _del)

        btn_mount = Gtk.Button(label="⏏ Monter maintenant")
        btn_mount.connect("clicked", self._mount_selected_nas)

        bbox.pack_start(btn_add,   False, False, 0)
        bbox.pack_start(btn_del,   False, False, 0)
        bbox.pack_end  (btn_mount, False, False, 0)
        vbox.pack_start(bbox, False, False, 0)

        sep = Gtk.Separator()
        sep.set_margin_top(8)
        vbox.pack_start(sep, False, False, 0)

        fstab_lbl = Gtk.Label()
        fstab_lbl.set_markup(
            "<small><b>Montage persistant (une seule fois, en administrateur) :</b>\n"
            "<tt>//HÔTE/PARTAGE  /POINT_MONTAGE  cifs  "
            "credentials=/home/USER/.smbcredentials,"
            "uid=UID,nofail,_netdev,x-systemd.automount,x-systemd.mount-timeout=5,x-systemd.idle-timeout=60,user,vers=3.0  0 0</tt>\n"
            "Puis : <tt>sudo systemctl daemon-reload &amp;&amp; sudo mount -a</tt></small>"
        )
        fstab_lbl.set_xalign(0)
        fstab_lbl.set_line_wrap(True)
        vbox.pack_start(fstab_lbl, False, False, 0)

        return self._wrap(vbox)

    def _mount_selected_nas(self, _):
        _, it = self._extra_nas_tv.get_selection().get_selected()
        if not it:
            return
        row       = list(self._extra_nas_store[it])
        host      = row[2]
        share     = row[3]
        mount_pt  = row[4]
        if not mount_pt:
            return
        Path(mount_pt).mkdir(parents=True, exist_ok=True)
        # 1er essai : mount via fstab
        r = subprocess.run(["mount", mount_pt], capture_output=True, timeout=15)
        if r.returncode == 0:
            _info_dialog(self, f"✓ Monté sur {mount_pt}")
            return
        # 2e essai : gio mount (sans point de montage fixe, mais sans sudo)
        if host and share:
            r2 = subprocess.run(
                ["gio", "mount", f"smb://{host}/{share}"],
                capture_output=True, timeout=15,
                env=os.environ.copy(),
            )
            if r2.returncode == 0:
                _info_dialog(self, f"✓ Monté via SMB : smb://{host}/{share}")
                return
        err = r.stderr.decode(errors="replace").strip() or \
              "Ajoutez une entrée dans /etc/fstab avec l'option 'user'."
        _info_dialog(self, f"Impossible de monter {mount_pt}\n\n{err}")

    # ── sauvegarde ────────────────────────────────────────────────────────────

    def _wrap(self, w):
        box = Gtk.Box(); box.pack_start(w, True, True, 0); return box

    def _save(self, _):
        cfg = dict(self._cfg)

        for key, entry in self._conn_entries.items():
            val = entry.get_text().strip()
            cfg[key] = int(val) if key == "nas_port" and val.isdigit() else val

        cfg["dirs"] = [
            {
                "enabled":      row[0],
                "local_sub":    row[1],
                "nas_sub":      row[2],
                "max_age_days": row[3],
                "max_size_mb":  row[4] * 1024,
            }
            for row in self._dirs_store
        ]

        cfg["check_interval"] = int(self._spin_check.get_value())
        cfg["sync_interval"]  = int(self._spin_sync.get_value())
        cfg["mtime_eps"]      = self._spin_eps.get_value()
        cfg["conflict_mode"]  = self._conflict_combo.get_active_id()

        cfg["notifications"]    = self._chk_notif.get_active()
        cfg["notif_min_files"]  = int(self._spin_min.get_value())

        # Avancé
        raw_excl = self._excl_buffer.get_text(
            self._excl_buffer.get_start_iter(),
            self._excl_buffer.get_end_iter(), False
        )
        cfg["exclude_patterns"]      = [l.strip() for l in raw_excl.splitlines() if l.strip()]
        cfg["backup_before_overwrite"] = self._chk_backup.get_active()
        cfg["backup_max_days"]        = int(self._spin_bkdays.get_value())
        cfg["deletion_sync"]          = self._chk_del.get_active()
        cfg["pause_on_battery"]       = self._chk_battery.get_active()
        cfg["pause_on_metered"]       = self._chk_metered.get_active()

        cfg["extra_nas"] = [
            {
                "enabled":          row[0],
                "name":             row[1],
                "host":             row[2],
                "share":            row[3],
                "mount_point":      row[4],
                "credentials_file": row[5],
                "auto_mount":       True,
            }
            for row in self._extra_nas_store
        ]

        cfg["mode"] = "portable"

        save_config(cfg)

        pid = get_daemon_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGHUP)
            except OSError:
                pass

        self.destroy()
        if self._on_saved:
            self._on_saved()


# ══════════════════════════════════════════════════════════════════════════════
# Application principale — icône barre système
# ══════════════════════════════════════════════════════════════════════════════

class NasSyncApp:
    def __init__(self):
        self._menu = self._build_menu()

        if HAS_APPINDICATOR:
            # Chemin AppIndicator3 (icône native barre système, nécessite l'extension GNOME)
            self._indicator  = AppIndicator3.Indicator.new(
                "nas-sync", ICON_ON,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
            self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            self._indicator.set_menu(self._menu)
            self._status_icon = None
        else:
            # Fallback Gtk.StatusIcon (fonctionne sur X11 et certaines sessions GNOME)
            self._indicator   = None
            self._status_icon = Gtk.StatusIcon()
            self._status_icon.set_from_icon_name(ICON_ON)
            self._status_icon.set_title("NAS Sync")
            self._status_icon.set_tooltip_text("NAS Sync — clic droit pour le menu")
            self._status_icon.set_visible(True)
            self._status_icon.connect("activate",   lambda _: self._show_recent())
            self._status_icon.connect("popup-menu", self._on_status_icon_popup)

        GLib.timeout_add(REFRESH, self._tick)
        self._tick()

    def _build_menu(self):
        menu = Gtk.Menu()

        self._item_status = Gtk.MenuItem(label="…")
        self._item_status.set_sensitive(False)
        menu.append(self._item_status)

        self._item_progress = Gtk.MenuItem(label="")
        self._item_progress.set_sensitive(False)
        menu.append(self._item_progress)

        menu.append(Gtk.SeparatorMenuItem())

        item_recent = Gtk.MenuItem(label="Fichiers récents…")
        item_recent.connect("activate", lambda _: self._show_recent())
        menu.append(item_recent)

        item_settings = Gtk.MenuItem(label="Paramètres…")
        item_settings.connect("activate", lambda _: self._show_settings())
        menu.append(item_settings)

        menu.append(Gtk.SeparatorMenuItem())

        item_sync_now = Gtk.MenuItem(label="Synchroniser maintenant")
        item_sync_now.connect("activate", lambda _: self._sync_now())
        menu.append(item_sync_now)

        self._item_toggle = Gtk.MenuItem(label="Désactiver la synchro")
        self._item_toggle.connect("activate", lambda _: self._toggle())
        menu.append(self._item_toggle)

        menu.append(Gtk.SeparatorMenuItem())

        item_about = Gtk.MenuItem(label=f"À propos  (v{APP_VERSION} — {APP_VERSION_NAME})")
        item_about.connect("activate", lambda _: self._show_about())
        menu.append(item_about)

        item_quit = Gtk.MenuItem(label="Quitter l'interface")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        return menu

    # ── fallback StatusIcon ───────────────────────────────────────────────────

    def _on_status_icon_popup(self, icon, button, time):
        self._menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, time)

    # ── actions ───────────────────────────────────────────────────────────────

    def _show_about(self):
        dlg = Gtk.AboutDialog()
        dlg.set_program_name("NAS Sync")
        dlg.set_version(f"{APP_VERSION}  —  {APP_VERSION_NAME}")
        dlg.set_comments("Synchronisation intelligente entre votre NAS et votre cache local.")
        dlg.set_license_type(Gtk.License.MIT_X11)
        dlg.run()
        dlg.destroy()

    def _show_recent(self):
        # fix 14 : nouvelle instance à chaque fois (fenêtre se destroy seule)
        win = RecentWindow()
        win.show_all()
        win.present()

    def _show_settings(self):
        # fix 14 : même approche
        win = SettingsWindow(on_saved=self._tick)
        win.show_all()
        win.present()

    def _sync_now(self):
        pid = get_daemon_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGUSR1)
                return
            except OSError:
                pass
        self._start_daemon()

    def _toggle(self):
        if is_daemon_running():
            self._stop_daemon()
        else:
            self._start_daemon()
        GLib.timeout_add(1500, self._tick)

    def _start_daemon(self):
        subprocess.Popen(
            [sys.executable, str(DAEMON_PY)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _stop_daemon(self):
        pid = get_daemon_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    # ── rafraîchissement ─────────────────────────────────────────────────────

    def _tick(self):
        running = is_daemon_running()

        # ── mise à jour de l'icône ──
        if self._indicator is not None:
            # AppIndicator3 : ACTIVE (visible) ou PASSIVE (invisible)
            if running:
                self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
                self._indicator.set_icon_full(ICON_ON, "NAS Sync actif")
            else:
                self._indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
                self._indicator.set_icon_full(ICON_OFF, "NAS Sync inactif")
        elif self._status_icon is not None:
            # Gtk.StatusIcon : toujours visible, icône change selon l'état
            icon = ICON_ON if running else ICON_OFF
            tip  = "NAS Sync — synchronisation active" if running else "NAS Sync — inactif"
            self._status_icon.set_from_icon_name(icon)
            self._status_icon.set_tooltip_text(tip)

        # ── mise à jour du menu ──
        if running:
            self._item_status.set_label("● Synchronisation active")
            self._item_toggle.set_label("Désactiver la synchro")
            prog   = read_progress()
            status = prog.get("status", "")
            if status == "synchro":
                done  = prog.get("done",  0)
                total = prog.get("total", 0)
                bd    = prog.get("bytes_done",  0)
                bt    = prog.get("bytes_total", 0)
                cur   = Path(prog.get("current", "")).name
                if bt > 0:
                    pct   = int(bd * 100 // bt)
                    label = (f"  {pct}% — {_fmt_size(bd)} / {_fmt_size(bt)}"
                             f"  ({done}/{total} fichiers)")
                else:
                    label = (f"  En cours : {cur}  ({done}/{total})"
                             if cur else f"  En cours ({done}/{total})")
            elif status == "pause":
                label = "  ⏸ En pause (batterie / réseau)"
            elif status == "hors ligne":
                label = "  Hors réseau local — en attente"
            elif status in ("idle", "arrêté", ""):
                label = "  ✓ Synchronisé"
            else:
                label = f"  {status.capitalize()}"
            self._item_progress.set_label(label)
        else:
            self._item_status.set_label("○ Synchronisation inactive")
            self._item_progress.set_label("")
            self._item_toggle.set_label("Activer la synchro")

        return True


# ══════════════════════════════════════════════════════════════════════════════

def main():
    from nas_sync_config import CONFIG_FILE

    # Premier démarrage : aucun fichier de config → lancer l'assistant
    if not CONFIG_FILE.exists():
        try:
            from first_run_wizard import run_wizard
        except ImportError:
            pass
        else:
            _done = [False]
            def _on_done():
                _done[0] = True
            run_wizard(on_done=_on_done)
            if not _done[0]:
                return  # assistant annulé par l'utilisateur

    # Migration : si l'ancien mode "fixe" était actif, basculer en automatique
    cfg = load_config()
    if cfg.get("mode") == "fixe":
        cfg["mode"] = "portable"
        save_config(cfg)
        _apply_mode_switch("portable", cfg)

    app = NasSyncApp()
    if not is_daemon_running():
        app._start_daemon()
    Gtk.main()


if __name__ == "__main__":
    main()
