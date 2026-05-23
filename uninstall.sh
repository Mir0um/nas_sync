#!/bin/bash
# uninstall.sh — Désinstallation complète de NAS Sync

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL="$HOME/offline_cache"
NAS="$HOME/NasShare"

# Palette de couleurs et styles
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; PURPLE='\033[0;35m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

# Fonctions d'affichage d'aide et de statut
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }
err()   { echo -e "  ${RED}✗${NC} $*"; }
info()  { echo -e "  $*"; }
title() { echo -e "\n${BOLD}${CYAN}● $*${NC}"; }
dim()   { echo -e "     ${DIM}$*${NC}"; }

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

# Variable globale : mode de restauration choisi à l'étape 4
# Valeurs possibles : "local" | "nas" | "keep"
RESTORE_MODE=""

# Variable globale : l'utilisateur veut-il se déconnecter du NAS ? (étape 8)
DISCONNECT_NAS=false

# ──────────────────────────────────────────────────────────────────────────────

echo -e ""
echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}  ${BOLD}${CYAN}         NAS Sync — Désinstallation         ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""
echo -e "  Ce script va désinstaller complètement votre système de"
echo -e "  synchronisation NAS intelligent."
echo -e ""
echo -e "  ${YELLOW}⚠ Attention : Cette opération va supprimer le service, l'interface${NC}"
echo -e "  ${YELLOW}et les fichiers de configuration.${NC}"
echo -e ""
echo -en "  Continuer la désinstallation ? [o/N] : "
read -r confirm
[[ "$confirm" =~ ^[oO]$ ]] || { echo -e "  Annulé."; exit 0; }
echo -e ""

# ==============================================================================
# PHASE 1 : Désactivation et arrêt
# ==============================================================================

echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}   ${BOLD}${CYAN}🛑  Phase 1 : Désactivation et arrêt        ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""

# ── Step 1: Arrêt et suppression du service systemd ───────────────────────────
title "① Arrêt du service systemd"

if systemctl --user is-active --quiet nas-sync.service 2>/dev/null; then
    run_with_spinner "Arrêt du service systemd en cours..." systemctl --user stop nas-sync.service
    ok "Service arrêté"
else
    ok "Service déjà arrêté"
fi

if systemctl --user is-enabled --quiet nas-sync.service 2>/dev/null; then
    systemctl --user disable nas-sync.service 2>/dev/null || true
    ok "Service désactivé"
fi

SERVICE_FILE="$HOME/.config/systemd/user/nas-sync.service"
if [ -f "$SERVICE_FILE" ]; then
    rm "$SERVICE_FILE"
    systemctl --user daemon-reload
    ok "Fichier service supprimé"
fi

# ── Step 2: Arrêt de l'interface (tray app) ───────────────────────────────────
title "② Arrêt de l'interface barre système"

if pkill -f "nas_sync_app.py" 2>/dev/null; then
    ok "Interface de contrôle arrêtée"
else
    ok "Interface non active"
fi

AUTOSTART="$HOME/.config/autostart/nas-sync-app.desktop"
if [ -f "$AUTOSTART" ]; then
    rm "$AUTOSTART"
    ok "Entrée de démarrage automatique (autostart) supprimée"
fi

APP_DESKTOP="$HOME/.local/share/applications/nas-sync.desktop"
if [ -f "$APP_DESKTOP" ]; then
    rm "$APP_DESKTOP"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    ok "Entrée du menu applications supprimée et base de données mise à jour"
fi

# ── Step 3: Suppression des fichiers temporaires et verrous ───────────────────
title "③ Suppression des fichiers temporaires et verrous"

REMOVED_TEMP=false
if [ -f "$HOME/.nas_sync.pid" ]; then
    rm "$HOME/.nas_sync.pid"
    REMOVED_TEMP=true
fi

XDG_RUNTIME_NAS="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/nas_sync"
if [ -d "$XDG_RUNTIME_NAS" ]; then
    [ -f "$XDG_RUNTIME_NAS/daemon.pid" ]  && rm "$XDG_RUNTIME_NAS/daemon.pid"  && REMOVED_TEMP=true
    [ -f "$XDG_RUNTIME_NAS/daemon.lock" ] && rm "$XDG_RUNTIME_NAS/daemon.lock" && REMOVED_TEMP=true
fi

if [ "$REMOVED_TEMP" = true ]; then
    ok "Fichiers verrous et PID de session nettoyés"
else
    ok "Aucun fichier verrou ou temporaire à nettoyer"
fi

# ==============================================================================
# PHASE 2 : Restauration et système
# ==============================================================================

