# CHANGELOG v9.17 → v9.18

**Date initiale :** 17 mai 2026 | **Dernière mise à jour :** 20 juin 2026
**Fichiers modifiés :** `barack_corp_v9_17.py` → `barack_corp_v9_18.py`
**Migration DB requise :** `migration_v9_18.sql`

---

## 5 patches initiaux + 8 améliorations post-lancement

### PATCH 1 — Race condition `confirmer_cotisation`

**Problème :** Si l'admin tape `OUI` (ex. CONF avant) deux fois rapidement, la cotisation pouvait être double-confirmée.

**Fix :** Ajout de `SELECT FOR UPDATE` (verrou pessimiste PostgreSQL) + try/except/finally pour libération garantie du verrou. La notification membre se fait HORS transaction.

**Localisation :** fonction `confirmer_cotisation` (ligne ~2102)

---

### PATCH 2 — Validation des montants aberrants

**Problème :** L'admin saisissait n'importe quel montant. Une faute de frappe (5000 au lieu de 50000) corrompait la base sans alerte.

**Fix :**
- Nouvelle classe `MontantAberrantError`
- Validation contre `tontines.montant_cotisation`
- Écart > 50% → refus catégorique
- Écart 15-50% → demande commande FORCE
- Écart 5-15% → accepté avec log warning
- Écart < 5% → accepté silencieusement
- Catch de `psycopg2.errors.UniqueViolation` (filet du PATCH 3)

**Localisation :** fonction `enregistrer_cotisation_manuelle` (ligne ~1995)

---

### PATCH 3 — Contraintes UNIQUE PostgreSQL

**Problème :** Si un bug applicatif contournait les protections Python, des doublons pouvaient s'insérer.

**Fix :** Migration SQL `migration_v9_18.sql` qui crée 2 index UNIQUE partiels :
- `idx_cotis_man_screenshot_confirmees` : empêche 2 cotisations confirmées avec même hash
- `idx_dettes_badf_unique_fmp` : empêche 2 dettes FMP pour même cotisation

La migration vérifie d'abord qu'il n'y a pas déjà des doublons (sinon ABORT).

---

### PATCH 4 — Menu admin simplifié avec descriptions

**Problème :** Les 15 options du menu admin étaient listées sans description. L'admin devait deviner.

**Fix :** Chaque option a maintenant un titre en gras + une description courte en-dessous. Exemple :
```
1️⃣  *Rapport du jour*
    └ Bilan cotisations & alertes
```

**Localisation :** constante `MENU_ADMIN_TXT` (ligne ~3816)

---

### PATCH 5 — Confirmation cotisation par OUI/NON

**Problème :** L'admin devait taper `CONF 42` ou `REJ 42 [raison]`. Format technique, non naturel, et destiné à disparaître quand les APIs MTN MoMo et Orange Money seront intégrées (Q1 2027).

**Fix :** Le bot affiche les cotisations **une par une**. L'admin répond :
- `OUI` → confirmation
- `NON [raison]` → rejet avec raison
- `PASSER` → suivante sans décision
- `0` → retour menu

La session mémorise la cotisation en cours et la file d'attente. Le bot passe automatiquement à la suivante après chaque décision.

Position dans la file affichée (`1/5`, `2/5`...). Re-check du statut DB à chaque étape (sécurité multi-admin).

Helper ajouté : `_passer_a_cotisation_suivante` (avant `traiter_menu_admin`)

**Localisation :** bloc `if texte == "15"` et `elif sess.get("etape") == "confirm_cotisation"` (ligne ~5222)

---

---

## Améliorations post-lancement (20 juin 2026)

### PATCH 6 — Suppression NotchPay

Suppression complète de NotchPay (webhook `/webhook/notchpay`, route désactivée,
paramètre `notchpay_code`, colonne DB, migration `notchpay_ref→reference`).
Le bot utilise exclusivement la vérification par screenshot OCR.

---

### PATCH 7 — Suppression photo dans le KYC

Étape photo CNI (adulte) et photo acte de naissance (mineur) supprimées du flux KYC.
Trop dissuasif pour les utilisateurs. KYC adulte : 4 étapes → 3. Mineur : 4 → 3.

---

### PATCH 8 — Pays réduits à CM, SN, CI

Suppression de NG, GH, KE, BR de `COUNTRY_CONFIG`.
Table `pays` supprimée de la DB. `get_pays()` lit désormais depuis `COUNTRY_CONFIG`
en mémoire (plus rapide, 0 requête DB). `detecter_pays_par_indicatif()` réduit
aux 3 indicatifs actifs (+237, +221, +225).

---

### PATCH 9 — Auto-inscription tontines en cours

Quand le bot rejoint un groupe avec une tontine déjà active (membres ou cycle > 1) :
- Auto-inscription de tous les participants sans frais ni KYC
- KYC demandé à la fin du cycle (`_verifier_fin_cycle()`)

Nouveau helper `_auto_inscrire_participants(conn, tontine_id, participants)`.

---

### PATCH 10 — Question "tontine déjà en cours ?" à la config

