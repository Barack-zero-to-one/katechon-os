# KATECHON OS — TontineBot Pro v9.18

**Barack & AI Development Facilities Ltd (BADF Ltd) · Yaoundé, Cameroon 🇨🇲**

`Python 3.11` · `Flask + Waitress` · `PostgreSQL 18` · `OWASP Audited` · `NIST CSF 82%` · `Stress Test 100% — 0 errors`

---

## What is KATECHON OS

Financial coordination protocol for the Global South informal economy. WhatsApp-native, 2G-compatible, built to serve ROSCAs the $800B–$1T/year rotating savings networks (tontines, chit funds, esusu, arisan, consórcio) that coordinate the financial lives of 400–500 million unbanked people across 5 continents. A single Python process orchestrates instant zero-friction onboarding, multi-layer fraud detection, behavioral credit scoring (Trust Graph), automated payout scheduling, screenshot OCR payment verification, and full financial accounting,zero external saas dependency, zero licensing requirement, deployable on a laptop.

WhatsApp is strictly our Trojan horse for hyper-viral, friction-free distribution. It is the tactical layer to aggregate the unbanked masses and map the initial Trust Graph. However, we are built for programmatic resilience. Our 5-layer decoupled architecture allows us to seamlessly swap the front-end presentation layer from WhatsApp to carrier-grade USSD protocol in Africa,native PIX rails in Brazil, or localized instant payment interfaces in Asia, maintaining 100% operational continuity during any black swan event. 

**WhatsApp is the distribution vector. KATECHON OS is the infrastructure underneath.**

---

## Architecture at a Glance

| Layer | Technology | Detail |
|-------|-----------|--------|
| Runtime | Python 3.11 | 10,351 lines, single orchestrator file |
| HTTP server | Flask + Waitress | 70 threads, port 5000 |
| Database | PostgreSQL 18 | ThreadedConnectionPool maxconn=80, 17 tables |
| Scheduler | APScheduler | 20 cron/interval jobs, timezone Africa/Douala |
| Messaging | Green API | WhatsApp Business session, webhook-compatible |
| OCR | Tesseract + OpenCV | Screenshot payment verification with dark mode support |
| Resilience | `@healed` decorator | 13 functions, auto-retry ×3 DB + network |
| Tunnel | ngrok fixed domain | Permanent public URL, no server required |
| Watchdog | Node.js | Auto-restarts Python bot on crash |
| Sessions | 3 in-memory dicts + JSON backup | SESSION_TIMEOUT=300s, saved every 60s |
| Circuit breaker | DB pool guard | 10 failures → open 60s → automatic reset |
| Outbox | Persistent JSON | WhatsApp messages survive bot crash |

---

## Performance Benchmarks — Stress Test v1.0

**Setup:** 8 groups × 100–150 members = **949 simulated members** · 70 concurrent workers · localhost · 6 scenarios

| Scenario | Requests | Success | P50 | P95 | Max |
|----------|----------|---------|-----|-----|-----|
| S1 — Status burst (opening hour) | 949 | **100%** | 168 ms | 2,142 ms | 2,469 ms |
| S2 — Simultaneous contribution screenshots | 949 | **100%** | 150 ms | 2,068 ms | 2,177 ms |
| S3 — Rate limiter burst (1 number × 15 msgs) | 15 | **100%** | 2,050 ms | 2,081 ms | 2,081 ms |
| S4 — Pool saturation (60 conns / maxconn=80) | 60 | **100%** | 2,040 ms | 2,061 ms | 2,067 ms |
| S5 — media_id duplicate anti-recycling | 50 | **100%** | 2,051 ms | 2,067 ms | 2,072 ms |
| S6 — Realistic mixed peak (80% img / 20% txt) | 949 | **100%** | 152 ms | 2,089 ms | 2,324 ms |
| **TOTAL** | **2,972** | **100% · 0 errors** | **160 ms** | **2,087 ms** | **2,469 ms** |

