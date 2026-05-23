#!/bin/bash
# install.sh — Installation du système de synchronisation NAS

set -e

APP_VERSION="1.0.0"
APP_VERSION_NAME="Cassis"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL="$HOME/offline_cache"
NAS="$HOME/NasShare"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/nas_sync/config.json"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; PURPLE='\033[0;35m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }
err()   { echo -e "  ${RED}✗${NC} $*"; exit 1; }
title() { echo -e "\n${BOLD}${CYAN}● $*${NC}"; }
dim()   { echo -e "     ${DIM}$*${NC}"; }

fmt_bytes() {
    local b=${1:-0}
    if   [ "$b" -ge 1073741824 ]; then awk "BEGIN{printf \"%.1f Go\", $b/1073741824}"
    elif [ "$b" -ge 1048576    ]; then awk "BEGIN{printf \"%.1f Mo\", $b/1048576}"
    elif [ "$b" -ge 1024       ]; then awk "BEGIN{printf \"%.0f Ko\", $b/1024}"
    else echo "${b} o"; fi
}

run_with_spinner() {
    local message="$1"
    shift
    # Exécuter la commande en arrière-plan
    "$@" &
    local pid=$!
    
    # Frames de l'animation de chargement
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    
    printf "\e[?25l" >&2 # Cacher le curseur
    
    while kill -0 "$pid" 2>/dev/null; do
        local frame="${spin:$i:1}"
        printf "\r  ${GREEN}%s${NC} %s" "$frame" "$message" >&2
        sleep 0.08
        i=$(( (i+1) % 10 ))
    done
    
    wait "$pid"
    local exit_code=$?
    
    printf "\e[?25h" >&2 # Réafficher le curseur
    printf "\r\e[K"  >&2 # Effacer la ligne courante
    
    return $exit_code
}

# ──────────────────────────────────────────────────────────────────────────────

echo -e ""
echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}  ${BOLD}${CYAN}          NAS Sync — Installation           ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}│${NC}  ${DIM}       v${APP_VERSION}  ·  \"${APP_VERSION_NAME}\"                ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""
echo -e "  Ce script va installer et configurer votre système de"
echo -e "  synchronisation NAS intelligent."
echo -e ""
echo -e "  ${DIM}Note : Ce processus interactif nécessite votre attention au début,${NC}"
echo -e "  ${DIM}puis s'exécutera en tâche de fond pour la copie initiale.${NC}"
echo -e ""

# ═══════════════════════════════════════════════════════════════════════════════
# QUESTIONS (phase interactive — soyez présent)
# ═══════════════════════════════════════════════════════════════════════════════

title "① Mode d'utilisation"
echo -e ""
echo -e "     ${BOLD}1)${NC} ${GREEN}PC portable${NC}  — cache local + synchronisation automatique  ${CYAN}(Recommandé)${NC}"
echo -e "        ${DIM}Vos fichiers sont copiés localement dans ~/offline_cache/.${NC}"
echo -e "        ${DIM}Ils restent accessibles à pleine vitesse même sans réseau.${NC}"
echo -e "     ${BOLD}2)${NC} ${YELLOW}PC fixe${NC}      — accès direct au NAS"
echo -e "        ${DIM}Vos dossiers pointent directement vers le NAS monté.${NC}"
echo -e "        ${DIM}Le NAS doit être joignable en permanence.${NC}"
echo -e ""
echo -en "  Votre choix [1/2, défaut=1] : "
read -r _MODE_CHOICE
case "${_MODE_CHOICE}" in
    2) INSTALL_MODE="fixe"     ;;
    *) INSTALL_MODE="portable" ;;
esac
ok "Mode choisi : ${GREEN}${INSTALL_MODE}${NC}"

# ── NAS disponible ? ──────────────────────────────────────────────────────────

echo ""
if mountpoint -q "$NAS" 2>/dev/null; then
    NAS_OK=true
    ok "NAS détecté et monté sur ${GREEN}$NAS${NC}"
else
    NAS_OK=false
    warn "NAS non détecté ou non monté — les tailles de fichiers seront indisponibles et la copie initiale sera ignorée"