echo -e ""
echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}   ${BOLD}${CYAN}📂  Phase 2 : Restauration des dossiers      ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""

# ── Step 4: Restauration des dossiers utilisateur ─────────────────────────────
title "④ Restauration des dossiers utilisateur"
echo -e ""

# Mappages : "nom_français|sous_dossier_cache"
DIR_MAPPINGS=(
    "Bureau|Desktop"
    "Téléchargements|Downloads"
    "Documents|Documents"
    "Musique|Music"
    "Images|Pictures"
    "Vidéos|video"
)

# Détecter si le NAS est disponible
NAS_AVAILABLE=false
mountpoint -q "$NAS" 2>/dev/null && NAS_AVAILABLE=true

# Détecter si le cache local existe
CACHE_EXISTS=false
[ -d "$LOCAL" ] && CACHE_EXISTS=true

# Détecter la source actuelle des liens (pour l'affichage)
CURRENT_TARGET="(inconnu)"
if [ -L "$HOME/Bureau" ]; then
    _cur=$(readlink -f "$HOME/Bureau" 2>/dev/null || true)
    case "$_cur" in
        "$LOCAL"*) CURRENT_TARGET="cache local ($LOCAL)" ;;
        "$NAS"*)   CURRENT_TARGET="NAS ($NAS)" ;;
        *)         CURRENT_TARGET="$_cur" ;;
    esac
fi

dim "Vos dossiers pointent actuellement vers : ${CURRENT_TARGET}"
echo -e ""
echo -e "  ${BOLD}Comment souhaitez-vous restaurer vos dossiers ?${NC}"
echo -e ""
echo -e "     ${BOLD}1)${NC} ${GREEN}Dossiers locaux standards${NC}  ${CYAN}(Recommandé)${NC}"
echo -e "        ${DIM}Remplace les liens par de vrais dossiers sous ~/,${NC}"
echo -e "        ${DIM}et y transfère vos fichiers depuis le cache local.${NC}"
echo -e "     ${BOLD}2)${NC} ${YELLOW}Liens directs vers le NAS${NC}"
echo -e "        ${DIM}Recrée les liens pointant vers ~/NasShare/…${NC}"
if [ "$NAS_AVAILABLE" != true ]; then
    echo -e "        ${RED}⚠ Le NAS n'est pas monté actuellement !${NC}"
fi
echo -e "     ${BOLD}3)${NC} Conserver les liens actuels"
echo -e "        ${DIM}Ne touche pas aux redirections existantes.${NC}"
echo -e ""
echo -en "  Votre choix [1/2/3, défaut=1] : "
read -r _RESTORE_CHOICE

case "${_RESTORE_CHOICE}" in
    2) RESTORE_MODE="nas"   ;;
    3) RESTORE_MODE="keep"  ;;
    *) RESTORE_MODE="local" ;;
esac

if [ "$RESTORE_MODE" = "local" ]; then
    echo -e ""
    for entry in "${DIR_MAPPINGS[@]}"; do
        IFS='|' read -r fr_name sub_dir <<< "$entry"
        link_path="$HOME/$fr_name"
        cache_dir="$LOCAL/$sub_dir"

        if [ -L "$link_path" ]; then
            rm "$link_path"
        fi

        # Créer le vrai dossier s'il n'existe pas
        mkdir -p "$link_path"

        # Transférer les fichiers depuis le cache local si disponible
        if [ -d "$cache_dir" ] && [ -n "$(ls -A "$cache_dir" 2>/dev/null)" ]; then
            cp -a "$cache_dir/." "$link_path/" 2>/dev/null || true
            ok "$link_path ← fichiers récupérés depuis le cache local"
        else
            ok "$link_path (nouveau dossier vide créé)"
        fi
    done
    ok "Dossiers locaux standards restaurés avec succès !"

elif [ "$RESTORE_MODE" = "nas" ]; then
    if [ "$NAS_AVAILABLE" != true ]; then
        echo -e ""
        warn "Le NAS n'est pas monté — les liens pointeront vers un emplacement absent."
        echo -en "  Continuer quand même ? [o/N] : "
        read -r _nas_confirm
        if [[ ! "$_nas_confirm" =~ ^[oO]$ ]]; then
            RESTORE_MODE="keep"
            ok "Liens existants conservés en l'état"
        fi
    fi

    if [ "$RESTORE_MODE" = "nas" ]; then
        echo -e ""
        for entry in "${DIR_MAPPINGS[@]}"; do
            IFS='|' read -r fr_name sub_dir <<< "$entry"
            link_path="$HOME/$fr_name"
            target="$NAS/$sub_dir"

            if [ -L "$link_path" ]; then
                rm "$link_path"
            elif [ -d "$link_path" ]; then
                # Si c'est un vrai dossier, on le conserve en .bak s'il n'est pas vide
                if [ -n "$(ls -A "$link_path" 2>/dev/null)" ]; then
                    mv "$link_path" "${link_path}.bak"
                    warn "$link_path non vide → renommé en ${link_path}.bak"
                else
                    rmdir "$link_path" 2>/dev/null || true
                fi
            fi
            ln -s "$target" "$link_path"
            ok "$link_path → $target"
        done
        ok "Liens directs vers le NAS configurés avec succès !"
    fi

