#!/usr/bin/env python3
"""
Assistant de premier démarrage NAS Sync.
Guide l'utilisateur pas à pas pour configurer la synchronisation.
Lancé automatiquement si aucune configuration n'existe.
"""

import os
import shutil
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
            f"cifs credentials=~/.smbcredentials,uid=$(id -u),nofail,_netdev,x-systemd.automount,x-systemd.mount-timeout=5,x-systemd.idle-timeout=60,user,vers=3.0 0 0\n\n"
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
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(20)

        self._dir_rows        = []
        self._dir_size_labels = []
        self._nas_dir_sizes   = {}

        # (nom affiché, local_sub, nas_sub, enabled, max_age_days, quota_go)
        defaults = [
            ("Bureau",          "Desktop",   "Desktop",   True,  0,   0),
            ("Téléchargements", "Downloads", "Downloads", True,  90,  0),
            ("Documents",       "Documents", "Documents", True,  0,   0),
            ("Images",          "Pictures",  "Pictures",  True,  0,   0),
            ("Musique",         "Music",     "Music",     True,  180, 0),
            ("Vidéos",          "video",     "video",     True,  90,  0),
        ]

        # ── en-têtes ─────────────────────────────────────────────────────────
        hdr = Gtk.Grid()
        hdr.set_column_spacing(10)
        for col, (h, w) in enumerate([
            ("Dossier", 160), ("Sur le NAS", 90), ("Quota (Go, 0 = tout)", 140), ("Âge max (j, 0=∞)", 120),
        ]):
            lbl = Gtk.Label()
            lbl.set_markup(f"<small><b>{h}</b></small>")
            lbl.set_xalign(0)
            lbl.set_size_request(w, -1)
            hdr.attach(lbl, col, 0, 1, 1)
        vbox.pack_start(hdr, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 0)

        # ── lignes dossiers ───────────────────────────────────────────────────
        self._dir_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for label, local_sub, nas_sub, enabled, max_age, quota_go in defaults:
            row_box = Gtk.Grid()
            row_box.set_column_spacing(10)
            row_box.set_margin_top(2)

            chk = Gtk.CheckButton(label=label)
            chk.set_active(enabled)
            chk.set_size_request(160, -1)
            chk.connect("toggled", lambda *_: GLib.idle_add(self._update_disk_banner))

            size_lbl = Gtk.Label(label="…")
            size_lbl.set_xalign(0)
            size_lbl.set_size_request(90, -1)

            spin_size = Gtk.SpinButton()
            spin_size.set_adjustment(Gtk.Adjustment(
                value=quota_go, lower=0, upper=100_000, step_increment=1, page_increment=10,
            ))
            spin_size.set_numeric(True)
            spin_size.set_size_request(140, -1)
            spin_size.set_tooltip_text("0 = tout télécharger. Sinon, seuls les fichiers les plus récents sont conservés.")

            spin_age = Gtk.SpinButton()
            spin_age.set_adjustment(Gtk.Adjustment(value=max_age, lower=0, upper=3650, step_increment=30))
            spin_age.set_numeric(True)
            spin_age.set_size_request(120, -1)
            spin_age.set_tooltip_text("0 = aucun filtre par âge.")

            # champs cachés (nom interne)
            e_local = Gtk.Entry(); e_local.set_text(local_sub); e_local.set_no_show_all(True)
            e_nas   = Gtk.Entry(); e_nas.set_text(nas_sub);     e_nas.set_no_show_all(True)

            row_box.attach(chk,       0, 0, 1, 1)
            row_box.attach(size_lbl,  1, 0, 1, 1)
            row_box.attach(spin_size, 2, 0, 1, 1)
            row_box.attach(spin_age,  3, 0, 1, 1)
            row_box.attach(e_local,   4, 0, 1, 1)
            row_box.attach(e_nas,     5, 0, 1, 1)

            self._dir_list_box.pack_start(row_box, False, False, 0)
            self._dir_rows.append((chk, e_local, e_nas, spin_age, spin_size))
            self._dir_size_labels.append(size_lbl)

        vbox.pack_start(self._dir_list_box, False, False, 0)
        vbox.pack_start(Gtk.Separator(), False, False, 4)

        # ── bannière bilan espace ─────────────────────────────────────────────
        self._disk_banner = Gtk.Label()
        self._disk_banner.set_markup(
            "<small><i>Connexion au NAS requise pour afficher les tailles réelles.</i></small>"
        )
        self._disk_banner.set_xalign(0)
        self._disk_banner.set_line_wrap(True)
        vbox.pack_start(self._disk_banner, False, False, 0)

        self._btn_recommend = Gtk.Button(label="Répartir automatiquement l'espace disponible")
        self._btn_recommend.connect("clicked", self._apply_recommendations)
        self._btn_recommend.set_sensitive(False)
        self._btn_recommend.set_halign(Gtk.Align.START)
        self._btn_recommend.set_tooltip_text(
            "Calcule un quota par dossier pour que tout tienne sur le disque (85% de l'espace libre)."
        )
        vbox.pack_start(self._btn_recommend, False, False, 0)

        return vbox

    # ── Scan NAS et recommandations ───────────────────────────────────────────

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n >= 1_073_741_824:
            return f"{n / 1_073_741_824:.1f} Go"
        if n >= 1_048_576:
            return f"{n / 1_048_576:.0f} Mo"
        if n >= 1024:
            return f"{n / 1024:.0f} Ko"
        return f"{n} o"

    def _scan_nas_sizes_async(self):
        """Lance le scan des tailles des dossiers NAS dans un thread de fond."""
        mount = self._wiz_entries["nas_mount"].get_text().strip()
        for lbl in self._dir_size_labels:
            lbl.set_markup("<small><i>…</i></small>")
        self._disk_banner.set_markup("<small><i>Calcul des tailles en cours…</i></small>")
        self._btn_recommend.set_sensitive(False)

        rows_snapshot = [
            (e_local.get_text().strip(), e_nas.get_text().strip())
            for _, e_local, e_nas, _, _ in self._dir_rows
        ]

        def do_scan():
            mount_path = Path(mount)
            if not mount_path.is_mount():
                GLib.idle_add(self._on_scan_done, {})
                return
            sizes = {}
            for local_sub, nas_sub in rows_snapshot:
                nas_dir = mount_path / nas_sub
                if nas_dir.is_dir():
                    try:
                        r = subprocess.run(
                            ["du", "-sb", str(nas_dir)],
                            capture_output=True, text=True, timeout=60,
                        )
                        sizes[local_sub] = int(r.stdout.split()[0]) if r.returncode == 0 else 0
                    except Exception:
                        sizes[local_sub] = 0
                else:
                    sizes[local_sub] = 0
            GLib.idle_add(self._on_scan_done, sizes)

        threading.Thread(target=do_scan, daemon=True).start()
        return False

    def _on_scan_done(self, sizes: dict):
        self._nas_dir_sizes = sizes
        for i, (_, e_local, _, _, _) in enumerate(self._dir_rows):
            sz = sizes.get(e_local.get_text().strip())
            if sz is None:
                self._dir_size_labels[i].set_text("—")
            elif sz == 0:
                self._dir_size_labels[i].set_markup("<small>vide</small>")
            else:
                self._dir_size_labels[i].set_markup(f"<small>{self._fmt_size(sz)}</small>")
        self._update_disk_banner()

    def _update_disk_banner(self):
        """Recalcule et affiche le bilan espace disque / recommandation."""
        if not self._nas_dir_sizes:
            return

        local_base = Path(DEFAULT_CONFIG["local_base"])
        if not local_base.exists():
            local_base = Path.home()
        try:
            avail = shutil.disk_usage(str(local_base)).free
        except Exception:
            avail = 0

        selected_total = sum(
            self._nas_dir_sizes.get(e_local.get_text().strip(), 0)
            for chk, e_local, _, _, _ in self._dir_rows
            if chk.get_active()
        )

        avail_s    = self._fmt_size(avail)
        selected_s = self._fmt_size(selected_total)

        if selected_total == 0:
            self._disk_banner.set_markup(
                f"<small>Disque libre : <b>{avail_s}</b> | Sélection NAS : —</small>"
            )
            self._btn_recommend.set_sensitive(False)
            return

        if selected_total <= avail * 0.85:
            self._disk_banner.set_markup(
                f'<small>Disque libre : <b>{avail_s}</b> | Sélection NAS : <b>{selected_s}</b> '
                f'<span foreground="green">✓ Tout rentre — aucune limite nécessaire</span></small>'
            )
        else:
            manque = self._fmt_size(selected_total - int(avail * 0.85))
            self._disk_banner.set_markup(
                f'<small>Disque libre : <b>{avail_s}</b> | Sélection NAS : <b>{selected_s}</b> '
                f'<span foreground="orange">⚠ Manque ~{manque} — appliquez les limites suggérées '
                f'ou désactivez des dossiers volumineux</span></small>'
            )
        self._btn_recommend.set_sensitive(True)

    def _apply_recommendations(self, _=None):
        """Remplit les spin_size avec des quotas proportionnels à l'espace disponible."""
        local_base = Path(DEFAULT_CONFIG["local_base"])
        if not local_base.exists():
            local_base = Path.home()
        try:
            avail = shutil.disk_usage(str(local_base)).free
        except Exception:
            return

        budget = int(avail * 0.85)
        selected = [
            (spin_size, self._nas_dir_sizes.get(e_local.get_text().strip(), 0))
            for chk, e_local, _, _, spin_size in self._dir_rows
            if chk.get_active()
        ]
        if not selected:
            return

        total_nas = sum(sz for _, sz in selected)

        if total_nas == 0 or total_nas <= budget:
            for spin, _ in selected:
                spin.set_value(0)   # 0 = illimité, tout rentre
        else:
            for spin, sz in selected:
                quota_go = max(1, int(sz / total_nas * budget) // 1_073_741_824) if sz > 0 else 0
                spin.set_value(quota_go)

        self._update_disk_banner()

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
        self._chk_notif   = Gtk.CheckButton(label="Afficher les notifications bureau (GNOME, KDE, XFCE…)")
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
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_border_width(20)

        # ── Phase 1 : Configuration ───────────────────────────────────────────
        ph1_hdr = Gtk.Label()
        ph1_hdr.set_markup("<b>① Configuration</b>")
        ph1_hdr.set_xalign(0)
        ph1_hdr.set_margin_bottom(6)
        vbox.pack_start(ph1_hdr, False, False, 0)

        self._step_labels = {}
        for key, text in [
            ("config",    "Paramètres enregistrés"),
            ("creds",     "Credentials SMB"),
            ("cache",     "Cache local créé"),
            ("symlinks",  "Liens symboliques"),
            ("xdg",       "Dossiers XDG"),
            ("service",   "Service systemd"),
            ("menu",      "Entrée menu applications"),
        ]:
            row = Gtk.Box(spacing=8)
            row.set_margin_start(16)
            row.set_margin_bottom(2)
            icon = Gtk.Label(label="○")
            icon.set_width_chars(2)
            lbl = Gtk.Label(label=text)
            lbl.set_xalign(0)
            row.pack_start(icon, False, False, 0)
            row.pack_start(lbl,  False, False, 0)
            vbox.pack_start(row, False, False, 0)
            self._step_labels[key] = (icon, lbl)

        vbox.pack_start(Gtk.Separator(), False, False, 10)

        # ── Phase 2 : Synchronisation ─────────────────────────────────────────
        ph2_hdr = Gtk.Label()
        ph2_hdr.set_markup("<b>② Synchronisation initiale</b>")
        ph2_hdr.set_xalign(0)
        ph2_hdr.set_margin_bottom(6)
        vbox.pack_start(ph2_hdr, False, False, 0)
        self._ph2_header = ph2_hdr

        self._sync_rows  = {}   # local_sub → (bar, lbl)
        self._sync_box   = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._sync_box.set_margin_start(16)
        vbox.pack_start(self._sync_box, False, False, 0)

        self._sync_pending = Gtk.Label()
        self._sync_pending.set_markup(
            "<small><i>Démarrera après la configuration…</i></small>"
        )
        self._sync_pending.set_xalign(0)
        self._sync_pending.set_margin_start(16)
        vbox.pack_start(self._sync_pending, False, False, 0)

        # Message "vous pouvez fermer"
        self._close_hint = Gtk.Label()
        self._close_hint.set_markup(
            '<small><span foreground="#1a7f37">● '
            'La synchronisation continue en arrière-plan — vous pouvez fermer cette fenêtre.'
            '</span></small>'
        )
        self._close_hint.set_xalign(0)
        self._close_hint.set_margin_top(10)
        self._close_hint.set_no_show_all(True)
        vbox.pack_start(self._close_hint, False, False, 0)

        return vbox

    def _step_done(self, key: str, ok: bool = True, detail: str = ""):
        icon, lbl = self._step_labels[key]
        if ok:
            icon.set_markup('<span foreground="#1a7f37">✓</span>')
        else:
            icon.set_markup('<span foreground="#c0392b">✗</span>')
        if detail:
            lbl.set_markup(f"{lbl.get_text()} <small>— {detail}</small>")
        return False

    def _step_skip(self, key: str, reason: str = ""):
        icon, lbl = self._step_labels[key]
        icon.set_markup('<span foreground="#888">—</span>')
        if reason:
            lbl.set_markup(f'<span foreground="#888">{lbl.get_text()}</span> <small>({reason})</small>')
        return False

    def _add_sync_row(self, local_sub: str, total_bytes: int):
        """Ajoute une ligne de progression rsync pour un dossier."""
        row = Gtk.Box(spacing=8)
        name_lbl = Gtk.Label(label=local_sub)
        name_lbl.set_width_chars(14)
        name_lbl.set_xalign(0)
        bar = Gtk.ProgressBar()
        bar.set_size_request(200, -1)
        bar.set_show_text(True)
        bar.set_text("en attente…")
        row.pack_start(name_lbl, False, False, 0)
        row.pack_start(bar,      True,  True,  0)
        self._sync_box.pack_start(row, False, False, 0)
        self._sync_box.show_all()
        self._sync_rows[local_sub] = bar

    def _set_sync_progress(self, local_sub: str, done: int, total: int):
        bar = self._sync_rows.get(local_sub)
        if not bar:
            return
        frac = min(done / total, 1.0) if total > 0 else 0.0
        bar.set_fraction(frac)
        bar.set_text(f"{self._fmt_size(done)} / {self._fmt_size(total)}")

    def _set_sync_done(self, local_sub: str, copied: int):
        bar = self._sync_rows.get(local_sub)
        if not bar:
            return
        bar.set_fraction(1.0)
        bar.set_text(f"✓ {self._fmt_size(copied)}" if copied else "✓ rien à copier")

    # ── Logique d'installation ────────────────────────────────────────────────

    def _on_prepare(self, assistant, page):
        """Déclenché quand on arrive sur une page."""
        if page is self._p_dossiers:
            GLib.idle_add(self._scan_nas_sizes_async)
        elif page is self._p_install:
            GLib.idle_add(self._run_install)

    def _run_install(self):
        threading.Thread(target=self._do_install, daemon=True).start()
        return False

    def _do_install(self):
        import re as _re
        step = lambda k, ok=True, d="": GLib.idle_add(self._step_done, k, ok, d)
        skip = lambda k, r="":          GLib.idle_add(self._step_skip, k, r)

        try:
            # ════════════════════════════════════════════════════════════════
            # PHASE 1 : Configuration (rapide, user doit être présent)
            # ════════════════════════════════════════════════════════════════
            mode = "fixe" if self._wiz_mode_fixe.get_active() else "portable"

            # 1. Config
            dirs = []
            for chk, e_local, e_nas, spin_age, spin_size in self._dir_rows:
                if chk.get_active():
                    quota_go = int(spin_size.get_value())
                    dirs.append({
                        "local_sub":    e_local.get_text().strip(),
                        "nas_sub":      e_nas.get_text().strip(),
                        "enabled":      True,
                        "max_age_days": int(spin_age.get_value()),
                        "max_size_mb":  quota_go * 1024 if quota_go > 0 else 0,
                    })
            cfg = DEFAULT_CONFIG.copy()
            cfg.update({
                "mode":                   mode,
                "nas_host":               self._wiz_entries["nas_host"].get_text().strip(),
                "nas_mount":              self._wiz_entries["nas_mount"].get_text().strip(),
                "check_interval":         int(self._spin_check.get_value()),
                "sync_interval":          int(self._spin_sync.get_value()),
                "conflict_mode":          self._conflict_combo.get_active_id(),
                "backup_before_overwrite": self._chk_backup.get_active(),
                "pause_on_battery":       self._chk_battery.get_active(),
                "notifications":          self._chk_notif.get_active(),
                "dirs":                   dirs,
            })
            save_config(cfg)
            step("config")

            # 2. Credentials SMB
            user = self._wiz_entries["nas_user"].get_text().strip()
            pwd  = self._wiz_entries["nas_pass"].get_text().strip()
            if user and pwd:
                creds = Path.home() / ".smbcredentials"
                creds.write_text(f"username={user}\npassword={pwd}\ndomain=WORKGROUP\n")
                creds.chmod(0o600)
                step("creds")
            else:
                skip("creds", "non renseignés")

            # 3. Cache local
            for d in dirs:
                (Path.home() / "offline_cache" / d["local_sub"]).mkdir(parents=True, exist_ok=True)
            step("cache")

            # 4. Liens symboliques
            nas_mount_path = Path(cfg["nas_mount"])
            link_base = nas_mount_path if mode == "fixe" else (Path.home() / "offline_cache")
            FR_LINKS = {
                "Desktop":   "Bureau",
                "Downloads": "Téléchargements",
                "Documents": "Documents",
                "Pictures":  "Images",
                "Music":     "Musique",
                "video":     "Vidéos",
            }
            local_subs = {d["local_sub"] for d in dirs}
            errs = []
            for local_sub, fr_name in FR_LINKS.items():
                if local_sub not in local_subs:
                    continue
                target = link_base / local_sub
                link   = Path.home() / fr_name
                if link.is_symlink():
                    link.unlink()
                elif link.is_dir():
                    try:
                        for item in list(link.iterdir()):
                            dest = target / item.name
                            if not dest.exists():
                                shutil.move(str(item), str(dest))
                        link.rmdir()
                    except OSError as exc:
                        errs.append(f"~/{fr_name}: {exc}")
                if not link.exists():
                    link.symlink_to(target)
                elif not link.is_symlink():
                    errs.append(f"~/{fr_name} non vide")
            step("symlinks", ok=not errs, d="; ".join(errs) if errs else "")

            # 5. XDG user-dirs
            xdg_file = Path.home() / ".config" / "user-dirs.dirs"
            xdg_file.parent.mkdir(parents=True, exist_ok=True)
            xdg_file.write_text(
                'XDG_DESKTOP_DIR="$HOME/Bureau"\n'
                'XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n'
                'XDG_TEMPLATES_DIR="$HOME/Modèles"\n'
                'XDG_PUBLICSHARE_DIR="$HOME/Public"\n'
                'XDG_DOCUMENTS_DIR="$HOME/Documents"\n'
                'XDG_MUSIC_DIR="$HOME/Musique"\n'
                'XDG_PICTURES_DIR="$HOME/Images"\n'
                'XDG_VIDEOS_DIR="$HOME/Vidéos"\n'
            )
            (Path.home() / "Modèles").mkdir(exist_ok=True)
            (Path.home() / "Public").mkdir(exist_ok=True)
            subprocess.run(["xdg-user-dirs-update"], capture_output=True)
            step("xdg")

            # 6. Service systemd
            IS_FLATPAK = Path("/.flatpak-info").exists()
            if mode == "portable" and not IS_FLATPAK:
                svc_dir = Path.home() / ".config" / "systemd" / "user"
                svc_dir.mkdir(parents=True, exist_ok=True)
                (svc_dir / SERVICE).write_text(
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
                step("service")
            elif mode == "portable" and IS_FLATPAK:
                step("service", d="Flatpak")
            else:
                skip("service", "mode PC fixe")

            # 7. Entrée menu
            _install_desktop_entry()
            step("menu")

            # ════════════════════════════════════════════════════════════════
            # PHASE 2 : Synchronisation initiale (peut prendre du temps)
            # L'utilisateur peut fermer la fenêtre dès ce point.
            # ════════════════════════════════════════════════════════════════

            nas_mnt = Path(cfg["nas_mount"])

            if mode != "portable" or not nas_mnt.is_mount():
                GLib.idle_add(self._sync_pending.set_markup,
                    "<small><i>NAS non monté — relancez l'assistant depuis chez vous "
                    "pour la copie initiale.</i></small>" if mode == "portable"
                    else "<small><i>Mode PC fixe — accès direct au NAS, pas de copie locale.</i></small>")
                GLib.idle_add(self._install_done)
                return

            # Préparer les barres de progression (phase 2 visible)
            GLib.idle_add(self._sync_pending.hide)
            GLib.idle_add(self._close_hint.show)

            # Vérifier l'espace disque total estimé
            total_bytes = 0
            dir_sizes   = {}
            for d in dirs:
                nas_dir = nas_mnt / d.get("nas_sub", d["local_sub"])
                if nas_dir.is_dir():
                    # Tenir compte du quota Go si défini
                    max_mb = d.get("max_size_mb", 0)
                    try:
                        r2 = subprocess.run(["du", "-sb", str(nas_dir)],
                                            capture_output=True, text=True, timeout=30)
                        full_sz = int(r2.stdout.split()[0]) if r2.returncode == 0 else 0
                    except Exception:
                        full_sz = 0
                    capped = min(full_sz, max_mb * 1_048_576) if max_mb > 0 else full_sz
                    dir_sizes[d["local_sub"]] = capped
                    total_bytes += capped

            avail  = shutil.disk_usage(str(Path.home() / "offline_cache")).free
            MARGIN = 1_073_741_824
            if total_bytes > 0 and avail < total_bytes + MARGIN:
                GLib.idle_add(self._sync_pending.set_markup,
                    f'<small><span foreground="#c0392b">⚠ Espace insuffisant '
                    f'({self._fmt_size(total_bytes + MARGIN)} requis, '
                    f'{self._fmt_size(avail)} disponibles). '
                    f'Libérez de l\'espace et relancez l\'assistant.</span></small>')
                GLib.idle_add(self._sync_pending.show)
                GLib.idle_add(self._install_done)
                return

            # Ajouter les barres de progression puis lancer rsync dir par dir
            for d in dirs:
                sz = dir_sizes.get(d["local_sub"], 0)
                GLib.idle_add(self._add_sync_row, d["local_sub"], sz)

            for d in dirs:
                src      = nas_mnt / d.get("nas_sub", d["local_sub"])
                dst      = Path.home() / "offline_cache" / d["local_sub"]
                sub      = d["local_sub"]
                dir_total = dir_sizes.get(sub, 0)

                if not src.is_dir():
                    GLib.idle_add(self._set_sync_done, sub, 0)
                    continue

                # Construire la commande rsync avec filtre par quota si besoin
                rsync_cmd = ["rsync", "-ah", "--ignore-existing", "--info=progress2"]
                max_mb = d.get("max_size_mb", 0)
                if max_mb > 0:
                    # Trier par date via find + rsync --files-from n'est pas simple ;
                    # on passe max_size à rsync via --max-size pour bloquer les très
                    # gros fichiers individuels, la sélection finale sera affinée par
                    # le démon (quota_trim). Ici on copie simplement jusqu'à l'espace.
                    rsync_cmd += [f"--max-size={max_mb}m"]
                rsync_cmd += [str(src) + "/", str(dst) + "/"]

                try:
                    proc = subprocess.Popen(
                        rsync_cmd,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True, bufsize=1,
                    )
                    dir_done = 0
                    for line in proc.stdout:
                        m = _re.match(r'\s*([\d,]+)\s+\d+%', line)
                        if m:
                            dir_done = int(m.group(1).replace(',', ''))
                            GLib.idle_add(self._set_sync_progress, sub, dir_done, dir_total)
                    proc.wait()
                    GLib.idle_add(self._set_sync_done, sub, dir_done)
                except FileNotFoundError:
                    GLib.idle_add(self._set_sync_done, sub, 0)
                except Exception:
                    GLib.idle_add(self._set_sync_done, sub, 0)

            # Démarrer le démon après la sync initiale
            if not IS_FLATPAK:
                subprocess.run(["systemctl", "--user", "start", SERVICE], capture_output=True)
            else:
                subprocess.Popen([sys.executable, str(DAEMON_PY)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            GLib.idle_add(self._install_done)

        except Exception as e:
            GLib.idle_add(self._install_error, str(e))

    def _install_done(self):
        self._ph2_header.set_markup(
            '<b>② Synchronisation initiale</b>  '
            '<span foreground="#1a7f37">✓ terminée</span>'
        )
        self.set_page_complete(self._p_install, True)
        return False

    def _install_error(self, msg: str):
        lbl = Gtk.Label()
        lbl.set_markup(f'<span foreground="#c0392b"><b>✗ Erreur : {msg}</b></span>')
        lbl.set_xalign(0)
        lbl.set_line_wrap(True)
        lbl.set_margin_top(8)
        self._sync_box.pack_start(lbl, False, False, 0)
        self._sync_box.show_all()
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
    IS_FLATPAK = Path("/.flatpak-info").exists()
    app_dir = Path.home() / ".local" / "share" / "applications"
    app_dir.mkdir(parents=True, exist_ok=True)
    desktop = app_dir / "nas-sync.desktop" if not IS_FLATPAK else app_dir / "org.cassis.NasSync.desktop"
    
    if IS_FLATPAK:
        exec_cmd = "flatpak run org.cassis.NasSync"
    else:
        exec_cmd = f"/usr/bin/python3 {SCRIPT_DIR}/nas_sync_app.py"

    desktop.write_text(
        f"[Desktop Entry]\n"
        f"Name=NAS Sync\n"
        f"GenericName=Synchronisation NAS\n"
        f"Comment=Synchronisation automatique avec le NAS d'entreprise\n"
        f"Exec={exec_cmd}\n"
        f"Icon=network-server\n"
        f"Type=Application\n"
        f"Categories=Utility;Network;FileManager;\n"
        f"Keywords=nas;sync;synchronisation;réseau;partage;\n"
        f"StartupNotify=false\n"
        f"Terminal=false\n"
    )
    
    if IS_FLATPAK:
        # Configuration de l'autostart pour démarrer le Flatpak au login
        autostart_dir = Path.home() / ".config" / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        autostart_file = autostart_dir / "org.cassis.NasSync.desktop"
        autostart_file.write_text(
            f"[Desktop Entry]\n"
            f"Name=NAS Sync\n"
            f"Comment=Interface de synchronisation NAS — barre système\n"
            f"Exec=flatpak run org.cassis.NasSync --background\n"
            f"Icon=network-server\n"
            f"Type=Application\n"
            f"Categories=Utility;\n"
            f"StartupNotify=false\n"
        )
    subprocess.run(["update-desktop-database", str(app_dir)], capture_output=True)


# ── point d'entrée ────────────────────────────────────────────────────────────

def run_wizard(on_done=None):
    wizard = SetupWizard(on_done=on_done)
    Gtk.main()


if __name__ == "__main__":
    run_wizard()