fi

# ── Dossiers : sélection + taille + quota ─────────────────────────────────────

title "② Dossiers à synchroniser"
echo -e ""
echo -e "  ${DIM}(Appuyez sur Entrée pour valider [Oui], tapez 'n' pour ignorer)${NC}"
echo -e ""

# Format : "Nom|local_sub|nas_sub|max_age_days"
DIR_DEFS=(
    "Bureau|Desktop|Desktop|0"
    "Téléchargements|Downloads|Downloads|90"
    "Documents|Documents|Documents|0"
    "Images|Pictures|Pictures|0"
    "Musique|Music|Music|180"
    "Vidéos|video|video|90"
)

# ── Pré-calcul des tailles NAS en parallèle ──────────────────────────────────
# Tous les scans démarrent immédiatement ; la boucle interactive n'attend que
# le dossier courant, et les autres sont déjà prêts quand on y arrive.
declare -A _NAS_SIZE_FILE  # nas_sub → chemin du fichier résultat
declare -A _NAS_SIZE_PID   # nas_sub → PID du sous-shell de calcul

if [ "$NAS_OK" = true ]; then
    dim "Calcul des volumes NAS en cours (arrière-plan)…"
    for _entry in "${DIR_DEFS[@]}"; do
        IFS='|' read -r _fn _ls _nas_sub _ma <<< "$_entry"
        if [ -d "$NAS/$_nas_sub" ]; then
            _tmpf=$(mktemp /tmp/nassync_sz_XXXXXX)
            _NAS_SIZE_FILE["$_nas_sub"]="$_tmpf"
            # find -printf : une seule passe, évite les stat() de répertoires de du
            ( find "$NAS/$_nas_sub" -type f -printf "%s\n" 2>/dev/null \
                | awk '{s+=$1}END{print s+0}' > "$_tmpf" ) &
            _NAS_SIZE_PID["$_nas_sub"]=$!
        fi
    done
fi

# Nettoyage des fichiers temporaires à la sortie du script (normal ou Ctrl+C)
_cleanup_sz() { rm -f "${_NAS_SIZE_FILE[@]}" 2>/dev/null; }
trap '_cleanup_sz' EXIT INT TERM

# SELECTED_DIRS entries : "Nom|local_sub|nas_sub|max_age_days|quota_go|nas_bytes"
SELECTED_DIRS=()
SELECTED_DIR_LABELS=()
TOTAL_NAS_BYTES=0

