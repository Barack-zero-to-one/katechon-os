# MIGRATION BAILEYS → META CLOUD API

## Pour Garga — BADF Ltd v9.17 → v9.18

---

## Pourquoi cette migration

Baileys a causé des bans WhatsApp (code 401, protocol detection). Vous avez migré vers Meta WhatsApp Cloud API officielle. Le code Python (`barack_corp_v9_17.py`) utilise déjà Meta API. Mais le watchdog et le démarrage Windows référençaient encore Baileys.

Les fichiers v2 livrés ici nettoient cette incohérence.

---

## Fichiers à REMPLACER sur ton Lenovo

Les 4 nouveaux fichiers v2 remplacent les anciens :

| Fichier | Action | Ce qui change |
|---------|--------|---------------|
| `watchdog.js` | **REMPLACER** | Surveille uniquement Python, plus de Baileys. Logs JSON structurés. Gestion 503. |
| `package.json` | **REMPLACER** | Dépendances Node nettoyées (plus de `@whiskeysockets/baileys`, `axios`, `express`, `pino`, `qrcode-terminal`). |
| `DEMARRAGE.bat` | **REMPLACER** | Plus de vérification baileys_server, plus de session backup. |
| `.env` | **À COMPLÉTER** | 4 valeurs Meta API à remplir. |

---

## Fichiers à SUPPRIMER (obsolètes)

Sur ton Lenovo, supprime ou archive :

```
baileys_server.js          → obsolète, plus utilisé
baileys_session/            → dossier session Baileys, plus utilisé
baileys_session_backup/     → backup obsolète
node_modules/               → réinstaller avec nouveau package.json
package-lock.json           → régénérer
```

**Commandes pour nettoyer (PowerShell ou cmd) :**

```cmd
REM Sauvegarder l'ancien Baileys au cas où
mkdir archive_baileys_v917
move baileys_server.js archive_baileys_v917\
move baileys_session archive_baileys_v917\
move baileys_session_backup archive_baileys_v917\

REM Réinstaller les dépendances Node propres
del package-lock.json
rmdir /s /q node_modules
npm install
```

---

## Ordre d'application sur le Lenovo (lundi matin)

### Étape 1 : Sauvegarder l'existant (5 min)

```cmd
cd C:\Users\lenovo\Desktop\TontineBot
xcopy . ..\TontineBot_BACKUP_avant_v918\ /E /I /Y
```

### Étape 2 : Copier les nouveaux fichiers v2 (2 min)

Télécharger les 4 fichiers depuis cette conversation et les placer dans `C:\Users\lenovo\Desktop\TontineBot\` :
- `watchdog.js` → remplace l'existant
- `package.json` → remplace l'existant
- `DEMARRAGE.bat` → remplace l'existant
- `.env.example` → fichier de référence

### Étape 3 : Configurer le vrai .env (10 min)

```cmd
copy .env.example .env
notepad .env
```

Remplir les 4 valeurs Meta :
- META_PHONE_ID
- META_TOKEN
- META_BUSINESS_ID
- META_APP_SECRET

### Étape 4 : Nettoyer Baileys (5 min)

```cmd
REM Archiver
mkdir archive_baileys_v917
move baileys_server.js archive_baileys_v917\ 2>nul
move baileys_session archive_baileys_v917\ 2>nul
move baileys_session_backup archive_baileys_v917\ 2>nul

REM Réinstaller les dépendances Node
del package-lock.json
rmdir /s /q node_modules
npm install
```

### Étape 5 : Configurer le webhook Meta (10 min)

Sur https://developers.facebook.com → ton app → WhatsApp → Configuration → Webhooks :

- **URL de rappel** : `https://lennox-unbiographical-jasmin.ngrok-free.dev/webhook/whatsapp`
- **Token de vérification** : `badf_meta_2026`
- **Champs** : cocher `messages`

