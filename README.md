# KATECHON OS — TontineBot Pro v9.18

**Barack & AI Development Facilities Ltd (BADF Ltd) · Yaoundé, Cameroon 🇨🇲**

`Python 3.11` · `Flask + Waitress` · `PostgreSQL 18` · `OWASP Audited` · `NIST CSF 82%` · `Stress Test 100% — 0 errors`

---

## What is KATECHON OS

Financial coordination operating system for the Global South informal economy. WhatsApp-native, 2G-compatible, built to serve ROSCAs the $800B–$1T/year rotating savings networks (tontines, chit funds, esusu, arisan, consórcio) that coordinate the financial lives of 400–500 million unbanked people across 5 continents. A single Python process orchestrates instant zero-friction onboarding, multi-layer fraud detection, behavioral credit scoring (Trust Graph), automated payout scheduling, screenshot OCR payment verification, and full financial accounting,zero external saas dependency, zero licensing requirement, deployable on a laptop.

WhatsApp is strictly our Trojan horse for hyper-viral, friction-free distribution. It is the tactical layer to aggregate the unbanked masses and map the initial Trust Graph. However, we are built for programmatic resilience. Our 5-layer decoupled architecture allows us to seamlessly swap the front-end presentation layer from WhatsApp to carrier-grade USSD protocol in Africa,native PIX rails in Brazil, or localized instant payment interfaces in Asia, maintaining 100% operational continuity during any black swan event. 

**WhatsApp is the distribution vector. KATECHON OS is the infrastructure underneath.**

---

## Architecture at a Glance

| Layer | Technology | Detail |
|-------|-----------|--------|
| Runtime | Python 3.11 | `katechon/` package (core, security, finance, messaging, handlers), 15-line entry point `barack_corp_v9_18.py` |
| HTTP server | Flask + Waitress | 70 threads, port 5000 |
| Database | PostgreSQL 18 | ThreadedConnectionPool maxconn=80, 17 tables |
| Scheduler | APScheduler | 23 cron/interval jobs, timezone Africa/Douala |
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
| **P0** | Webhook without authentication | Shared-secret token required on every webhook — constant-time comparison, fail-closed (503 if unset, 403 on mismatch) |
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
| PR — Protect | 82% | Constant-time token auth, rate limiting, network blacklist, SHA-256, SSRF whitelist |
| DE — Detect | 93% | 68+ event types in `audit_log`, Trust Graph fugue model, burst fraud alert ≥5/h |
| RS — Respond | 80% | Auto-ban ×3 fraud, automatic 72h suspension, 3 fugue stages + ANIF/COBAC deterrence |
| RC — Recover | 84% | `pg_dump` daily 7-day rotation + integrity check, outbox JSON, session backup, `@healed`, watchdog |

---

## 26 Security Layers — In-Code

| # | Layer | Mechanism |
|---|-------|-----------|
| 1 | **Authenticated Webhook** | Shared-secret token required on every inbound webhook — constant-time comparison (`compare_digest`), fail-closed, validated before payload processing |
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

## Staging — Testing on Telegram Before Members See It

Every change is played on a staging bot reached through Telegram before it is deployed to the WhatsApp members. Staging runs the same `katechon/` code as production, on a separate machine, with its own Telegram bot, its own database and its own tunnel. It never touches a real member or a real franc.

### What is isolated

| | Production | Staging |
|---|---|---|
| Machine | production PC | second PC |
| Channel | WhatsApp (Green API) | Telegram (Bot API) |
| Database | `barack_corp` | `barack_corp_staging` |
| Public URL | ngrok fixed domain | its own ngrok tunnel |
| `KATECHON_TELEGRAM` | absent | `1` |
| Green API variables | set | absent |

The bot listens on port 5000, which is hard-coded in `katechon/core/socle.py`, `watchdog.js` and `DEMARRAGE.bat`. That is why staging needs its own machine rather than a second port next to production.

### Telegram variables

| Variable | Purpose |
|----------|---------|
| `KATECHON_TELEGRAM` | `1` enables the channel and `/webhook/telegram`; without it the route answers 404 |
| `TELEGRAM_BOT_TOKEN` | Token given by @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | 1 to 256 characters from `A-Z a-z 0-9 _ -` only (Telegram rejects anything else); checked on every update and used to sign invitation codes |
| `TELEGRAM_API_BASE` | Defaults to `https://api.telegram.org`; only changed by the tests |
| `TG_ECART_PRIVE`, `TG_ECART_GROUPE`, `TG_DEBIT_GLOBAL` | Pacing, defaults 1 s per private chat, 3 s per group, 25 messages/s overall (Telegram allows about 1/s per chat, 20/min per group, 30/s overall) |
| `OWNER_WA` / `OWNER_IDS` | Your own login on staging: `OWNER_WA=tg:<your id>`, or add it to `OWNER_IDS` |

### One-time setup

