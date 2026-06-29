#!/usr/bin/env python3
"""
KATECHON OS — Stress Test v2.0
Simule 8 groupes de tontine × 100-150 membres qui envoient
des screenshots de cotisation en rafale.

Usage :
    python stress_test.py
    python stress_test.py --url http://localhost:5000
    python stress_test.py --workers 200 --groupes 8

Pré-requis : bot en cours d'exécution (python barack_corp_v9_18.py)
"""

import argparse
import base64
import hashlib
import json
import os
import random
import statistics
import struct
import sys
import time
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import requests
except ImportError:
    print("❌ Installe requests : pip install requests")
    sys.exit(1)


def _charger_env(chemin: str = "ENV") -> dict:
    """Charge les variables depuis le fichier ENV (format KEY=VALUE)."""
    vars_ = {}
    if not os.path.exists(chemin):
        return vars_
    with open(chemin, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            vars_[k.strip()] = v.strip()
    return vars_

_ENV = _charger_env()
_GREENAPI_SECRET   = _ENV.get("GREENAPI_WEBHOOK_SECRET",   os.getenv("GREENAPI_WEBHOOK_SECRET",   ""))
_GREENAPI_INSTANCE = _ENV.get("GREENAPI_INSTANCE_ID",      os.getenv("GREENAPI_INSTANCE_ID",      "0"))

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_URL     = "http://localhost:5000"
NB_GROUPES      = 8
MEMBRES_MIN     = 100
MEMBRES_MAX     = 150
MONTANT_FCFA    = 750
TIMEOUT_REQ     = 30       # secondes par requête
MAX_WORKERS_DEF = 200      # threads simultanés max

NOMS_GROUPES = [
    "Alpha", "Beta", "Gamma", "Delta",
    "Epsilon", "Zeta", "Eta", "Theta"
]

# ══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION PNG SYNTHÉTIQUE
# Génère un PNG 1×1 RGB minimal et valide — hash unique par seed
# ══════════════════════════════════════════════════════════════════════════════

def make_png(seed: int = 0) -> bytes:
    r = (seed * 37 + 100) % 256
    g = (seed * 73 + 150) % 256
    b = (seed * 113 + 200) % 256

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + tag + data + crc

    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00" + bytes([r, g, b]), 9))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend

def png_b64(seed: int) -> str:
    return base64.b64encode(make_png(seed)).decode()

# ══════════════════════════════════════════════════════════════════════════════
# MODÈLES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Membre:
    wa: str
    nom: str
    groupe_idx: int
    tontine_nom: str
    seed: int

@dataclass
class ResultatReq:
    wa: str
    scenario: str
    duree_ms: float
    status_code: int
    erreur: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200

@dataclass
class Rapport:
    scenario: str
    nb_total: int = 0
    nb_succes: int = 0
    nb_erreurs: int = 0
    durees_ms: List[float] = field(default_factory=list)
    erreurs_detail: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def ajouter(self, res: ResultatReq):
        self.durees_ms.append(res.duree_ms)
        if res.ok:
            self.nb_succes += 1
        else:
            self.nb_erreurs += 1
            cle = res.erreur or f"HTTP_{res.status_code}"
            self.erreurs_detail[cle] += 1

    @property
    def taux_succes(self) -> float:
        return 100 * self.nb_succes / max(1, self.nb_total)

    def _percentile(self, p: float) -> float:
        if not self.durees_ms:
            return 0.0
        s = sorted(self.durees_ms)
        idx = int(len(s) * p / 100)
        return s[min(idx, len(s) - 1)]

    @property
    def p50(self) -> float:
        return statistics.median(self.durees_ms) if self.durees_ms else 0

    @property
    def p95(self) -> float:
        return self._percentile(95)

    @property
    def p99(self) -> float:
        return self._percentile(99)

    @property
    def max_ms(self) -> float:
        return max(self.durees_ms) if self.durees_ms else 0

    def afficher(self, verbose: bool = True):
        ok_sym   = "✅" if self.taux_succes >= 95 else "⚠️ " if self.taux_succes >= 80 else "❌"
        lat_sym  = "✅" if self.p95 < 2000 else "⚠️ " if self.p95 < 5000 else "❌"
        print(f"\n{'─'*60}")
        print(f"  {self.scenario}")
        print(f"{'─'*60}")
        print(f"  Requêtes      : {self.nb_total}")
        print(f"  {ok_sym} Succès 2xx  : {self.nb_succes} ({self.taux_succes:.1f}%)")
        if self.nb_erreurs:
            print(f"  ❌ Erreurs     : {self.nb_erreurs}")
        print(f"  {lat_sym} P50 latence : {self.p50:.0f} ms")
        print(f"     P95 latence : {self.p95:.0f} ms")
        print(f"     P99 latence : {self.p99:.0f} ms")
        print(f"     Max latence : {self.max_ms:.0f} ms")
        if verbose and self.erreurs_detail:
            print(f"  Erreurs détail :")
            for err, n in sorted(self.erreurs_detail.items(), key=lambda x: -x[1]):
                print(f"    {n:4d}× {err}")

# ══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION DES MEMBRES
# ══════════════════════════════════════════════════════════════════════════════

def generer_membres(nb_groupes: int = NB_GROUPES) -> List[Membre]:
    membres = []
    wa_set  = set()

    for g in range(nb_groupes):
        nb = random.randint(MEMBRES_MIN, MEMBRES_MAX)
        nom_groupe = NOMS_GROUPES[g % len(NOMS_GROUPES)]

        for m in range(nb):
            seed = g * 10_000 + m
            rng  = random.Random(seed)

            # Numéros camerounais : +237 6XX XXX XXX ou +237 9XX XXX XXX
            prefix = rng.choice(["6", "9"])
            wa = f"237{prefix}" + "".join(str(rng.randint(0, 9)) for _ in range(7))

            # Anti-collision
            while wa in wa_set:
                seed += 1_000_000
                rng = random.Random(seed)
                prefix = rng.choice(["6", "9"])
                wa = f"237{prefix}" + "".join(str(rng.randint(0, 9)) for _ in range(7))

            wa_set.add(wa)
            membres.append(Membre(
                wa=wa,
                nom=f"{nom_groupe}_{m + 1:03d}",
                groupe_idx=g,
                tontine_nom=nom_groupe,
                seed=seed,
            ))

    return membres

# ══════════════════════════════════════════════════════════════════════════════
# CONSTRUCTEURS PAYLOAD GREEN API
# Format exact attendu par webhook_whatsapp_greenapi()
# ══════════════════════════════════════════════════════════════════════════════

def _msg_id(wa: str, extra: str = "") -> str:
    h = hashlib.md5(f"{wa}{extra}{time.time()}".encode()).hexdigest()[:16]
    return f"greenapi.{h}"

def _instance_data() -> dict:
    return {
        "idInstance": int(_GREENAPI_INSTANCE) if _GREENAPI_INSTANCE.isdigit() else 0,
        "wid": "237600000001@c.us",
        "typeInstance": "whatsapp",
    }

def payload_texte(wa: str, texte: str) -> dict:
    return {
        "typeWebhook": "incomingMessageReceived",
        "instanceData": _instance_data(),
        "senderData": {
            "chatId": f"{wa}@c.us",
            "sender": f"{wa}@c.us",
            "senderName": "StressTest",
        },
        "messageData": {
            "typeMessage": "textMessage",
            "idMessage": _msg_id(wa, texte),
            "timestamp": int(time.time()),
            "textMessageData": {
                "textMessage": texte,
            },
        },
    }

def payload_image(wa: str, download_url: str, caption: str = "") -> dict:
    """
    Payload Green API pour un message image (screenshot de cotisation).
    Sans GREENAPI_TOKEN le bot skippera le téléchargement, mais toute
    la pipeline HTTP (parsing, rate-limit, routing) est testée.
    """
    return {
        "typeWebhook": "incomingMessageReceived",
        "instanceData": _instance_data(),
        "senderData": {
            "chatId": f"{wa}@c.us",
            "sender": f"{wa}@c.us",
            "senderName": "StressTest",
        },
        "messageData": {
            "typeMessage": "imageMessage",
            "idMessage": _msg_id(wa, download_url),
            "timestamp": int(time.time()),
            "imageData": {
                "downloadUrl": download_url,
                "caption": caption,
                "mimeType": "image/jpeg",
            },
        },
    }

# ══════════════════════════════════════════════════════════════════════════════
# SESSION HTTP PERSISTANTE PAR THREAD (réutilisation TCP — élimine le coût setup)
# ══════════════════════════════════════════════════════════════════════════════

import threading as _threading
_tls = _threading.local()

def _session() -> requests.Session:
    if not hasattr(_tls, "session"):
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=1,
            max_retries=0,
        )
        s.mount("http://", adapter)
        _tls.session = s
    return _tls.session