else
    ok "Liens actuels conservés en l'état (pointent vers $CURRENT_TARGET)"
fi

# ── Step 5: Restauration de XDG user-dirs (noms français standard) ───────────
title "⑤ Restauration de la configuration XDG user-dirs"

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
ok "Configuration ~/.config/user-dirs.dirs mise à jour (noms français)"

# ==============================================================================
# PHASE 3 : Nettoyage des fichiers
# ==============================================================================

echo -e ""
echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}   ${BOLD}${CYAN}🧹  Phase 3 : Nettoyage des fichiers        ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""

# ── Step 6: Fichiers de configuration et logs ─────────────────────────────────
title "⑥ Fichiers de configuration et logs"
echo -e ""

echo -en "  Supprimer les fichiers de configuration et les journaux de logs ? [o/N] : "
read -r del_cfg
if [[ "$del_cfg" =~ ^[oO]$ ]]; then
    echo -e ""
    # Anciens emplacements (~/)
    for f in \
        "$HOME/.nas_sync_config.json" \
        "$HOME/.nas_sync_state.json" \
        "$HOME/.nas_sync_events.jsonl" \
        "$HOME/.nas_sync.log"
    do
        [ -f "$f" ] && rm "$f" && ok "$f supprimé"
    done
    
    # Emplacements XDG
    XDG_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/nas_sync"
    XDG_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/nas_sync"
    XDG_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/nas_sync"
    [ -d "$XDG_CFG"   ] && rm -rf "$XDG_CFG"   && ok "Dossier de configuration supprimé : $XDG_CFG"
    [ -d "$XDG_CACHE" ] && rm -rf "$XDG_CACHE"  && ok "Dossier de cache supprimé : $XDG_CACHE"
    [ -d "$XDG_DATA"  ] && rm -rf "$XDG_DATA"   && ok "Dossier de données supprimé : $XDG_DATA"
    [ -d "$XDG_RUNTIME_NAS" ] && rm -rf "$XDG_RUNTIME_NAS" && ok "Dossier temporaire supprimé : $XDG_RUNTIME_NAS"

    # Identifiants SMB (créés par deploy.sh)
    SMB_CREDS="$HOME/.smbcredentials"
    if [ -f "$SMB_CREDS" ]; then
        echo -e ""
        warn "Fichier d'identifiants NAS détecté : ${BOLD}$SMB_CREDS${NC}"
        echo -en "  Supprimer ce fichier confidentiel d'identifiants ? [o/N] : "
        read -r del_smb
        if [[ "$del_smb" =~ ^[oO]$ ]]; then
            rm "$SMB_CREDS"
            ok "Fichier $SMB_CREDS supprimé"
        else
            ok "Identifiants conservés dans $SMB_CREDS"
        fi
    fi
else
    ok "Fichiers de configuration et logs conservés"
fi

# ── Step 7: Cache local (offline_cache) ───────────────────────────────────────
title "⑦ Nettoyage du cache local (offline_cache)"
echo -e ""

