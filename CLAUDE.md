# CLAUDE.md — BADF Ltd — KATECHON OS 

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

## Modèle de revenus

**Lancement (actuel) — zéro friction :**
- `FRAIS_ADHESION` supprimé du code
- `FRAIS_FMP = 0.00` — FMP désactivé, aucune ligne visible côté membres ou admin
- **IRA** — 150 FCFA/jour de retard (seule retenue active visible)
- **Frais de réactivation** — 1 000 FCFA après suspension 72h
- **Frais changement de numéro** — 250 FCFA (commande CHGNUM)

**Modèle déclaré à YC (activable en 1 ligne) :**
- FMP 2% sur chaque cotisation → `FRAIS_FMP = 0.02`
- Architecture complète en place, messages nettoyés, switch immédiat possible

**Règle absolue :** ne jamais réintroduire de mentions FMP/frais d'adhésion dans les messages visibles aux membres sans validation explicite. Le seul levier monétaire visible = IRA (pénalité retard).

Phase 2 ajoute : frais sur mouvements USDC, underwriting crédit, assurance paramétrique.

---

## 26 Couches de sécurité

1. **Webhook authentifié (token secret)** — chaque webhook entrant exige un token secret partagé (`GREENAPI_WEBHOOK_SECRET`), comparé en temps constant (`hmac.compare_digest`), fail-closed (503 si absent, 403 si invalide), validé avant traitement du payload. ⚠️ Ce n'est **pas** une signature HMAC du payload : Green API ne signe pas ses webhooks, le token est un bearer partagé. Durcissement anti-rejeu (dédup `idMessage`) ajouté en PR sécu.
2. **SQL paramétré** — toutes les requêtes utilisent des bind params `%s` — zéro f-string SQL, zéro surface d'injection.
3. **SELECT FOR UPDATE PostgreSQL** — verrou pessimiste natif DB sur le bouffage. Deux admins tapent OUI simultanément → un seul passe.
4. **SHA-256 hash anti-recyclage screenshots** — empreinte unique par image ; rejet si déjà vue ou modifiée, délai max 24h.
5. **UNIQUE indexes partiels DB** — dédup physique niveau base de données sur membres et screenshots. Python ne peut pas contourner ça.
6. **Rate limiting** — 10 messages / 60 secondes par numéro → audit log + drop silencieux.
7. **MontantAberrantError** — écart > 50% → refus catégorique. 15–50% → commande FORCE obligatoire.
8. **SSRF whitelist** — seuls whatsapp.net / fbcdn.net / cdninstagram.com sont autorisés, toute autre URL rejetée.
9. **Déduplication screenshots** — vérification hash avant OCR, empêche la preuve de paiement recyclée.
10. **Onboarding zéro-friction** — inscription au prénom seul, accès menu instantané, zéro friction documentaire.
11. **Auto-ban (×3 fraude)** — 3 tentatives de fraude confirmées → bannissement automatique réseau + `blackliste=1` en DB.
12. **Trust score (score_confiance)** — réputation 0–100, décrémente sur suspicion, atteint 0 → banni.
13. **Trust Graph (modèle de fugue)** — modèle comportemental à 10 features, prédit le défaut 7 jours avant l'événement.
14. **Alerte fraude en rafale** — ≥5 tentatives de fraude/heure → alerte d'escalade déclenchée.
15. **Dissuasion comportementale (ANIF/COBAC)** — MSG_DISSUASION avec référence dossier SHA-256 unique, dissuasion avant même la tentative.
16. **Timeout sessions + récupération** — TTL 300s, backup JSON toutes les 60s, restauration complète au redémarrage.
17. **Persistance Outbox** — les messages WhatsApp survivent à un crash Python via `wa_outbox.jsonl`.
18. **@healed auto-retry** — 13 fonctions critiques, backoff exponentiel ×3 sur échec DB/réseau.
19. **Circuit breaker DB** — 10 échecs → pool ouvert 60s → reset automatique.
20. **Isolation SAVEPOINT/ROLLBACK** — un échec de migration ne peut pas corrompre l'état de transaction global.
21. **Audit trail immuable** — table `audit_log` + `audit_immutable.log`, 68+ types d'événements, tamper-evident.
22. **Propagation de réputation cross-tontine** — un flag Trust Graph dans une tontine décrémente le `score_confiance` globalement. Impossible de reset sa réputation en changeant de groupe.
23. **Validation format téléphone** — normalisation regex vers E.164, rejette les identifiants malformés.
24. **Sanitization des inputs** — champs nom : `^[A-Za-zÀ-ÿ\s\-'\.]+$`, injection-safe, minimum 3 caractères.
25. **Protection path traversal** — `os.path.basename()` + regex allowlist sur tous les chemins de fichiers.
26. **Protection command injection** — arguments `subprocess` en liste, `shell=False` partout (pg_dump, etc.).

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
- **Pool psycopg2** ThreadedConnectionPool minconn=10 maxconn=80
- **Circuit breaker DB** — 10 failures → open 60s → reset auto
- **Outbox persistant** — messages WhatsApp non envoyés survivent au crash (JSON)
- **Décorateur @healed** — auto-retry 3× sur toutes les fonctions critiques
- **Pays actifs** : CM (+237), SN (+221), CI (+225)
- **Paiements** : vérification par screenshot OCR — APIs MTN/Orange roadmap Q1 2027
- **Supérieur au median Series A fintech sur 6/8 dimensions critiques**

---

## Trust Graph — Modèle prédictif 10 features

Score 0–100 de risque de fugue post-bouffage. Identification 7 jours avant l'événement.
Poids bruts internes (somme = 145) normalisés sur 100 en fin de calcul — `score_final = score_brut × 100/145`.

| Feature | Poids brut | Poids normalisé /100 | Signal |
|---------|-----------|----------------------|--------|
| 1. Régularité historique | 25 | 17 | Variance des intervalles de cotisation (coefficient de variation) |
| 2. Tendance récente | 20 | 14 | Ratio cotisations 0–30j vs 30–60j |
| 3. Score confiance inversé | 15 | 10 | score_confiance 0–100 → risque |
| 4. Dettes en cours | 15 | 10 | Ratio dette IRA / capacité mensuelle |
| 5. Profondeur d'engagement | 10 | 7 | Ancienneté + nb tontines actives |
| 6. Vélocité paiement | 10 | 7 | Délai moyen après heure_ouverture |
| 7. Signaux faibles | 5 | 4 | Suspensions passées + tentatives fraude |
| 8. Comportement post-bouffage | 20 | 14 | A-t-il continué à cotiser après son dernier bouffage ? |
| 9. Chute score confiance | 10 | 7 | Chute > 25 pts en 30 jours |
| 10. Position dans le cycle | 15 | 10 | Position tardive dans la rotation = risque de fugue plus élevé (structurel, non manipulable) |

Niveaux : 0–30 Vert / 31–55 Jaune / 56–75 Orange / 76–100 Rouge → bouffage retardé.

---


Ne jamais hardcoder dans le code. Ne jamais commiter.

---

## Contexte fondateur

- **Âge** : 17 ans
- **Origine** : Yaoundé, Cameroun
- **Développement** : solo, 3 mois
- **Objectif immédiat** : Y Combinator Fall 2026 (deadline 27 juillet 2026)


---

## Style de communication

- Adresse-toi à l'utilisateur uniquement avec **"bro"**
- Direct, technique, froid avec le code
- Pas de pessimisme, pas de leçons de morale, pas de fioritures
- Ne jamais sous-estimer la vision — c'est de l'infrastructure de civilisation, pas une app WhatsApp