# ══════════════════════════════════════════════════════════════════════════════
# ENVOI D'UNE REQUÊTE
# ══════════════════════════════════════════════════════════════════════════════

def envoyer(url: str, payload: dict, wa: str, scenario: str) -> ResultatReq:
    debut = time.perf_counter()
    body  = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    hdrs  = {"Content-Type": "application/json"}
    # Auth Green API : token dans le query string (PATCH 27)
    webhook_url = f"{url}/webhook/whatsapp"
    if _GREENAPI_SECRET:
        webhook_url += f"?token={_GREENAPI_SECRET}"
    # Retry une fois sur ConnectionError (connexion keep-alive périmée)
    for tentative in range(2):
        try:
            r = _session().post(
                webhook_url,
                data=body,
                timeout=TIMEOUT_REQ,
                headers=hdrs,
            )
            duree = (time.perf_counter() - debut) * 1000
            return ResultatReq(wa=wa, scenario=scenario,
                               duree_ms=duree, status_code=r.status_code)
        except requests.ConnectionError:
            if tentative == 0:
                if hasattr(_tls, "session"):
                    try:
                        _tls.session.close()
                    except Exception:
                        pass
                    del _tls.session
                continue
            duree = (time.perf_counter() - debut) * 1000
            return ResultatReq(wa=wa, scenario=scenario, duree_ms=duree,
                               status_code=0, erreur="CONNECTION_REFUSED")
        except requests.Timeout:
            duree = (time.perf_counter() - debut) * 1000
            return ResultatReq(wa=wa, scenario=scenario, duree_ms=duree,
                               status_code=0, erreur="TIMEOUT")
        except Exception as e:
            duree = (time.perf_counter() - debut) * 1000
            return ResultatReq(wa=wa, scenario=scenario, duree_ms=duree,
                               status_code=0, erreur=str(e)[:60])
    duree = (time.perf_counter() - debut) * 1000
    return ResultatReq(wa=wa, scenario=scenario, duree_ms=duree,
                       status_code=0, erreur="CONNECTION_REFUSED")

