# CHANGELOG v9.17 → v9.18

**Date initiale :** 17 mai 2026 | **Dernière mise à jour :** 28 juin 2026
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

---

## Améliorations post-lancement (28 juin 2026)

### PATCH 15 — Pipeline OCR WhatsApp : OpenCV dark mode + compression

**Problème :** Tesseract recevait les screenshots bruts compressés par WhatsApp (artefacts JPEG 8×8, petit texte illisible, mode sombre non géré → texte blanc sur fond noir rejeté).

**Fix :** Nouvelle fonction `_pretraiter_screenshot_whatsapp(image_bytes)` :
1. Décodage OpenCV (BGR) avec fallback PIL si image corrompue
2. Conversion grayscale
3. Détection dark mode : `mean < 127` → `cv2.bitwise_not()` (inversion)
4. Upscale INTER_CUBIC si largeur < 1400px
5. `GaussianBlur(3,3)` pour lisser les artefacts JPEG
6. `adaptiveThreshold(blockSize=31, C=15)` — gère l'illumination mixte (barre de statut colorée + corps blanc)
7. Bordure blanche 20px → Tesseract reçoit toujours du noir pur sur blanc pur

Config Tesseract mise à jour : `--psm 6 --oem 3 -c preserve_interword_spaces=1`

**Dépendances ajoutées :** `opencv-python==4.10.0.84`, `numpy==2.2.5` dans `requirements.txt`

---

### PATCH 16 — Extraction regex ultra-tolérante (montant + référence)

**Problème :** Regex montant trop étroite : ratait `5.000 FCFA`, montants > 999 999, devise FRS. Regex référence : aucune logique par opérateur, zéro fallback si OCR manglait le mot-clé "Référence".

**Fix — Montant :**
- `_NBR = r"(\d{4,10}|\d{1,3}(?:[. \xa0]\d{3})*)"` — `\d{4,10}` testé en premier (évite capture partielle)
- Devises : `FCFA|XAF|CFA|FRS|F\b`
- Nettoyage : `re.sub(r"[^\d]", "")` → `int()` ne lève jamais ValueError

**Fix — Référence :** 3 niveaux de priorité :
1. Pattern spécifique opérateur : Orange `OM\d{8,12}`, MTN `TXN?\d{8,12}`, SwitchN `SWN?-?[A-Z0-9]{6,14}`
2. Patterns génériques REF/TXN/ID
3. Fallback OCR : chaîne alphanumérique majuscule 10-15 chars commençant par lettre, filtrée contre liste de mots-clés connus

---

### PATCH 17 — Détection SwitchN avant MTN/Orange

**Problème :** SwitchN (agrégateur camerounais, 500k+ downloads) mentionne "MTN" et "ORANGE" dans ses reçus comme opérateurs sous-jacents → détecté à tort comme MTN ou Orange.

**Fix :** Ordre de détection : `SWITCHN/SWITCH N` vérifié EN PREMIER, avant `MTN/MOMO/MOBILE MONEY` et `ORANGE/FLOOZ/OM `.

---

### PATCH 18 — 7 bugs corrigés (code review)

| # | Bug | Fix |
|---|-----|-----|
| 1 | `conn.commit()` + `release_conn()` après INSERT membre, AVANT `inscrire_dans_tontine()` — la nouvelle connexion du pool ne voyait pas le membre | Déplacé commit/release avant l'appel |
| 2 | `_traiter_screenshot_adhesion_dm` ne retournait pas tôt pour membres déjà actifs | `release_conn(conn); return True` ajouté |
| 3 | `statut_global='Actif'` à l'INSERT inline → membre actif sans KYC complété | Corrigé en `'En_attente_kyc'` |
| 4 | KYC ne démarrait pas : `cur_new` non déclaré, `inserted` jamais évalué | `cur_new = q(...)`, `inserted = cur_new.rowcount > 0` |
| 5 | `demarrer_kyc()` mutait `_sessions_kyc` sans verrou | `with _sessions_lock: _sessions_kyc[wa] = {...}` |
| 6 | 5 requêtes analytiques utilisaient `SUM(montant)` au lieu de `SUM(montant_brut)` | Corrigé dans toutes les occurrences |
| 7 | Message confirmation adhesion mentionnait encore le paiement alors que `FRAIS_ADHESION=0` | Ligne supprimée |

