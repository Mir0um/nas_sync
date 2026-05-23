#!/bin/bash
# install.sh — Installation du système de synchronisation NAS
# À lancer UNE FOIS, chez soi, avec le NAS (Cassis.local) monté sur ~/NasShare.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL="$HOME/offline_cache"
NAS="$HOME/NasShare"
CONFIG="$HOME/.nas_sync_config.json"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "══════════════════════════════════════════════"
echo "   NAS Sync — Installation"
echo "══════════════════════════════════════════════"
echo ""

# ── Mode d'utilisation ────────────────────────────────────────────────────────

echo "Mode d'utilisation :"
echo ""
echo "  1) PC portable  — cache local + synchronisation hors ligne (défaut)"
echo "     Vos fichiers sont copiés localement. Accessibles même sans réseau."
echo "     Synchronisés automatiquement dès la reconnexion au NAS."
echo ""
echo "  2) PC fixe      — accès direct au NAS"
echo "     Vos dossiers pointent directement vers le NAS."
echo "     Simple et rapide. Le NAS doit être disponible en permanence."
echo ""
read -rp "Votre choix [1/2, défaut=1] : " _MODE_CHOICE
case "${_MODE_CHOICE}" in
    2) INSTALL_MODE="fixe"     ;;
    *) INSTALL_MODE="portable" ;;
esac
ok "Mode : $INSTALL_MODE"
echo ""

# ── Paramétrage des dossiers à synchroniser ──────────────────────────────────

# Format : "Nom français|local_sub|nas_sub|XDG_KEY|max_age_days"
DIR_DEFS=(
    "Bureau|Desktop|Desktop|XDG_DESKTOP_DIR|0"
    "Téléchargements|Downloads|Downloads|XDG_DOWNLOAD_DIR|90"
    "Documents|Documents|Documents|XDG_DOCUMENTS_DIR|0"
    "Musique|Music|Music|XDG_MUSIC_DIR|180"
    "Images|Pictures|Pictures|XDG_PICTURES_DIR|0"
    "Vidéos|video|video|XDG_VIDEOS_DIR|90"
)

SELECTED_DIRS=()
SELECTED_DIR_LABELS=()

echo "Choix des dossiers à synchroniser :"
echo "(Entrée = Oui, n = Non)"
for entry in "${DIR_DEFS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub xdg_key max_age <<< "$entry"
    read -rp "  Synchroniser '$fr_name' ? [O/n] : " answer
    case "$answer" in
        n|N|no|NO|non|NON)
            warn "$fr_name ignoré"
            ;;
        *)
            SELECTED_DIRS+=("$entry")
            SELECTED_DIR_LABELS+=("$fr_name")
            ;;
    esac
done

