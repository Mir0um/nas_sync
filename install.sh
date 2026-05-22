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

# ── Dépendances ───────────────────────────────────────────────────────────────

echo "Vérification des dépendances…"

HAS_RSYNC=true

_dnf_install() {
    local pkg="$1" desc="$2"
    echo "  Installation de $pkg…"
    if sudo dnf install -y "$pkg" &>/dev/null; then
        ok "$desc installé"
    else
        warn "Échec installation $pkg — certaines fonctionnalités seront limitées"
    fi
}

if ! python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null; then
    _dnf_install python3-gobject "PyGObject GTK3"
    python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null \
        || err "PyGObject GTK3 toujours absent après installation — vérifiez votre système"
fi
ok "PyGObject GTK3"

if ! python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    _dnf_install libappindicator-gtk3 "AppIndicator3"
fi
python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null \
    && ok "AppIndicator3" || warn "AppIndicator3 non disponible — icône barre système désactivée"

if ! command -v notify-send &>/dev/null; then
    _dnf_install libnotify "notify-send"
fi
command -v notify-send &>/dev/null && ok "notify-send" || warn "notify-send absent"

if ! command -v rsync &>/dev/null; then
    _dnf_install rsync "rsync"
fi
if command -v rsync &>/dev/null; then
    ok "rsync"; HAS_RSYNC=true
else
    warn "rsync absent — la synchro initiale sera ignorée"; HAS_RSYNC=false
fi

# ── Extension GNOME AppIndicator (nécessaire pour l'icône dans la barre) ──────

echo ""
echo "Vérification de l'extension GNOME AppIndicator…"

APPIND_EXT="appindicatorsupport@rgcjonas.gmail.com"

# Installer le paquet si absent
if ! rpm -q gnome-shell-extension-appindicator &>/dev/null; then
    _dnf_install gnome-shell-extension-appindicator "Extension GNOME AppIndicator"
fi

if rpm -q gnome-shell-extension-appindicator &>/dev/null; then
    ok "Paquet gnome-shell-extension-appindicator présent"
    # Activer l'extension dans la session courante si possible
    if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
        if gnome-extensions enable "$APPIND_EXT" 2>/dev/null; then
            ok "Extension $APPIND_EXT activée"
        else
            warn "Activation auto impossible — reconnectez-vous puis activez manuellement :"
            warn "  gnome-extensions enable $APPIND_EXT"
            warn "  ou via : https://extensions.gnome.org/extension/615/"
        fi
    else
        warn "Pas de session graphique — l'extension sera activée à la prochaine connexion"
        warn "Si l'icône n'apparaît pas, lancez : gnome-extensions enable $APPIND_EXT"
    fi
else
    warn "Extension AppIndicator non disponible — l'app utilisera Gtk.StatusIcon en fallback"
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
for dir in Desktop Downloads Documents Music Pictures video; do
    mkdir -p "$LOCAL/$dir"
done
ok "Dossiers créés dans $LOCAL"

# ── Synchro initiale NAS → local ─────────────────────────────────────────────

if [ "$NAS_OK" = true ] && [ "$HAS_RSYNC" = true ] && [ "$INSTALL_MODE" = "portable" ]; then
    echo ""
    echo "Synchronisation initiale NAS → cache local …"
    declare -A NAS_DIRS=( [Desktop]=Desktop [Downloads]=Downloads \
        [Documents]=Documents [Music]=Music [Pictures]=Pictures [video]=video )

    # Calculer la taille totale avant de commencer
    total_bytes=0
    for nas_sub in Desktop Downloads Documents Music Pictures video; do
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
    echo ""

    for local_sub in "${!NAS_DIRS[@]}"; do
        nas_sub="${NAS_DIRS[$local_sub]}"
        if [ -d "$NAS/$nas_sub" ]; then
            nb=$(find "$NAS/$nas_sub" -type f 2>/dev/null | wc -l)
            sz=$(du -sh "$NAS/$nas_sub" 2>/dev/null | awk '{print $1}')
            echo "  $nas_sub ($nb fichiers, $sz) :"
            rsync -ah --ignore-existing --info=progress2 \
                "$NAS/$nas_sub/" "$LOCAL/$local_sub/" 2>/dev/null || true
            echo ""
            ok "$nas_sub synchronisé"
        else
            warn "$nas_sub absent sur le NAS — ignoré"
        fi
    done
fi

# ── Liens symboliques ─────────────────────────────────────────────────────────

