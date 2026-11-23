#!/usr/bin/env python3
"""
KATECHON OS — OCR Benchmark v1.0
Teste la pipeline OCR sur les reçus réels du dossier Sample_receipts.

Usage :
    python ocr_benchmark.py
    python ocr_benchmark.py --dossier "Sample_receipts/ORANGE MONEY AND MOMO"
    python ocr_benchmark.py --verbose

Pré-requis : pip install pytesseract pillow opencv-python numpy pdfplumber
             + Tesseract-OCR installé dans C:\\Program Files\\Tesseract-OCR\\
"""

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

# Force UTF-8 sur Windows pour les caractères spéciaux
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    # Vérification des dépendances du module ocr avant le premier reçu
    import cv2, numpy, PIL, pytesseract, pdfplumber  # noqa: F401
except ImportError as e:
    print(f"❌ Dépendance manquante : {e}")
    print("   pip install pytesseract pillow opencv-python numpy pdfplumber")
    sys.exit(1)

DOSSIER_PAR_DEFAUT = r"Sample_receipts\ORANGE MONEY AND MOMO"

# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE OCR — le module réel du bot, plus aucune copie
# ══════════════════════════════════════════════════════════════════════════════

from ocr import lire_screenshot_mobile_money  # noqa: E402  (après la vérification des dépendances)


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
    # Une lecture en échec (ok False) n'entre ni dans les confiances ni dans
    # les opérateurs : elle est comptée à part et listée avec sa raison.
    lus     = [r for _, r, _ in resultats if r.get("ok", False)]
    haute   = sum(1 for r in lus if r.get("confiance") == "haute")
    moyenne = sum(1 for r in lus if r.get("confiance") == "moyenne")
    faible  = sum(1 for r in lus if r.get("confiance") == "faible")
    ok_ocr  = len(lus)
    avec_montant = sum(1 for _, r, _ in resultats if r.get("montant"))
    avec_ref     = sum(1 for _, r, _ in resultats if r.get("reference"))
    mtn     = sum(1 for r in lus if r.get("operateur") == "MTN")
    orange  = sum(1 for r in lus if r.get("operateur") == "Orange")
    inconnu = sum(1 for r in lus if r.get("operateur") == "Inconnu")
    switchn = sum(1 for r in lus if r.get("operateur") == "SwitchN")
    echecs_lecture = total - ok_ocr

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
    print(f"  Opérateur SwitchN  : {switchn}")
    print(f"  Opérateur inconnu  : {inconnu}")
    print(f"  Lecture en échec   : {echecs_lecture}")
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
    faibles = [(nom, r) for nom, r, _ in resultats
               if r.get("ok", False) and r.get("confiance") == "faible"]
    if faibles:
        print(f"\n  REÇUS EN CONFIANCE FAIBLE ({len(faibles)}) :")
        for nom, r in faibles:
            print(f"    • {nom}  op={r.get('operateur','?')}  "
                  f"montant={r.get('montant','—')}  "
                  f"ref={r.get('reference','—')}")
    echoues = [(nom, r) for nom, r, _ in resultats if not r.get("ok", False)]
    if echoues:
        print(f"\n  LECTURES EN ÉCHEC ({len(echoues)}) :")
        for nom, r in echoues:
            print(f"    • {nom}  raison={r.get('raison', 'illisible')}")

    print("═" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="KATECHON OS — OCR Benchmark v1.0")
    parser.add_argument("--dossier", default=DOSSIER_PAR_DEFAUT,
                        help="Dossier contenant les screenshots")
    parser.add_argument("--verbose", action="store_true",
                        help="Afficher le texte OCR brut pour chaque reçu")
    args = parser.parse_args()
    # Les erreurs OCR sont listées avec leur raison en fin de rapport : on
    # n'imprime pas en plus les logs du module au milieu du tableau.
    logging.getLogger("BarackCorp").setLevel(logging.CRITICAL)
    benchmark(args.dossier, verbose=args.verbose)


if __name__ == "__main__":
    main()
