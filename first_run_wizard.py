#!/usr/bin/env python3
"""
Assistant de premier démarrage NAS Sync.
Guide l'utilisateur pas à pas pour configurer la synchronisation.
Lancé automatiquement si aucune configuration n'existe.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nas_sync_config import (
    load_config, save_config, DEFAULT_CONFIG,
    CONFIG_FILE, LOCAL_BASE, NAS_MOUNT,
)

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

DAEMON_PY  = Path(__file__).parent / "nas_sync_daemon.py"
SERVICE    = "nas-sync.service"
SCRIPT_DIR = Path(__file__).parent


# ── page helpers ──────────────────────────────────────────────────────────────

def section(text: str) -> Gtk.Label:
    lbl = Gtk.Label()
    lbl.set_markup(f"<b>{text}</b>")
    lbl.set_xalign(0)
    lbl.set_margin_top(8)
    return lbl


def note(text: str) -> Gtk.Label:
    lbl = Gtk.Label()
    lbl.set_markup(f"<small><i>{text}</i></small>")
    lbl.set_xalign(0)
    lbl.set_line_wrap(True)
    return lbl


# ══════════════════════════════════════════════════════════════════════════════
# Assistant GTK
# ══════════════════════════════════════════════════════════════════════════════

class SetupWizard(Gtk.Assistant):
    def __init__(self, on_done=None):
        super().__init__()
        self._on_done = on_done
        self._cfg     = DEFAULT_CONFIG.copy()

        self.set_title("NAS Sync — Assistant de configuration")
        self.set_default_size(620, 500)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)

        self.connect("cancel",  self._on_cancel)
        self.connect("close",   self._on_close)
        self.connect("prepare", self._on_prepare)

        self._build_pages()
        self.show_all()

    # ── construction des pages ────────────────────────────────────────────────

    def _build_pages(self):
        self._p_welcome    = self._page_welcome()
        self._p_mode       = self._page_mode()
        self._p_connexion  = self._page_connexion()
        self._p_dossiers   = self._page_dossiers()
        self._p_options    = self._page_options()
        self._p_install    = self._page_install()

        for p, ptype, title, complete in [
            (self._p_welcome,   Gtk.AssistantPageType.INTRO,    "Bienvenue",                  True),
            (self._p_mode,      Gtk.AssistantPageType.CONTENT,  "1 · Mode d'utilisation",     True),
            (self._p_connexion, Gtk.AssistantPageType.CONTENT,  "2 · Connexion au NAS",       False),
            (self._p_dossiers,  Gtk.AssistantPageType.CONTENT,  "3 · Dossiers",               True),
            (self._p_options,   Gtk.AssistantPageType.CONTENT,  "4 · Options",                True),
            (self._p_install,   Gtk.AssistantPageType.SUMMARY,  "5 · Installation",           False),
        ]:
            self.append_page(p)
            self.set_page_type(p, ptype)
            self.set_page_title(p, title)
            self.set_page_complete(p, complete)

    # ── Page 0 : Bienvenue ────────────────────────────────────────────────────

    def _page_welcome(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_border_width(40)

        title = Gtk.Label()
        title.set_markup(
            "<span size='xx-large' weight='bold'>NAS Sync</span>\n"
            "<span size='large' foreground='#555'>Synchronisation automatique avec votre NAS</span>"
        )
        title.set_justify(Gtk.Justification.CENTER)
        vbox.pack_start(title, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 8)

        desc = Gtk.Label(
            label="Cet assistant va configurer la synchronisation automatique\n"
                  "de vos dossiers entre cette machine et votre NAS.\n\n"
                  "Une fois configuré, l'application fonctionne en arrière-plan :\n"
                  "• Vos fichiers sont toujours disponibles, même sans réseau.\n"
                  "• La synchronisation reprend automatiquement au retour.\n"
                  "• En cas de conflit, vous choisissez quelle version garder."
        )
        desc.set_justify(Gtk.Justification.CENTER)
        desc.set_line_wrap(True)
        vbox.pack_start(desc, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 8)

        prereq = Gtk.Label()
        prereq.set_markup(
            "<small>Prérequis : le NAS doit être monté sur <tt>~/NasShare</tt>\n"
            "avant de continuer. Sinon, contactez votre administrateur.</small>"
        )
        prereq.set_justify(Gtk.Justification.CENTER)
        vbox.pack_start(prereq, False, False, 0)

        return vbox

    # ── Page 1 : Mode d'utilisation ──────────────────────────────────────────

    def _page_mode(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(32)

        vbox.pack_start(note(
            "Choisissez le mode adapté à votre usage. "
            "Vous pourrez en changer à tout moment dans les Paramètres."
        ), False, False, 0)
        vbox.pack_start(Gtk.Separator(), False, False, 4)

        self._wiz_mode_portable = Gtk.RadioButton.new_with_label(
            None, "PC portable — cache local + synchronisation automatique"
        )
        self._wiz_mode_portable.set_active(True)
        desc_p = Gtk.Label(
            label="    Vos fichiers sont copiés localement dans ~/offline_cache/.\n"
                  "    Accessibles même sans réseau. Synchronisés dès la reconnexion au NAS."
        )
        desc_p.set_xalign(0)
        desc_p.set_line_wrap(True)
        vbox.pack_start(self._wiz_mode_portable, False, False, 0)
        vbox.pack_start(desc_p, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)

        self._wiz_mode_fixe = Gtk.RadioButton.new_with_label_from_widget(
            self._wiz_mode_portable, "PC fixe — accès direct au NAS"
        )
        desc_f = Gtk.Label(
            label="    Vos dossiers pointent directement vers le NAS.\n"
                  "    Simple et rapide. Le NAS doit être accessible en permanence.\n"
                  "    La synchronisation automatique est désactivée."
        )
        desc_f.set_xalign(0)
        desc_f.set_line_wrap(True)
        vbox.pack_start(self._wiz_mode_fixe, False, False, 0)
        vbox.pack_start(desc_f, False, False, 0)

        return vbox

    # ── Page 2 : Connexion NAS ────────────────────────────────────────────────

    def _page_connexion(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_border_width(24)

        vbox.pack_start(note("Renseignez les paramètres de votre NAS, puis testez la connexion."), False, False, 0)

        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(12)
        grid.set_margin_top(8)

        fields = [
            ("Hôte NAS :",     "nas_host",   "Cassis.local",  False),
            ("Partage SMB :",  "nas_share",  "home",          False),
            ("Point de montage :", "nas_mount", str(Path.home() / "NasShare"), False),
            ("Utilisateur :",  "nas_user",   "votre_login",   False),
            ("Mot de passe :", "nas_pass",   "••••••••",      True),
        ]
        self._wiz_entries = {}
        for row, (lbl, key, ph, secret) in enumerate(fields):
            l = Gtk.Label(label=lbl); l.set_xalign(1)
            e = Gtk.Entry()
            e.set_placeholder_text(ph)
            e.set_hexpand(True)
            if secret:
                e.set_visibility(False)
            if key == "nas_host":
                e.set_text(self._cfg.get("nas_host", "Cassis.local"))
            elif key == "nas_mount":
                e.set_text(str(Path.home() / "NasShare"))
            grid.attach(l, 0, row, 1, 1)
            grid.attach(e, 1, row, 1, 1)
            self._wiz_entries[key] = e
        vbox.pack_start(grid, False, False, 0)

        # Zone test
        test_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        test_box.set_margin_top(8)
        self._btn_test = Gtk.Button(label="Tester la connexion")
        self._btn_test.connect("clicked", self._test_connection)
        self._status_lbl = Gtk.Label(label="")
        test_box.pack_start(self._btn_test, False, False, 0)
        test_box.pack_start(self._status_lbl, False, False, 0)
        vbox.pack_start(test_box, False, False, 0)

        # Montage NAS
        vbox.pack_start(Gtk.Separator(), False, False, 4)
        vbox.pack_start(section("Montage SMB"), False, False, 0)

        self._mount_status = Gtk.Label()
        self._mount_status.set_xalign(0)
        self._mount_status.set_line_wrap(True)
        vbox.pack_start(self._mount_status, False, False, 0)

        self._fstab_frame = Gtk.Frame(label="  Commande administrateur (si pas encore monté)  ")
        fstab_tv = Gtk.TextView()
        fstab_tv.set_editable(False)
        fstab_tv.set_monospace(True)
        fstab_tv.set_wrap_mode(Gtk.WrapMode.WORD)
        self._fstab_buf = fstab_tv.get_buffer()
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(60)
        sw.add(fstab_tv)
        self._fstab_frame.add(sw)
        vbox.pack_start(self._fstab_frame, False, False, 0)

        self._update_fstab_hint()
        for e in self._wiz_entries.values():
            e.connect("changed", lambda _: self._update_fstab_hint())

        self._check_mount_status()
        return vbox

    def _update_fstab_hint(self):
        host   = self._wiz_entries["nas_host"].get_text().strip()
        share  = self._wiz_entries["nas_share"].get_text().strip()
        mount  = self._wiz_entries["nas_mount"].get_text().strip()
        user   = self._wiz_entries["nas_user"].get_text().strip()
        user_display = user or "USER"
        line   = (
            f"# 1. Créer le fichier de credentials :\n"
            f"echo 'username={user_display}' > ~/.smbcredentials\n"
            f"echo 'password=MOT_DE_PASSE'    >> ~/.smbcredentials\n"
            f"chmod 600 ~/.smbcredentials\n\n"
            f"# 2. Ajouter dans /etc/fstab (une ligne) :\n"
            f"//{host or 'NAS_HOST'}/{share or 'SHARE'} {mount or '~/NasShare'} "
            f"cifs credentials=~/.smbcredentials,uid=$(id -u),nofail,_netdev,vers=3.0 0 0\n\n"
            f"# 3. Monter :\n"
            f"sudo systemctl daemon-reload && sudo mount -a"
        )
        self._fstab_buf.set_text(line)

    def _check_mount_status(self):
        mount = self._wiz_entries["nas_mount"].get_text().strip()
        if Path(mount).is_mount():
            self._mount_status.set_markup(
                '<span foreground="green">✓ NAS déjà monté — vous pouvez continuer.</span>'
            )
            self._fstab_frame.hide()
            self.set_page_complete(self._p_connexion, True)
        else:
            self._mount_status.set_markup(
                '<span foreground="orange">⚠ NAS non monté. Suivez les instructions ci-dessous\n'
                'ou demandez à votre administrateur, puis relancez cet assistant.</span>'
            )
            self._fstab_frame.show_all()

    def _test_connection(self, _):
        host = self._wiz_entries["nas_host"].get_text().strip()
        self._btn_test.set_sensitive(False)
        self._status_lbl.set_markup("<small>Test en cours…</small>")

        def do_test():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                ok = s.connect_ex((host, 445)) == 0
                s.close()
            except Exception:
                ok = False
            GLib.idle_add(self._on_test_done, ok)

        threading.Thread(target=do_test, daemon=True).start()

    def _on_test_done(self, ok: bool):
        if ok:
            self._status_lbl.set_markup(
                '<small><span foreground="green">✓ NAS accessible sur le port 445</span></small>'
            )
            self._check_mount_status()
        else:
            self._status_lbl.set_markup(
                '<small><span foreground="red">✗ Inaccessible — vérifiez l\'hôte et le réseau</span></small>'
            )
        self._btn_test.set_sensitive(True)
        return False

    # ── Page 2 : Dossiers ─────────────────────────────────────────────────────

    def _page_dossiers(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_border_width(24)
        vbox.pack_start(note(
            "Sélectionnez les dossiers à synchroniser. "
            "Le filtre par âge évite de dupliquer des années de fichiers volumineux."
        ), False, False, 0)

        self._dir_rows = []
        defaults = [
            ("Bureau",          "Desktop",   "Desktop",   True,  0),
            ("Téléchargements", "Downloads", "Downloads", True,  90),
            ("Documents",       "Documents", "Documents", True,  0),
            ("Images",          "Pictures",  "Pictures",  True,  0),
            ("Musique",         "Music",     "Music",     True,  180),
            ("Vidéos",          "video",     "video",     True,  90),
        ]

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(10)
        grid.set_margin_top(8)

        for col, (h, w) in enumerate(
            [("", 30), ("Dossier", 160), ("NAS", 130), ("Âge max (jours, 0=tout)", 180)]
        ):
            lbl = Gtk.Label()
            lbl.set_markup(f"<b>{h}</b>")
            lbl.set_xalign(0)
            lbl.set_size_request(w, -1)
            grid.attach(lbl, col, 0, 1, 1)

        for row, (label, local_sub, nas_sub, enabled, max_age) in enumerate(defaults, 1):
            chk = Gtk.CheckButton(label=label)
            chk.set_active(enabled)

            e_local = Gtk.Entry(); e_local.set_text(local_sub); e_local.set_width_chars(14)
            e_nas   = Gtk.Entry(); e_nas.set_text(nas_sub);     e_nas.set_width_chars(12)

            spin = Gtk.SpinButton()
            spin.set_adjustment(Gtk.Adjustment(value=max_age, lower=0, upper=3650, step_increment=30))
            spin.set_numeric(True)

            grid.attach(chk,     0, row, 1, 1)
            grid.attach(e_local, 1, row, 1, 1)
            grid.attach(e_nas,   2, row, 1, 1)
            grid.attach(spin,    3, row, 1, 1)
            self._dir_rows.append((chk, e_local, e_nas, spin))

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(280)
        sw.add(grid)
        vbox.pack_start(sw, True, True, 0)
        return vbox

    # ── Page 3 : Options ──────────────────────────────────────────────────────

    def _page_options(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(24)

        vbox.pack_start(section("Fréquence de synchronisation"), False, False, 0)

        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)

        self._spin_check = self._spin_widget(5, 3600, 5, 30)
        self._spin_sync  = self._spin_widget(30, 86400, 30, 300)

        for i, (lbl, w, suf) in enumerate([
            ("Vérification NAS toutes les :", self._spin_check, "secondes"),
            ("Synchro périodique toutes les :", self._spin_sync, "secondes"),
        ]):
            grid.attach(Gtk.Label(label=lbl), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
            grid.attach(Gtk.Label(label=suf), 2, i, 1, 1)
        vbox.pack_start(grid, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)
        vbox.pack_start(section("Comportement"), False, False, 0)

        self._chk_backup  = Gtk.CheckButton(label="Sauvegarder les fichiers avant de les écraser")
        self._chk_backup.set_active(True)
        self._chk_battery = Gtk.CheckButton(label="Mettre en pause si sur batterie")
        self._chk_notif   = Gtk.CheckButton(label="Afficher les notifications bureau")
        self._chk_notif.set_active(True)

        for w in [self._chk_backup, self._chk_battery, self._chk_notif]:
            vbox.pack_start(w, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)
        vbox.pack_start(section("Résolution automatique des conflits"), False, False, 0)

        self._conflict_combo = Gtk.ComboBoxText()
        for val, lbl in [
            ("ask",        "Demander à chaque fois (recommandé)"),
            ("keep_local", "Toujours garder la version locale"),
            ("keep_nas",   "Toujours garder la version NAS"),
        ]:
            self._conflict_combo.append(val, lbl)
        self._conflict_combo.set_active(0)
        vbox.pack_start(self._conflict_combo, False, False, 0)

        return vbox

    def _spin_widget(self, lo, hi, step, val) -> Gtk.SpinButton:
        s = Gtk.SpinButton()
        s.set_adjustment(Gtk.Adjustment(value=val, lower=lo, upper=hi, step_increment=step))
        s.set_numeric(True)
        return s

    # ── Page 4 : Installation ─────────────────────────────────────────────────

    def _page_install(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(24)

        self._install_title = Gtk.Label()
        self._install_title.set_markup("<b>Installation en cours…</b>")
        self._install_title.set_xalign(0)
        vbox.pack_start(self._install_title, False, False, 0)

        self._progress = Gtk.ProgressBar()
        self._progress.set_pulse_step(0.1)
        vbox.pack_start(self._progress, False, False, 0)

        self._install_log = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=self._install_log)
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(280)
        sw.add(tv)
        self._install_tv = tv
        vbox.pack_start(sw, True, True, 0)

        return vbox

    # ── Logique d'installation ────────────────────────────────────────────────

    def _on_prepare(self, assistant, page):
        """Déclenché quand on arrive sur une page."""
        if page is self._p_install:
            GLib.idle_add(self._run_install)

    def _log(self, msg: str):
        end = self._install_log.get_end_iter()
        self._install_log.insert(end, msg + "\n")
        # scroll vers le bas
        adj = self._install_tv.get_vadjustment()
        adj.set_value(adj.get_upper())

    def _set_progress(self, frac: float, text: str = ""):
        self._progress.set_fraction(frac)
        if text:
            self._progress.set_text(text)
            self._progress.set_show_text(True)

    def _run_install(self):
        threading.Thread(target=self._do_install, daemon=True).start()
        return False

    def _do_install(self):
        def ui(fn): GLib.idle_add(fn)
        def log(m): GLib.idle_add(self._log, m)
        def prog(f, t=""): GLib.idle_add(self._set_progress, f, t)

        try:
            # ── 0. Lire le mode choisi ────────────────────────────────────────
            mode = "fixe" if self._wiz_mode_fixe.get_active() else "portable"
            log(f"→ Mode sélectionné : {mode}")

            # ── 1. Construire la config ───────────────────────────────────────
            log("→ Création de la configuration…")
            prog(0.05, "Configuration…")

            dirs = []
            for chk, e_local, e_nas, spin in self._dir_rows:
                if chk.get_active():
                    dirs.append({
                        "local_sub":    e_local.get_text().strip(),
                        "nas_sub":      e_nas.get_text().strip(),
                        "enabled":      True,
                        "max_age_days": int(spin.get_value()),
                    })

            cfg = DEFAULT_CONFIG.copy()
            cfg["mode"]                   = mode
            cfg["nas_host"]               = self._wiz_entries["nas_host"].get_text().strip()
            cfg["nas_mount"]              = self._wiz_entries["nas_mount"].get_text().strip()
            cfg["check_interval"]         = int(self._spin_check.get_value())
            cfg["sync_interval"]          = int(self._spin_sync.get_value())
            cfg["conflict_mode"]          = self._conflict_combo.get_active_id()
            cfg["backup_before_overwrite"] = self._chk_backup.get_active()
            cfg["pause_on_battery"]       = self._chk_battery.get_active()
            cfg["notifications"]          = self._chk_notif.get_active()
            cfg["dirs"]                   = dirs
            save_config(cfg)
            log("  ✓ Configuration enregistrée")
            prog(0.15)

            # ── 2. Écrire .smbcredentials ─────────────────────────────────────
            user = self._wiz_entries["nas_user"].get_text().strip()
            pwd  = self._wiz_entries["nas_pass"].get_text().strip()
            if user and pwd:
                creds = Path.home() / ".smbcredentials"
                creds.write_text(f"username={user}\npassword={pwd}\ndomain=WORKGROUP\n")
                creds.chmod(0o600)
                log("  ✓ Credentials SMB enregistrés")
            prog(0.25)

            # ── 3. Créer le cache local (toujours, pour pouvoir basculer en portable) ─
            log("→ Création du cache local ~/offline_cache/…")
            for d in dirs:
                (Path.home() / "offline_cache" / d["local_sub"]).mkdir(parents=True, exist_ok=True)
            log("  ✓ Dossiers créés")
            prog(0.40)

            # ── 4. Liens symboliques ──────────────────────────────────────────
            log("→ Mise à jour des liens symboliques…")
            nas_mount_path = Path(cfg.get("nas_mount", str(Path.home() / "NasShare")))
            link_base = nas_mount_path if mode == "fixe" else (Path.home() / "offline_cache")
            FR_LINKS = {
                "Desktop":   ["Bureau"],
                "Downloads": ["Téléchargements"],
                "Documents": ["Documents"],
                "Pictures":  ["Images"],
                "Music":     ["Musique"],
                "video":     ["Vidéos"],
            }
            local_subs = {d["local_sub"] for d in dirs}
            for local_sub, fr_names in FR_LINKS.items():
                if local_sub not in local_subs:
                    continue
                target = link_base / local_sub
                for name in fr_names:
                    link = Path.home() / name
                    if link.is_symlink():
                        link.unlink()
                    elif link.is_dir():
                        try:
                            link.rmdir()
                        except OSError:
                            pass
                    if not link.exists():
                        link.symlink_to(target)
                        log(f"  ✓ ~/{name} → {target}")
            prog(0.55)

            # ── 5. XDG user-dirs ─────────────────────────────────────────────
            log("→ Mise à jour de ~/.config/user-dirs.dirs…")
            xdg_base = f"$HOME/NasShare" if mode == "fixe" else "$HOME/offline_cache"
            xdg_map = {
                "Desktop":   "XDG_DESKTOP_DIR",
                "Downloads": "XDG_DOWNLOAD_DIR",
                "Documents": "XDG_DOCUMENTS_DIR",
                "Music":     "XDG_MUSIC_DIR",
                "Pictures":  "XDG_PICTURES_DIR",
                "video":     "XDG_VIDEOS_DIR",
            }
            lines = []
            for local_sub, xdg_key in xdg_map.items():
                if local_sub in local_subs:
                    lines.append(f'{xdg_key}="{xdg_base}/{local_sub}"')
            xdg_file = Path.home() / ".config" / "user-dirs.dirs"
            xdg_file.write_text("\n".join(lines) + "\n")
            subprocess.run(["xdg-user-dirs-update"], capture_output=True)
            log("  ✓ XDG dirs mis à jour")
            prog(0.65)

            # ── 6. Service systemd (portable seulement) ───────────────────────
            if mode == "portable":
                log("→ Installation du service systemd…")
                svc_dir = Path.home() / ".config" / "systemd" / "user"
                svc_dir.mkdir(parents=True, exist_ok=True)
                svc = svc_dir / SERVICE
                svc.write_text(
                    f"[Unit]\nDescription=NAS Sync Daemon\n"
                    f"After=network.target graphical-session.target\n"
                    f"PartOf=graphical-session.target\n\n"
                    f"[Service]\nType=simple\n"
                    f"ExecStart=/usr/bin/python3 {DAEMON_PY}\n"
                    f"Restart=on-failure\nRestartSec=15\n\n"
                    f"[Install]\nWantedBy=graphical-session.target\n"
                )
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
                subprocess.run(["systemctl", "--user", "enable", SERVICE], capture_output=True)
                log("  ✓ Service installé et activé")
            else:
                log("  ℹ Mode PC fixe — service de synchronisation non activé")
            prog(0.80)

            # ── 7. Entrée menu GNOME ──────────────────────────────────────────
            log("→ Création de l'entrée menu GNOME…")
            _install_desktop_entry()
            log("  ✓ Application visible dans GNOME Activities")
            prog(0.90)

            # ── 8. Synchronisation initiale NAS → cache local ─────────────────
            if mode == "portable":
                import re as _re
                nas_mnt = Path(cfg.get("nas_mount", str(Path.home() / "NasShare")))
                if nas_mnt.is_mount():
                    log("→ Synchronisation initiale NAS → cache local…")

                    total_bytes = 0
                    for d in dirs:
                        nas_dir = nas_mnt / d.get("nas_sub", d["local_sub"])
                        if nas_dir.exists():
                            try:
                                r2 = subprocess.run(
                                    ["du", "-sb", str(nas_dir)],
                                    capture_output=True, text=True, timeout=30,
                                )
                                if r2.returncode == 0:
                                    total_bytes += int(r2.stdout.split()[0])
                            except Exception:
                                pass

                    def _fmt_b(n):
                        if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f} Go"
                        if n >= 1_000_000:     return f"{n/1_000_000:.1f} Mo"
                        if n >= 1_000:         return f"{n/1_000:.0f} Ko"
                        return f"{n} o"

                    if total_bytes > 0:
                        log(f"  Volume estimé : {_fmt_b(total_bytes)}")

                    bytes_offset = 0
                    for d in dirs:
                        src = nas_mnt / d.get("nas_sub", d["local_sub"])
                        dst = Path.home() / "offline_cache" / d["local_sub"]
                        if not src.exists():
                            log(f"  ⚠ {d['local_sub']} absent sur le NAS — ignoré")
                            continue
                        log(f"  Copie {d['local_sub']}…")
                        try:
                            proc = subprocess.Popen(
                                ["rsync", "-ah", "--ignore-existing", "--info=progress2",
                                 str(src) + "/", str(dst) + "/"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, bufsize=1,
                            )
                            dir_bytes = 0
                            for line in proc.stdout:
                                m2 = _re.match(r'\s*([\d,]+)\s+\d+%', line)
                                if m2:
                                    dir_bytes = int(m2.group(1).replace(',', ''))
                                    if total_bytes > 0:
                                        done_b = bytes_offset + dir_bytes
                                        pct    = int(done_b * 100 / total_bytes)
                                        frac   = 0.90 + 0.07 * done_b / total_bytes
                                        GLib.idle_add(
                                            self._set_progress,
                                            min(frac, 0.97),
                                            f"{pct}% — {_fmt_b(done_b)} / {_fmt_b(total_bytes)}",
                                        )
                            proc.wait()
                            bytes_offset += dir_bytes
                            log(f"  ✓ {d['local_sub']} ({_fmt_b(dir_bytes)} copiés)")
                        except FileNotFoundError:
                            log("  ⚠ rsync non disponible — synchro initiale ignorée")
                            break
                        except Exception as e:
                            log(f"  ⚠ Erreur rsync {d['local_sub']}: {e}")
                else:
                    log("  ℹ NAS non monté — synchro initiale ignorée")
                    log("    Relancez l'assistant depuis chez vous pour la copie initiale")
            prog(0.97)

            # ── 9. Démarrer le démon (portable seulement) ─────────────────────
            if mode == "portable":
                log("→ Démarrage du service…")
                subprocess.run(["systemctl", "--user", "start", SERVICE], capture_output=True)
                import time; time.sleep(1)
                r = subprocess.run(["systemctl", "--user", "is-active", SERVICE],
                                   capture_output=True, text=True)
                if r.stdout.strip() == "active":
                    log("  ✓ Service démarré")
                else:
                    log("  ⚠ Service démarré (vérifiez : systemctl --user status nas-sync)")
            else:
                log("  ℹ Mode PC fixe — pas de démon, accès direct au NAS")
            prog(1.0)

            GLib.idle_add(self._install_done)

        except Exception as e:
            GLib.idle_add(self._install_error, str(e))

    def _install_done(self):
        self._install_title.set_markup(
            '<span foreground="green"><b>✓ Installation terminée !</b></span>\n'
            '<small>NAS Sync est actif. L\'icône apparaît dans la barre système\n'
            'quand la synchronisation est en cours.</small>'
        )
        self.set_page_complete(self._p_install, True)
        return False

    def _install_error(self, msg: str):
        self._install_title.set_markup(
            f'<span foreground="red"><b>✗ Erreur lors de l\'installation</b></span>\n'
            f'<small>{msg}</small>'
        )
        self._log(f"\nERREUR : {msg}")
        self.set_page_complete(self._p_install, True)
        return False

    def _on_cancel(self, _):
        Gtk.main_quit()

    def _on_close(self, _):
        if self._on_done:
            self._on_done()
        Gtk.main_quit()


# ── entrée menu GNOME ─────────────────────────────────────────────────────────

def _install_desktop_entry():
    app_dir = Path.home() / ".local" / "share" / "applications"
    app_dir.mkdir(parents=True, exist_ok=True)
    desktop = app_dir / "nas-sync.desktop"
    desktop.write_text(
        f"[Desktop Entry]\n"
        f"Name=NAS Sync\n"
        f"GenericName=Synchronisation NAS\n"
        f"Comment=Synchronisation automatique avec le NAS d'entreprise\n"
        f"Exec=/usr/bin/python3 {SCRIPT_DIR}/nas_sync_app.py\n"
        f"Icon=network-server\n"
        f"Type=Application\n"
        f"Categories=Utility;Network;FileManager;\n"
        f"Keywords=nas;sync;synchronisation;réseau;partage;\n"
        f"StartupNotify=false\n"
        f"Terminal=false\n"
    )
    subprocess.run(["update-desktop-database", str(app_dir)], capture_output=True)


# ── point d'entrée ────────────────────────────────────────────────────────────

def run_wizard(on_done=None):
    wizard = SetupWizard(on_done=on_done)
    Gtk.main()


if __name__ == "__main__":
    run_wizard()