**Throughput:** 232 req/s sustained · **2,972 requests · zero failures · zero timeouts**

> *P95 latency measured on Windows loopback (initial TCP handshake overhead per thread).
> In production with persistent connections: P50 < 100 ms expected.*

---

## Security Audits

### OWASP Top 10 — 11 Vulnerabilities Patched

| Priority | Vulnerability | Fix Applied |
|----------|--------------|-------------|
| **P0** | SQL Injection — fetchall/fetchone | Systematic bind parameters, zero f-string SQL |
| **P0** | Webhook without HMAC validation | X-Hub-Signature-256 validated before any payload parsing |
| **P0** | Race condition double-confirm contribution | `SELECT FOR UPDATE` PostgreSQL — native pessimistic lock |
| **P0** | Screenshot recycling — no hash | SHA-256 + `UNIQUE INDEX` DB + 24h max delay |
| **P0** | SSRF via arbitrary URL | Whitelisted domains only, all other URLs rejected |
| **P1** | Broken Auth — sessions lost on restart | `SESSION_TIMEOUT` 300s + JSON backup every 60s + restoration |
| **P1** | Path traversal on backup filename | `os.path.basename()` + allowed-character regex |
| **P1** | ReDoS in passage list parser | Unicode normalization before match, no catastrophic backtracking |
| **P1** | Command injection pg_dump | `subprocess` list args, `shell=False` everywhere |
| **P1** | MontantAberrantError bypass | ±50% validation mandatory, `FORCE` command required for 15–50% |
| **P1** | Absence of rate limiting | 10 msgs/60s per number → `audit_log` auto-entry + silent drop |

### NIST Cybersecurity Framework 2.0 — Global Score: **82%**

| Function | Score | Key Controls |
|----------|-------|-------------|
| GV — Govern | 65% | Owner / Admin / Member hierarchy, permission gating, blocking debt |
| ID — Identify | 88% | 17-table asset inventory, name-only instant onboarding, `requirements.txt` SBOM |
| PR — Protect | 82% | HMAC-SHA256, rate limiting, network blacklist, SHA-256, SSRF whitelist |
| DE — Detect | 93% | 68+ event types in `audit_log`, Trust Graph fugue model, burst fraud alert ≥5/h |
| RS — Respond | 80% | Auto-ban ×3 fraud, automatic 72h suspension, 3 fugue stages + ANIF/COBAC deterrence |
| RC — Recover | 84% | `pg_dump` daily 7-day rotation + integrity check, outbox JSON, session backup, `@healed`, watchdog |

---

## 26 Security Layers — In-Code

