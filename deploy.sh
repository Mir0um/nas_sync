#!/bin/bash
# deploy.sh — Déploiement entreprise silencieux de NAS Sync
#
# Usage :
#   sudo bash deploy.sh \
#       --nas-host Cassis.local \
#       --nas-share home \
#       --nas-user prenom.nom \
#       --nas-password MotDePasse \
#       --deploy-user prenom.nom
#
# Compatible Ansible / SSH / MDM.
# N'affiche aucune fenêtre graphique.

set -euo pipefail

# ── Valeurs par défaut ────────────────────────────────────────────────────────

NAS_HOST="Cassis.local"
NAS_SHARE="home"
NAS_USER=""
NAS_PASS=""
DEPLOY_USER=""
INSTALL_DEPS=true
SETUP_MOUNT=true
APP_DIR="/opt/nas_sync"
SKIP_INITIAL_SYNC=false

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[!!]${NC}  $*"; }
err()  { echo -e "${RED}[KO]${NC}  $*" >&2; exit 1; }
log()  { echo "      $*"; }

# ── Lecture des arguments ─────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case $1 in
        --nas-host)        NAS_HOST="$2";      shift 2 ;;
        --nas-share)       NAS_SHARE="$2";     shift 2 ;;
        --nas-user)        NAS_USER="$2";      shift 2 ;;
        --nas-password)    NAS_PASS="$2";      shift 2 ;;
        --deploy-user)     DEPLOY_USER="$2";   shift 2 ;;
        --app-dir)         APP_DIR="$2";       shift 2 ;;
        --no-deps)         INSTALL_DEPS=false; shift ;;
        --no-mount)        SETUP_MOUNT=false;  shift ;;
        --skip-sync)       SKIP_INITIAL_SYNC=true; shift ;;
        -h|--help)
            echo "Usage: sudo bash deploy.sh [OPTIONS]"
            echo ""
            echo "Options obligatoires :"
            echo "  --nas-user USER          Identifiant SMB"
            echo "  --nas-password PASS      Mot de passe SMB"
            echo "  --deploy-user USER       Utilisateur Linux cible"
            echo ""
            echo "Options facultatives :"
            echo "  --nas-host HOST          Hôte NAS (défaut: Cassis.local)"
            echo "  --nas-share SHARE        Partage SMB (défaut: home)"
            echo "  --app-dir DIR            Répertoire d'installation (défaut: /opt/nas_sync)"
            echo "  --no-deps                Ne pas installer les dépendances système"
            echo "  --no-mount               Ne pas configurer le montage SMB"
            echo "  --skip-sync              Ne pas faire la synchro initiale NAS→local"
            exit 0 ;;
        *) err "Option inconnue : $1 (--help pour l'aide)" ;;
    esac
done

# ── Vérifications préliminaires ───────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo "   NAS Sync — Déploiement entreprise"
echo "══════════════════════════════════════════════════════"
echo ""

[[ $EUID -eq 0 ]] || err "Ce script doit être exécuté en root (sudo bash deploy.sh …)"

if [[ -z "$DEPLOY_USER" ]]; then
    # Essayer de déduire depuis SUDO_USER
    if [[ -n "${SUDO_USER:-}" ]] && [[ "$SUDO_USER" != "root" ]]; then
        DEPLOY_USER="$SUDO_USER"
        warn "--deploy-user non spécifié — utilisation de SUDO_USER=$DEPLOY_USER"
    else
        err "--deploy-user est obligatoire"
    fi
fi

id "$DEPLOY_USER" &>/dev/null || err "L'utilisateur '$DEPLOY_USER' n'existe pas"

USER_HOME=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)
[[ -n "$USER_HOME" ]] || err "Impossible de trouver le répertoire home de $DEPLOY_USER"

NAS_MOUNT="$USER_HOME/NasShare"
LOCAL_BASE="$USER_HOME/offline_cache"
CONFIG_FILE="$USER_HOME/.nas_sync_config.json"
CREDS_FILE="$USER_HOME/.smbcredentials"

