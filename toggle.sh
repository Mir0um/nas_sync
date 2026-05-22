#!/bin/bash
# toggle.sh — Activer / Désactiver le démon NAS Sync

SERVICE="nas-sync.service"

if systemctl --user is-active --quiet "$SERVICE"; then
    systemctl --user stop "$SERVICE"
    notify-send --icon=network-offline "NAS Sync" "Synchronisation désactivée" 2>/dev/null || true
    echo "Synchronisation NAS désactivée."
else
    systemctl --user start "$SERVICE"
    notify-send --icon=network-server "NAS Sync" "Synchronisation activée" 2>/dev/null || true
    echo "Synchronisation NAS activée."
fi
