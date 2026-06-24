# KATECHON OS — TontineBot Pro v9.18

**Barack & AI Development Facilities Ltd (BADF Ltd) · Cameroun 🇨🇲**

`Python 3.11` · `Flask + Waitress` · `PostgreSQL 18` · `OWASP Audited` · `NIST CSF 82%` · `Stress Test 100% — 0 erreur`

---

## What is KATECHON OS

Financial coordination protocol for the Global South informal economy. WhatsApp-native, 2G-compatible, built to serve ROSCAs — the $800B–$1T/year rotating savings networks (tontines, chit funds, esusu, arisan, consórcio) that coordinate the financial lives of 400–500 million unbanked people across 5 continents. A single Python process orchestrates KYC onboarding, multi-layer fraud detection, behavioral credit scoring (Trust Graph), automated payout scheduling, screenshot OCR payment verification, and full financial accounting — zero external fintech dependency, zero licensing requirement, deployable on a laptop.

**WhatsApp is the Trojan horse. KATECHON OS is the infrastructure underneath.**

---

## Architecture at a Glance

| Layer | Technology | Detail |
|-------|-----------|--------|
| Runtime | Python 3.11 | 10 351 lignes, 1 fichier orchestrateur |
| HTTP server | Flask + Waitress | 70 threads, port 5000 |
| Database | PostgreSQL 18 | ThreadedConnectionPool maxconn=80, 17 tables |
| Scheduler | APScheduler | 20 jobs cron/interval, timezone Africa/Douala |
| Messaging | Meta WhatsApp Cloud API v21 | HMAC-SHA256 webhook validation |
| OCR | Tesseract + Pillow | Vérification screenshot paiement |
| Resilience | `@healed` decorator | 13 fonctions, auto-retry ×3 DB + réseau |
| Tunnel | ngrok domaine fixe | URL publique permanente sans serveur |
| Watchdog | Node.js | Auto-restart bot Python en cas de crash |
| Sessions | 3 dicts mémoire + JSON backup | SESSION_TIMEOUT=300s, sauvegarde toutes 60s |
| Circuit breaker | DB pool guard | 10 failures → open 60s → reset automatique |
| Outbox | JSON persistant | Messages WhatsApp survivent au crash du bot |

---

## Performance Benchmarks — Stress Test v1.0

**Setup :** 8 groupes × 100–150 membres = **949 membres simulés** · 70 workers concurrents · localhost · 6 scénarios

| Scénario | Requêtes | Succès | P50 | P95 | Max |
|----------|----------|--------|-----|-----|-----|
| S1 — Rafale 'statut' (heure ouverture) | 949 | **100%** | 168 ms | 2 142 ms | 2 469 ms |
| S2 — Screenshots cotisation simultanés | 949 | **100%** | 150 ms | 2 068 ms | 2 177 ms |
| S3 — Rate limiter burst (1 num × 15 msgs) | 15 | **100%** | 2 050 ms | 2 081 ms | 2 081 ms |
| S4 — Pool saturation (60 conns / maxconn=80) | 60 | **100%** | 2 040 ms | 2 061 ms | 2 067 ms |
| S5 — Doublons media_id anti-recyclage | 50 | **100%** | 2 051 ms | 2 067 ms | 2 072 ms |
| S6 — Pic mixte réaliste (80% img / 20% txt) | 949 | **100%** | 152 ms | 2 089 ms | 2 324 ms |
| **TOTAL** | **2 972** | **100% · 0 erreur** | **160 ms** | **2 087 ms** | **2 469 ms** |

**Throughput :** 232 req/s soutenu · **2 972 requêtes · zéro échec · zéro timeout**

> *Latence P95 mesurée sur loopback Windows (overhead TCP handshake initial par thread).
> En production avec connexions persistantes : P50 < 100 ms attendu.*

---

## Security Audits

### OWASP Top 10 — 11 vulnérabilités corrigées

