/**
 * ════════════════════════════════════════════════════════════════════════
 * WATCHDOG v2 — TontineBot Pro v9.18 — BADF Ltd
 *
 * Architecture : Python Flask + Meta WhatsApp Cloud API (PAS Baileys)
 *
 * Responsabilités :
 *   1. Démarrage du processus Python (barack_corp_v9_18.py)
 *   2. Health check toutes les 15s sur http://localhost:5000/health
 *   3. Redémarrage automatique avec backoff exponentiel si crash
 *   4. Rejeu de la queue de messages persistante en cas de panne
 *   5. Logs structurés dans logs/watchdog.log
 *   6. Arrêt propre sur SIGINT/SIGTERM
 *
 * Différences v1 → v2 :
 *   - Suppression complète du processus Baileys (Meta API gérée par Python)
 *   - Suppression backup/restore session Baileys
 *   - Health check unique (Python uniquement)
 *   - Logs structurés JSON pour observabilité
 *   - Codes 200 et 503 acceptés (503 = service vivant mais dégradé)
 * ════════════════════════════════════════════════════════════════════════
 */

const { spawn } = require("child_process");
const fs        = require("fs");
const path      = require("path");
const http      = require("http");

// ── Configuration ─────────────────────────────────────────────────────────
const PYTHON_CMD        = "python";
const PYTHON_SCRIPT     = "barack_corp_v9_18.py";
const HEALTH_URL        = "http://localhost:5000/health";
const WEBHOOK_URL       = "http://localhost:5000/webhook/whatsapp";
const QUEUE_FILE        = "logs/message_queue.json";
const WATCHDOG_LOG      = "logs/watchdog.log";
const HEALTH_INTERVAL   = 15000;    // 15s
const INITIAL_BACKOFF   = 5000;     // 5s
const MAX_BACKOFF       = 60000;    // 60s max
const QUEUE_MSG_TTL     = 86400000; // 24h
const MAX_503_BEFORE_KILL = 3;      // 3 fois 503 consécutif → redémarrage

// ── État ──────────────────────────────────────────────────────────────────
let pythonProcess  = null;
let pythonBackoff  = INITIAL_BACKOFF;
let pythonOk       = false;
let count503       = 0;
let arret          = false;

// ── Setup dossiers ────────────────────────────────────────────────────────
["logs", "backups"].forEach(d => {
    if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
});
if (!fs.existsSync(QUEUE_FILE)) fs.writeFileSync(QUEUE_FILE, "[]");

// ══════════════════════════════════════════════════════════════════════════
// LOGGING STRUCTURÉ
// ══════════════════════════════════════════════════════════════════════════

function log(level, event, details = {}) {
    const entry = {
        ts:      new Date().toISOString(),
        level:   level,
        event:   event,
        ...details
    };
    const line = JSON.stringify(entry);
    console.log(line);
    try {
        fs.appendFileSync(WATCHDOG_LOG, line + "\n");
    } catch (e) {
        // Si on ne peut pas écrire dans le log, on continue sans crasher
    }
}

// ══════════════════════════════════════════════════════════════════════════
// DÉMARRAGE DU PROCESSUS PYTHON
// ══════════════════════════════════════════════════════════════════════════

function demarrerPython() {
    if (arret) return;

    log("INFO", "python_start_attempt", { script: PYTHON_SCRIPT });

    pythonProcess = spawn(PYTHON_CMD, [PYTHON_SCRIPT], {
        stdio: ["ignore", "pipe", "pipe"],
        env:   process.env,
    });

    pythonProcess.stdout.on("data", d => {
        process.stdout.write(`[PY] ${d}`);
    });

    pythonProcess.stderr.on("data", d => {
        process.stderr.write(`[PY-ERR] ${d}`);
    });

    pythonProcess.on("exit", (code, signal) => {
        pythonOk = false;
        log("WARN", "python_exited", { code, signal, backoff_ms: pythonBackoff });

        if (arret) return;

        setTimeout(() => {
            pythonBackoff = Math.min(pythonBackoff * 2, MAX_BACKOFF);
            demarrerPython();
        }, pythonBackoff);
    });

    pythonProcess.on("error", e => {
        log("ERROR", "python_spawn_error", { message: e.message });
    });
}

// ══════════════════════════════════════════════════════════════════════════
// HEALTH CHECK
// ══════════════════════════════════════════════════════════════════════════

function healthCheck(url) {
    return new Promise(resolve => {
        const req = http.get(url, { timeout: 5000 }, res => {
            resolve({
                ok:         res.statusCode === 200,
                degraded:   res.statusCode === 503,
                statusCode: res.statusCode
            });
        });
        req.on("error",   () => resolve({ ok: false, degraded: false, statusCode: 0 }));
        req.on("timeout", () => {
            req.destroy();
            resolve({ ok: false, degraded: false, statusCode: -1 });
        });
    });
}

