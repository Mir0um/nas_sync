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
    ok "Entrée autostart supprimée"
fi
APP_DESKTOP="$HOME/.local/share/applications/nas-sync.desktop"
[ -f "$APP_DESKTOP" ] && rm "$APP_DESKTOP" && ok "Entrée menu applications supprimée"

# ── 3. Suppression des fichiers PID/lock (anciens et XDG) ────────────────────

[ -f "$HOME/.nas_sync.pid" ]  && rm "$HOME/.nas_sync.pid"
XDG_RUNTIME_NAS="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/nas_sync"
[ -f "$XDG_RUNTIME_NAS/daemon.pid" ]  && rm "$XDG_RUNTIME_NAS/daemon.pid"
[ -f "$XDG_RUNTIME_NAS/daemon.lock" ] && rm "$XDG_RUNTIME_NAS/daemon.lock"

# ── 4. Restauration des liens symboliques ────────────────────────────────────

echo ""
echo "Restauration des liens symboliques…"

# Déterminer la cible : NasShare si monté, sinon offline_cache si disponible
if mountpoint -q "$NAS" 2>/dev/null; then
    LINK_BASE="$NAS"
    LINK_BASE_LABEL="NasShare"
elif [ -d "$LOCAL" ]; then
    LINK_BASE="$LOCAL"
    LINK_BASE_LABEL="offline_cache (NAS non monté)"
else
    warn "NAS non monté et cache local absent — liens symboliques non restaurés"
    LINK_BASE=""
fi

if [ -n "$LINK_BASE" ]; then
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
            ln -s "$target" "$link_path"
            ok "  $link_path → $target"
        fi
    done
    ok "Liens restaurés vers $LINK_BASE_LABEL"
fi

# ── 5. Restauration de XDG user-dirs (noms français standard) ────────────────
# IMPORTANT : on écrit toujours les noms français ($HOME/Bureau, etc.), jamais
# les chemins NAS absolus. Si xdg-user-dirs-update s'exécute au prochain login
# alors que le NAS est hors ligne, un chemin NAS absent ferait réinitialiser les
# dossiers XDG vers $HOME — ce qui afficherait tous les fichiers cachés sur le bureau.

echo ""
echo "Restauration de ~/.config/user-dirs.dirs…"
mkdir -p "$HOME/.config"
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
xdg-user-dirs-update 2>/dev/null || true
ok "XDG user-dirs restauré (noms français standard)"

# ── 6. Fichiers de configuration et logs ─────────────────────────────────────

echo ""
read -rp "Supprimer les fichiers de configuration et logs ? [o/N] " del_cfg
if [[ "$del_cfg" =~ ^[oO]$ ]]; then
    # Anciens emplacements (~/)
    for f in \
        "$HOME/.nas_sync_config.json" \
        "$HOME/.nas_sync_state.json" \
        "$HOME/.nas_sync_events.jsonl" \
        "$HOME/.nas_sync.log"
    do
        [ -f "$f" ] && rm "$f" && ok "  $f supprimé"
    done
    # Emplacements XDG
    XDG_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/nas_sync"
    XDG_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/nas_sync"
    XDG_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/nas_sync"
    [ -d "$XDG_CFG"   ] && rm -rf "$XDG_CFG"   && ok "  $XDG_CFG supprimé"
    [ -d "$XDG_CACHE" ] && rm -rf "$XDG_CACHE"  && ok "  $XDG_CACHE supprimé"
    [ -d "$XDG_DATA"  ] && rm -rf "$XDG_DATA"   && ok "  $XDG_DATA supprimé"
    [ -d "$XDG_RUNTIME_NAS" ] && rm -rf "$XDG_RUNTIME_NAS" && ok "  $XDG_RUNTIME_NAS supprimé"
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
echo "  Vos dossiers (~/Bureau, ~/Documents…) pointent vers leur cible restaurée."
echo "  Si le NAS était la cible, il doit être connecté pour accéder aux fichiers."
echo ""
echo "  Pour réinstaller : bash ~/programs/nas_sync/install.sh"
echo ""