| Priorité | Vulnérabilité | Fix appliqué |
|----------|--------------|--------------|
| **P0** | SQL Injection — fetchall/fetchone | Paramètres bindés systématiques, jamais de f-string SQL |
| **P0** | Webhook sans validation HMAC | X-Hub-Signature-256 validé avant tout parsing payload |
| **P0** | Race condition double-confirm cotisation | `SELECT FOR UPDATE` PostgreSQL — verrou pessimiste natif |
| **P0** | Screenshot recycling — zéro hash | SHA-256 + `UNIQUE INDEX` DB + délai max 24h |
| **P0** | SSRF via URL arbitraire | Whitelist domaines Meta uniquement, toute autre URL rejetée |
| **P1** | Broken Auth — sessions perdues au restart | `SESSION_TIMEOUT` 300s + backup JSON toutes 60s + restauration |
| **P1** | Path traversal sur filename backup | `os.path.basename()` + regex caractères autorisés uniquement |
| **P1** | ReDoS dans parser liste passage | Normalisation Unicode avant match, regex sans backtracking catastrophique |
| **P1** | Command injection pg_dump | `subprocess` list args, `shell=False` partout |
| **P1** | Bypass MontantAberrantError | Validation ±50% obligatoire, commande `FORCE` requise entre 15–50% |
| **P1** | Absence rate limiting | 10 msgs/60s par numéro → `audit_log` automatique + drop silencieux |

### NIST Cybersecurity Framework 2.0 — Score global : **82 %**

| Fonction | Score | Contrôles principaux |
|----------|-------|---------------------|
| GV — Govern | 65 % | Hiérarchie Owner / Admin / Membre, permission gating, dette bloquante |
| ID — Identify | 88 % | 17 tables asset inventory, KYC 5-step, `requirements.txt` SBOM |
| PR — Protect | 82 % | HMAC-SHA256, rate limiting, blacklist réseau, SHA-256, whitelist SSRF |
| DE — Detect | 93 % | 68+ types événements `audit_log`, Trust Graph fugue model, alerte burst fraude ≥5/h |
| RS — Respond | 80 % | Auto-ban ×3 fraudes, suspension auto 72h, 3 stades fugue + MSG_DISSUASION ANIF/COBAC |
| RC — Recover | 84 % | `pg_dump` daily rotation 7j + vérification intégrité, outbox JSON, sessions backup, `@healed`, watchdog |

---

## 8 Security Layers — In-Code

| # | Couche | Mécanisme |
|---|--------|-----------|
| 1 | **SHA-256 anti-recyclage** | Empreinte unique par screenshot, rejet immédiat si déjà vu ou >24h |
| 2 | **SELECT FOR UPDATE** | Verrou pessimiste PostgreSQL sur `confirmer_cotisation` — double-confirm concurrent impossible |
| 3 | **Rate limiting** | 10 msgs/60s par numéro → audit log automatique + drop silencieux |
| 4 | **UNIQUE partial indexes DB** | Filet niveau base — refuse doublons même si Python contournait tout le reste |
| 5 | **X-Hub-Signature-256 HMAC** | Chaque webhook Meta signé et validé avant lecture du corps |
| 6 | **MontantAberrantError** | Écart >50% → refus catégorique ; 15–50% → commande FORCE obligatoire |
| 7 | **MSG_DISSUASION ANIF/COBAC** | Référence dossier SHA-256 unique par fraude — dissuasion comportementale avant tentative |
| 8 | **KYC + blacklist + auto-ban** | 3 tentatives fraude → bannissement réseau BADF + `blackliste=1` en DB |

---

## Trust Graph — Behavioral Credit Model

Score de risque 0–100 de fugue post-bouffage. Détection **7 jours avant l'événement**.
Premier credit bureau comportemental du Global South sur des populations jamais vues par les agences de notation classiques.

| Feature | Poids | Signal mesuré |
|---------|-------|---------------|
| Régularité historique | 25 % | Coefficient de variation des intervalles de cotisation |
| Tendance récente | 20 % | Ratio cotisations 0–30j vs 30–60j |
| Score confiance inversé | 15 % | `score_confiance` 0–100 → risque |
| Dettes en cours | 15 % | Ratio dette IRA / capacité mensuelle estimée |
| Profondeur d'engagement | 10 % | Ancienneté + nombre de tontines + complétude KYC |
| Vélocité de paiement | 10 % | Délai moyen après `heure_ouverture` |
| Signaux faibles | 5 % | Suspensions passées + tentatives de fraude |
| Comportement post-bouffage | 20 % | Continue à cotiser après avoir reçu son bouffage ? |
| Chute score confiance | 10 % | Chute >25 pts sur 30 jours glissants |

