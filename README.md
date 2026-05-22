# NAS Sync

Synchronisation automatique et bidirectionnelle entre votre machine locale
et un NAS (Cassis.local) via SMB/CIFS.

Fonctionne en arrière-plan, sans intervention. Conçu pour Nobara Linux (GNOME)
sur un ordinateur portable qui quitte régulièrement le réseau local.

---

## Comment ça marche

Quand vous êtes **chez vous** (NAS accessible) :
- Vos fichiers sont synchronisés automatiquement entre votre machine et le NAS.

Quand vous êtes **en déplacement** (NAS inaccessible) :
- Vous continuez à travailler normalement dans vos dossiers habituels.
- Vos fichiers sont stockés localement dans `~/offline_cache/`.

Quand vous **rentrez** (NAS reconnecté) :
- La synchronisation reprend automatiquement.
- Seuls les fichiers modifiés sont copiés.
- En cas de conflit, une fenêtre vous demande quoi faire.

---

## Installation

### Installation interactive (usage personnel)

Lancer le script depuis chez soi, NAS monté sur `~/NasShare` :

```bash
bash ~/programs/nas_sync/install.sh
```

Le script installe automatiquement les dépendances manquantes, puis s'occupe de tout :
- Copie initiale NAS → cache local
- Redirige vos dossiers (`~/Bureau`, `~/Téléchargements`, etc.) vers le cache local
- Installe et démarre le service systemd (démon de synchro)
- Configure le démarrage automatique de l'interface au login GNOME

**Premier démarrage :** si aucune configuration n'existe, un **assistant de configuration**
s'ouvre automatiquement pour vous guider étape par étape.

### Action manuelle dans Nautilus (une seule fois)

Dans l'explorateur de fichiers :
- Clic droit sur les anciens favoris dans la barre latérale → **Retirer des favoris**
- Glisser vos dossiers depuis `~/offline_cache/` dans la barre latérale

---

## Déploiement en entreprise

Pour déployer sur plusieurs machines silencieusement (SSH / Ansible / MDM) :

```bash
sudo bash ~/programs/nas_sync/deploy.sh \
    --nas-host Cassis.local \
    --nas-share home \
    --nas-user prenom.nom \
    --nas-password MotDePasse \
    --deploy-user prenom.nom
```