Cliquer "Vérifier et enregistrer". Si vert → OK.

### Étape 6 : Premier test (5 min)

```cmd
DEMARRAGE.bat
```

Tu devrais voir dans la console :
- PostgreSQL OK
- ngrok lancé
- watchdog v2 actif
- Python qui démarre
- Health check OK toutes les 15s

### Étape 7 : Envoyer un message test

Depuis WhatsApp, envoyer "menu" au numéro BADF. Le bot doit répondre.

Si réponse → 🎉 migration réussie.
Si pas de réponse → vérifier logs/autostart.log et logs/watchdog.log.

---

## Vérification rapide post-migration

Pour confirmer que tout est propre, lancer ces commandes :

```cmd
REM Vérifier qu'aucune référence Baileys ne reste
findstr /s /i "baileys" *.js *.bat *.py *.json 2>nul

REM Doit retourner UNIQUEMENT :
REM - Le fichier archive_baileys_v917 (normal, c'est l'archive)
REM - Rien d'autre
```

Si tu vois des références Baileys dans `watchdog.js`, `package.json`, `DEMARRAGE.bat` → tu n'as pas remplacé les bons fichiers.

---

## Différences détaillées watchdog v1 vs v2

| Aspect | v1 (Baileys) | v2 (Meta API) |
|--------|--------------|---------------|
| Processus surveillés | 2 (Python + Baileys) | 1 (Python uniquement) |
| Health checks | 2 endpoints | 1 endpoint |
| Backup session | Oui (Baileys auth) | Non (Meta gère côté serveur) |
| Restauration session | Oui | Non |
| Logs | console.log uniquement | JSON structuré + fichier |
| Gestion 503 | OK = vivant | OK mais redémarrage si 3 consécutifs |
| Code lignes | ~290 | ~200 (plus simple) |

---

## Si quelque chose ne marche pas

**Erreur "npm install" échoue** :
- Vérifier que Node.js >= 18 est installé : `node --version`
- Vider le cache npm : `npm cache clean --force`

**Bot ne répond pas aux messages** :
- Vérifier que ngrok tourne : `curl https://lennox-unbiographical-jasmin.ngrok-free.dev/health`
- Vérifier les logs Meta dans developers.facebook.com → WhatsApp → API Setup → Logs
- Vérifier que META_TOKEN n'est pas expiré (utiliser System User pour token permanent)

**Watchdog redémarre Python en boucle** :
- Lire `logs/watchdog.log` (JSON structuré) pour comprendre pourquoi
- Vérifier que PostgreSQL est vraiment lancé
- Vérifier que le port 5000 n'est pas occupé par un autre process

**Webhook Meta retourne "Échec de la vérification"** :
- Vérifier que `META_VERIFY_TOKEN` dans .env correspond à celui saisi dans Meta
- Vérifier que ngrok pointe bien vers le port 5000
- Vérifier que le bot Python tourne et expose `/webhook/whatsapp`

---

## État final attendu

Une fois la migration finie, tu auras :

```
C:\Users\lenovo\Desktop\TontineBot\
├── barack_corp_v9_17.py        ← code Python (Meta API natif)
├── watchdog.js                  ← v2, surveille Python uniquement
├── package.json                 ← v2, sans Baileys
├── DEMARRAGE.bat                ← v2, simplifié
├── INSTALLER_DEMARRAGE_AUTO.bat ← inchangé
├── .env                         ← rempli avec valeurs Meta
├── .env.example                 ← template
├── create_db_v917.sql           ← schema PostgreSQL
├── stress_test_v917.py          ← tests stress
├── logs/
│   ├── autostart.log
│   ├── watchdog.log             ← nouveau, JSON structuré
│   └── message_queue.json
├── backups/
└── archive_baileys_v917/        ← ancien code archivé (peut être supprimé après validation)
```

Architecture propre, plus de bans WhatsApp, prêt pour lancement fin mai.