[ ${#SELECTED_DIRS[@]} -gt 0 ] || err "Aucun dossier sélectionné. Installation annulée."

SELECTED_LABELS_JOINED="${SELECTED_DIR_LABELS[*]}"
ok "Dossiers sélectionnés : $SELECTED_LABELS_JOINED"
echo ""

# ── Dépendances ───────────────────────────────────────────────────────────────

echo "Vérification des dépendances…"

HAS_RSYNC=false

_dnf_install() {
    local pkg="$1" desc="$2"
    echo "  Installation de $pkg…"
    if sudo dnf install -y "$pkg" &>/dev/null; then
        ok "$desc installé"
    else
        warn "Échec installation $pkg — certaines fonctionnalités seront limitées"
    fi
}

# PyGObject GTK3 (obligatoire)
if ! python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null; then
    _dnf_install python3-gobject "PyGObject GTK3"
    python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null \
        || err "PyGObject GTK3 toujours absent après installation — vérifiez votre système"
fi
ok "PyGObject GTK3"

# AppIndicator3 (optionnel — icône barre système enrichie sur GNOME/KDE/XFCE)
if python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    ok "AppIndicator3 (icône barre système native)"
else
    warn "AppIndicator3 non disponible — l'app utilisera le fallback standard (Gtk.StatusIcon)"
    warn "  Pour l'activer sur Fedora/Nobara : sudo dnf install libappindicator-gtk3"
fi

# notify-send (notifications bureau — fonctionne sur GNOME, KDE, XFCE, Cinnamon…)
if ! command -v notify-send &>/dev/null; then
    _dnf_install libnotify "notify-send"
fi
command -v notify-send &>/dev/null && ok "notify-send" || warn "notify-send absent"

# rsync (synchro initiale)
if ! command -v rsync &>/dev/null; then
    _dnf_install rsync "rsync"
fi
if command -v rsync &>/dev/null; then
    ok "rsync"; HAS_RSYNC=true
else
    warn "rsync absent — la synchro initiale sera ignorée"; HAS_RSYNC=false
fi

# ── NAS disponible ? ──────────────────────────────────────────────────────────

echo ""
if mountpoint -q "$NAS" 2>/dev/null; then
    NAS_OK=true
    ok "NAS monté sur $NAS"
else
    NAS_OK=false
    warn "NAS non monté — la synchronisation initiale sera ignorée"
    warn "Relancez ce script depuis chez vous pour la copie initiale"
fi

# ── Cache local ───────────────────────────────────────────────────────────────

echo ""
echo "Création du cache local $LOCAL …"
for entry in "${SELECTED_DIRS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub xdg_key max_age <<< "$entry"
    mkdir -p "$LOCAL/$local_sub"
done
ok "Dossiers créés dans $LOCAL"

# ── Synchro initiale NAS → local ─────────────────────────────────────────────

if [ "$NAS_OK" = true ] && [ "$HAS_RSYNC" = true ] && [ "$INSTALL_MODE" = "portable" ]; then
    echo ""
    echo "Synchronisation initiale NAS → cache local …"

    # Calcul de la taille totale
    total_bytes=0
    for entry in "${SELECTED_DIRS[@]}"; do
        IFS='|' read -r fr_name local_sub nas_sub xdg_key max_age <<< "$entry"
        if [ -d "$NAS/$nas_sub" ]; then
            sz=$(du -sb "$NAS/$nas_sub" 2>/dev/null | awk '{print $1}')
            total_bytes=$(( total_bytes + ${sz:-0} ))
        fi
    done
    if [ "$total_bytes" -gt 1073741824 ]; then
        total_human=$(awk "BEGIN{printf \"%.1f Go\", $total_bytes/1073741824}")
    elif [ "$total_bytes" -gt 1048576 ]; then
        total_human=$(awk "BEGIN{printf \"%.1f Mo\", $total_bytes/1048576}")
    else
        total_human="${total_bytes} o"
    fi
    echo "  Volume total à copier : $total_human"

    # Vérification espace disque (marge de sécurité : 1 Go)
    available_kb=$(df -k "$LOCAL" 2>/dev/null | awk 'NR==2 {print $4}')
    needed_kb=$(( total_bytes / 1024 ))
    margin_kb=1048576
    if [ -n "$available_kb" ] && [ $(( needed_kb + margin_kb )) -gt "$available_kb" ]; then
        warn "Espace disque insuffisant pour la synchronisation initiale !"
        warn "  Nécessaire : $(awk "BEGIN{printf \"%.1f Go\", ($needed_kb+$margin_kb)/1048576}")"
        warn "  Disponible : $(awk "BEGIN{printf \"%.1f Go\", $available_kb/1048576}")"
        warn "  Synchronisation initiale ignorée — libérez de l'espace puis relancez install.sh"
    else
        echo ""
        for entry in "${SELECTED_DIRS[@]}"; do
            IFS='|' read -r fr_name local_sub nas_sub xdg_key max_age <<< "$entry"
            if [ -d "$NAS/$nas_sub" ]; then
                nb=$(find "$NAS/$nas_sub" -type f 2>/dev/null | wc -l)
                sz=$(du -sh "$NAS/$nas_sub" 2>/dev/null | awk '{print $1}')
                echo "  $fr_name ($nb fichiers, $sz) :"
                rsync -ah --ignore-existing --info=progress2 \
                    "$NAS/$nas_sub/" "$LOCAL/$local_sub/" 2>/dev/null || true
                echo ""
                ok "$fr_name synchronisé"
            else
                warn "$fr_name absent sur le NAS — ignoré"
            fi
        done
    fi
fi

# ── Liens symboliques ─────────────────────────────────────────────────────────

echo ""
echo "Mise à jour des liens symboliques …"
if [ "$INSTALL_MODE" = "fixe" ]; then
    LINK_BASE="$NAS"
else
    LINK_BASE="$LOCAL"
fi
for entry in "${SELECTED_DIRS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub xdg_key max_age <<< "$entry"
    link_path="$HOME/$fr_name"
    target="$LINK_BASE/$local_sub"
    mkdir -p "$target"
    if [ -L "$link_path" ]; then
        rm "$link_path"
    elif [ -d "$link_path" ]; then
        # Déplacer le contenu vers la cible avant de supprimer
        if [ -n "$(ls -A "$link_path" 2>/dev/null)" ]; then
            cp -a "$link_path/." "$target/" 2>/dev/null || true
        fi
        rmdir "$link_path" 2>/dev/null || { warn "  $link_path non vide — ignoré"; continue; }
    fi
    ln -s "$target" "$link_path"
    ok "  $link_path → $target"
done

# ── XDG user-dirs (noms français standards, valables quel que soit le mode) ──

echo ""
echo "Mise à jour de ~/.config/user-dirs.dirs …"
mkdir -p "$HOME/.config"
# Les XDG dirs pointent TOUJOURS vers les noms français (~/Bureau, ~/Téléchargements…)
# qui sont eux-mêmes des liens symboliques vers NAS ou cache local selon le mode.
# Cela évite les conflits avec xdg-user-dirs-update et fonctionne sur tous les DE.
cat > "$HOME/.config/user-dirs.dirs" << 'XDGEOF'
XDG_DESKTOP_DIR="$HOME/Bureau"
XDG_DOWNLOAD_DIR="$HOME/Téléchargements"
XDG_TEMPLATES_DIR="$HOME/Modèles"
XDG_PUBLICSHARE_DIR="$HOME/Public"
XDG_DOCUMENTS_DIR="$HOME/Documents"
XDG_MUSIC_DIR="$HOME/Musique"
XDG_PICTURES_DIR="$HOME/Images"
XDG_VIDEOS_DIR="$HOME/Vidéos"
XDGEOF
mkdir -p "$HOME/Modèles" "$HOME/Public"
xdg-user-dirs-update 2>/dev/null || true
ok "XDG user-dirs mis à jour (noms français standards)"

# ── Configuration initiale ────────────────────────────────────────────────────

if [ ! -f "$CONFIG" ]; then
    echo ""
    echo "Génération de la configuration initiale…"
    PYTHONPATH="$SCRIPT_DIR" python3 - << 'PY'
from nas_sync_config import load_config
load_config()
PY
    ok "Configuration créée : $CONFIG"
fi

# Enregistrer le mode et les dossiers sélectionnés dans la configuration
python3 - << PYEOF
import json
from pathlib import Path
cfg_path = Path("$CONFIG")
if cfg_path.exists():
    cfg = json.loads(cfg_path.read_text())
    cfg["mode"] = "$INSTALL_MODE"
    cfg["dirs"] = [
$(for entry in "${SELECTED_DIRS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub xdg_key max_age <<< "$entry"
    echo "        {\"local_sub\": \"$local_sub\", \"nas_sub\": \"$nas_sub\", \"enabled\": True, \"max_age_days\": $max_age},"
done)
    ]
    cfg_path.write_text(json.dumps(cfg, indent=2))
PYEOF
ok "Mode '$INSTALL_MODE' enregistré dans la configuration"

# ── Service systemd (mode portable uniquement) ────────────────────────────────

echo ""
if [ "$INSTALL_MODE" = "portable" ]; then
    echo "Installation du service systemd …"
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/nas-sync.service" << EOF
[Unit]
Description=NAS Sync Daemon — Synchronisation bidirectionnelle offline_cache ↔ NAS
After=network.target graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $SCRIPT_DIR/nas_sync_daemon.py
Restart=on-failure
RestartSec=15
StandardOutput=null
StandardError=journal

[Install]
WantedBy=graphical-session.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable nas-sync.service
    systemctl --user start nas-sync.service
    sleep 2
    systemctl --user is-active --quiet nas-sync.service \
        && ok "Service nas-sync démarré et activé" \
        || warn "Service démarré (vérifiez avec : systemctl --user status nas-sync)"
else
    ok "Mode PC fixe — service de synchronisation non activé"
fi

# ── Autostart (XDG standard — fonctionne sur GNOME, KDE, XFCE, Cinnamon…) ────

echo ""
echo "Installation du démarrage automatique …"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/nas-sync-app.desktop" << EOF
[Desktop Entry]
Name=NAS Sync
Comment=Interface de synchronisation NAS — barre système
Exec=/usr/bin/python3 $SCRIPT_DIR/nas_sync_app.py
Icon=network-server
Type=Application
Categories=Utility;Network;
StartupNotify=false
EOF
# Entrée dans le menu des applications
mkdir -p "$HOME/.local/share/applications"
cp "$HOME/.config/autostart/nas-sync-app.desktop" \
   "$HOME/.local/share/applications/nas-sync.desktop" 2>/dev/null || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
ok "Démarrage automatique configuré"

# Lancer l'interface immédiatement (si session graphique active)
if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
    python3 "$SCRIPT_DIR/nas_sync_app.py" &
    ok "Interface lancée (icône dans la barre système)"
fi

# ── Résumé ────────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════"
echo "   Installation terminée !"
echo "══════════════════════════════════════════════"
echo ""
echo "  Mode            : $INSTALL_MODE"
if [ "$INSTALL_MODE" = "portable" ]; then
echo "  Cache local     : $LOCAL"
echo "  Dossiers        : ${SELECTED_LABELS_JOINED}"
echo "                    → liens symboliques vers le cache local"
echo "                    → synchronisés automatiquement avec le NAS"
echo ""
echo "  Commandes utiles :"
echo "    Statut démon       : systemctl --user status nas-sync"
echo "    Logs               : tail -f ~/.nas_sync.log"
echo "    Activer/désactiver : bash $SCRIPT_DIR/toggle.sh"
else
echo "  Dossiers        : ${SELECTED_LABELS_JOINED}"
echo "                    → pointent directement vers le NAS ($NAS)"
echo "                    (le NAS doit être monté pour accéder aux fichiers)"
fi
echo ""
echo "  Interface       : icône dans la barre système"
echo "                    Démarre automatiquement à chaque login"
echo "                    Pour changer de mode : Paramètres → onglet Mode"
echo ""