for entry in "${DIR_DEFS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub max_age <<< "$entry"

    # Taille sur le NAS — attendre uniquement ce dossier (les autres continuent)
    nas_bytes=0
    nas_size_str=""
    if [ "$NAS_OK" = true ] && [ -d "$NAS/$nas_sub" ]; then
        _pid="${_NAS_SIZE_PID[$nas_sub]:-}"
        _tmpf="${_NAS_SIZE_FILE[$nas_sub]:-}"
        if [ -n "$_pid" ]; then
            wait "$_pid" 2>/dev/null || true
        fi
        if [ -n "$_tmpf" ] && [ -f "$_tmpf" ]; then
            nas_bytes=$(cat "$_tmpf")
            nas_bytes=${nas_bytes:-0}
        fi
        nas_size_str=" (${GREEN}$(fmt_bytes "$nas_bytes")${NC} sur le NAS)"
    fi

    echo -en "  [?] Synchroniser le dossier '$fr_name'$nas_size_str ? [O/n] : "
    read -r answer
    case "$answer" in
        n|N|no|NO|non|NON)
            echo -e "     ${DIM}→ dossier '$fr_name' ignoré${NC}"
            continue
            ;;
    esac

    # Quota en Go (seulement si NAS disponible et taille connue)
    quota_go=0
    if [ "$NAS_OK" = true ] && [ "$nas_bytes" -gt 0 ] && [ "$INSTALL_MODE" = "portable" ]; then
        avail_kb=$(df -k "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')
        avail_bytes=$(( ${avail_kb:-0} * 1024 ))
        if [ "$nas_bytes" -gt "$avail_bytes" ]; then
            suggested_go=$(awk "BEGIN{printf \"%d\", $avail_bytes*0.8/1073741824}")
            [ "$suggested_go" -lt 1 ] && suggested_go=1
            warn "Ce dossier fait $(fmt_bytes $nas_bytes) ; espace libre sur votre PC : $(fmt_bytes $avail_bytes)"
            echo -en "     ${YELLOW}»${NC} Définir un quota en Go (0 = illimité, conseillé ≤ ${suggested_go} Go) : "
            read -r quota_go
        else
            echo -en "     ${CYAN}»${NC} Définir un quota en Go (0 = tout télécharger) : "
            read -r quota_go
        fi
        [[ "$quota_go" =~ ^[0-9]+$ ]] || quota_go=0
    fi

    if [ "$quota_go" -gt 0 ]; then
        effective_bytes=$(( quota_go * 1073741824 ))
        [ "$effective_bytes" -gt "$nas_bytes" ] && effective_bytes=$nas_bytes
    else
        effective_bytes=$nas_bytes
    fi
    TOTAL_NAS_BYTES=$(( TOTAL_NAS_BYTES + effective_bytes ))

    SELECTED_DIRS+=("$fr_name|$local_sub|$nas_sub|$max_age|$quota_go|$nas_bytes")
    SELECTED_DIR_LABELS+=("$fr_name")
    ok "$fr_name${quota_go:+ — quota : ${quota_go} Go}"
done

[ ${#SELECTED_DIRS[@]} -gt 0 ] || err "Aucun dossier sélectionné. Installation annulée."

# ── Récapitulatif avant de commencer ─────────────────────────────────────────

echo -e ""
echo -e "  ${BLUE}┌────────────────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}                 ${BOLD}${CYAN}Récapitulatif de l'installation${NC}        ${BLUE}│${NC}"
echo -e "  ${BLUE}├────────────────────────────────────────────────────────┤${NC}"
echo -e "  ${BLUE}│${NC}  Mode     : ${GREEN}$INSTALL_MODE${NC}"
echo -e "  ${BLUE}│${NC}  Dossiers : ${YELLOW}${SELECTED_DIR_LABELS[*]}${NC}"

if [ "$NAS_OK" = true ] && [ "$INSTALL_MODE" = "portable" ]; then
    avail_bytes=$(( $(df -k "$HOME" 2>/dev/null | awk 'NR==2 {print $4}') * 1024 ))
    echo -e "  ${BLUE}│${NC}  À copier : ${CYAN}$(fmt_bytes $TOTAL_NAS_BYTES)${NC}"
    echo -e "  ${BLUE}│${NC}  Esp. PC  : $(fmt_bytes $avail_bytes)"
    echo -e "  ${BLUE}├────────────────────────────────────────────────────────┤${NC}"
    MARGIN=1073741824
    if [ "$TOTAL_NAS_BYTES" -gt $(( avail_bytes - MARGIN )) ]; then
        echo -e "  ${BLUE}│${NC}  ${YELLOW}⚠ Attention : Espace disque local très juste !${NC}"
        echo -e "  ${BLUE}│${NC}    Il est vivement conseillé de réduire les quotas."
    else
        echo -e "  ${BLUE}│${NC}  ${GREEN}✓ Espace disque local suffisant.${NC}"
    fi
fi
echo -e "  ${BLUE}└────────────────────────────────────────────────────────┘${NC}"
echo -e ""
echo -en "  [?] Lancer l'installation maintenant ? [O/n] : "
read -r _CONFIRM
case "${_CONFIRM:-o}" in
    n|N|no|NO|non|NON) echo "  Annulé."; exit 0 ;;
esac

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 : Configuration (automatique, quelques secondes)
# ═══════════════════════════════════════════════════════════════════════════════

echo -e ""
echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}   ${BOLD}${CYAN}⚙  Phase 1 : Configuration automatique    ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""

# ── Dépendances ───────────────────────────────────────────────────────────────

_dnf_install() {
    local pkg="$1" desc="$2"
    local tmp_err
    tmp_err=$(mktemp)
    if run_with_spinner "Installation de $desc ($pkg)..." bash -c "sudo dnf install -y $pkg >/dev/null 2>'$tmp_err'"; then
        ok "$desc installé"
    else
        warn "Échec installation $pkg — certaines fonctionnalités seront limitées (voir stderr)"
        dim "$(cat "$tmp_err" 2>/dev/null)"
    fi
    rm -f "$tmp_err"
}

HAS_RSYNC=false

if ! python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null; then
    _dnf_install python3-gobject "PyGObject GTK3"
    python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null \
        || err "PyGObject GTK3 introuvable après installation"
fi
ok "PyGObject GTK3"

if ! python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    warn "AppIndicator3 absent — icône barre système en mode dégradé"
    dim "    (sudo dnf install libappindicator-gtk3 pour l'activer)"
else
    ok "AppIndicator3"
fi

command -v notify-send &>/dev/null || _dnf_install libnotify "notify-send"
command -v notify-send &>/dev/null && ok "notify-send" || warn "notify-send absent"

if command -v rsync &>/dev/null; then
    ok "rsync"; HAS_RSYNC=true
else
    _dnf_install rsync "rsync"
    command -v rsync &>/dev/null && { ok "rsync"; HAS_RSYNC=true; } \
        || warn "rsync absent — sync initiale ignorée"
fi

# ── Cache local ───────────────────────────────────────────────────────────────

for entry in "${SELECTED_DIRS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub max_age quota_go nas_bytes <<< "$entry"
    mkdir -p "$LOCAL/$local_sub"
done
ok "Cache local $LOCAL créé"

# ── Liens symboliques ─────────────────────────────────────────────────────────

[ "$INSTALL_MODE" = "fixe" ] && LINK_BASE="$NAS" || LINK_BASE="$LOCAL"

for entry in "${SELECTED_DIRS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub max_age quota_go nas_bytes <<< "$entry"
    link_path="$HOME/$fr_name"
    target="$LINK_BASE/$local_sub"
    mkdir -p "$target"
    if [ -L "$link_path" ]; then
        rm "$link_path"
    elif [ -d "$link_path" ]; then
        if [ -n "$(ls -A "$link_path" 2>/dev/null)" ]; then
            run_with_spinner "Copie du contenu local de '$fr_name' vers le nouveau dossier..." cp -a "$link_path/." "$target/"
        fi
        rmdir "$link_path" 2>/dev/null || { warn "$link_path non vide — ignoré"; continue; }
    fi
    ln -s "$target" "$link_path"
done
ok "Liens symboliques créés (→ $LINK_BASE)"

# ── XDG user-dirs ─────────────────────────────────────────────────────────────

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
mkdir -p "$HOME/Modèles" "$HOME/Public"
xdg-user-dirs-update 2>/dev/null || true
ok "XDG user-dirs (noms français)"

# ── Configuration JSON ────────────────────────────────────────────────────────

mkdir -p "$(dirname "$CONFIG")"
PYTHONPATH="$SCRIPT_DIR" python3 - << PYEOF
import json, sys
from pathlib import Path
from nas_sync_config import load_config, save_config

cfg = load_config()
cfg["mode"] = "$INSTALL_MODE"
cfg["dirs"] = [
$(for entry in "${SELECTED_DIRS[@]}"; do
    IFS='|' read -r fr_name local_sub nas_sub max_age quota_go nas_bytes <<< "$entry"
    quota_mb=$(( quota_go * 1024 ))
    echo "    {\"local_sub\": \"$local_sub\", \"nas_sub\": \"$nas_sub\", \"enabled\": True, \"max_age_days\": $max_age, \"max_size_mb\": $quota_mb},"
done)
]
save_config(cfg)
PYEOF
ok "Configuration enregistrée ($CONFIG)"

# ── Service systemd (portable uniquement) ─────────────────────────────────────

if [ "$INSTALL_MODE" = "portable" ]; then
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/nas-sync.service" << EOF
[Unit]
Description=NAS Sync Daemon
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
    ok "Service systemd activé"
else
    ok "Mode PC fixe — service de synchronisation non activé"
fi

# ── Autostart + menu ──────────────────────────────────────────────────────────

mkdir -p "$HOME/.config/autostart" "$HOME/.local/share/applications"
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
cp "$HOME/.config/autostart/nas-sync-app.desktop" \
   "$HOME/.local/share/applications/nas-sync.desktop" 2>/dev/null || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
ok "Démarrage automatique + menu applications"

echo -e ""
ok "Phase 1 terminée avec succès !"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 : Synchronisation initiale (peut prendre du temps)
# ═══════════════════════════════════════════════════════════════════════════════

echo -e ""
echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}   ${BOLD}${CYAN}🔄  Phase 2 : Synchronisation initiale      ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""