1. **Create the bot.** In @BotFather, send `/newbot` and keep the token. Then send `/setprivacy`, choose the bot and select *Disable*. With privacy mode on, a bot only receives commands in groups (`/liste@NomDuBot`) and would never see the payment screenshots posted there. Making the bot an admin of the group has the same effect. A change of privacy mode only applies once the bot is removed from the group and added again.
2. **Create the database.** Create an empty `barack_corp_staging` database. `init_db()` builds the schema at the first start. Never load production data into staging: test with test tontines and your own accounts.
3. **Write the staging `ENV`.** It is the only configuration file of this machine, gitignored like production's. Set `PG_DB=barack_corp_staging`, the three startup secrets (`FLASK_SECRET_KEY`, `DASHBOARD_TOKEN`, `HEALTH_TOKEN`, 16 characters minimum), the Telegram variables above, and your own `tg:` id as owner. Leave every `GREENAPI_*` variable out. To find your Telegram id, send any message to @userinfobot.
4. **Open the tunnel before `DEMARRAGE.bat`.** Run `ngrok http 127.0.0.1:5000` with the staging machine's own ngrok account. `DEMARRAGE.bat` only starts ngrok when none is running, and when it does, it uses production's fixed domain; starting the staging tunnel first keeps it away from that domain. Telegram only calls webhooks on ports 443, 80, 88 or 8443, and the ngrok HTTPS URL uses 443. A free account without a fixed domain gets a new URL at each start; register the webhook again when it changes.
5. **Register the webhook.**

   ```
   curl -s "https://api.telegram.org/bot<TOKEN>/setWebhook" \
        -d "url=https://<staging-domain>/webhook/telegram" \
        -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>" \
        -d "drop_pending_updates=true"         -d "max_connections=1"
   curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
   ```

   `getWebhookInfo` must show the URL, `pending_update_count` close to 0 and no `last_error_message`. `max_connections=1` makes Telegram deliver updates one at a time, in order; the bot also handles each member's enrolment steps one at a time.
6. **Create a test tontine and its admin.** In `psql` on `barack_corp_staging`:

   ```sql
   INSERT INTO tontines (nom, type_tontine, montant_place, capacite_max)
   VALUES ('Staging Njangi', 'Journaliere', 1000, 10) RETURNING id;
   INSERT INTO admins_groupe (tontine_id, whatsapp, nom, numero_collecte)
   VALUES (<id>, 'tg:<your id>', 'Admin staging', '+2376XXXXXXXX');
   ```

   This `admins_groupe` row is what lets your account link a Telegram group to the tontine later: the bot only links a group added by an admin of that tontine or by the owner.

7. **Get the invitation link.** In a shell where `TELEGRAM_WEBHOOK_SECRET` holds the staging value, `python -c "from katechon.messaging.telegram import code_invitation; print(code_invitation(<id>))"` prints the code. The link is `https://t.me/<bot username>?start=<code>`. The code is signed with `TELEGRAM_WEBHOOK_SECRET`, so changing the secret invalidates old links.

### Test cycle for each change

1. Pull the branch on the staging machine, make sure the staging ngrok tunnel is running, then restart through `DEMARRAGE.bat`.
2. Play the scenarios touched by the change, among these:
   - **Enrolment:** open the invitation link and press *Start*. The bot asks for the Mobile Money number with its operator (e.g. `MTN +2376…`), reads it back, and keeps it only after you answer `OUI`. Only then do you enter the payout calendar. Any other answer starts the number over; a closed tontine refuses the enrolment.
   - **Payout number change:** in the member menu, option 9 changes a Telegram member's withdrawal number, with the same `OUI` confirmation. The login itself never changes.
   - **Menu:** send `menu` in private.
   - **Contribution:** send a payment screenshot in private or in the group, then confirm it from the admin menu.
   - **Group:** name a Telegram group exactly like the tontine and add the bot **from the admin account** (the `tg:` id in `admins_groupe`, or the owner). A group added by anyone else is not linked, and the refusal is written to the audit log. Then send `/liste@<bot>` and `/rappel@<bot>`. If Telegram upgrades the group to a supergroup, the new id is picked up automatically.
   - **Late payment:** pay after the deadline and check the IRA amount.
   - **Payout:** at your turn, the stored number is applied at once and the admin receives *NUMÉRO REÇU*.
3. When every touched scenario behaves as expected, merge the PR, then deploy to production. Production keeps `KATECHON_TELEGRAM` absent, so its members only ever see WhatsApp.

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
| 3 | **Number change fee** | 500 FCFA (CHGNUM command) |
| 4 | **IRA (late penalty)** | Not BADF revenue. Penalties go to on-time members: enforcement, not revenue. 15% of the stake after a 5-minute grace, plus 2% per day, capped at 50%, floor 200 FCFA, deducted from the late member's payout and kept for the group's on-time members |

---

## Phase 2 — The USDC Layer

Phase 2 transforms BADF into the silent USDC distribution rail for hundreds of millions of unbanked users who will never know they touch crypto. The Trust Graph becomes the underwriting layer for non-bank credit, parametric insurance, and financial inclusion across five continents. We are not building a Cameroonian fintech. We are building the infrastructure layer that two billion people will eventually need.

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
