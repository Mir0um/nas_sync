#!/usr/bin/env python3
"""
Dialogue de résolution de conflit NAS Sync.
Options : garder LOCAL, garder NAS, renommer et conserver les deux,
          ou aperçu du contenu pour les fichiers texte (fix 10).

Usage  : conflict_dialog.py <clé_relative> <chemin_local> <chemin_nas>
Sortie : "local" | "nas" | "rename:local:nom" | "rename:nas:nom" | (rien)
"""

import sys
from pathlib import Path
from datetime import datetime

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango, GLib

# Extensions considérées comme texte (fix 10)
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".sh", ".bash", ".zsh",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg",
    ".xml", ".html", ".htm", ".css", ".js", ".ts",
    ".csv", ".log", ".env", ".sql",
}


# ── utilitaires ───────────────────────────────────────────────────────────────

def fmt_date(path: str) -> str:
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%d/%m/%Y à %H:%M:%S")
    except Exception:
        return "—"


def fmt_size(path: str) -> str:
    try:
        s = Path(path).stat().st_size
        for unit in ("o", "Ko", "Mo", "Go"):
            if s < 1024 or unit == "Go":
                return f"{s:.0f} {unit}" if unit == "o" else f"{s:.1f} {unit}"
            s /= 1024
    except Exception:
        return "—"


def suggest_name(original_key: str, side: str, local_p: str, nas_p: str) -> str:
    filename = original_key.split("/")[-1]
    path = nas_p if side == "nas" else local_p
    try:
        ts = datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y%m%d_%H%M")
    except Exception:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        return f"{stem}_{side}_{ts}.{ext}"
    return f"{filename}_{side}_{ts}"


def read_text_preview(path: str, max_lines: int = 30) -> str:
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()[:max_lines]
        result = "\n".join(lines)
        if len(content.splitlines()) > max_lines:
            result += f"\n\n… ({len(content.splitlines()) - max_lines} lignes supplémentaires)"
        return result
    except Exception as e:
        return f"(impossible de lire le fichier : {e})"


# ── sous-dialogue de renommage ────────────────────────────────────────────────

def show_rename_dialog(parent: Gtk.Window, rel_key: str,
                       local_p: str, nas_p: str) -> str | None:
    dlg = Gtk.Dialog(
        title="Renommer et conserver les deux versions",
        transient_for=parent, modal=True,
    )
    dlg.set_default_size(500, 1)
    dlg.set_border_width(0)
    dlg.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    vbox.set_border_width(20)
    dlg.get_content_area().add(vbox)

    t = Gtk.Label()
    t.set_markup("<b>Renommer et conserver les deux versions</b>")
    t.set_xalign(0)
    vbox.pack_start(t, False, False, 0)

    info = Gtk.Label(
        label="Choisissez quelle version renommer. Les deux fichiers seront\n"
              "conservés et synchronisés sur le NAS et en local."
    )
    info.set_xalign(0)
    info.set_line_wrap(True)
    vbox.pack_start(info, False, False, 0)
    vbox.pack_start(Gtk.Separator(), False, False, 0)

    lbl = Gtk.Label()
    lbl.set_markup("<b>Quelle version renommer ?</b>")
    lbl.set_xalign(0)
    vbox.pack_start(lbl, False, False, 0)

    radio_local = Gtk.RadioButton.new_with_label(
        None,
        f"La version locale   ({fmt_date(local_p)}, {fmt_size(local_p)})\n"
        f"→ La version NAS deviendra la référence"
    )
    radio_nas = Gtk.RadioButton.new_with_label_from_widget(
        radio_local,
        f"La version NAS      ({fmt_date(nas_p)}, {fmt_size(nas_p)})\n"
        f"→ La version locale deviendra la référence"
    )
    vbox.pack_start(radio_local, False, False, 0)
    vbox.pack_start(radio_nas,   False, False, 0)
    vbox.pack_start(Gtk.Separator(), False, False, 0)

    lbl_name = Gtk.Label()
    lbl_name.set_markup("<b>Nouveau nom du fichier renommé :</b>")
    lbl_name.set_xalign(0)
    vbox.pack_start(lbl_name, False, False, 0)

    entry = Gtk.Entry()
    entry.set_hexpand(True)
    entry.set_text(suggest_name(rel_key, "local", local_p, nas_p))
    vbox.pack_start(entry, False, False, 0)

    note = Gtk.Label()
    note.set_markup("<small><i>Les deux versions seront présentes sur le NAS et en local.</i></small>")
    note.set_xalign(0)
    vbox.pack_start(note, False, False, 0)

    def update_suggestion(*_):
        side = "local" if radio_local.get_active() else "nas"
        entry.set_text(suggest_name(rel_key, side, local_p, nas_p))

    radio_local.connect("toggled", update_suggestion)
    radio_nas.connect("toggled",   update_suggestion)

    dlg.add_button("Annuler",   Gtk.ResponseType.CANCEL)
    btn_ok = dlg.add_button("Confirmer", Gtk.ResponseType.OK)
    btn_ok.get_style_context().add_class("suggested-action")

    dlg.show_all()
    response = dlg.run()
    new_name = entry.get_text().strip()
    side = "local" if radio_local.get_active() else "nas"
    dlg.destroy()

    if response == Gtk.ResponseType.OK and new_name:
        return f"rename:{side}:{new_name}"
    return None