Aucune fenêtre graphique. Compatible avec toute méthode de déploiement à distance.
Voir [DOCUMENTATION.md](DOCUMENTATION.md#déploiement-entreprise) pour le détail complet.

---

## Désinstallation

```bash
bash ~/programs/nas_sync/uninstall.sh
```

Le script restaure la configuration d'origine (liens symboliques vers `~/NasShare`)
et demande confirmation avant de supprimer les données.

---

## Utilisation

### Icône dans la barre système

Après installation, une icône apparaît dans la barre système GNOME.

| Icône | Signification |
|-------|---------------|
| Visible (`network-server`) | Synchronisation active |
| Invisible | Démon arrêté — l'app tourne en silence |

**Clic droit** sur l'icône pour accéder au menu.

### Menu principal

| Entrée | Action |
|--------|--------|
| ● Synchronisation active | Statut et progression en cours |
| Fichiers récents… | Historique des fichiers synchro |
| Paramètres… | Réglages complets |
| Synchroniser maintenant | Force une synchro immédiate |
| Désactiver / Activer | Bascule le démon on/off |
| Quitter l'interface | Ferme l'interface (le démon continue) |

### Activer / désactiver rapidement

```bash
bash ~/programs/nas_sync/toggle.sh
```

---

## Résolution de conflits

Un **conflit** se produit quand un fichier a été modifié des deux côtés
(localement et sur le NAS) depuis la dernière synchro.

Une fenêtre apparaît avec quatre options :

| Bouton | Effet |
|--------|-------|
| **Garder LOCAL** | Écrase la version NAS avec la version locale |
| **Garder NAS** | Écrase la version locale avec la version NAS |
| **Renommer…** | Conserve les **deux versions** en renommant l'une d'elles |
| **Ignorer** | Reporte la décision à la prochaine synchro |

L'option **Renommer** permet de choisir :
- Quelle version renommer (locale ou NAS)
- Le nouveau nom (pré-rempli automatiquement avec la date)

Les deux fichiers sont ensuite synchronisés partout — aucune version n'est perdue.

---

## Paramètres

Accès : **clic droit sur l'icône → Paramètres…**

### Connexion
- Hôte NAS, port SMB, chemin de montage, dossier du cache local

### Dossiers
- Activer/désactiver chaque dossier
- Définir un **filtre par âge** : par exemple, ne synchroniser que les fichiers
  des 90 derniers jours pour les Téléchargements ou Vidéos (évite de dupliquer
  des années de fichiers volumineux)

Valeurs par défaut :

| Dossier | Filtre |
|---------|--------|
| Bureau, Documents, Images | Aucun (tous les fichiers) |
| Téléchargements, Vidéos | 90 derniers jours |
| Musique | 180 derniers jours |

### Synchronisation
- Fréquence de vérification du NAS (défaut : 30 s)
- Fréquence de synchro périodique (défaut : 5 min)
- Mode conflit automatique : demander / toujours garder local / toujours garder NAS

### Notifications
- Activer/désactiver les notifications bureau
- Seuil de déclenchement (nombre de fichiers minimum)

### Filtres & Avancé
- Patterns de fichiers à exclure (ex : `*.tmp`, `~$*`)
- Sauvegarde avant écrasement (dans `~/.nas_sync_backups/`)
- Synchronisation des suppressions
- Pause si sur batterie / connexion limitée

---

## Commandes utiles

```bash
# État du service
systemctl --user status nas-sync

# Logs en temps réel
tail -f ~/.nas_sync.log

# Forcer une synchro immédiate
kill -USR1 $(cat ~/.nas_sync.pid)

# Redémarrer le démon
systemctl --user restart nas-sync

# Recharger la configuration sans redémarrer
kill -HUP $(cat ~/.nas_sync.pid)
```

---

## Fichiers du projet

```
nas_sync/
├── nas_sync_daemon.py    Démon de synchronisation (arrière-plan)
├── nas_sync_app.py       Interface barre système GNOME
├── nas_sync_config.py    Configuration partagée (module Python)
├── conflict_dialog.py    Fenêtre de résolution de conflit
├── first_run_wizard.py   Assistant de configuration au premier démarrage
├── install.sh            Installation interactive (usage personnel)
├── deploy.sh             Déploiement silencieux (entreprise / SSH / Ansible)
├── uninstall.sh          Désinstallation complète
├── toggle.sh             Activer / désactiver rapidement
├── tools/make_pdf.py     Génération du PDF de documentation
├── README.md             Ce fichier
└── DOCUMENTATION.md      Documentation technique complète
```

Fichiers générés dans `~/` :

```
~/.nas_sync_config.json     Configuration (modifiable via l'interface)
~/.nas_sync_state.json      État de synchro de chaque fichier
~/.nas_sync_events.jsonl    Historique des événements
~/.nas_sync.log             Journal du démon
~/.nas_sync.pid             PID du démon en cours
~/.nas_sync_backups/        Sauvegardes avant écrasement
~/.smbcredentials           Identifiants SMB (chmod 600)
```

---

## Dépannage rapide

| Problème | Solution |
|----------|----------|
| L'icône n'apparaît pas | Installer l'extension GNOME *AppIndicator Support* |
| Aucune synchro | Vérifier : `mountpoint ~/NasShare` |
| Fenêtre de conflit invisible | Redémarrer la session GNOME |
| Réinitialiser la synchro | `rm ~/.nas_sync_state.json` puis redémarrer le service |
| L'assistant ne se lance pas | `python3 ~/programs/nas_sync/first_run_wizard.py` |

Pour le dépannage détaillé, voir [DOCUMENTATION.md](DOCUMENTATION.md).
# nas_sync