Dans `traiter_config_tontine` (DM admin lors de la config d'un nouveau groupe),
ajout d'une 4ème question : "Cette tontine est-elle déjà en cours ? OUI/NON".
- OUI → auto-inscription des participants + message groupe + KYC fin de cycle
- NON → flux normal (intro + demande KYC + adhésion)

La liste des participants est désormais stockée dans la session pending pour être
disponible quand l'admin répond en DM (potentiellement plusieurs minutes après).

---

### PATCH 11 — Trust graph prédictif amélioré

Fonction `calculer_score_risque_fugue()` : 7 features → 9 features.

**Feature 8 — Comportement post-bouffage (poids 20)**
Signal le plus important : le membre a-t-il continué à cotiser dans les 30 jours
après son dernier bouffage ? 0 cotisation = 20 pts de risque. ≤2 = 12 pts.

**Feature 9 — Chute brusque du score de confiance (poids 10)**
Chute de >25 pts en 30 jours = 10 pts de risque. Utilise `historique_score_confiance`.

**Fix bug vélocité (feature 6)**
Utilisait `INTERVAL '5 hours'` hardcodé. Utilise maintenant `tontines.heure_ouverture`.

---

### PATCH 12 — Audit trail score confiance

Nouvelle table `historique_score_confiance (membre_id, score_av, score_ap, delta, raison, created_at)`.
Nouveau wrapper `_update_score_confiance(conn, membre_id, raison, delta, set_val)`.
Toutes les mises à jour du score passent par ce wrapper et sont loguées automatiquement.
(9 occurrences remplacées dans le code.)

---

### PATCH 13 — Fix watchdog.js

`PYTHON_SCRIPT` corrigé : `barack_corp_v9_17.py` → `barack_corp_v9_18.py`.
Bug critique : le watchdog démarrait le mauvais fichier.

---

### PATCH 14 — Fix DEMARRAGE.bat : chargement ENV

**Problème :** `DEMARRAGE.bat` ne chargeait jamais le fichier `ENV` avant de lancer
le watchdog. Les variables `META_TOKEN`, `META_PHONE_ID`, `META_BUSINESS_ID`,
`META_APP_SECRET` étaient donc vides au démarrage de Python → le bot ne pouvait
pas envoyer ni recevoir de messages WhatsApp même avec les credentials configurés.

**Fix :** Ajout d'une boucle `for /f` lisant `ENV` ligne par ligne (ignorant les
commentaires `#` et les lignes vides) et exportant chaque paire `CLÉ=VALEUR`
dans l'environnement avant le lancement du watchdog.

**Effet immédiat :** Remplir `META_PHONE_ID`, `META_TOKEN`, `META_BUSINESS_ID`,
`META_APP_SECRET` dans le fichier `ENV` puis `DEMARRAGE.bat` → le bot répond.

---

## Ordre d'application lundi matin

### 1. Backup complet (5 min)
```cmd
cd C:\Users\lenovo\Desktop\TontineBot
xcopy . ..\TontineBot_BACKUP_AVANT_V918\ /E /I /Y
pg_dump -h localhost -U postgres barack_corp > backup_avant_v918.sql
```

### 2. Appliquer la migration SQL (2 min)
```cmd
psql -h localhost -U postgres -d barack_corp -f migration_v9_18.sql
```
Doit afficher : `NOTICE: OK: Migration v9.18 reussie, 2 index UNIQUE crees`

### 3. Remplacer le code Python (1 min)
```cmd
copy barack_corp_v9_17.py barack_corp_v9_17_OLD.py
copy ..\Downloads\barack_corp_v9_18.py barack_corp_v9_17.py
```
(On garde le nom `barack_corp_v9_17.py` car référencé dans `watchdog.js` et `DEMARRAGE.bat`)

### 4. Vérification syntaxique (30 sec)
```cmd
python -c "import ast; ast.parse(open('barack_corp_v9_17.py').read()); print('OK')"
```

### 5. Lancer le bot (1 min)
```cmd
DEMARRAGE.bat
```

### 6. Tests fonctionnels (15 min)
- Test menu admin : tape `admin [tontine]` → vérifier nouveau menu avec descriptions
- Test cotisation OUI/NON : option 15 → OUI/NON/PASSER → vérifier flow
- Test race condition : taper OUI deux fois rapidement → vérifier qu'un seul enregistrement
- Test validation montant : saisir montant aberrant → vérifier refus

---

## Rollback si problème

```cmd
copy barack_corp_v9_17_OLD.py barack_corp_v9_17.py
psql -h localhost -U postgres -d barack_corp < backup_avant_v918.sql
```

(Les index UNIQUE peuvent rester, ils ne cassent rien.)

---

## Vérifications après application

```sql
-- Vérifier les 2 nouveaux index
SELECT indexname FROM pg_indexes
WHERE indexname IN (
    'idx_cotis_man_screenshot_confirmees',
    'idx_dettes_badf_unique_fmp'
);
-- Doit retourner 2 lignes
```

---

## État du code après application

- **9 945 lignes** v9.17 → **~10 087 lignes** v9.18 (ajout ~142 lignes)
- **144 fonctions** v9.17 → **145 fonctions** v9.18 (ajout `_passer_a_cotisation_suivante`)
- **1 nouvelle classe** : `MontantAberrantError`
- **0 fonction supprimée**
- **0 commentaire/docstring perdu**
- **Syntaxe Python validée** (ast.parse OK)
