#!/bin/bash
# uninstall.sh — Désinstallation complète de NAS Sync

set -e

LOCAL="$HOME/offline_cache"
NAS="$HOME/NasShare"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
info() { echo -e "  $*"; }

echo ""
echo "══════════════════════════════════════════════"
echo "   NAS Sync — Désinstallation"
echo "══════════════════════════════════════════════"
echo ""
warn "Cette opération va supprimer le service, l'interface et les fichiers de configuration."
echo ""
read -rp "Continuer ? [o/N] " confirm
[[ "$confirm" =~ ^[oO]$ ]] || { echo "Annulé."; exit 0; }
echo ""

# ── 1. Arrêt et suppression du service systemd ───────────────────────────────

echo "Arrêt du service systemd…"
if systemctl --user is-active --quiet nas-sync.service 2>/dev/null; then
    systemctl --user stop nas-sync.service
    ok "Service arrêté"
else
    info "Service déjà arrêté"
fi

if systemctl --user is-enabled --quiet nas-sync.service 2>/dev/null; then
    systemctl --user disable nas-sync.service
    ok "Service désactivé"
fi

SERVICE_FILE="$HOME/.config/systemd/user/nas-sync.service"
if [ -f "$SERVICE_FILE" ]; then
    rm "$SERVICE_FILE"
    systemctl --user daemon-reload
    ok "Fichier service supprimé"
fi

# ── 2. Arrêt de l'interface (tray app) ───────────────────────────────────────

echo ""
echo "Arrêt de l'interface barre système…"
pkill -f "nas_sync_app.py" 2>/dev/null && ok "Interface arrêtée" || info "Interface non active"

AUTOSTART="$HOME/.config/autostart/nas-sync-app.desktop"
if [ -f "$AUTOSTART" ]; then
    rm "$AUTOSTART"
    ok "Entrée autostart GNOME supprimée"
fi

# ── 3. Suppression du fichier PID ─────────────────────────────────────────────

[ -f "$HOME/.nas_sync.pid" ] && rm "$HOME/.nas_sync.pid" && ok "PID supprimé"

# ── 4. Restauration des liens symboliques → NasShare ────────────────────────

echo ""
echo "Restauration des liens symboliques vers NasShare…"

declare -A SYMLINKS=(
    ["$HOME/Bureau"]="$NAS/Desktop"
    ["$HOME/Téléchargements"]="$NAS/Downloads"
    ["$HOME/Documents"]="$NAS/Documents"
    ["$HOME/Musique"]="$NAS/Music"
    ["$HOME/Images"]="$NAS/Pictures"
    ["$HOME/Vidéos"]="$NAS/video"
)

for link_path in "${!SYMLINKS[@]}"; do
    target="${SYMLINKS[$link_path]}"
    if [ -L "$link_path" ]; then
        rm "$link_path"
        ln -s "$target" "$link_path"
        ok "  $link_path → $target"
    fi
done

# ── 5. Restauration de XDG user-dirs → NasShare ──────────────────────────────

echo ""
echo "Restauration de ~/.config/user-dirs.dirs…"
mkdir -p "$HOME/.config"
cat > "$HOME/.config/user-dirs.dirs" << 'EOF'
XDG_DESKTOP_DIR="$HOME/NasShare/Desktop"
XDG_DOWNLOAD_DIR="$HOME/NasShare/Downloads"
XDG_DOCUMENTS_DIR="$HOME/NasShare/Documents"
XDG_MUSIC_DIR="$HOME/NasShare/Music"
XDG_PICTURES_DIR="$HOME/NasShare/Pictures"
XDG_VIDEOS_DIR="$HOME/NasShare/video"
EOF
xdg-user-dirs-update 2>/dev/null || true
ok "XDG user-dirs restauré vers NasShare"

# ── 6. Fichiers de configuration et logs ─────────────────────────────────────

echo ""
read -rp "Supprimer les fichiers de configuration et logs ? [o/N] " del_cfg
if [[ "$del_cfg" =~ ^[oO]$ ]]; then
    for f in \
        "$HOME/.nas_sync_config.json" \
        "$HOME/.nas_sync_state.json" \
        "$HOME/.nas_sync_events.jsonl" \
        "$HOME/.nas_sync.log"
    do
        [ -f "$f" ] && rm "$f" && ok "  $f supprimé"
    done
else
    info "Fichiers de configuration conservés"
fi

# ── 7. Cache local (offline_cache) ───────────────────────────────────────────

echo ""
if [ -d "$LOCAL" ]; then
    SIZE=$(du -sh "$LOCAL" 2>/dev/null | cut -f1)
    echo -e "${YELLOW}⚠${NC}  Le cache local ${BOLD}$LOCAL${NC} contient ${BOLD}$SIZE${NC} de données."
    echo "   Ces fichiers sont normalement déjà présents sur le NAS."
    echo ""
    read -rp "Supprimer le cache local ? [o/N] " del_cache
    if [[ "$del_cache" =~ ^[oO]$ ]]; then
        rm -rf "$LOCAL"
        ok "Cache local supprimé"
    else
        info "Cache local conservé dans $LOCAL"
    fi
fi

# ── Résumé ────────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════"
echo "   Désinstallation terminée"
echo "══════════════════════════════════════════════"
echo ""
echo "  Vos dossiers pointent à nouveau vers ~/NasShare."
echo "  Le NAS doit être connecté pour y accéder."
echo ""
echo "  Pour réinstaller : bash ~/programs/nas_sync/install.sh"
echo ""
