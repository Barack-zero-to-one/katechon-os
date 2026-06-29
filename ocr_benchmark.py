#!/usr/bin/env python3
"""
KATECHON OS — OCR Benchmark v1.0
Teste la pipeline OCR sur les reçus réels du dossier Sample_receipts.

Usage :
    python ocr_benchmark.py
    python ocr_benchmark.py --dossier "Sample_receipts/ORANGE MONEY AND MOMO"
    python ocr_benchmark.py --verbose

Pré-requis : pip install pytesseract pillow opencv-python numpy
             + Tesseract-OCR installé dans C:\\Program Files\\Tesseract-OCR\\
"""

import argparse
import io
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

# Force UTF-8 sur Windows pour les caractères spéciaux
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import cv2
    import numpy as np
    from PIL import Image
    import pytesseract
except ImportError as e:
    print(f"❌ Dépendance manquante : {e}")
    print("   pip install pytesseract pillow opencv-python numpy")
    sys.exit(1)

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

DOSSIER_PAR_DEFAUT = r"Sample_receipts\ORANGE MONEY AND MOMO"

# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE OCR — copie exacte de barack_corp_v9_18.py
# ══════════════════════════════════════════════════════════════════════════════

def _pretraiter_screenshot_whatsapp(image_bytes: bytes):
    arr    = np.frombuffer(image_bytes, np.uint8)
    img_cv = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_cv is None:
        pil = Image.open(io.BytesIO(image_bytes)).convert("L")
        w, h = pil.size
        if w < 1400:
            scale = 1400.0 / w
            pil = pil.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return pil

    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    if float(np.mean(gray < 100)) > 0.35:
        gray = cv2.bitwise_not(gray)

    h_cv, w_cv = gray.shape
    if w_cv < 1400:
        scale = 1400.0 / w_cv
        gray  = cv2.resize(gray, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_CUBIC)

    h_cv, w_cv = gray.shape
    if h_cv > w_cv:
        crop_bottom = int(h_cv * 0.18)
        gray = gray[:h_cv - crop_bottom, :]

    gray   = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31, C=15
    )
    binary = cv2.copyMakeBorder(binary, 20, 20, 20, 20,
                                cv2.BORDER_CONSTANT, value=255)
    return Image.fromarray(binary)