| # | Layer | Mechanism |
|---|-------|-----------|
| 1 | **HMAC-SHA256 Webhook Auth** | Every inbound webhook signed and validated before payload read |
| 2 | **Parameterized SQL** | All queries use `%s` bind params — zero f-string SQL, zero injection surface |
| 3 | **SELECT FOR UPDATE** | Pessimistic PostgreSQL lock on cashout — concurrent double-confirm impossible |
| 4 | **SHA-256 Screenshot Anti-Replay** | Unique fingerprint per image; rejected if seen before or >24h old |
| 5 | **UNIQUE Partial Indexes** | DB-level physical dedup on members, screenshots — Python can't bypass this |
| 6 | **Rate Limiting** | 10 msgs/60s per number → audit log + silent drop |
| 7 | **MontantAberrantError** | >50% deviation → hard reject; 15–50% → FORCE command required |
| 8 | **SSRF Whitelist** | Only whatsapp.net / fbcdn.net / cdninstagram.com — all other URLs rejected |
| 9 | **Screenshot Deduplication** | Hash check before OCR — prevents recycled proof-of-payment |
| 10 | **Zero-Friction Onboarding** | Name-only registration — instant menu access, zero document friction |
| 11 | **Auto-Ban (×3 fraud)** | 3 confirmed fraud attempts → automatic network ban + `blackliste=1` in DB |
| 12 | **Trust Score (score_confiance)** | 0–100 reputation; decrements on suspicion; reaches 0 → banned |
| 13 | **Trust Graph (fugue model)** | 9-feature behavioral model predicts default 7 days before the event |
| 14 | **Burst Fraud Alert** | ≥5 fraud attempts/hour → escalation alert triggered |
| 15 | **Behavioral Deterrence (ANIF/COBAC)** | MSG_DISSUASION with SHA-256 case reference — pre-crime deterrence |
| 16 | **Session Timeout + Recovery** | 300s TTL; JSON backup every 60s; fully restored on restart |
| 17 | **Outbox Persistence** | WhatsApp messages survive Python crash via `wa_outbox.jsonl` |
| 18 | **@healed Auto-Retry** | 13 critical functions — exponential backoff ×3 on DB/network failure |
| 19 | **DB Circuit Breaker** | 10 failures → pool open 60s → automatic reset |
| 20 | **SAVEPOINT/ROLLBACK Isolation** | Migration failures cannot corrupt global transaction state |
| 21 | **Immutable Audit Trail** | `audit_log` table + `audit_immutable.log` — 68+ event types, tamper-evident |
| 22 | **Cross-Tontine Reputation Propagation** | Trust Graph flag in one ROSCA decrements `score_confiance` globally — bad actors can't reset reputation by switching groups |
| 23 | **Phone Format Validation** | Regex normalization to E.164 — rejects malformed identifiers |
| 24 | **Input Sanitization** | Name fields: `^[A-Za-zÀ-ÿ\s\-'\.]+$` — injection-safe, 3-char minimum |
| 25 | **Path Traversal Guard** | `os.path.basename()` + allowlist regex on all file paths |
| 26 | **Command Injection Guard** | `subprocess` list args, `shell=False` everywhere (pg_dump, etc.) |

---

## Trust Graph — Behavioral Credit Model

Risk score 0–100 for post-cashout default prediction. Detection **7 days before the event**.
First behavioral credit bureau for Global South populations never seen by traditional rating agencies.
Raw internal weights (sum = 145) are normalized to 100 at the end of scoring — `final_score = raw_score × 100/145`.

| Feature | Raw weight | Normalized /100 | Signal Measured |
|---------|-----------|------------------|----------------|
| Historical regularity | 25 | 17 | Coefficient of variation of contribution intervals |
| Recent trend | 20 | 14 | Ratio contributions 0–30d vs 30–60d |
| Inverted trust score | 15 | 10 | `score_confiance` 0–100 → risk |
| Outstanding debt | 15 | 10 | IRA debt ratio / estimated monthly capacity |
| Engagement depth | 10 | 7 | Seniority + number of active tontines |
| Payment velocity | 10 | 7 | Average delay after `heure_ouverture` |
| Weak signals | 5 | 4 | Past suspensions + fraud attempts |
| Post-cashout behavior | 20 | 14 | Continued contributing after receiving cashout? |
| Trust score drop | 10 | 7 | Drop >25 pts over 30 rolling days |
| Cycle position | 15 | 10 | Late position in rotation = higher flight risk (structural, non-manipulable) |

**Risk levels:** 🟢 0–30 Green · 🟡 31–55 Yellow · 🟠 56–75 Orange · 🔴 76–100 Red → admin alerted with full evidence, no automatic block

---

## Global South ROSCA Ecosystem

The ROSCA (Rotating Savings and Credit Association) is the most widespread informal financial instrument in the developing world, coordinating the financial lives of 400–500 million unbanked people across 5 continents under dozens of local names, each embedded in the cultural fabric of its region.

In West and Central Africa, the *tontine* (Cameroon, Senegal, Ivory Coast, Congo, Gabon) and the *njangi* (Anglophone Cameroon) are the social contract of the working class. This is our home market and ground zero for KATECHON OS: highest density of trust networks, zero existing digital coordination infrastructure, and maximum willingness to organize via WhatsApp. KATECHON OS deploys here as **Tontine OS** and **Njangi OS**.