**Niveaux de risque :** 🟢 0–30 Vert · 🟡 31–55 Jaune · 🟠 56–75 Orange · 🔴 76–100 Rouge → bouffage retardé automatiquement

---

## Codebase Stats

| Métrique | Valeur |
|----------|--------|
| Lignes Python | **10 351** |
| Tables PostgreSQL | **17** |
| Jobs APScheduler | **20** (cron + interval) |
| Types d'événements audit | **68+** |
| Fonctions `@healed` auto-retry | **13** |
| Endpoints Flask | **4** |
| Commandes utilisateur reconnues | **35+** |
| Threads Waitress | **70** |
| Pool DB maxconn | **80** |
| Sessions timeout | **300s** |
| Marchés cibles | **8** |
| Sources de revenus | **5** |
| Durée développement | **3 mois · solo** |

---

## Markets

| Pays / Région | Pratique locale | Nom produit |
|---------------|----------------|-------------|
| Cameroun · Sénégal · Côte d'Ivoire | Tontine | **Tontine OS** |
| Nigeria | Ajo / Esusu | **Ajo OS** |
| Ghana | Susu | **Susu OS** |
| Kenya · Tanzanie | Chama | **Chama OS** |
| Brésil | Consórcio | **Consórcio OS** |
| Inde | Chit Fund | **Chit OS** |
| Indonésie | Arisan | **Arisan OS** |
| Philippines | Paluwagan | **Paluwagan OS** |

**TAM :** $20 000 milliards (économie informelle globale) · 400–500 M personnes · $800B–$1T/an via ROSCAs · Zéro infrastructure digitale existante sur ce marché

---

## Revenue Streams

| # | Source | Modèle |
|---|--------|--------|
| 1 | **Frais d'adhésion** | 1 000 FCFA · one-time · valable à vie réseau BADF |
| 2 | **FMP 2%** | Prélevé automatiquement sur chaque cotisation confirmée |
| 3 | **IRA** | 150 FCFA/jour de retard · cumulé et déduit du bouffage |
| 4 | **Frais de réactivation** | 1 000 FCFA après suspension 72h |
| 5 | **Frais changement numéro** | 250 FCFA (commande `CHGNUM`) |

**Phase 2 :** frais sur mouvements USDC · underwriting crédit non-bancaire · assurance paramétrique

---

## Git History

```
ec79c9d  test: stress_test — keep-alive, 232 req/s, 0 erreur sur 2 972 req
cc7c44a  fix: NameError healed — déplacer définition avant premier usage (@5826)
6cd8a24  fix: parser_liste_passage — normalisation robuste artefacts WhatsApp
c7c1e0b  security: NIST CSF 2.0 — 3 gaps fermés (ID + DE + RC)  →  78% → 82%
b4c8188  security: OWASP audit — 11 vulnérabilités corrigées + H6 sessions backup
90915be  feat: TontineBot Pro v9.18 — KATECHON OS production release
```

---

## 5 Structural Moats

1. **Trust Graph** — 24–36 mois de données comportementales irréplicables. Impossible à racheter ou copier.
2. **2G-native** — zéro concurrent tech capable de servir ce marché. Silicon Valley ne peut pas descendre sous 4G architecturalement.
3. **Hors licensing** — positionné hors licensing Payment Institution par design. Les concurrents passent 2–3 ans en régulation COBAC/BCEAO.
4. **Économie asymétrique** — CAC = 0 (groupes WhatsApp existants). Infra = laptop + ngrok. Un concurrent bien financé dépense 100× plus pour le même résultat.
5. **Founder-market fit absolu** — fondateur né dans le problème. Un VC californien ne peut pas envoyer une équipe comprendre ce marché en 6 mois.

---

*BADF Ltd · Maroua, Cameroun · 2026*