def lire_screenshot_mobile_money(image_bytes: bytes) -> dict:
    result = {
        "ok": True, "montant": None, "operateur": "Inconnu",
        "type": "inconnu", "date": None, "reference": None,
        "confiance": "faible", "brut": "",
    }
    try:
        img = _pretraiter_screenshot_whatsapp(image_bytes)
        texte = pytesseract.image_to_string(
            img,
            lang="fra+eng",
            config="--psm 6 --oem 3 -c preserve_interword_spaces=1"
        )
        texte_brut = texte
        texte      = texte.upper()
        texte      = texte.replace("_", " ")  # artefact OCR WhatsApp ("2000 _\nFCFA")
        result["brut"] = texte_brut[:300]

        if "SWITCHN" in texte or "SWITCH N" in texte:
            result["operateur"] = "SwitchN"
        elif any(k in texte for k in ("ORANGE MONEY", "FLOOZ", "OM ", "TRANSFERT DE")):
            result["operateur"] = "Orange"
        elif any(k in texte for k in ("MTN MOMO", "MOMO", "MOBILE MONEY",
                                       "CASH IN OF", "HAVE TRANSFERRED",
                                       "YOU HAVE TRANSFERRED")):
            result["operateur"] = "MTN"

        if any(k in texte for k in ("ENVOI", "TRANSFERT", "VOUS AVEZ ENVOYE",
                                     "TRANSFER", "SENT", "PAYMENT")):
            result["type"] = "envoi"
        elif any(k in texte for k in ("RECHARGE", "CREDIT", "AIRTIME")):
            result["type"] = "recharge"

        _NBR = r"(\d{1,3}(?:[\s\.  ]\d{3})*|\d{4,10})"
        _DEC = r"(\d{3,7})[.,]\d{1,2}"
        _DEV = r"(?:FCFA|XAF|CFA|FRS|F\b)"
        patterns_montant = [
            r"(?:MONTANT\W{0,10}TRANSACTION|MONTANT\s+BRUT)\s*[:\-=]?\s*" + _DEC,
            r"(?:MONTANT\W{0,10}TRANSACTION|MONTANT\s+BRUT)\s*[:\-=]?\s*" + _NBR,
            _NBR + r"\s*" + _DEV,
            _DEC + r"\s*" + _DEV,
            _DEV + r"\s*:?\s*" + _NBR,
            r"(?:MONTANT|AMOUNT|SOMME|TOTAL)\s*[:\-=]?\s*" + _NBR,
        ]
        for pat in patterns_montant:
            for m in re.finditer(pat, texte):
                val_str = re.sub(r"[^\d]", "", m.group(1))
                try:
                    val = int(val_str)
                    if 100 <= val <= 10_000_000:
                        result["montant"] = val
                        break
                except ValueError:
                    continue
            if result["montant"]:
                break

        m_date = re.search(r"(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})", texte)
        if m_date and not (2020 <= int(m_date.group(1)) <= 2035):
            m_date = None
        if not m_date:
            m_date = re.search(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})", texte)
        if m_date:
            result["date"] = m_date.group(0)

        _op = result["operateur"]
        patterns_ref_kw = []
        if _op == "Orange":
            patterns_ref_kw = [
                r"\b(PP\d{6}\.\d{4}\.[A-Z0-9]{4,8})\b",
                r"\b(OM\d{8,12})\b",
                r"(?:R[EÉ]F[EÉ]RENCE?|TRANS(?:ACTION)?|ID)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        elif _op == "MTN":
            patterns_ref_kw = [
                r"\bTRANSACTION\s+ID\s*[:\-]?\s*(\d{8,15})\b",
                r"\bTXN?(\d{8,12})\b",
                r"(?:TRANSACTION\s*ID|TXN?|REF)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        elif _op == "SwitchN":
            patterns_ref_kw = [
                r"\bSWN?-?([A-Z0-9]{6,14})\b",
                r"(?:ORDER|REF\.?|TRANSACTION|RECU)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        patterns_ref_kw += [
            r"(?:REF\.?|R[EÉ]F[EÉ]RENCE?)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            r"(?:TXN?|TRANSACTION)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            r"(?:N[°O]\.?|ID)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
        ]
        for pat in patterns_ref_kw:
            m_ref = re.search(pat, texte)
            if m_ref:
                result["reference"] = m_ref.group(1)
                break
        if not result["reference"]:
            _MOTS_CONNUS = {
                "FCFA", "MOMO", "MOBILE", "MONEY", "ORANGE", "FLOOZ",
                "CREDIT", "SUCCES", "SUCCESS", "REUSSI", "CONFIRME",
                "EFFECTUE", "COMPLETED", "MONTANT", "AMOUNT", "TRANSFERT",
                "RECHARGE", "AIRTIME", "ENVOI", "PAYMENT", "SOMME", "TOTAL",
            }
            for m_fb in re.finditer(r"\b([A-Z][A-Z0-9]{9,14})\b", texte):
                cand = m_fb.group(1)
                if cand not in _MOTS_CONNUS:
                    result["reference"] = cand
                    break

        score = 0
        if result["montant"]:                        score += 2
        if result["operateur"] != "Inconnu":         score += 1
        if result["type"]      != "inconnu":         score += 1
        if result["date"]:                           score += 1
        if result["reference"]:                      score += 1
        if any(k in texte for k in ("SUCCES", "SUCCESS", "CONFIRME",
                                     "REUSSI", "EFFECTUE", "COMPLETED")):
            score += 2

        result["confiance"] = "haute"   if score >= 5 else \
                              "moyenne" if score >= 3 else "faible"
        result["score"] = score
        return result

    except Exception as e:
        return {"ok": False, "raison": str(e)[:80], "confiance": "faible",
                "montant": None, "operateur": "Inconnu", "score": 0}


# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK
# ══════════════════════════════════════════════════════════════════════════════

CONF_SYM = {"haute": "🟢", "moyenne": "🟡", "faible": "🔴"}

def benchmark(dossier: str, verbose: bool = False):
    chemins = sorted(Path(dossier).glob("*"))
    chemins = [p for p in chemins if p.suffix.upper() in (".JPG", ".JPEG", ".PNG", ".PDF")]

    if not chemins:
        print(f"❌ Aucun fichier image trouvé dans : {dossier}")
        sys.exit(1)

    total = len(chemins)
    print("═" * 70)
    print(f"  KATECHON OS — OCR Benchmark v1.0")
    print(f"  {total} reçus — {dossier}")
    print("═" * 70)
    print(f"\n  {'#':>3}  {'Fichier':<22} {'Op.':<8} {'Montant':>9}  {'Réf.':<14} {'Conf.':>7}  {'ms':>5}")
    print(f"  {'─'*3}  {'─'*22} {'─'*8} {'─'*9}  {'─'*14} {'─'*7}  {'─'*5}")

    resultats = []
    durees_ms = []

    for i, chemin in enumerate(chemins, 1):
        image_bytes = chemin.read_bytes()
        t0     = time.perf_counter()
        r      = lire_screenshot_mobile_money(image_bytes)
        duree  = (time.perf_counter() - t0) * 1000
        durees_ms.append(duree)
        resultats.append((chemin.name, r, duree))

        sym     = CONF_SYM.get(r.get("confiance", "faible"), "🔴")
        op      = r.get("operateur", "?")[:7]
        montant = str(r.get("montant") or "—")
        ref     = (r.get("reference") or "—")[:13]
        conf    = r.get("confiance", "faible")
        nom     = chemin.name[:21]

        print(f"  {i:>3}  {nom:<22} {op:<8} {montant:>9}  {ref:<14} {sym} {conf:<6}  {duree:>5.0f}")

        if verbose and r.get("brut"):
            print(f"       OCR brut : {r['brut'][:120].strip()}")
            print()

    # ── Statistiques ───────────────────────────────────────────────────────
    haute   = sum(1 for _, r, _ in resultats if r.get("confiance") == "haute")
    moyenne = sum(1 for _, r, _ in resultats if r.get("confiance") == "moyenne")
    faible  = sum(1 for _, r, _ in resultats if r.get("confiance") == "faible")
    ok_ocr  = sum(1 for _, r, _ in resultats if r.get("ok", False))
    avec_montant = sum(1 for _, r, _ in resultats if r.get("montant"))
    avec_ref     = sum(1 for _, r, _ in resultats if r.get("reference"))
    mtn     = sum(1 for _, r, _ in resultats if r.get("operateur") == "MTN")
    orange  = sum(1 for _, r, _ in resultats if r.get("operateur") == "Orange")
    inconnu = sum(1 for _, r, _ in resultats if r.get("operateur") == "Inconnu")

    p50 = statistics.median(durees_ms)
    p95 = sorted(durees_ms)[int(len(durees_ms) * 0.95)]
    p99 = sorted(durees_ms)[int(len(durees_ms) * 0.99)]
    max_ms = max(durees_ms)
    total_s = sum(durees_ms) / 1000

    taux_haute  = 100 * haute  / total
    taux_utiles = 100 * (haute + moyenne) / total
    taux_montant= 100 * avec_montant / total
    taux_ref    = 100 * avec_ref / total

    print(f"\n{'═'*70}")
    print(f"  RÉSULTATS")
    print(f"{'─'*70}")
    print(f"  Reçus traités      : {total}")
    print(f"  OCR OK             : {ok_ocr}")
    print(f"")
    print(f"  🟢 Confiance haute  : {haute:>3}  ({taux_haute:.1f}%)")
    print(f"  🟡 Confiance moyenne: {moyenne:>3}  ({100*moyenne/total:.1f}%)")
    print(f"  🔴 Confiance faible : {faible:>3}  ({100*faible/total:.1f}%)")
    print(f"  ✅ Exploitables     : {haute+moyenne:>3}  ({taux_utiles:.1f}%)")
    print(f"")
    print(f"  Montant extrait    : {avec_montant}/{total}  ({taux_montant:.1f}%)")
    print(f"  Référence extraite : {avec_ref}/{total}  ({taux_ref:.1f}%)")
    print(f"  Opérateur MTN      : {mtn}")
    print(f"  Opérateur Orange   : {orange}")
    print(f"  Opérateur inconnu  : {inconnu}")
    print(f"")
    print(f"  Latence P50        : {p50:.0f} ms")
    print(f"  Latence P95        : {p95:.0f} ms")
    print(f"  Latence P99        : {p99:.0f} ms")
    print(f"  Latence max        : {max_ms:.0f} ms")
    print(f"  Temps total        : {total_s:.1f}s")

    # ── Verdict ────────────────────────────────────────────────────────────
    criteres = [
        ("Confiance haute ≥ 80%",   taux_haute   >= 80, f"{taux_haute:.1f}%"),
        ("Exploitables ≥ 95%",      taux_utiles  >= 95, f"{taux_utiles:.1f}%"),
        ("Montant extrait ≥ 90%",   taux_montant >= 90, f"{taux_montant:.1f}%"),
        ("P95 < 3 000 ms",          p95 < 3000,         f"{p95:.0f} ms"),
    ]

    print(f"\n  VERDICT :")
    tout_ok = True
    for label, ok, valeur in criteres:
        sym = "✅" if ok else "❌"
        print(f"    {sym}  {label:<28} → {valeur}")
        if not ok:
            tout_ok = False

    print()
    if tout_ok:
        print("  🚀 Pipeline OCR prête pour production")
    else:
        print("  ⚠️  Voir reçus en confiance faible — OCR brut avec --verbose")

    # ── Détail des échecs ─────────────────────────────────────────────────
    echecs = [(nom, r, d) for nom, r, d in resultats if r.get("confiance") == "faible"]
    if echecs:
        print(f"\n  REÇUS EN CONFIANCE FAIBLE ({len(echecs)}) :")
        for nom, r, d in echecs:
            print(f"    • {nom}  op={r.get('operateur','?')}  "
                  f"montant={r.get('montant','—')}  "
                  f"ref={r.get('reference','—')}")

    print("═" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="KATECHON OS — OCR Benchmark v1.0")
    parser.add_argument("--dossier", default=DOSSIER_PAR_DEFAUT,
                        help="Dossier contenant les screenshots")
    parser.add_argument("--verbose", action="store_true",
                        help="Afficher le texte OCR brut pour chaque reçu")
    args = parser.parse_args()
    benchmark(args.dossier, verbose=args.verbose)


if __name__ == "__main__":
    main()