---

### PATCH 19 — Sursis premier retard de cotisation

**Problème :** Membre en retard > 72H suspendu et taxé 1 000 FCFA dès la PREMIÈRE fois, même pour une erreur honnête.

**Fix :**
- Nouvelle colonne `membres.nb_avertissements_retard INTEGER DEFAULT 0`
- Migration `ALTER TABLE IF NOT EXISTS` au démarrage
- `verifier_suspensions_retard()` bifurquée :
  - `nb_avertissements_retard == 0` → sursis silencieux (compteur +1, trace `sanctions`, aucun message)
  - `nb_avertissements_retard >= 1` → suspension 72H + 1 000 FCFA (comportement existant)
- Message intro groupe ARTICLE 3 mis à jour : "Premier retard → Sursis accordé. Récidive → Suspension + 1 000 FCFA"

---

### PATCH 20 — Crédit communication 1 000 FCFA : gate anti-fraude 5 transactions

**Problème :** Le message de bienvenue promettait "instantanément 1 000 FCFA de crédit" dès l'ajout du bot. Zéro implémentation. Risque : faux groupes de 3 amis créés pour obtenir le bonus sans activité réelle.

**Fix :**
- Nouvelle colonne `tontines.credit_comm_statut TEXT DEFAULT 'Non_eligible'` (états : `Non_eligible` → `Eligible` → `Verse`)
- Migration `ALTER TABLE IF NOT EXISTS` au démarrage
- Nouvelle fonction `_verifier_credit_comm(conn, tontine_id)` : appelée après chaque cotisation confirmée, déclenche `Eligible` + notification `OWNER_WA` à la 5ème transaction réelle
- Commande owner `CREDIT_VERSE <id>` : marque le crédit comme versé manuellement (`Verse`)
- Message bienvenue groupe corrigé : "5 premières transactions validées" remplace "instantanément"

---

### PATCH 21 — 5 bugs corrigés (code review post-sursis)

| # | Bug | Fix |
|---|-----|-----|
| 1 | `nb_avertissements_retard` dans `membres` (global) → un membre en retard dans 2 tontines perdait son sursis dans la 2ème | Colonne déplacée dans `adhesions` (per membre/tontine) — migration `ALTER TABLE adhesions ADD COLUMN IF NOT EXISTS` |
| 2 | `nb_avertissements_retard` jamais remis à 0 à la réactivation → sursis one-shot permanent à vie | `UPDATE adhesions SET nb_avertissements_retard=0` ajouté dans le flux de confirmation cotisation (lever suspension) |
| 3 | `_verifier_credit_comm` : `conn.commit()` puis `wa_prive()` sans isolation — si Green API échoue, owner jamais notifié mais DB dit `Eligible` | `wa_prive` isolé dans son propre `try/except`, log warning explicite avec rappel de la commande `CREDIT_VERSE <id>` |
| 4 | `CREDIT_VERSE` : `UPDATE` sans vérifier existence tontine → succès silencieux sur ID inexistant | `fetchone()` avant UPDATE, erreur explicite si introuvable, info si déjà versé |
| 5 | `verifier_suspensions_retard` : `conn.commit()` par membre dans la boucle sans isolation — échec sur membre B laisse membres C/D/E non traités | `try/except` par membre avec `conn.rollback()` + `continue` pour isolation totale |

---

### PATCH 23 — Calibration OCR sur 59 vrais reçus (56 Orange/MTN + 3 SwitchN PDF)

