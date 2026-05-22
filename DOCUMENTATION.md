# NAS Sync — Documentation complète

**Machine :** jp@litchi (Nobara Linux, GNOME)
**NAS :** Cassis.local — volume `home` monté sur `~/NasShare`
**Date :** 22 mai 2026

---

## Sommaire

1. [Problème de départ](#1-problème-de-départ)
2. [Architecture](#2-architecture)
3. [Installation interactive](#3-installation-interactive)
4. [Déploiement entreprise](#4-déploiement-entreprise)
5. [Manuel d'utilisation](#5-manuel-dutilisation)
6. [Résolution de conflits](#6-résolution-de-conflits)
7. [Référence des fichiers](#7-référence-des-fichiers)
8. [Dépannage](#8-dépannage)

---

## 1. Problème de départ

Depuis la migration Windows → Nobara Linux (*Rapport_Migration_NAS_Nobara.pdf*),
les dossiers utilisateurs étaient redirigés **directement** vers le NAS :

```
~/Bureau  →  ~/NasShare/Desktop       (lien symbolique)
~/Téléchargements  →  ~/NasShare/Downloads
…
```

**Problème en déplacement :** le NAS est inaccessible → les dossiers sont vides ou
cassés → impossible de travailler.

---

## 2. Architecture

### Principe général

Un **cache local permanent** `~/offline_cache/` sert de point de travail unique.
Un démon surveille en permanence la disponibilité du NAS et synchronise les fichiers
modifiés dans les deux sens.

```
╔═════════════════════════════════════════════╗
║          Machine jp@litchi                  ║
║                                             ║
║  ~/Bureau, ~/Téléchargements…               ║
║        │ (liens symboliques)                ║
║        ▼                                    ║
║   ~/offline_cache/     ◄────────────────────╫──── NAS Cassis.local
║     Desktop/           synchro auto         ║     ~/NasShare/
║     Downloads/    ◄──────────────────────►  ║       Desktop/
║     Documents/         nas_sync_daemon.py   ║       Downloads/
║     Music/                                  ║       Documents/
║     Pictures/    nas_sync_app.py            ║       …
║     video/       (icône barre système)      ║
╚═════════════════════════════════════════════╝
```

### Comportement selon la connectivité

| Situation | Comportement |
|-----------|-------------|
| **Au bureau** (NAS connecté) | Synchro automatique périodique (toutes les 5 min) |
| **NAS reconnecté** | Synchro immédiate déclenchée |
| **NAS déconnecté** | Mode hors ligne — les fichiers locaux restent accessibles |
| **De retour** (NAS réapparu) | Synchro des modifications faites en déplacement |

### Règles de synchronisation intelligente

| Cas | Action |
|-----|--------|
| Fichier modifié **en local seulement** | Copié vers le NAS |
| Fichier modifié **sur le NAS seulement** | Copié en local |
| Fichier **nouveau** d'un côté | Copié vers l'autre |
| Fichier **trop ancien** (filtre par âge) | Ignoré (configurable par dossier) |
| Fichier modifié **des deux côtés** | Fenêtre de résolution affichée |

### Filtre par âge (dossiers volumineux)

| Dossier | Filtre par défaut |
|---------|------------------|
| Desktop, Documents, Pictures | Aucun (tous les fichiers) |
| Downloads, video | 90 derniers jours |
| Music | 180 derniers jours |

Ces valeurs sont modifiables dans les **Paramètres → Dossiers**.

### Composants

| Fichier | Rôle | Lancé par |
|---------|------|-----------|
| `nas_sync_daemon.py` | Synchro silencieuse en arrière-plan | systemd user service |
| `nas_sync_app.py` | Interface barre système + détection premier démarrage | GNOME autostart (login) |
| `nas_sync_config.py` | Config et événements partagés | (module Python) |
| `conflict_dialog.py` | Fenêtre de résolution de conflit | Appelé par le démon |
| `first_run_wizard.py` | Assistant de configuration guidé | Lancé par nas_sync_app.py |

---

## 3. Installation interactive

### Principe

Destinée à un usage personnel. Le script `install.sh` est à lancer **une seule fois**,
depuis chez soi, NAS accessible.

### Lancer l'installation

```bash
bash ~/programs/nas_sync/install.sh
```

Les dépendances manquantes sont installées **automatiquement** via `sudo dnf install` :
- `python3-gobject` — interface GTK3
- `libappindicator-gtk3` — icône barre système
- `libnotify` — notifications bureau
- `rsync` — copie initiale

Le script effectue ensuite dans l'ordre :

1. Vérification et installation des dépendances
2. Création de `~/offline_cache/{Desktop,Downloads,Documents,Music,Pictures,video}/`
3. Copie initiale `~/NasShare/ → ~/offline_cache/` (sans écraser)
4. Mise à jour des liens symboliques :
   ```
   ~/Bureau          →  ~/offline_cache/Desktop
   ~/Téléchargements →  ~/offline_cache/Downloads
   ~/Documents       →  ~/offline_cache/Documents
   ~/Musique         →  ~/offline_cache/Music
   ~/Images          →  ~/offline_cache/Pictures
   ~/Vidéos          →  ~/offline_cache/video
   ```
5. Mise à jour de `~/.config/user-dirs.dirs`
6. Installation du service systemd `nas-sync.service`
7. Création de l'entrée GNOME autostart pour l'interface
8. Création de l'entrée **GNOME Activities** (`~/.local/share/applications/nas-sync.desktop`)
9. Démarrage du service et de l'interface

### Assistant de premier démarrage

Si aucun fichier `~/.nas_sync_config.json` n'existe au lancement de `nas_sync_app.py`,
l'**assistant de configuration** (`first_run_wizard.py`) s'ouvre automatiquement.

Il guide l'utilisateur en 4 étapes :

| Page | Contenu |
|------|---------|
| **Bienvenue** | Présentation et prérequis |
| **1 · Connexion** | Hôte NAS, partage SMB, point de montage, identifiants ; test de connexion en direct ; commandes fstab si le NAS n'est pas encore monté |
| **2 · Dossiers** | Sélection des dossiers à synchroniser, nom local, nom NAS, filtre par âge |
| **3 · Options** | Intervalles de synchro, sauvegarde, pause batterie, notifications, mode conflit |
| **4 · Installation** | Barre de progression, journal des opérations en temps réel |

L'assistant peut aussi être lancé manuellement :
```bash
python3 ~/programs/nas_sync/first_run_wizard.py
```

### Action manuelle unique (Nautilus)

Dans l'explorateur de fichiers :
- Clic droit sur les anciens favoris cassés → **"Retirer des favoris"**
- Glisser les dossiers de `~/offline_cache/` dans la barre latérale

---

## 4. Déploiement entreprise

### Principe

Le script `deploy.sh` déploie NAS Sync sur une machine pour un utilisateur donné,
**sans aucune interaction graphique**. Compatible SSH, Ansible, MDM.

À lancer en `root` (ou `sudo`).

### Commande type

```bash
sudo bash /chemin/vers/nas_sync/deploy.sh \
    --nas-host    Cassis.local \
    --nas-share   home \
    --nas-user    prenom.nom \
    --nas-password MotDePasse \
    --deploy-user prenom.nom
```

### Options disponibles

| Option | Obligatoire | Description |
|--------|-------------|-------------|
| `--nas-user USER` | Oui | Identifiant SMB |
| `--nas-password PASS` | Oui | Mot de passe SMB |
| `--deploy-user USER` | Oui* | Utilisateur Linux cible |
| `--nas-host HOST` | Non | Hôte NAS (défaut : `Cassis.local`) |
| `--nas-share SHARE` | Non | Partage SMB (défaut : `home`) |
| `--app-dir DIR` | Non | Répertoire d'installation (défaut : `/opt/nas_sync`) |
| `--no-deps` | Non | Ne pas installer les dépendances système |
| `--no-mount` | Non | Ne pas configurer le montage SMB dans `/etc/fstab` |
| `--skip-sync` | Non | Ne pas faire la synchro initiale NAS → local |

\* Si omis, utilise `$SUDO_USER` automatiquement.

### Ce que fait le script

1. **Dépendances système** — installe via `dnf` (Fedora/Nobara) ou `apt` (Ubuntu/Debian) les paquets manquants : PyGObject, AppIndicator3, rsync, libnotify, cifs-utils
2. **Déploiement** — copie les fichiers `.py` dans `/opt/nas_sync/`
3. **Credentials SMB** — écrit `~utilisateur/.smbcredentials` (chmod 600)
4. **Montage SMB** — crée le point de montage, ajoute une ligne dans `/etc/fstab`, monte immédiatement si possible
5. **Configuration** — génère `~/.nas_sync_config.json` avec les valeurs par défaut si inexistant
6. **Cache local** — crée `~/offline_cache/{Desktop,Downloads,Documents,Music,Pictures,video}/`
7. **Liens symboliques** — redirige `~/Bureau`, `~/Téléchargements`, etc. vers `~/offline_cache/`
8. **XDG user-dirs** — met à jour `~/.config/user-dirs.dirs`
9. **Service systemd** — installe et active `~/.config/systemd/user/nas-sync.service`
10. **GNOME** — crée l'entrée Applications et l'entrée autostart
11. **Synchro initiale** — rsync NAS → local si le NAS est accessible

### Exemple Ansible

```yaml
- name: Déployer NAS Sync
  become: true
  command: >
    bash /opt/nas_sync_src/deploy.sh
      --nas-host Cassis.local
      --nas-user "{{ ansible_user }}"
      --nas-password "{{ vault_nas_password }}"
      --deploy-user "{{ ansible_user }}"
```

### Premier démarrage après déploiement

Au login suivant de l'utilisateur :
- `nas_sync_app.py` démarre via GNOME autostart
- La configuration existe déjà → **pas d'assistant** → l'icône barre système apparaît directement
- Le démon démarre automatiquement via systemd

---

## 5. Manuel d'utilisation

### 5.1 Icône dans la barre système

| État | Icône | Signification |
|------|-------|---------------|
| **Active** | `network-server` (visible) | Démon en cours, synchro active |
| **Inactive** | *(invisible)* | Démon arrêté, application en arrière-plan silencieux |

> L'application continue de tourner en arrière-plan même quand l'icône est invisible,
> prête à redémarrer le démon sur demande.

### 5.2 Menu de l'icône (clic droit)

| Entrée | Action |
|--------|--------|
| ● / ○ Statut | Indique l'état actuel |
| Fichier en cours / progression | Affiché pendant une synchro |
| **Fichiers récents…** | Ouvre la fenêtre d'historique |
| **Paramètres…** | Ouvre la fenêtre de configuration |
| **Synchroniser maintenant** | Force une synchro immédiate (signal SIGUSR1) |
| **Désactiver / Activer** | Arrête ou démarre le démon |
| **Quitter l'interface** | Ferme uniquement l'interface (le démon continue) |

### 5.3 Fenêtre — Fichiers récents

Affiche l'historique des 300 derniers événements avec :
- Heure de l'opération
- Direction (→ NAS, ← Local, ⚠ conflit, ✎ renommé, 🗑 supprimé)
- Nom du fichier (chemin relatif)
- Détail (version retenue, nouveau nom…)

Se rafraîchit automatiquement toutes les 3 secondes.  
Bouton : **Effacer l'historique**

### 5.4 Fenêtre — Paramètres (5 onglets)

#### Onglet Connexion
- Hôte NAS (`Cassis.local` ou IP fixe)
- Port SMB (défaut : 445)
- Point de montage NAS
- Dossier du cache local

#### Onglet Dossiers
- Liste de tous les dossiers synchronisés
- Pour chaque dossier : activer/désactiver, nom local, nom NAS
- **Âge max (jours)** : 0 = tous les fichiers, N = ignorer les fichiers plus anciens que N jours
- Boutons pour ajouter ou supprimer un dossier

#### Onglet Synchronisation
- Intervalle de vérification NAS (défaut : 30 s)
- Intervalle de synchro périodique (défaut : 5 min)
- Tolérance mtime SMB (défaut : 2 s — précision du protocole SMB)
- Mode conflit : **Demander** | **Toujours garder local** | **Toujours garder NAS**

#### Onglet Notifications
- Activer / désactiver les notifications bureau GNOME
- Seuil : notifier seulement si ≥ N fichiers synchro par cycle

#### Onglet Filtres & Avancé
- **Fichiers exclus** : patterns fnmatch, un par ligne (ex : `*.tmp`, `~$*`, `.DS_Store`)
- **Sauvegarde avant écrasement** : copie dans `~/.nas_sync_backups/YYYY-MM-DD/` + durée de rétention
- **Synchronisation des suppressions** : propager les suppressions d'un côté à l'autre
- **Pause intelligente** : mettre en pause si sur batterie ou connexion limitée (metered network)

### 5.5 Notifications GNOME

| Notification | Déclencheur |
|---|---|
| *"NAS connecté — synchronisation en cours…"* | NAS détecté après une absence |
| *"NAS hors ligne — mode local actif"* | NAS devenu inaccessible |
| *"NAS Sync — Conflits détectés"* | Fichiers modifiés des deux côtés |

### 5.6 Activer / Désactiver rapidement

```bash
bash ~/programs/nas_sync/toggle.sh
```

### 5.7 Commandes systemd

```bash
systemctl --user status nas-sync     # État du démon
systemctl --user start  nas-sync     # Démarrer
systemctl --user stop   nas-sync     # Arrêter
systemctl --user restart nas-sync    # Redémarrer
```

### 5.8 Logs en direct

```bash
tail -f ~/.nas_sync.log
```

Exemple de log :
```
2026-05-22 09:00:01 INFO === nas-sync démarré (PID 12345) ===
2026-05-22 09:00:03 INFO NAS connecté → synchronisation
2026-05-22 09:00:05 INFO   copié rapport.pdf → NasShare/Downloads
2026-05-22 09:00:06 INFO Synchro: →NAS=1 ←NAS=0 conflits=0
2026-05-22 10:00:31 INFO NAS hors ligne — mode hors connexion
2026-05-22 18:45:12 INFO NAS connecté → synchronisation
2026-05-22 18:45:15 INFO   renommé (nas) Documents/note.txt → note_nas_20260522_1023.txt
```

---

## 6. Résolution de conflits

Un conflit se produit quand un fichier a été modifié **localement ET sur le NAS**
depuis la dernière synchronisation.

### Fenêtre de conflit

```
┌────────────────────────────────────────────────────────┐
│  ⚠  Conflit de synchronisation                         │
│                                                        │
│  Fichier : Documents/rapport-projet.odt                │
│  Ce fichier a été modifié localement ET sur le NAS…    │
│                                                        │
│  ──────────────────────────────────────────────────    │
│              Version LOCALE       Version NAS          │
│  Modifié le  22/05/2026 14:32     22/05/2026 09:10     │
│  Taille      42.3 Ko              38.1 Ko              │
│  ──────────────────────────────────────────────────    │
│  ▶ Aperçu du contenu  (fichiers texte uniquement)      │
│                                                        │
│  [Ignorer]  [Renommer…]  [Garder NAS]  [Garder LOCAL]  │
└────────────────────────────────────────────────────────┘
```

### Options disponibles

| Bouton | Effet |
|--------|-------|
| **Garder LOCAL** | Le fichier local écrase la version NAS |
| **Garder NAS** | La version NAS écrase le fichier local |
| **Renommer…** | Conserve les **deux** versions (voir ci-dessous) |
| **Ignorer** | Reporté à la prochaine synchronisation |

Pour les fichiers texte (`.txt`, `.md`, `.py`, `.json`…), un aperçu côte-à-côte
des premières lignes est disponible via le panneau dépliable **"Aperçu du contenu"**.

### Dialogue Renommer

```
┌────────────────────────────────────────────────────┐
│  Renommer et conserver les deux versions           │
│                                                    │
│  Quelle version renommer ?                         │
│  ○ La version locale   (22/05 14:32, 42.3 Ko)      │
│    → La version NAS deviendra la référence         │
│  ● La version NAS      (22/05 09:10, 38.1 Ko)      │
│    → La version locale deviendra la référence      │
│                                                    │
│  Nouveau nom du fichier renommé :                  │
│  [rapport-projet_nas_20260522_0910.odt        ]    │
│                                                    │
│  Les deux fichiers seront présents sur le NAS      │
│  et en local.                                      │
│                                                    │
│  [Annuler]                          [Confirmer]    │
└────────────────────────────────────────────────────┘
```

**Résultat :** les deux fichiers sont synchronisés sur le NAS et en local.
Aucune version n'est perdue.

### Mode automatique (sans dialogue)

Dans **Paramètres → Synchronisation → En cas de conflit** :
- **Toujours garder local** : la version locale gagne toujours
- **Toujours garder NAS** : la version NAS gagne toujours

---

## 7. Référence des fichiers

### Scripts du programme

| Fichier | Rôle |
|---------|------|
| `nas_sync_daemon.py` | Démon de synchro — détection, copie, conflits |
| `nas_sync_app.py` | Interface barre système — menu, fenêtres, détection premier démarrage |
| `nas_sync_config.py` | Configuration et événements (module partagé) |
| `conflict_dialog.py` | Fenêtre GTK3 de résolution de conflit |
| `first_run_wizard.py` | Assistant GTK de configuration au premier démarrage |
| `install.sh` | Installation interactive avec auto-install des dépendances |
| `deploy.sh` | Déploiement entreprise silencieux (root / SSH / Ansible) |
| `uninstall.sh` | Désinstallation complète |
| `toggle.sh` | Activation / désactivation rapide |
| `tools/make_pdf.py` | Génération du PDF de documentation |

### Fichiers générés

| Fichier | Rôle |
|---------|------|
| `~/.nas_sync_config.json` | Configuration (modifiée via l'interface ou le wizard) |
| `~/.nas_sync_state.json` | État de synchro de chaque fichier |
| `~/.nas_sync_events.jsonl` | Historique des événements (fichiers récents) |
| `~/.nas_sync.log` | Journal détaillé du démon |
| `~/.nas_sync.pid` | PID du démon (pour signaux SIGHUP/SIGUSR1) |
| `~/.nas_sync.lock` | Verrou instance unique (fcntl) |
| `~/.nas_sync_progress.json` | Progression de la synchro en cours |
| `~/.nas_sync_backups/` | Sauvegardes avant écrasement (par date) |
| `~/.smbcredentials` | Identifiants SMB (chmod 600) |
| `~/.config/systemd/user/nas-sync.service` | Service systemd utilisateur |
| `~/.config/autostart/nas-sync-app.desktop` | Autostart GNOME pour l'interface |
| `~/.local/share/applications/nas-sync.desktop` | Entrée GNOME Activities |

### Signaux du démon

| Signal | Effet |
|--------|-------|
| `SIGHUP` | Recharger la configuration sans redémarrer |
| `SIGUSR1` | Déclencher une synchronisation immédiate |
| `SIGTERM` | Arrêt propre du démon |

```bash
kill -HUP  $(cat ~/.nas_sync.pid)   # recharger config
kill -USR1 $(cat ~/.nas_sync.pid)   # synchro immédiate
```

### Paramètres du fichier de configuration

| Clé | Défaut | Description |
|-----|--------|-------------|
| `nas_host` | `"Cassis.local"` | Nom ou IP du NAS |
| `nas_port` | `445` | Port SMB |
| `nas_mount` | `~/NasShare` | Point de montage NAS |
| `local_base` | `~/offline_cache` | Cache local |
| `check_interval` | `30` | Secondes entre vérifications NAS |
| `sync_interval` | `300` | Secondes entre synchros périodiques |
| `mtime_eps` | `2.0` | Tolérance mtime en secondes |
| `notifications` | `true` | Notifications bureau actives |
| `notif_min_files` | `1` | Seuil de notification |
| `conflict_mode` | `"ask"` | `"ask"` / `"keep_local"` / `"keep_nas"` |
| `exclude_patterns` | `["*.tmp", …]` | Patterns de fichiers exclus (fnmatch) |
| `backup_before_overwrite` | `true` | Sauvegarder avant d'écraser |
| `backup_max_days` | `30` | Durée de rétention des sauvegardes |
| `deletion_sync` | `false` | Propager les suppressions |
| `pause_on_battery` | `false` | Pause si sur batterie |
| `pause_on_metered` | `false` | Pause si connexion limitée |
| `dirs[].local_sub` | — | Nom du sous-dossier local |
| `dirs[].nas_sub` | — | Nom du sous-dossier NAS |
| `dirs[].enabled` | `true` | Activer ce dossier |
| `dirs[].max_age_days` | `0` | Filtre par âge (0 = désactivé) |

---

## 8. Dépannage

### L'icône n'apparaît pas dans la barre système

L'extension GNOME **"AppIndicator and KStatusNotifierItem Support"** est requise.
```bash
gnome-extensions list --enabled | grep appindicator
```
Si absente : installer depuis https://extensions.gnome.org/extension/615/

### Le service ne démarre pas

```bash
systemctl --user status nas-sync
journalctl --user -u nas-sync -n 50
```

### Aucune synchro malgré le NAS connecté

```bash
mountpoint ~/NasShare && echo "monté" || sudo mount -a
```

### La fenêtre de conflit n'apparaît pas

Vérifier que la session GNOME exporte les variables d'affichage :
```bash
echo $WAYLAND_DISPLAY $DISPLAY
```
Si vides, redémarrer la session puis :
```bash
systemctl --user restart nas-sync
```

### L'assistant de premier démarrage ne s'ouvre pas

```bash
# Lancer manuellement
python3 ~/programs/nas_sync/first_run_wizard.py

# Forcer le re-lancement (supprimer la config existante)
rm ~/.nas_sync_config.json
python3 ~/programs/nas_sync/first_run_wizard.py
```

### Réinitialiser l'état de synchronisation

```bash
systemctl --user stop nas-sync
rm ~/.nas_sync_state.json
systemctl --user start nas-sync
```

### Ajouter un dossier à synchroniser

Via **Paramètres → Dossiers → + Ajouter**, ou manuellement dans
`~/.nas_sync_config.json` (le démon rechargera avec SIGHUP).

### Le déploiement entreprise échoue

```bash
# Vérifier que cifs-utils est installé (requis pour mount.cifs)
rpm -q cifs-utils || sudo dnf install cifs-utils -y

# Vérifier les credentials
sudo cat /home/utilisateur/.smbcredentials

# Tester le montage manuellement
sudo mount -t cifs //Cassis.local/home /home/utilisateur/NasShare \
    -o credentials=/home/utilisateur/.smbcredentials,uid=$(id -u utilisateur)
```

---

*Documentation — jp@litchi — 22 mai 2026*