In Nigeria and the wider West African diaspora, the *ajo* (Yoruba), *esusu* (Igbo), and *adashi* (Hausa) operate at massive scale, inside Lagos neighbourhoods and inside diaspora communities across London, Houston, and Toronto. Nigeria's informal economy is the largest in Africa by volume. KATECHON OS deploys as **Ajo OS**.

In Ghana, the *susu* is a regulated financial practice; susu collectors hold a formal profession recognized by the Bank of Ghana. KATECHON OS digitizes the coordination layer without displacing the collector role, deploying as **Susu OS**.

In East Africa, the *chama* (Kenya, Tanzania, Uganda) manages an estimated KES 300 billion annually. M-Pesa provides the payment rail but no ROSCA-native coordination infrastructure exists above it. KATECHON OS fills that gap as **Chama OS**.

In South Asia, India's *chit fund* sector has been legally regulated since the Chit Funds Act of 1982 and engages 50 million active participants today, processing $5 billion annually through roughly 350,000 foremen. The coordination overhead alone is the addressable market. KATECHON OS enters as **Chit OS**.

In Southeast Asia, the *arisan* in Indonesia is not a niche instrument; approximately 87% of Indonesian households have participated in one. It is woven into every socioeconomic layer, from village cooperatives to corporate teams, yet no digital coordination infrastructure reaches the grassroots. KATECHON OS deploys as **Arisan OS**.

In the Philippines, the *paluwagan* runs inside OFW (Overseas Filipino Worker) remittance networks and barangay communities, a dual domestic and diaspora surface that spans Manila, Riyadh, and Dubai. KATECHON OS deploys as **Paluwagan OS**.

In Latin America, Brazil's *consórcio* sector is legally regulated, managed by approximately 300 licensed administrators, and processes $40 billion annually. Peru's *pandero* and Mexico's *tanda* extend the same model into the Andean economies and into the Mexican-American communities of the US Southwest. KATECHON OS deploys as **Consórcio OS**.

One protocol. Every informal economy. No competitor has built cross-ROSCA infrastructure at this scope.

**TAM:** $20 trillion (global informal economy) · 400–500M people · $800B–$1T/year via ROSCAs · Zero existing digital infrastructure on this market

---

## Revenue Streams

| # | Source | Model |
|---|--------|-------|
| 1 | **FMP 2%** | Automatically deducted from each confirmed contribution |
| 2 | **Reactivation fee** | 1,000 FCFA after 72h suspension |
| 3 | **Number change fee** | 250 FCFA (CHGNUM command) |

> **IRA is not a revenue stream.** It is a behavioral-regulation mechanism — a late penalty indexed on the stake (Schelling cliff + daily accrual, capped at 50%) whose sole purpose is to make lateness irrational. Collected IRA is redistributed to punctual members as a rotation-priority reward, not booked as company revenue.

---

## Phase 2 — The USDC Layer: Inflation Shield + Yield Engine

Every ROSCA has a structural vulnerability that no coordination software has ever solved: the pot sits idle in fiat currency between the day contributions are collected and the day the winner receives the cashout. In economies where inflation runs at 20–30% annually, this idle period silently erodes the real value of every member's contribution. In high-volatility environments like Nigeria (NGN depreciated 70% in 2023–2024) or Ghana (GHS lost over 50% against the dollar in 2022), the problem is existential.

Phase 2 solves this by making USDC the invisible settlement layer underneath every ROSCA managed by KATECHON OS.

When a contribution is confirmed, KATECHON OS automatically converts the local currency amount to USDC at the interbank rate. The pot is held in USDC, not in fiat. When the cashout window opens, the USDC balance is converted back to local currency and delivered to the winner. Members experience zero friction: they send FCFA or NGN or GHS as they always have, via mobile money or SwitchN. The USD peg works silently underneath.