if [ "$NAS_OK" != true ] || [ "$HAS_RSYNC" != true ] || [ "$INSTALL_MODE" != "portable" ]; then
    if [ "$INSTALL_MODE" != "portable" ]; then
        ok "Mode PC fixe — aucun cache local à alimenter"
    elif [ "$NAS_OK" != true ]; then
        warn "NAS non joignable — synchronisation initiale ignorée"
        dim "Relancez l'installation depuis votre réseau local pour copier vos fichiers."
    else
        warn "rsync absent — synchronisation initiale ignorée"
    fi
else
    dim "Vous pouvez laisser tourner et fermer ce terminal si vous le souhaitez."
    dim "Le démon prendra automatiquement le relais une fois terminé."
    echo ""

    for entry in "${SELECTED_DIRS[@]}"; do
        IFS='|' read -r fr_name local_sub nas_sub max_age quota_go nas_bytes <<< "$entry"
        src="$NAS/$nas_sub"
        dst="$LOCAL/$local_sub"

        [ -d "$src" ] || { warn "Dossier '$fr_name' absent sur le NAS — ignoré"; continue; }

        # Vérifier ce qui est déjà présent dans le cache local
        local_count=0
        local_bytes_cached=0
        if [ -d "$dst" ] && [ -n "$(ls -A "$dst" 2>/dev/null)" ]; then
            local_count=$(find "$dst" -type f 2>/dev/null | wc -l)
            local_bytes_cached=$(du -sb "$dst" 2>/dev/null | awk '{print $1}')
            local_bytes_cached=${local_bytes_cached:-0}
            dim "→ $local_count fichier(s) déjà en cache ($(fmt_bytes $local_bytes_cached)) — seuls les fichiers manquants ou modifiés sur le NAS seront téléchargés"
        fi

        # Taille effective (avec quota si défini)
        files_from_tmp=""
        if [ "$quota_go" -gt 0 ]; then
            effective_bytes=$(( quota_go * 1073741824 ))
            [ "$effective_bytes" -gt "$nas_bytes" ] && effective_bytes=$nas_bytes
            size_label="quota ${quota_go} Go"
            # Construire une liste de fichiers triés par date (plus récent d'abord)
            # dont le cumul ne dépasse pas le budget quota.
            files_from_tmp=$(mktemp /tmp/nassync_quota_XXXXXX.txt)
            find "$src" -type f -printf '%T@ %s %P\n' | sort -rn | awk -v budget="$effective_bytes" '
            BEGIN { cumul = 0 }
            {
                sz = $2
                if (cumul + sz <= budget) {
                    cumul += sz
                    # Imprimer le chemin relatif (du champ 3 à la fin)
                    for (i = 3; i <= NF; i++) printf "%s%s", (i>3 ? " " : ""), $i
                    printf "\n"
                }
            }' > "$files_from_tmp"
            rsync_extra_args="--files-from=$files_from_tmp"
        else
            effective_bytes=$nas_bytes
            size_label="$(fmt_bytes $effective_bytes)"
            rsync_extra_args=""
        fi

        echo -e "  ${BOLD}${CYAN}»${NC} Synchronisation de : ${BOLD}$fr_name${NC} ($size_label)"
        rsync -ah --info=progress2 $rsync_extra_args \
            "$src/" "$dst/" 2>/dev/null | \
            awk -v fr="$fr_name" -v RS='\r' '
            {
                # Extraire le pourcentage
                pct = 0
                if (match($0, /[0-9]+%/)) {
                    pct = substr($0, RSTART, RLENGTH - 1) + 0
                }
                
                # Extraire la vitesse
                speed = "0 Ko/s"
                if (match($0, /[0-9]+(\.[0-9]+)?[a-zA-Z]+B\/s/)) {
                    speed = substr($0, RSTART, RLENGTH)
                }
                
                # Extraire la taille transférée
                size = $1
                
                # Extraire le temps restant (ETA)
                eta = "--:--:--"
                if (match($0, /[0-9]+:[0-9]+:[0-9]+/)) {
                    eta = substr($0, RSTART, RLENGTH)
                }
                
                # Générer la barre de progression (longueur 20)
                filled = int(pct / 5)
                bar = ""
                for (i = 1; i <= 20; i++) {
                    if (i <= filled) {
                        bar = bar "\033[0;32m█\033[0m"
                    } else {
                        bar = bar "\033[2m░\033[0m"
                    }
                }
                
                # Affichage formaté
                printf "\r    %-16s  [%s]  \033[1;32m%3d%%\033[0m  (%s, %s, rest. %s)   ", 
                       fr, bar, pct, size, speed, eta
                fflush()
            }
            END {
                # Afficher la ligne finale à 100%
                filled = 20
                bar = ""
                for (i = 1; i <= 20; i++) bar = bar "\033[0;32m█\033[0m"
                printf "\r    %-16s  [%s]  \033[1;32m100%%\033[0m                                 \n", fr, bar
            }
            ' || true
        ok "$fr_name synchronisé avec succès"
        [ -n "$files_from_tmp" ] && rm -f "$files_from_tmp"
        echo ""
    done