echo "  Utilisateur cible : $DEPLOY_USER ($USER_HOME)"
echo "  NAS               : //$NAS_HOST/$NAS_SHARE"
echo "  Montage           : $NAS_MOUNT"
echo "  Cache local       : $LOCAL_BASE"
echo "  App               : $APP_DIR"
echo ""

# ── 1. Dépendances système ────────────────────────────────────────────────────

if [[ "$INSTALL_DEPS" == true ]]; then
    echo "── Dépendances système ──────────────────────────────────"

    # Détection du gestionnaire de paquets
    if command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
    elif command -v apt-get &>/dev/null; then
        PKG_MGR="apt"
    else
        warn "Gestionnaire de paquets non reconnu — vérifiez manuellement les dépendances"
        PKG_MGR=""
    fi

    if [[ "$PKG_MGR" == "dnf" ]]; then
        PKGS=()
        python3 -c "import gi; gi.require_version('Gtk','3.0')" 2>/dev/null || PKGS+=(python3-gobject)
        python3 -c "import gi; gi.require_version('AppIndicator3','0.1')" 2>/dev/null || PKGS+=(libappindicator-gtk3)
        command -v rsync       &>/dev/null || PKGS+=(rsync)
        command -v notify-send &>/dev/null || PKGS+=(libnotify)
        rpm -q cifs-utils      &>/dev/null || PKGS+=(cifs-utils)
        # Extension GNOME pour l'icône barre système
        rpm -q gnome-shell-extension-appindicator &>/dev/null || PKGS+=(gnome-shell-extension-appindicator)

        if [[ ${#PKGS[@]} -gt 0 ]]; then
            log "Installation : ${PKGS[*]}"
            dnf install -y "${PKGS[@]}" &>/dev/null && ok "Dépendances installées" \
                || warn "Certains paquets n'ont pas pu être installés"
        else
            ok "Toutes les dépendances sont déjà présentes"
        fi

        # Activer l'extension pour l'utilisateur cible si session active
        APPIND_EXT="appindicatorsupport@rgcjonas.gmail.com"
        USER_BUS="unix:path=/run/user/$(id -u "$DEPLOY_USER")/bus"
        if sudo -u "$DEPLOY_USER" \
               DBUS_SESSION_BUS_ADDRESS="$USER_BUS" \
               gnome-extensions enable "$APPIND_EXT" 2>/dev/null; then
            ok "Extension GNOME AppIndicator activée pour $DEPLOY_USER"
        else
            warn "Extension AppIndicator à activer manuellement au 1er login :"
            warn "  gnome-extensions enable $APPIND_EXT"
        fi

    elif [[ "$PKG_MGR" == "apt" ]]; then
        PKGS=()
        python3 -c "import gi; gi.require_version('Gtk','3.0')" 2>/dev/null || PKGS+=(python3-gi gir1.2-gtk-3.0)
        python3 -c "import gi; gi.require_version('AppIndicator3','0.1')" 2>/dev/null || PKGS+=(gir1.2-appindicator3-0.1)
        command -v rsync       &>/dev/null || PKGS+=(rsync)
        command -v notify-send &>/dev/null || PKGS+=(libnotify-bin)
        dpkg -l cifs-utils &>/dev/null 2>&1 || PKGS+=(cifs-utils)
        dpkg -l gnome-shell-extension-appindicator &>/dev/null 2>&1 || PKGS+=(gnome-shell-extension-appindicator)

        if [[ ${#PKGS[@]} -gt 0 ]]; then
            log "Installation : ${PKGS[*]}"
            DEBIAN_FRONTEND=noninteractive apt-get install -y "${PKGS[@]}" &>/dev/null \
                && ok "Dépendances installées" \
                || warn "Certains paquets n'ont pas pu être installés"
        else
            ok "Toutes les dépendances sont déjà présentes"
        fi
    fi
    echo ""
fi

# ── 2. Déployer les fichiers de l'application ─────────────────────────────────

echo "── Déploiement des fichiers ─────────────────────────────"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$APP_DIR"
cp -r "$SCRIPT_DIR/"*.py "$APP_DIR/"
[[ -d "$SCRIPT_DIR/tools" ]] && cp -r "$SCRIPT_DIR/tools" "$APP_DIR/"
chmod 644 "$APP_DIR/"*.py
chmod 755 "$APP_DIR"
ok "Fichiers copiés dans $APP_DIR"
echo ""

# ── 3. Credentials SMB ───────────────────────────────────────────────────────

if [[ -n "$NAS_USER" ]] && [[ -n "$NAS_PASS" ]]; then
    echo "── Credentials SMB ─────────────────────────────────────"
    cat > "$CREDS_FILE" << CREDS
username=$NAS_USER
password=$NAS_PASS
domain=WORKGROUP
CREDS
    chmod 600 "$CREDS_FILE"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$CREDS_FILE"
    ok "Fichier $CREDS_FILE créé (chmod 600)"
    echo ""
fi

# ── 4. Montage SMB ────────────────────────────────────────────────────────────

if [[ "$SETUP_MOUNT" == true ]]; then
    echo "── Montage SMB ─────────────────────────────────────────"

    # Créer le point de montage
    mkdir -p "$NAS_MOUNT"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$NAS_MOUNT"

    # Vérifier si déjà dans fstab
    FSTAB_ENTRY="//$NAS_HOST/$NAS_SHARE $NAS_MOUNT cifs credentials=$CREDS_FILE,uid=$(id -u "$DEPLOY_USER"),gid=$(id -g "$DEPLOY_USER"),nofail,_netdev,vers=3.0,iocharset=utf8 0 0"

    if grep -qF "//$NAS_HOST/$NAS_SHARE" /etc/fstab 2>/dev/null; then
        warn "Entrée fstab pour //$NAS_HOST/$NAS_SHARE déjà présente — ignorée"
    else
        echo "$FSTAB_ENTRY" >> /etc/fstab
        systemctl daemon-reload 2>/dev/null || true
        ok "Entrée fstab ajoutée"
    fi

    # Essayer de monter
    if mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
        ok "NAS déjà monté sur $NAS_MOUNT"
    else
        mount "$NAS_MOUNT" 2>/dev/null && ok "NAS monté sur $NAS_MOUNT" \
            || warn "Montage impossible maintenant — se fera au prochain démarrage réseau"
    fi
    echo ""
fi

# ── 5. Configuration JSON ────────────────────────────────────────────────────

echo "── Configuration ───────────────────────────────────────"

# Écrire la config uniquement si elle n'existe pas déjà
if [[ ! -f "$CONFIG_FILE" ]]; then
    python3 - << PYEOF
import json, sys
from pathlib import Path

home = Path("$USER_HOME")
cfg = {
    "nas_host":        "$NAS_HOST",
    "nas_port":        445,
    "nas_mount":       "$NAS_MOUNT",
    "local_base":      "$LOCAL_BASE",
    "check_interval":  30,
    "sync_interval":   300,
    "mtime_eps":       2.0,
    "notifications":   True,
    "notif_min_files": 1,
    "conflict_mode":   "ask",
    "exclude_patterns": [
        "*.tmp","*.lock","~\$*",".DS_Store","Thumbs.db",
        "desktop.ini","*.part","*.crdownload","*.nastmp",
        ".Trash*","*.swp","*.swo","*.pyc"
    ],
    "backup_before_overwrite": True,
    "backup_max_days": 30,
    "deletion_sync": False,
    "pause_on_battery": False,
    "pause_on_metered": False,
    "dirs": [
        {"local_sub":"Desktop",   "nas_sub":"Desktop",   "enabled":True,  "max_age_days":0},
        {"local_sub":"Downloads", "nas_sub":"Downloads", "enabled":True,  "max_age_days":90},
        {"local_sub":"Documents", "nas_sub":"Documents", "enabled":True,  "max_age_days":0},
        {"local_sub":"Music",     "nas_sub":"Music",     "enabled":True,  "max_age_days":180},
        {"local_sub":"Pictures",  "nas_sub":"Pictures",  "enabled":True,  "max_age_days":0},
        {"local_sub":"video",     "nas_sub":"video",     "enabled":True,  "max_age_days":90},
    ],
}
Path("$CONFIG_FILE").write_text(json.dumps(cfg, indent=2))
PYEOF
    chown "$DEPLOY_USER:$DEPLOY_USER" "$CONFIG_FILE"
    ok "Configuration créée : $CONFIG_FILE"
else
    ok "Configuration existante conservée : $CONFIG_FILE"
fi
echo ""

# ── 6. Cache local et liens symboliques ───────────────────────────────────────

echo "── Cache local et liens symboliques ────────────────────"

DIRS=(Desktop Downloads Documents Music Pictures video)
for d in "${DIRS[@]}"; do
    mkdir -p "$LOCAL_BASE/$d"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$LOCAL_BASE/$d"
done
chown "$DEPLOY_USER:$DEPLOY_USER" "$LOCAL_BASE"
ok "Répertoires créés dans $LOCAL_BASE"

declare -A SYMLINKS=(
    ["$USER_HOME/Bureau"]="$LOCAL_BASE/Desktop"
    ["$USER_HOME/Téléchargements"]="$LOCAL_BASE/Downloads"
    ["$USER_HOME/Documents"]="$LOCAL_BASE/Documents"
    ["$USER_HOME/Musique"]="$LOCAL_BASE/Music"
    ["$USER_HOME/Images"]="$LOCAL_BASE/Pictures"
    ["$USER_HOME/Vidéos"]="$LOCAL_BASE/video"
)
for link in "${!SYMLINKS[@]}"; do
    target="${SYMLINKS[$link]}"
    if [[ -L "$link" ]]; then
        rm "$link"
    elif [[ -d "$link" ]]; then
        rmdir "$link" 2>/dev/null || { warn "  $link non vide — ignoré"; continue; }
    fi
    ln -s "$target" "$link"
    chown -h "$DEPLOY_USER:$DEPLOY_USER" "$link"
done
ok "Liens symboliques créés"
echo ""

# ── 7. XDG user-dirs ─────────────────────────────────────────────────────────

echo "── XDG user-dirs ───────────────────────────────────────"

XDG_DIR="$USER_HOME/.config"
mkdir -p "$XDG_DIR"
cat > "$XDG_DIR/user-dirs.dirs" << XDG
XDG_DESKTOP_DIR="\$HOME/offline_cache/Desktop"
XDG_DOWNLOAD_DIR="\$HOME/offline_cache/Downloads"
XDG_DOCUMENTS_DIR="\$HOME/offline_cache/Documents"
XDG_MUSIC_DIR="\$HOME/offline_cache/Music"
XDG_PICTURES_DIR="\$HOME/offline_cache/Pictures"
XDG_VIDEOS_DIR="\$HOME/offline_cache/video"
XDG
chown "$DEPLOY_USER:$DEPLOY_USER" "$XDG_DIR/user-dirs.dirs"
# Mettre à jour en tant qu'utilisateur cible si session graphique disponible
sudo -u "$DEPLOY_USER" xdg-user-dirs-update 2>/dev/null || true
ok "XDG user-dirs configurés"
echo ""

# ── 8. Service systemd utilisateur ───────────────────────────────────────────

echo "── Service systemd ─────────────────────────────────────"

SVC_DIR="$USER_HOME/.config/systemd/user"
mkdir -p "$SVC_DIR"
cat > "$SVC_DIR/nas-sync.service" << SVC
[Unit]
Description=NAS Sync Daemon — Synchronisation bidirectionnelle offline_cache ↔ $NAS_HOST
After=network.target graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $APP_DIR/nas_sync_daemon.py
Restart=on-failure
RestartSec=15
StandardOutput=null
StandardError=journal

[Install]
WantedBy=graphical-session.target
SVC
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$SVC_DIR"

# Activer le service en tant qu'utilisateur cible
sudo -u "$DEPLOY_USER" systemctl --user daemon-reload 2>/dev/null \
    || warn "daemon-reload échoué (normal hors session graphique)"
sudo -u "$DEPLOY_USER" systemctl --user enable nas-sync.service 2>/dev/null \
    && ok "Service nas-sync activé (démarrera à la prochaine session)" \
    || warn "Activation manuelle à faire : systemctl --user enable nas-sync"
echo ""

# ── 9. Entrée menu GNOME ─────────────────────────────────────────────────────

echo "── Menu GNOME ──────────────────────────────────────────"

APP_ENTRY_DIR="$USER_HOME/.local/share/applications"
mkdir -p "$APP_ENTRY_DIR"
mkdir -p "$USER_HOME/.config/autostart"
cat > "$APP_ENTRY_DIR/nas-sync.desktop" << DESKTOP
[Desktop Entry]
Name=NAS Sync
GenericName=Synchronisation NAS
Comment=Synchronisation automatique avec le NAS d'entreprise
Exec=/usr/bin/python3 $APP_DIR/nas_sync_app.py
Icon=network-server
Type=Application
Categories=Utility;Network;FileManager;
Keywords=nas;sync;synchronisation;réseau;partage;
StartupNotify=false
Terminal=false
DESKTOP

cat > "$USER_HOME/.config/autostart/nas-sync-app.desktop" << AUTOSTART
[Desktop Entry]
Name=NAS Sync
Comment=Interface de synchronisation NAS — barre système
Exec=/usr/bin/python3 $APP_DIR/nas_sync_app.py
Icon=network-server
Type=Application
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
AUTOSTART

chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_ENTRY_DIR"
chown "$DEPLOY_USER:$DEPLOY_USER" "$USER_HOME/.config/autostart/nas-sync-app.desktop"
update-desktop-database "$APP_ENTRY_DIR" 2>/dev/null || true
ok "Entrée GNOME Activities créée"
ok "Autostart GNOME configuré"
echo ""

# ── 10. Synchro initiale NAS → local ─────────────────────────────────────────

if [[ "$SKIP_INITIAL_SYNC" == false ]] && command -v rsync &>/dev/null \
   && mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
    echo "── Synchronisation initiale ────────────────────────────"
    for sub in Desktop Downloads Documents Music Pictures video; do
        if [[ -d "$NAS_MOUNT/$sub" ]]; then
            nb=$(find "$NAS_MOUNT/$sub" -type f 2>/dev/null | wc -l)
            log "$sub : $nb fichiers…"
            sudo -u "$DEPLOY_USER" rsync -a --ignore-existing \
                "$NAS_MOUNT/$sub/" "$LOCAL_BASE/$sub/" 2>/dev/null || true
        fi
    done
    ok "Synchronisation initiale terminée"
    echo ""
fi

# ── Résumé ────────────────────────────────────────────────────────────────────

echo "══════════════════════════════════════════════════════"
echo "   Déploiement terminé pour : $DEPLOY_USER"
echo "══════════════════════════════════════════════════════"
echo ""
echo "  Application     : $APP_DIR"
echo "  Cache local     : $LOCAL_BASE"
echo "  Configuration   : $CONFIG_FILE"
echo "  Service systemd : $SVC_DIR/nas-sync.service"
echo ""
echo "  Actions à la prochaine session utilisateur :"
echo "    • Le service nas-sync démarrera automatiquement"
echo "    • L'icône NAS Sync apparaîtra dans la barre système"
echo "    • Les dossiers ~/Bureau, ~/Documents… pointent vers le cache local"
echo ""
echo "  Commandes utiles (en tant que $DEPLOY_USER) :"
echo "    systemctl --user status nas-sync"
echo "    tail -f $USER_HOME/.nas_sync.log"
echo ""