The yield layer activates the moment the pot exceeds a protocol-defined threshold. Idle USDC is deployed into institutional-grade yield protocols — Circle's yield products, Aave, or equivalent DeFi infrastructure — generating passive returns on the float. This yield is distributed proportionally to contributing members at the end of each ROSCA cycle, effectively acting as a rebate that reduces the net cost of the FMP 2% fee. In a 20-member tontine running for 20 days, even a conservative 5% annualized yield on the average float produces a meaningful per-member return that exceeds what any local savings account would offer to this population.

The Trust Graph becomes the credit layer. After 12–24 months of behavioral data, a member with a Green Trust Score (0–30) holds a cryptographically-verifiable track record that no traditional bank has ever assessed. KATECHON OS uses this as on-chain collateral to underwrite micro-credit at USDC rates. A member who has contributed on time for 18 months across three tontines can access a USDC micro-loan priced proportionally to their Trust Graph score, without a bank account, without a credit history, without collateral beyond their own demonstrated behavior.

Parametric insurance closes the loop. Smart contract oracles monitor commodity price indices, rainfall data, and regional economic shock indicators. When a trigger condition is met, a drought index crossing a threshold or a commodity price falling below a floor, affected members receive automatic USDC payouts. No claims process. No adjuster. No paperwork. Settlement in seconds, on-chain, auditable by any party.

Members never need to know what USDC is. They participate in their tontine as they always have. KATECHON OS handles the conversion, the yield deployment, the credit scoring, and the insurance settlement transparently. The result is a financial product that outperforms anything a retail bank in Cameroon, Nigeria, or Ghana has ever offered to this population, at zero marginal infrastructure cost.

---

## Revenue Streams (Phase 2)

| # | Source | Model |
|---|--------|-------|
| 1 | **USDC conversion spread** | Basis-point margin on each fiat ↔ USDC conversion |
| 2 | **Yield share** | Protocol retains a portion of DeFi yield generated on idle ROSCA float |
| 3 | **Credit underwriting** | Origination fee on USDC micro-loans priced by Trust Graph score |
| 4 | **Parametric insurance premiums** | Subscription-based coverage per ROSCA cycle |

---

## Git History

```
44ce7d8  feat: v9.18 — OpenCV OCR pipeline + regex ultra-tolérante + CI GitHub Actions
6202390  refactor: message intro tontine en cours — version percutante
7e8dddd  refactor: message intro tontine en cours — version courte et percutante
c0a93fa  feat: message intro tontine en cours — dissuasion comportementale complète
11b349e  feat: relevé FMP envoyé post-bouffage +10min au lieu de 20h fixe
323842b  fix: _reclasser_en_dernier retourne positions avant/après — DM membre précis
```

---

## 5 Structural Moats

1. **Trust Graph** — the strongest moat. 24–36 months of irreplicable behavioral data. First behavioral credit bureau for Global South populations that traditional rating agencies have never seen. Builds only with time — impossible to buy or copy. In Phase 2, it becomes a compounding flywheel: more members generate more behavioral data, which enables better USDC credit pricing, which produces more competitive yield, which attracts more members. The same self-reinforcing dynamic Stripe built on fraud data — applied to the 400M-person unbanked segment.

2. **2G-native** — absolute technical barrier. Zero tech competitor capable of serving this market. Silicon Valley cannot architect below 4G. By design here.

3. **Outside licensing** — positioned architecturally outside Payment Institution licensing. Potential competitors spend 2–3 years on COBAC/BCEAO approvals.

4. **Asymmetric economics** — CAC = 0 (existing WhatsApp groups). Infrastructure = laptop + ngrok. A well-funded competitor copying this spends 100× more for the same result.

5. **Absolute founder-market fit** — founder born into the problem, Yaoundé, Cameroon. A California VC cannot send a team to understand this market in 6 months. Not learned in an MBA.

---

*BADF Ltd · Yaoundé, Cameroon · 2026*