async function surveillerProcessus() {
    const result = await healthCheck(HEALTH_URL);

    if (result.ok) {
        // Service en bon état
        pythonOk = true;
        pythonBackoff = INITIAL_BACKOFF;  // Reset backoff
        count503 = 0;

        // Rejouer la queue si elle a des messages
        await rejouerQueue();

    } else if (result.degraded) {
        // 503 = service vivant mais dégradé (DB down, etc.)
        count503 += 1;
        pythonOk = false;
        log("WARN", "python_degraded_503", { count: count503, max: MAX_503_BEFORE_KILL });

        if (count503 >= MAX_503_BEFORE_KILL) {
            log("ERROR", "python_killed_due_to_503", { reason: "trop de 503 consécutifs" });
            if (pythonProcess) pythonProcess.kill();
            count503 = 0;
        }

    } else {
        // Service ne répond pas du tout
        pythonOk = false;
        log("WARN", "python_unresponsive", { statusCode: result.statusCode });
        if (pythonProcess) pythonProcess.kill();
    }
}

// ══════════════════════════════════════════════════════════════════════════
// MESSAGE QUEUE PERSISTANTE
// ══════════════════════════════════════════════════════════════════════════

function sauvegarderMessage(msg) {
    try {
        const queue = JSON.parse(fs.readFileSync(QUEUE_FILE, "utf8") || "[]");
        queue.push({ ...msg, ts: Date.now() });
        fs.writeFileSync(QUEUE_FILE, JSON.stringify(queue, null, 2));
        log("INFO", "queue_message_added", { queue_size: queue.length });
    } catch (e) {
        log("ERROR", "queue_save_error", { message: e.message });
    }
}

async function rejouerQueue() {
    try {
        const queue = JSON.parse(fs.readFileSync(QUEUE_FILE, "utf8") || "[]");
        if (queue.length === 0) return;

        log("INFO", "queue_replay_start", { count: queue.length });
        const restants = [];
        let rejoues   = 0;
        let abandones = 0;

        for (const msg of queue) {
            // Messages plus vieux de 24h → abandonnés
            if (Date.now() - msg.ts > QUEUE_MSG_TTL) {
                abandones += 1;
                continue;
            }

            const ok = await envoyerVersPython(msg);
            if (ok) {
                rejoues += 1;
                await new Promise(r => setTimeout(r, 200));  // 200ms entre envois (throttling)
            } else {
                restants.push(msg);
            }
        }

        fs.writeFileSync(QUEUE_FILE, JSON.stringify(restants, null, 2));
        log("INFO", "queue_replay_done", {
            rejoues:   rejoues,
            restants:  restants.length,
            abandones: abandones
        });

    } catch (e) {
        log("ERROR", "queue_replay_error", { message: e.message });
    }
}

function envoyerVersPython(data) {
    return new Promise(resolve => {
        const body = JSON.stringify(data);
        const url  = new URL(WEBHOOK_URL);

        const options = {
            hostname: url.hostname,
            port:     url.port,
            path:     url.pathname,
            method:   "POST",
            headers:  {
                "Content-Type":   "application/json",
                "Content-Length": Buffer.byteLength(body),
            },
            timeout: 10000,
        };

        const req = http.request(options, res => {
            resolve(res.statusCode >= 200 && res.statusCode < 300);
        });
        req.on("error",   () => resolve(false));
        req.on("timeout", () => { req.destroy(); resolve(false); });
        req.write(body);
        req.end();
    });
}

// ══════════════════════════════════════════════════════════════════════════
// DÉMARRAGE PRINCIPAL
// ══════════════════════════════════════════════════════════════════════════

log("INFO", "watchdog_start", {
    version: "v2",
    stack:   "Python + Meta Cloud API",
    config:  { HEALTH_URL, HEALTH_INTERVAL, MAX_BACKOFF }
});

console.log("━".repeat(70));
console.log("🛡️  WATCHDOG v2 — TontineBot Pro v9.18 — BADF Ltd");
console.log("    Stack : Python + Meta WhatsApp Cloud API");
console.log("━".repeat(70));

// Démarrer Python
demarrerPython();

// Health check toutes les 15s
setInterval(surveillerProcessus, HEALTH_INTERVAL);

// ══════════════════════════════════════════════════════════════════════════
// ARRÊT PROPRE
// ══════════════════════════════════════════════════════════════════════════

async function arreterProprement() {
    log("INFO", "watchdog_shutdown_start", {});
    arret = true;

    if (pythonProcess) {
        pythonProcess.kill("SIGTERM");
        log("INFO", "python_sigterm_sent", {});

        // Laisser 5s pour shutdown propre, sinon SIGKILL
        await new Promise(r => setTimeout(r, 5000));

        if (pythonProcess && !pythonProcess.killed) {
            pythonProcess.kill("SIGKILL");
            log("WARN", "python_sigkill_sent", { reason: "shutdown propre échoué" });
        }
    }

    log("INFO", "watchdog_shutdown_done", {});
    process.exit(0);
}

process.on("SIGINT",  arreterProprement);
process.on("SIGTERM", arreterProprement);
process.on("uncaughtException", e => {
    log("FATAL", "uncaught_exception", { message: e.message, stack: e.stack });
});