# ── fix 10 : aperçu du contenu texte ─────────────────────────────────────────

def build_preview_widget(local_p: str, nas_p: str) -> Gtk.Widget:
    """Construit un widget côte-à-côte avec les premiers lignes de chaque fichier."""
    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

    for label, path in [("Version LOCALE", local_p), ("Version NAS", nas_p)]:
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        lbl = Gtk.Label()
        lbl.set_markup(f"<b>{label}</b>")
        lbl.set_xalign(0)
        vb.pack_start(lbl, False, False, 0)

        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.NONE)
        tv.get_buffer().set_text(read_text_preview(path))

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(160)
        sw.set_min_content_width(260)
        sw.add(tv)
        vb.pack_start(sw, True, True, 0)
        hbox.pack_start(vb, True, True, 0)

    return hbox


# ── dialogue principal ────────────────────────────────────────────────────────

def run(rel_key: str, local_p: str, nas_p: str) -> str | None:
    result: list[str | None] = [None]

    win = Gtk.Window(title="NAS Sync — Conflit de synchronisation")
    win.set_default_size(640, 1)
    win.set_border_width(24)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_keep_above(True)
    win.set_resizable(True)
    win.connect("delete-event", lambda *_: Gtk.main_quit())

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    win.add(root)

    # Titre
    title = Gtk.Label()
    title.set_markup("<b><big>⚠  Conflit de synchronisation</big></b>")
    title.set_xalign(0)
    root.pack_start(title, False, False, 0)

    # Fichier
    file_lbl = Gtk.Label()
    file_lbl.set_markup(f"Fichier : <tt>{rel_key}</tt>")
    file_lbl.set_xalign(0)
    file_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    root.pack_start(file_lbl, False, False, 0)

    desc = Gtk.Label(
        label="Ce fichier a été modifié localement ET sur le NAS depuis la dernière synchronisation."
    )
    desc.set_xalign(0)
    root.pack_start(desc, False, False, 0)

    root.pack_start(Gtk.Separator(), False, False, 0)

    # Tableau comparatif
    grid = Gtk.Grid()
    grid.set_column_spacing(32)
    grid.set_row_spacing(8)
    root.pack_start(grid, False, False, 0)

    for col, text in enumerate(["", "Version LOCALE\n(offline_cache)", "Version NAS\n(Cassis.local)"]):
        h = Gtk.Label()
        h.set_markup(f"<b>{text}</b>")
        h.set_xalign(0)
        grid.attach(h, col, 0, 1, 1)

    for row, (lbl_text, lv, nv) in enumerate([
        ("Modifié le :", fmt_date(local_p), fmt_date(nas_p)),
        ("Taille :",     fmt_size(local_p), fmt_size(nas_p)),
    ], 1):
        for col, val in enumerate([lbl_text, lv, nv]):
            l = Gtk.Label(label=val)
            l.set_xalign(0)
            grid.attach(l, col, row, 1, 1)

    # fix 10 : aperçu texte (si fichier texte)
    ext = Path(rel_key).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        root.pack_start(Gtk.Separator(), False, False, 0)
        expander = Gtk.Expander(label="  Aperçu du contenu")
        expander.add(build_preview_widget(local_p, nas_p))
        root.pack_start(expander, False, False, 0)

    root.pack_start(Gtk.Separator(), False, False, 0)

    # Boutons
    bbox = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
    bbox.set_layout(Gtk.ButtonBoxStyle.END)
    bbox.set_spacing(8)
    root.pack_start(bbox, False, False, 0)

    def choose(val: str | None):
        result[0] = val
        Gtk.main_quit()

    btn_skip   = Gtk.Button(label="Ignorer")
    btn_rename = Gtk.Button(label="Renommer…")
    btn_nas    = Gtk.Button(label="Garder NAS")
    btn_local  = Gtk.Button(label="Garder LOCAL")
    btn_local.get_style_context().add_class("suggested-action")

    btn_skip.connect("clicked",  lambda _: choose(None))
    btn_nas.connect("clicked",   lambda _: choose("nas"))
    btn_local.connect("clicked", lambda _: choose("local"))

    def on_rename(_):
        win.set_sensitive(False)
        res = show_rename_dialog(win, rel_key, local_p, nas_p)
        win.set_sensitive(True)
        if res:
            choose(res)

    btn_rename.connect("clicked", on_rename)

    bbox.add(btn_skip)
    bbox.add(btn_rename)
    bbox.add(btn_nas)
    bbox.add(btn_local)

    win.show_all()
    Gtk.main()
    return result[0]


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.stderr.write("Usage: conflict_dialog.py <clé> <chemin_local> <chemin_nas>\n")
        sys.exit(1)
    _, rel_key, local_p, nas_p = sys.argv
    choice = run(rel_key, local_p, nas_p)
    if choice:
        print(choice)