if [ -d "$LOCAL" ]; then
    SIZE=$(du -sh "$LOCAL" 2>/dev/null | cut -f1)
    warn "Le cache local ${BOLD}$LOCAL${NC} contient ${BOLD}$SIZE${NC} de données."

    # Vérifier si des liens symboliques pointent encore vers le cache
    LINKS_TO_CACHE=false
    for entry in "${DIR_MAPPINGS[@]}"; do
        IFS='|' read -r fr_name sub_dir <<< "$entry"
        link_path="$HOME/$fr_name"
        if [ -L "$link_path" ]; then
            _target=$(readlink -f "$link_path" 2>/dev/null || true)
            case "$_target" in
                "$LOCAL"*) LINKS_TO_CACHE=true; break ;;
            esac
        fi
    done

    if [ "$LINKS_TO_CACHE" = true ]; then
        echo -e ""
        echo -e "  ${RED}${BOLD}⚠ ATTENTION :${NC} Des liens symboliques (~/Bureau, ~/Documents…)"
        echo -e "  ${RED}pointent encore vers ce cache local.${NC}"
        echo -e "  ${RED}Le supprimer rendrait vos dossiers inaccessibles (liens brisés).${NC}"
        echo -e ""
        warn "Conseil : Relancez ce script et choisissez l'option 1 à l'étape ④."
        echo -e ""
        echo -en "  Supprimer quand même le cache local ? (NON recommandé) [o/N] : "
        read -r del_cache
    else
        dim "Ces fichiers sont normalement déjà présents sur le NAS."
        if [ "$RESTORE_MODE" = "local" ]; then
            dim "De plus, ils ont été copiés dans vos dossiers utilisateurs standards à l'étape ④."
        fi
        echo -e ""
        echo -en "  Supprimer tout le cache local ($LOCAL) ? [o/N] : "
        read -r del_cache
    fi

    if [[ "$del_cache" =~ ^[oO]$ ]]; then
        run_with_spinner "Suppression du cache local..." rm -rf "$LOCAL"
        ok "Cache local supprimé"
    else
        ok "Cache local conservé dans $LOCAL"
    fi
else
    ok "Aucun cache local détecté sur $LOCAL"
fi

# ==============================================================================
# PHASE 4 : Connexion et montage NAS
# ==============================================================================

echo -e ""
echo -e "  ${BLUE}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}   ${BOLD}${CYAN}🔌  Phase 4 : Connexion et montage NAS       ${NC}${BLUE}│${NC}"
echo -e "  ${BLUE}└──────────────────────────────────────────────┘${NC}"
echo -e ""

# ── Step 8: Connexion NAS (montage / fstab) ───────────────────────────────────
title "⑧ Déconnexion / Gestion du montage NAS"
echo -e ""

# Détecter les infos de montage depuis fstab
FSTAB_LINE=""
FSTAB_NAS_HOST=""
FSTAB_NAS_SHARE=""
if [ -f /etc/fstab ]; then
    FSTAB_LINE=$(grep -E "^//[^ ]+\s+$NAS\s" /etc/fstab 2>/dev/null || true)
    if [ -n "$FSTAB_LINE" ]; then
        _fstab_src=$(echo "$FSTAB_LINE" | awk '{print $1}')
        FSTAB_NAS_HOST=$(echo "$_fstab_src" | sed 's|^//||; s|/.*||')
        FSTAB_NAS_SHARE=$(echo "$_fstab_src" | sed 's|^//[^/]*/||')
    fi
fi

# Détecter si le NAS est actuellement monté
NAS_IS_MOUNTED=false
mountpoint -q "$NAS" 2>/dev/null && NAS_IS_MOUNTED=true

# Afficher l'état actuel
if [ "$NAS_IS_MOUNTED" = true ]; then
    ok "Le NAS est actuellement ${GREEN}monté${NC} sur $NAS"
else
    warn "Le NAS n'est actuellement ${YELLOW}pas monté${NC}"
fi
if [ -n "$FSTAB_LINE" ]; then
    dim "Configuration fstab détectée : //${FSTAB_NAS_HOST}/${FSTAB_NAS_SHARE}"
fi
echo -e ""

echo -e "  ${BOLD}Souhaitez-vous déconnecter complètement le NAS de cet ordinateur ?${NC}"
echo -e ""
echo -e "     ${BOLD}1)${NC} ${GREEN}Non — Conserver la connexion NAS${NC}  ${CYAN}(Mode PC fixe)${NC}"
echo -e "        ${DIM}Le NAS restera accessible sur ~/NasShare/ et sera monté${NC}"
echo -e "        ${DIM}automatiquement à chaque démarrage du système.${NC}"
echo -e "     ${BOLD}2)${NC} ${RED}Oui — Se déconnecter complètement${NC}"
echo -e "        ${DIM}Démonte le NAS, supprime l'entrée de montage automatique (/etc/fstab)${NC}"
echo -e "        ${DIM}et retire le point de montage ~/NasShare/ (si vide).${NC}"
if [ -n "$FSTAB_LINE" ]; then
    echo -e "        ${DIM}(Nécessite les droits sudo pour modifier /etc/fstab)${NC}"
fi
echo -e ""
echo -en "  Votre choix [1/2, défaut=1] : "
read -r _NAS_CHOICE

case "${_NAS_CHOICE}" in
    2) DISCONNECT_NAS=true ;;
    *) DISCONNECT_NAS=false ;;