# ══════════════════════════════════════════════════════════════════════════════
# RUNNER DE SCÉNARIO
# ══════════════════════════════════════════════════════════════════════════════

def run_scenario(
    nom: str,
    taches: list,
    max_workers: int = 150,
    pause_entre: float = 0.0,
) -> Rapport:
    rapport = Rapport(scenario=nom, nb_total=len(taches))
    print(f"\n▶  {nom}")
    print(f"   {len(taches)} requêtes — {max_workers} workers simultanés")

    debut_global = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(fn) for fn in taches]
        done = 0
        for fut in as_completed(futures):
            res: ResultatReq = fut.result()
            rapport.ajouter(res)
            done += 1
            if done % 50 == 0 or done == len(taches):
                elapsed = time.perf_counter() - debut_global
                rps = done / max(0.001, elapsed)
                print(
                    f"   {done:4d}/{len(taches)} "
                    f"| ✅ {rapport.nb_succes} ❌ {rapport.nb_erreurs} "
                    f"| {rps:.0f} req/s",
                    end="\r",
                )
            if pause_entre > 0:
                time.sleep(pause_entre)

    elapsed = time.perf_counter() - debut_global
    print(f"\n   Terminé en {elapsed:.2f}s")
    return rapport

# ══════════════════════════════════════════════════════════════════════════════
# VÉRIFICATION SANTÉ INITIALE
# ══════════════════════════════════════════════════════════════════════════════