fi

# ── Démarrer le démon ─────────────────────────────────────────────────────────

if [ "$INSTALL_MODE" = "portable" ]; then
    systemctl --user start nas-sync.service 2>/dev/null
    sleep 1
    systemctl --user is-active --quiet nas-sync.service \
        && ok "Démon NAS Sync démarré et actif" \
        || warn "Démon démarré (vérifiez avec : systemctl --user status nas-sync)"
fi

# Lancer l'interface immédiatement si session graphique
if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
    python3 "$SCRIPT_DIR/nas_sync_app.py" &
    ok "Interface de contrôle lancée (icône dans la barre système)"
fi

# ── Résumé ────────────────────────────────────────────────────────────────────

echo -e ""
echo -e "  ${GREEN}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${GREEN}│${NC}   ${BOLD}${GREEN}🎉   Félicitations, installation terminée !  ${NC}${GREEN}│${NC}"
echo -e "  ${GREEN}└──────────────────────────────────────────────┘${NC}"
echo -e ""
echo -e "  ${BOLD}Mode sélectionné :${NC} ${GREEN}$INSTALL_MODE${NC}"
echo -e "  ${BOLD}Dossiers configurés :${NC} ${YELLOW}${SELECTED_DIR_LABELS[*]}${NC}"
echo -e ""
if [ "$INSTALL_MODE" = "portable" ]; then
echo -e "  ${BOLD}Commandes utiles :${NC}"
echo -e "    ${CYAN}• Statut du démon :${NC}  systemctl --user status nas-sync"
echo -e "    ${CYAN}• Consulter les logs :${NC} tail -f \${XDG_CACHE_HOME:-\$HOME/.cache}/nas_sync/daemon.log"
echo -e "    ${CYAN}• Activer / Suspendre :${NC} bash $SCRIPT_DIR/toggle.sh"
fi
echo -e "  ${BOLD}Note :${NC} L'application démarrera automatiquement à chaque ouverture de session."
echo -e "         Vous pouvez ajuster les filtres et les quotas dans les Paramètres de la barre système."
echo -e ""