esac

if [ "$DISCONNECT_NAS" = true ]; then
    echo -e ""

    # Démonter le NAS s'il est monté
    if [ "$NAS_IS_MOUNTED" = true ]; then
        if umount "$NAS" 2>/dev/null; then
            ok "NAS démonté avec succès"
        else
            warn "Échec du démontage standard — tentative avec sudo..."
            if sudo umount "$NAS" 2>/dev/null; then
                ok "NAS démonté avec succès via sudo"
            else
                err "Impossible de démonter $NAS. Des fichiers sont peut-être ouverts."
                warn "Fermez les applications utilisant le NAS puis relancez le script."
            fi
        fi
    else
        ok "NAS déjà démonté"
    fi

    # Supprimer l'entrée fstab
    if [ -n "$FSTAB_LINE" ]; then
        _escaped=$(echo "$FSTAB_LINE" | sed 's/[&/\\]/\\&/g')
        if sudo sed -i "\|^${_escaped}$|d" /etc/fstab 2>/dev/null; then
            sudo systemctl daemon-reload 2>/dev/null || true
            ok "Entrée fstab supprimée pour //${FSTAB_NAS_HOST}/${FSTAB_NAS_SHARE}"
        else
            warn "Impossible de modifier /etc/fstab automatiquement."
            echo -e "  ${YELLOW}Veuillez supprimer manuellement cette ligne de /etc/fstab :${NC}"
            echo -e "  ${DIM}$FSTAB_LINE${NC}"
        fi
    else
        ok "Aucune entrée fstab à supprimer"
    fi

    # Désactiver les unités systemd automount associées
    _mount_unit=$(systemd-escape --path "$NAS" 2>/dev/null || echo "")
    if [ -n "$_mount_unit" ]; then
        sudo systemctl stop "${_mount_unit}.automount" 2>/dev/null || true
        sudo systemctl stop "${_mount_unit}.mount" 2>/dev/null || true
        sudo systemctl disable "${_mount_unit}.automount" 2>/dev/null || true
    fi

    # Supprimer le point de montage (seulement s'il est vide)
    if [ -d "$NAS" ]; then
        if [ -z "$(ls -A "$NAS" 2>/dev/null)" ]; then
            rmdir "$NAS" 2>/dev/null && ok "Point de montage $NAS supprimé" \
                || ok "Point de montage $NAS conservé"
        else
            warn "$NAS n'est pas vide — point de montage conservé"
        fi
    fi

    ok "Déconnexion du NAS terminée avec succès !"
else
    ok "Connexion au NAS conservée (PC fixe)"
    if [ "$NAS_IS_MOUNTED" = true ]; then
        dim "Le NAS reste accessible à tout moment dans $NAS."
    else
        dim "Le NAS sera monté automatiquement au démarrage."
    fi
fi

# ── Résumé et Clôture ─────────────────────────────────────────────────────────

echo -e ""
echo -e "  ${GREEN}┌──────────────────────────────────────────────┐${NC}"
echo -e "  ${GREEN}│${NC}   ${BOLD}${GREEN}🎉   Désinstallation terminée avec succès !  ${NC}${GREEN}│${NC}"
echo -e "  ${GREEN}└──────────────────────────────────────────────┘${NC}"
echo -e ""

case "$RESTORE_MODE" in
    local)
        echo -e "  ${BOLD}Restauration des dossiers :${NC} ${GREEN}Dossiers locaux standards${NC}"
        dim "Vos fichiers ont été récupérés depuis le cache local."
        ;;
    nas)
        echo -e "  ${BOLD}Restauration des dossiers :${NC} ${YELLOW}Liens directs vers le NAS${NC}"
        dim "Le NAS doit être connecté pour accéder aux dossiers."
        ;;
    keep)
        echo -e "  ${BOLD}Restauration des dossiers :${NC} Aucun changement"
        dim "Vos dossiers existants ont été laissés inchangés."
        ;;
esac

echo -e ""
if [ "$DISCONNECT_NAS" = true ]; then
    echo -e "  ${BOLD}Connexion NAS :${NC} ${RED}Déconnecté${NC}"
    dim "Le montage automatique fstab a été désactivé."
else
    echo -e "  ${BOLD}Connexion NAS :${NC} ${GREEN}Conservée${NC}"
    dim "Le NAS reste monté sur $NAS (mode PC fixe)."
fi

echo -e ""
echo -e "  ${BOLD}Pour réinstaller l'application :${NC}"
echo -e "    ${CYAN}bash $SCRIPT_DIR/install.sh${NC}"
echo -e ""
