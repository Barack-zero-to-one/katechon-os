# CLAUDE.md — BADF Ltd — KATECHON OS — TontineBot Pro v9.18

## Identité du projet

**BADF Ltd** (Barack & AI Development Facilities) est l'entreprise.
**KATECHON OS** est le nom officiel du protocole — nom investisseurs / YC / institutionnel.
**Noms locaux** — ce que les utilisateurs voient sur WhatsApp :

| Marché | Pratique locale | Nom produit |
|--------|----------------|-------------|
| Cameroun / Sénégal / CI | Tontine | Tontine OS |
| Nigeria | Ajo / Esusu | Ajo OS |
| Ghana | Susu | Susu OS |
| Kenya / Tanzanie | Chama | Chama OS |
| Brésil | Consórcio | Consórcio OS |
| Inde | Chit Fund | Chit OS |
| Indonésie | Arisan | Arisan OS |
| Philippines | Paluwagan | Paluwagan OS |

Le nom local crée la confiance immédiate. KATECHON OS crée la légitimité institutionnelle.
Même produit. Deux identités. Aucun compromis.

---

## Vision — Le Katechon des ROSCAs

KATECHON OS est le **protocole de coordination financière de l'économie informelle du Global South** — une infrastructure 2G-native qui agrège 400 millions de non-bancarisés via WhatsApp pour devenir le rail silencieux de distribution USDC et la couche d'underwriting du crédit non-bancaire sur 5 continents.

Le katechon (paulinien, repris par Carl Schmitt) est la force qui retient le chaos avant la dissolution de l'ordre. Dans les ROSCAs : le chaos c'est la fugue, la fraude, la défaillance, l'absence de trust. Le système actuel tient uniquement par la pression sociale — fragile, non scalable, zéro infrastructure.

**KATECHON OS substitue la loi au lien social comme mécanisme d'ordre.** Il rend le comportement honnête financièrement rationnel avant même que la tentation existe.

Ce n'est pas une app de gestion de tontine. C'est l'infrastructure de confiance de l'économie informelle mondiale.

**WhatsApp est le Trojan horse, pas le produit final.**

---

## Marché

- 400–500 millions de personnes coordonnent $800B–$1T/an via ROSCAs (tontines, chit funds, esusu, arisan, consórcio)
- Économie informelle globale : **$20 000 milliards** — jamais servie par aucune infrastructure
- Afrique seule : $1 432B mobile money (66% valeur mondiale), 92B transactions/an, 2,3B comptes enregistrés (GSMA)
- **Zéro infrastructure digitale existante** sur ce marché

---

## Architecture en phases

**Phase 1 (actuel)** — OS de coordination
- WhatsApp comme Trojan horse
- Agrégation des masses non-bancarisées
- Construction du Trust Graph (données comportementales irréplicables)
- Preuve de marché : tontines CM, SN, CI

**Phase 2** — Protocole financier décentralisé
- BADF devient le **rail silencieux de distribution USDC**
- Des centaines de millions d'utilisateurs non-bancarisés touchent à la crypto sans le savoir
- **Trust Graph → couche d'underwriting** pour le crédit non-bancaire
- Assurance paramétrique sur 5 continents
- Swap front-end possible : WhatsApp → USSD (Afrique) → PIX (Brésil) → tout protocole local (black swan hedge, pas un moat offensif)

---

## 5 Moats structurels

1. **Trust Graph** — le plus fort. 24–36 mois de données comportementales irréplicables. Premier credit bureau comportemental du Global South sur des populations que les agences de notation n'ont jamais vues. Se construit uniquement dans le temps — impossible à racheter ou copier.

2. **2G-native** — barrière technique absolue. Zéro concurrent tech capable de servir ce marché. Silicon Valley ne peut pas descendre sous 4G architecturalement. By design ici.

3. **Régulation hors licensing** — positionné hors licensing Payment Institution par design architectural. Les concurrents potentiels passent 2–3 ans à obtenir des licences COBAC/BCEAO.

4. **Économie asymétrique** — CAC = 0 (groupes WhatsApp existants). Infra = laptop + ngrok. Un concurrent bien financé qui veut copier dépense 100× plus pour le même résultat.

5. **Founder-market fit absolu** — fondateur né dans le problème, Maroua, Cameroun. Un VC californien ne peut pas envoyer une équipe comprendre ce marché en 6 mois. Pas appris dans un MBA.

---

## 5 Sources de revenus actuelles

1. **Frais d'adhésion** — 1 000 FCFA, une fois, valable à vie sur le réseau BADF
2. **FMP 2%** — Frais de Mission et de Prestation, prélevé automatiquement sur chaque cotisation confirmée
3. **IRA** — 150 FCFA/jour de retard, cumulé et déduit du bouffage
4. **Frais de réactivation** — 1 000 FCFA après suspension 72h
5. **Frais changement de numéro** — 250 FCFA (commande CHGNUM)

Phase 2 ajoute : frais sur mouvements USDC, underwriting crédit, assurance paramétrique.

---

## 8 Couches de sécurité