echo ""
echo "Mise à jour des liens symboliques …"
if [ "$INSTALL_MODE" = "fixe" ]; then
    LINK_BASE="$NAS"
else
    LINK_BASE="$LOCAL"
fi
declare -A SYMLINKS=(
    ["$HOME/Bureau"]="$LINK_BASE/Desktop"
    ["$HOME/Téléchargements"]="$LINK_BASE/Downloads"
    ["$HOME/Documents"]="$LINK_BASE/Documents"
    ["$HOME/Musique"]="$LINK_BASE/Music"
    ["$HOME/Images"]="$LINK_BASE/Pictures"
    ["$HOME/Vidéos"]="$LINK_BASE/video"
)
for link_path in "${!SYMLINKS[@]}"; do
    target="${SYMLINKS[$link_path]}"
    if [ -L "$link_path" ]; then
        rm "$link_path"
    elif [ -d "$link_path" ]; then
        rmdir "$link_path" 2>/dev/null || { warn "  $link_path non vide — ignoré"; continue; }
    fi
    ln -s "$target" "$link_path"
    ok "  $link_path → $target"
done

# ── XDG user-dirs ─────────────────────────────────────────────────────────────

echo ""
echo "Mise à jour de ~/.config/user-dirs.dirs …"
mkdir -p "$HOME/.config"
if [ "$INSTALL_MODE" = "fixe" ]; then
    XDG_BASE="\$HOME/NasShare"
else
    XDG_BASE="\$HOME/offline_cache"
fi
cat > "$HOME/.config/user-dirs.dirs" << EOF
XDG_DESKTOP_DIR="${XDG_BASE}/Desktop"
XDG_DOWNLOAD_DIR="${XDG_BASE}/Downloads"
XDG_DOCUMENTS_DIR="${XDG_BASE}/Documents"
XDG_MUSIC_DIR="${XDG_BASE}/Music"
XDG_PICTURES_DIR="${XDG_BASE}/Pictures"
XDG_VIDEOS_DIR="${XDG_BASE}/video"
EOF
xdg-user-dirs-update 2>/dev/null || true
ok "XDG user-dirs mis à jour"

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

# Enregistrer le mode dans la configuration
python3 - << PYEOF
import json
from pathlib import Path
cfg_path = Path("$CONFIG")
if cfg_path.exists():
    cfg = json.loads(cfg_path.read_text())
    cfg["mode"] = "$INSTALL_MODE"
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
Description=NAS Sync Daemon — Synchronisation bidirectionnelle offline_cache ↔ Cassis.local
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

# ── Autostart GNOME pour l'interface ─────────────────────────────────────────

echo ""
echo "Installation de l'interface (autostart GNOME) …"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/nas-sync-app.desktop" << EOF
[Desktop Entry]
Name=NAS Sync
Comment=Interface de synchronisation NAS — barre système
Exec=/usr/bin/python3 $SCRIPT_DIR/nas_sync_app.py
Icon=network-server
Type=Application
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF
ok "Entrée autostart GNOME créée"

# Lancer l'interface immédiatement (si on est dans une session graphique)
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
echo "  Dossiers        : ~/Bureau, ~/Téléchargements, ~/Documents…"
echo "                    → pointent vers le cache local"
echo "                    → synchronisés automatiquement avec le NAS"
echo ""
echo "  Commandes utiles :"
echo "    Statut démon       : systemctl --user status nas-sync"
echo "    Logs               : tail -f ~/.nas_sync.log"
echo "    Activer/désactiver : bash $SCRIPT_DIR/toggle.sh"
else
echo "  Dossiers        : ~/Bureau, ~/Téléchargements, ~/Documents…"
echo "                    → pointent directement vers le NAS ($NAS)"
echo "                    (le NAS doit être monté pour accéder aux fichiers)"
fi
echo ""
echo "  Interface       : icône dans la barre système GNOME"
echo "                    Démarre automatiquement à chaque login"
echo "                    Pour changer de mode : Paramètres → onglet Mode"
echo ""
echo "  Action Nautilus (une seule fois) :"
echo "    Clic droit sur anciens favoris → Retirer des favoris"
if [ "$INSTALL_MODE" = "portable" ]; then
echo "    Glisser les dossiers de ~/offline_cache/ dans la barre latérale"
else
echo "    Glisser les dossiers de ~/NasShare/ dans la barre latérale"
fi
echo ""