def check_sante(url: str) -> dict:
    try:
        r = requests.get(f"{url}/health", timeout=5)
        if r.status_code == 200:
            return r.json()
        return {"status": f"HTTP_{r.status_code}"}
    except requests.ConnectionError:
        return {"status": "CONNECTION_REFUSED"}
    except Exception as e:
        return {"status": str(e)[:60]}

def check_sante_detail(url: str) -> Optional[dict]:
    try:
        r = requests.get(f"{url}/health/detail", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="KATECHON OS — Stress Test v2.0")
    parser.add_argument("--url",     default=DEFAULT_URL,   help="URL du bot")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS_DEF, help="Threads max")
    parser.add_argument("--groupes", type=int, default=NB_GROUPES, help="Nombre de groupes")
    parser.add_argument("--seed",    type=int, default=42,  help="Seed aléatoire")
    args = parser.parse_args()

    random.seed(args.seed)
    BOT_URL   = args.url
    WORKERS   = args.workers
    NB_G      = args.groupes

    # ── Header ────────────────────────────────────────────────────────────────
    print("═" * 64)
    print("  KATECHON OS — Stress Test v2.0 (Green API)")
    print(f"  {NB_G} groupes × {MEMBRES_MIN}-{MEMBRES_MAX} membres")
    print(f"  Target  : {BOT_URL}")
    print(f"  Workers : {WORKERS}")
    if not _GREENAPI_SECRET:
        print("  ⚠️  GREENAPI_WEBHOOK_SECRET vide — toutes les requêtes → 503")
    print("═" * 64)

    # ── Sanity check ──────────────────────────────────────────────────────────
    print("\n[0/6] Vérification santé du bot ...")
    sante = check_sante(BOT_URL)
    status = sante.get("status", "?")
    if "CONNECTION_REFUSED" in status:
        print(f"  ❌ Bot injoignable : lance d'abord → python barack_corp_v9_18.py")
        sys.exit(1)
    elif status != "ok":
        print(f"  ⚠️  /health → {status}  (DB KO ? Bot en erreur ?)")
    else:
        db   = sante.get("db", "?")
        ver  = sante.get("version", "?")
        uptime = sante.get("uptime_s", 0)
        print(f"  ✅ Bot OK   version={ver}  db={db}  uptime={uptime:.0f}s")

    sante_detail_avant = check_sante_detail(BOT_URL)
    if sante_detail_avant:
        pool = sante_detail_avant.get("pool", {})
        print(f"     Pool DB avant : {pool}")

    # ── Génération membres ────────────────────────────────────────────────────
    print(f"\n[1/6] Génération membres ...")
    membres = generer_membres(NB_G)
    total_membres = len(membres)
    print(f"  {total_membres} membres sur {NB_G} groupes :")
    for g in range(NB_G):
        gm = [m for m in membres if m.groupe_idx == g]
        print(f"    Groupe {g+1} — {NOMS_GROUPES[g % len(NOMS_GROUPES)]:<8s} : {len(gm):3d} membres")

    rapports: List[Rapport] = []

    # ══════════════════════════════════════════════════════════════════════════
    # S1 — RAFALE HEURE OUVERTURE (texte "statut")
    # Tous les membres tapent "statut" en même temps dès 05:00
    # Teste : pool DB, rate limiter, Flask threading
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n[2/6] Scénarios de charge ...")

    taches_s1 = [
        (lambda m=m: envoyer(BOT_URL, payload_texte(m.wa, "statut"),
                             m.wa, "S1"))
        for m in membres
    ]
    r1 = run_scenario(
        f"S1 — Rafale heure ouverture : {total_membres} membres tapent 'statut'",
        taches_s1,
        max_workers=WORKERS,
    )
    rapports.append(r1)
    time.sleep(3)

    # ══════════════════════════════════════════════════════════════════════════
    # S2 — RAFALE SCREENSHOTS COTISATION
    # Tous les membres envoient un screenshot (downloadUrl unique par membre)
    # Teste : pipeline image, hash SHA-256, routing
    # Note : sans GREENAPI_TOKEN le dl échoue proprement — le 200 est garanti
    # ══════════════════════════════════════════════════════════════════════════
    taches_s2 = [
        (lambda m=m: envoyer(
            BOT_URL,
            payload_image(
                m.wa,
                f"https://media.green-api.com/stress/{m.seed:010d}.jpg",
                f"Cotisation {m.tontine_nom} — {MONTANT_FCFA} FCFA"
            ),
            m.wa, "S2"
        ))
        for m in membres
    ]
    r2 = run_scenario(
        f"S2 — Rafale screenshots cotisation : {total_membres} images simultanées",
        taches_s2,
        max_workers=WORKERS,
    )
    rapports.append(r2)
    time.sleep(3)

    # ══════════════════════════════════════════════════════════════════════════
    # S3 — RATE LIMITER (10 msgs / 60s par numéro)
    # 1 attaquant envoie 15 messages en rafale → doit être bloqué après 10
    # Teste : _rate_buckets, log_audit
    # ══════════════════════════════════════════════════════════════════════════
    wa_attaquant = membres[0].wa
    taches_s3 = [
        (lambda wa=wa_attaquant, i=i: envoyer(
            BOT_URL,
            payload_texte(wa, f"spam_{i}"),
            wa, "S3"
        ))
        for i in range(15)
    ]
    r3 = run_scenario(
        "S3 — Rate limiter : 1 numéro × 15 messages en burst",
        taches_s3,
        max_workers=15,
    )
    rapports.append(r3)
    time.sleep(3)

    # ══════════════════════════════════════════════════════════════════════════
    # S4 — POOL DB SATURATION (maxconn = 50)
    # 60 requêtes simultanées → les 10 en excès doivent retrier proprement
    # Teste : circuit breaker DB, get_conn retry logic
    # ══════════════════════════════════════════════════════════════════════════
    membres_s4 = random.sample(membres, min(60, total_membres))
    taches_s4 = [
        (lambda m=m: envoyer(BOT_URL, payload_texte(m.wa, "menu"),
                             m.wa, "S4"))
        for m in membres_s4
    ]
    r4 = run_scenario(
        "S4 — Pool saturation : 60 connexions simultanées (maxconn=50)",
        taches_s4,
        max_workers=60,
    )
    rapports.append(r4)
    time.sleep(3)

    # ══════════════════════════════════════════════════════════════════════════
    # S5 — DUPLICATS SCREENSHOT (même downloadUrl pour 50 membres différents)
    # Anti-recyclage SHA-256 + UNIQUE INDEX doivent bloquer les doublons
    # ══════════════════════════════════════════════════════════════════════════
    URL_DUPLIQUEE = "https://media.green-api.com/stress/DUPLICATE_STRESS_001.jpg"
    membres_s5 = random.sample(membres, min(50, total_membres))
    taches_s5 = [
        (lambda m=m: envoyer(
            BOT_URL,
            payload_image(m.wa, URL_DUPLIQUEE, "Duplicate test"),
            m.wa, "S5"
        ))
        for m in membres_s5
    ]
    r5 = run_scenario(
        "S5 — Duplicats : 50 membres, même downloadUrl (test anti-recyclage)",
        taches_s5,
        max_workers=50,
    )
    rapports.append(r5)
    time.sleep(3)

    # ══════════════════════════════════════════════════════════════════════════
    # S6 — PIC MIXTE RÉALISTE (80% screenshots + 20% texte, burst 3 minutes)
    # Simule l'heure de cotisation réelle : flood mixte
    # ══════════════════════════════════════════════════════════════════════════
    taches_s6 = []
    for m in membres:
        if random.random() < 0.8:
            fn = (lambda m=m: envoyer(
                BOT_URL,
                payload_image(
                    m.wa,
                    f"https://media.green-api.com/stress/real_{m.seed}.jpg",
                    "Cotisation"
                ),
                m.wa, "S6"
            ))
        else:
            fn = (lambda m=m: envoyer(
                BOT_URL, payload_texte(m.wa, "statut"),
                m.wa, "S6"
            ))
        taches_s6.append(fn)

    random.shuffle(taches_s6)
    r6 = run_scenario(
        f"S6 — Pic mixte réaliste : 80% screenshots + 20% texte, {len(taches_s6)} req",
        taches_s6,
        max_workers=WORKERS,
    )
    rapports.append(r6)

    # ── Pool DB après stress ───────────────────────────────────────────────────
    sante_detail_apres = check_sante_detail(BOT_URL)
    if sante_detail_apres:
        pool_apres = sante_detail_apres.get("pool", {})
        print(f"\n  Pool DB après stress : {pool_apres}")

    # ══════════════════════════════════════════════════════════════════════════
    # RAPPORT FINAL
    # ══════════════════════════════════════════════════════════════════════════
    print("\n\n" + "═" * 64)
    print("  RAPPORT FINAL — KATECHON OS STRESS TEST")
    print("═" * 64)

    all_durees: List[float] = []
    total_req   = 0
    total_ok    = 0
    total_err   = 0

    for r in rapports:
        r.afficher(verbose=True)
        all_durees.extend(r.durees_ms)
        total_req += r.nb_total
        total_ok  += r.nb_succes
        total_err += r.nb_erreurs

    # Métriques globales
    all_durees.sort()
    p50g = statistics.median(all_durees) if all_durees else 0
    p95g = all_durees[int(len(all_durees) * 0.95)] if all_durees else 0
    p99g = all_durees[int(len(all_durees) * 0.99)] if all_durees else 0
    maxg = max(all_durees) if all_durees else 0

    taux = 100 * total_ok / max(1, total_req)

    print(f"\n{'═'*64}")
    print(f"  SYNTHÈSE GLOBALE")
    print(f"{'─'*64}")
    print(f"  Requêtes totales : {total_req}")
    print(f"  Succès 2xx       : {total_ok} ({taux:.1f}%)")
    print(f"  Erreurs          : {total_err}")
    print(f"  Latence P50      : {p50g:.0f} ms")
    print(f"  Latence P95      : {p95g:.0f} ms")
    print(f"  Latence P99      : {p99g:.0f} ms")
    print(f"  Latence max      : {maxg:.0f} ms")

    # ── Verdict ───────────────────────────────────────────────────────────────
    criteres = [
        ("Taux succès ≥ 95%",  taux >= 95,       f"{taux:.1f}%"),
        ("P95 < 2 000 ms",     p95g < 2000,       f"{p95g:.0f} ms"),
        ("P99 < 5 000 ms",     p99g < 5000,       f"{p99g:.0f} ms"),
        ("Max < 30 000 ms",    maxg < 30_000,     f"{maxg:.0f} ms"),
    ]

    print(f"\n  VERDICT :")
    tout_ok = True
    for label, ok, valeur in criteres:
        sym = "✅" if ok else "❌"
        print(f"    {sym}  {label:<22s} → {valeur}")
        if not ok:
            tout_ok = False

    print()
    if tout_ok:
        print("  🚀 Bot prêt pour production")
        print(f"     {NB_G} groupes × {MEMBRES_MIN}-{MEMBRES_MAX} membres = VALIDÉ")
    else:
        print("  ⚠️  Goulots détectés — voir détail par scénario")
        print("     Pistes : pool DB (maxconn=50), APScheduler, outbox lock")

    print()
    print("  NOTE : S2/S5/S6 testent la pipeline HTTP screenshot.")
    print("  Le traitement OCR réel nécessite GREENAPI_TOKEN configuré.")
    print("  Sans credentials, le bot répond 200 et skip le dl media.")
    print("═" * 64 + "\n")


if __name__ == "__main__":
    main()