1. **SHA-256 hash anti-recyclage screenshots** — chaque screenshot a une empreinte unique. Déjà vu ou modifié → rejet immédiat. Délai max 24h.
2. **SELECT FOR UPDATE PostgreSQL** — verrou pessimiste natif DB sur `confirmer_cotisation`. Deux admins tapent OUI simultanément → un seul passe.
3. **Rate limiting** — 10 messages / 60 secondes par identifiant → audit log automatique.
4. **UNIQUE indexes partiels DB** — filet de sécurité niveau base de données. Même si un bug Python contourne tout le reste, la DB refuse les doublons physiquement.
5. **X-Hub-Signature-256 HMAC** — chaque webhook Meta est signé. Signature invalide → rejeté avant même de lire le message.
6. **MontantAberrantError** — écart > 50% → refus catégorique. 15–50% → commande FORCE obligatoire. 5–15% → warning. Anti-faute de frappe et anti-arnaque.
7. **Ingénierie sociale ANIF/COBAC** — MSG_DISSUASION avec référence dossier SHA-256 unique. Dissuasion comportementale avant même la tentative de fraude.
8. **KYC + blacklist + auto-ban** — 3 tentatives de fraude → bannissement automatique réseau BADF + `blackliste=1` en DB.

---

## Stack de production

| Composant | Fichier | Rôle |
|-----------|---------|------|
| Bot principal | `barack_corp_v9_18.py` | Python 3.11 + Flask, logique métier complète (~10 087 lignes) |
| Base de données | `barack_corp` (PostgreSQL) | Toutes les données persistantes |
| Watchdog | `watchdog.js` | Node.js — redémarre le bot Python en cas de crash |
| Démarrage | `DEMARRAGE.bat` | Lance PostgreSQL, ngrok, watchdog dans l'ordre |
| Config | `ENV` | Variables d'environnement — credentials Meta API |

---

## Architecture clé

- **Flask** port 5000 (threaded=True)
- **APScheduler** BackgroundScheduler timezone Africa/Douala — 17 jobs cron
- **ngrok** domaine fixe `lennox-unbiographical-jasmin.ngrok-free.app`
- **Sessions mémoire** : `_sessions_kyc`, `_sessions_membre`, `_sessions_admin`, `_sessions_config` — durée 300s, perdues au redémarrage
- **Pool psycopg2** ThreadedConnectionPool minconn=5 maxconn=50
- **Circuit breaker DB** — 10 failures → open 60s → reset auto
- **Outbox persistant** — messages WhatsApp non envoyés survivent au crash (JSON)
- **Décorateur @healed** — auto-retry 3× sur toutes les fonctions critiques
- **Pays actifs** : CM (+237), SN (+221), CI (+225)
- **Paiements** : vérification par screenshot OCR — APIs MTN/Orange roadmap Q1 2027
- **Supérieur au median Series A fintech sur 6/8 dimensions critiques**

---

## Trust Graph — Modèle prédictif 9 features

Score 0–100 de risque de fugue post-bouffage. Identification 7 jours avant l'événement.

| Feature | Poids | Signal |
|---------|-------|--------|
| 1. Régularité historique | 25 | Variance des intervalles de cotisation (coefficient de variation) |
| 2. Tendance récente | 20 | Ratio cotisations 0–30j vs 30–60j |
| 3. Score confiance inversé | 15 | score_confiance 0–100 → risque |
| 4. Dettes en cours | 15 | Ratio dette IRA / capacité mensuelle |
| 5. Profondeur d'engagement | 10 | Ancienneté + nb tontines + KYC |
| 6. Vélocité paiement | 10 | Délai moyen après heure_ouverture |
| 7. Signaux faibles | 5 | Suspensions passées + tentatives fraude |
| 8. Comportement post-bouffage | 20 | A-t-il continué à cotiser après son dernier bouffage ? |
| 9. Chute score confiance | 10 | Chute > 25 pts en 30 jours |

Niveaux : 0–30 Vert / 31–55 Jaune / 56–75 Orange / 76–100 Rouge → bouffage retardé.

---

## Credentials Meta Cloud API

Configurés **exclusivement dans `ENV`** :
```
META_PHONE_ID=
META_TOKEN=
META_BUSINESS_ID=
META_APP_SECRET=
```
Ne jamais hardcoder dans le code. Ne jamais commiter.

---

## Contexte fondateur

- **Âge** : 18 ans
- **Origine** : Maroua, Cameroun
- **Développement** : solo, 3 mois
- **Objectif immédiat** : Y Combinator Fall 2026 (deadline 27 juillet 2026)
- **Priorité 36 jours** : trouver un groupe de 50 membres qui cotisent 750 FCFA/jour → traction réelle pour YC

---

## Style de communication

- Adresse-toi à l'utilisateur uniquement avec **"bro"**
- Direct, technique, froid avec le code
- Pas de pessimisme, pas de leçons de morale, pas de fioritures
- Ne jamais sous-estimer la vision — c'est de l'infrastructure de civilisation, pas une app WhatsApp