| # | Problème | Fix |
|---|----------|-----|
| 1 | SwitchN envoie des PDFs — `cv2.imdecode()` crash sur PDF bytes | Nouvelle fonction `_lire_pdf_switchn()` : extraction directe via `pdfplumber` (zéro OCR, 100% précision). Dispatch automatique dans `lire_screenshot_mobile_money` via magic bytes `b'%PDF'`. Handler `document` ajouté dans `_traiter_message_meta`. |
| 2 | ID Orange Money format `PP260623.1152.AD63N5` non capturé | Pattern `r"\b(PP\d{6}\.\d{4}\.[A-Z0-9]{4,8})\b"` ajouté en priorité 1 Orange |
| 3 | `Montant Transaction: X FCFA` capturé après `Montant Net` | Pattern `MONTANT TRANSACTION` ajouté en tête de `patterns_montant` |
| 4 | Reçus Orange→MTN : `"MTN"` détecté avant `"ORANGE MONEY"` | Détection opérateur réécrite : `"MTN"` seul supprimé, remplacé par `"MTN MOMO"/"MOMO"`. `"TRANSFERT DE"` ajouté comme signal Orange exclusif. |
| 5 | Clavier WhatsApp QWERTY en bas du screenshot pollue OCR | Crop 18% du bas si portrait (`h > w`) dans `_pretraiter_screenshot_whatsapp` |

**Dépendance ajoutée :** `pdfplumber==0.11.4` dans `requirements.txt`

---

### PATCH 24 — OCR : dark mode inversion + format MTN anglais Cameroun

**Résultat :** 91% → **96% confiance haute** sur 56 reçus réels (stress test 1 screenshot/seconde).

| # | Problème | Root cause | Fix |
|---|----------|-----------|-----|
| 1 | Notifications MTN anglaises ("Cash in of 5000 XAF") → `operateur=Inconnu`, chiffres lus comme lettres ("SOOO" au lieu de "5000") | `gray.mean() < 127` ne déclenchait pas l'inversion : la bulle WhatsApp violet-gris a `mean≈130-137` (tiré vers le haut par les zones blanches hors-bulle), donc le texte blanc restait sur fond noir → Tesseract lisait des lettres | Inversion basée sur le **ratio de pixels sombres** : `np.mean(gray < 100) > 0.35`. Gap naturel entre bulles sombres (0.69–0.74) et reçus fond clair (0.08–0.33). |
| 2 | "Cash in of" non reconnu comme MTN | Aucun keyword MTN dans ces notifications anglaises | Ajout : `"CASH IN OF"`, `"HAVE TRANSFERRED"`, `"YOU HAVE TRANSFERRED"` dans détection opérateur MTN |
| 3 | Type `inconnu` sur ces notifications | "CASH IN" absent des keywords de type | Ajout : `"CASH IN"`, `"HAVE TRANSFERRED"` dans détection type envoi |
| 4 | Date `2026-06-17` non détectée | Regex `\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}` ne matchait pas `YYYY-MM-DD` | Ajout prioritaire du pattern ISO `\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}` |
| 5 | `Transaction ID:17566837242` (numérique pur) non capturé comme référence MTN | Pattern MTN cherchait `[A-Z0-9]{8,15}` mais pas de motif spécifique pour `TRANSACTION ID:\d+` | Ajout pattern MTN : `TRANSACTION\s+ID\s*[:\-]?\s*(\d{8,15})` en priorité 1 |

**2 cas non résolus** (4%) : photos physiques d'écran à travers vitre rayée (FDXJ9842, GAEG3286). Inrécupérables par preprocessing — le bot demandera au membre de renvoyer une capture d'écran propre.

**Localisation :** `_pretraiter_screenshot_whatsapp` (ligne ~1816), `lire_screenshot_mobile_money` (ligne ~1985–2050)

---

### PATCH 22 — GitHub Ruleset : Merge bloqué tant que CI n'est pas vert

**Problème :** Les 3 jobs CI (syntax, secrets, dependencies) tournaient mais GitHub laissait quand même merger même si l'un d'eux était rouge.

**Fix :** Ruleset créé dans Settings > Rulesets (gratuit sur repo privé) :
- Target : `Include default branch` (main)
- Rule activée : `Require status checks to pass`
- Checks requis : `Python Syntax Check`, `No Hardcoded Secrets`, `Dependency Install`

**Effet :** Bouton Merge physiquement grisé tant que les 3 jobs ne sont pas verts. Zéro bypass possible.

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
