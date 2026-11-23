"""
Lecture des reçus Mobile Money (module pur) : prétraitement OpenCV des
captures WhatsApp, lecture des reçus PDF SwitchN, OCR Tesseract et analyse
du texte (montant, opérateur, type, date, référence, confiance).

Extrait de barack_corp_v9_18.py sans changement de comportement ; aucune
dépendance au bot. cv2, numpy, PIL, pytesseract et pdfplumber restent
importés à l'appel, comme avant. Le bot réexporte chaque nom, et
ocr_benchmark.py mesure ce même code.
"""
import logging
import re

__all__ = ["_pretraiter_screenshot_whatsapp", "_lire_pdf_switchn", "lire_screenshot_mobile_money"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


def _pretraiter_screenshot_whatsapp(image_bytes: bytes):
    """
    Pipeline OpenCV 7 étapes pour screenshots WhatsApp compressés.
    Gère : artéfacts JPEG, mode sombre, texte petit, binarisation adaptative.
    Retourne une PIL Image noir/blanc pur prête pour Tesseract.
    """
    import cv2
    import numpy as np
    from PIL import Image
    import io

    # 1. Décoder les bytes → tableau BGR OpenCV
    arr = np.frombuffer(image_bytes, np.uint8)
    img_cv = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_cv is None:
        # Fallback PIL si format non supporté par OpenCV
        pil = Image.open(io.BytesIO(image_bytes)).convert("L")
        w, h = pil.size
        if w < 1400:
            scale = 1400.0 / w
            pil = pil.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return pil

    # 2. Convertir en niveaux de gris
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # 3. Détection fond sombre + inversion
    #    Ratio > 35% de pixels < 100 → fond sombre (bulle WhatsApp violet-gris)
    #    Plus robuste que la moyenne : les zones blanches hors-bulle tirent
    #    la moyenne vers le haut sans représenter le fond réel du texte.
    if float(np.mean(gray < 100)) > 0.35:
        gray = cv2.bitwise_not(gray)

    # 4. Agrandir pour les petits textes (cible : 1400 px de large minimum)
    h_cv, w_cv = gray.shape
    if w_cv < 1400:
        scale = 1400.0 / w_cv
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)

    # 4b. Crop clavier WhatsApp (18% bas) — portrait uniquement
    #     Supprime le bruit QWERTY que Tesseract lit comme texte
    h_cv, w_cv = gray.shape
    if h_cv > w_cv:
        crop_bottom = int(h_cv * 0.18)
        gray = gray[:h_cv - crop_bottom, :]

    # 5. Lissage des artéfacts JPEG WhatsApp (blocs 8×8)
    #    Gaussian σ=1 (kernel 3×3) — supprime le bruit intra-bloc
    #    sans flouter les contours inter-caractères
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # 6. Seuillage adaptatif → noir/blanc pur
    #    blockSize=31 : fenêtre large pour gérer les en-têtes colorés des apps
    #    C=15 : marge de contraste pour les polices fines
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31, C=15
    )

    # 7. Bordure blanche 20px — empêche Tesseract de tronquer les bords
    binary = cv2.copyMakeBorder(binary, 20, 20, 20, 20,
                                cv2.BORDER_CONSTANT, value=255)

    return Image.fromarray(binary)


def _lire_pdf_switchn(pdf_bytes: bytes) -> dict:
    """
    Extrait les données d'un reçu SwitchN PDF (text-based, pas d'OCR requis).
    Format SwitchN : MONTANT TRANSFÉRÉ, ID DE TRANSACTION OPÉRATEUR, DATE.
    """
    result = {
        "ok": True, "montant": None, "operateur": "SwitchN",
        "type": "envoi", "date": None, "reference": None,
        "confiance": "faible", "brut": "",
    }
    try:
        import pdfplumber, io as _io
        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
            texte_brut = "\n".join(p.extract_text() or "" for p in pdf.pages)
        result["brut"] = texte_brut[:300]
        texte = texte_brut.upper()

        # Montant : MONTANT TRANSFÉRÉ (ce que le bénéficiaire reçoit = montant cotisation)
        # Format PDF : "1,000 FCFA" (virgule comme séparateur de milliers)
        m_mt = re.search(r"MONTANT\s+TRANSF[EÉ]R[EÉ]\s+([\d,\s]+)\s*FCFA", texte)
        if not m_mt:
            m_mt = re.search(r"MONTANT\s+PAY[EÉ]\s+([\d,\s]+)\s*FCFA", texte)
        if m_mt:
            val_str = re.sub(r"[^\d]", "", m_mt.group(1))
            try:
                val = int(val_str)
                if 100 <= val <= 10_000_000:
                    result["montant"] = val
            except ValueError:
                pass

        # Référence priorité 1 : ID DE TRANSACTION OPÉRATEUR (ex: MP260604.1129.C95759)
        m_ref = re.search(
            r"ID\s+DE\s+TRANSACTION\s+OP[EÉ]RATEUR\s+([A-Z]{2}\d{6}\.\d{4}\.[A-Z0-9]{4,8})",
            texte)
        if not m_ref:
            # Priorité 2 : ID DU TRANSFERT (numérique court)
            m_ref = re.search(r"ID\s+DU\s+TRANSFERT\s+(\d{5,12})", texte)
        if not m_ref:
            # Priorité 3 : ID DE LA DEMANDE DE PAIEMENT
            m_ref = re.search(
                r"ID\s+DE\s+LA\s+DEMANDE\s+DE\s+PAIEMENT\s+([\w_-]{5,20})", texte)
        if m_ref:
            result["reference"] = m_ref.group(1).strip()

        # Date — format DD/MM/YYYY HH:MM
        m_date = re.search(r"(\d{2}/\d{2}/\d{4})", texte)
        if m_date:
            result["date"] = m_date.group(1)

        # Confirmation : "TERMINÉ" (statut SwitchN) ou confirmation opérateur
        if "TERMIN" in texte or "SUCCESSFULLY" in texte:
            result["confiance"] = "haute" if (result["montant"] and result["reference"]) \
                                  else "moyenne"

    except Exception as _e:
        log.warning(f"⚠️ _lire_pdf_switchn : {_e}")
        result["ok"] = False
    return result


def lire_screenshot_mobile_money(image_bytes: bytes) -> dict:
    """
    Analyse un screenshot ou reçu PDF Mobile Money.
    PDFs SwitchN : extraction directe (pas d'OCR). JPG/PNG : OCR Tesseract.

    Retourne un dict :
    {
      "ok":        True/False,
      "montant":   int ou None,
      "operateur": "MTN" | "Orange" | "SwitchN" | "Inconnu",
      "type":      "envoi" | "recharge" | "inconnu",
      "date":      str ou None,
      "reference": str ou None,
      "confiance": "haute" | "moyenne" | "faible",
      "brut":      str (texte extrait brut)
    }
    """
    # Dispatch PDF SwitchN — magic bytes %PDF
    if image_bytes[:4] == b'%PDF':
        return _lire_pdf_switchn(image_bytes)

    try:
        import pytesseract
        from PIL import Image
        import io  # io reste local (utilisé uniquement ici)
        # re est déjà importé globalement en haut du fichier
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

        # Prétraitement OpenCV (dark mode, JPEG artifacts, upscale, binarisation)
        img = _pretraiter_screenshot_whatsapp(image_bytes)

        # OCR — français + anglais pour couvrir MTN/Orange
        texte = pytesseract.image_to_string(
            img,
            lang="fra+eng",
            config="--psm 6 --oem 3 -c preserve_interword_spaces=1"
        )
        texte_brut = texte
        texte      = texte.upper()
        texte      = texte.replace("_", " ")  # artefact OCR WhatsApp ("2000 _\nFCFA")

        result = {
            "ok":        True,
            "montant":   None,
            "operateur": "Inconnu",
            "type":      "inconnu",
            "date":      None,
            "reference": None,
            "confiance": "faible",
            "brut":      texte_brut[:300],
        }

        # ── Détection opérateur — ordre critique : SwitchN > Orange > MTN ──
        # "MTN" seul retiré : trop générique, apparaît dans reçus Orange→MTN
        if "SWITCHN" in texte or "SWITCH N" in texte:
            result["operateur"] = "SwitchN"
        elif any(k in texte for k in ("ORANGE MONEY", "FLOOZ", "OM ", "TRANSFERT DE")):
            result["operateur"] = "Orange"
        elif any(k in texte for k in ("MTN MOMO", "MOMO", "MOBILE MONEY",
                                       "CASH IN OF", "HAVE TRANSFERRED",
                                       "YOU HAVE TRANSFERRED")):
            result["operateur"] = "MTN"

        # ── Détection type ────────────────────────────────────────────────
        # "CASH IN" retiré : c'est un dépôt reçu (sens inverse de envoi)
        # "HAVE TRANSFERRED" retiré : déjà dans la détection opérateur MTN,
        # le dupliquer ici donnait +2 score depuis un seul keyword (vecteur fraude)
        if any(k in texte for k in ("ENVOI", "TRANSFERT", "VOUS AVEZ ENVOYE",
                                     "TRANSFER", "SENT", "PAYMENT")):
            result["type"] = "envoi"
        elif any(k in texte for k in ("RECHARGE", "CREDIT", "AIRTIME")):
            result["type"] = "recharge"

        # ── Extraction montant ────────────────────────────────────────────
        # _NBR : groupes de 3 chiffres séparés par espace/point/NBSP
        #        OU bloc brut 4-10 chiffres
        # Couvert : "5000", "5 000", "5.000", "1 000 000", "10.500.000"
        _NBR = r"(\d{1,3}(?:[\s\.  ]\d{3})*|\d{4,10})"
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

        # ── Extraction date ───────────────────────────────────────────────
        # ISO (2026-06-17) ou DD/MM/YYYY — cherche les deux formats
        # Validation année 2020-2035 sur le pattern ISO pour éviter de matcher
        # des montants ou fragments d'ID (ex. "5000.06.28")
        m_date = re.search(r"(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})", texte)
        if m_date and not (2020 <= int(m_date.group(1)) <= 2035):
            m_date = None
        if not m_date:
            m_date = re.search(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})", texte)
        if m_date:
            result["date"] = m_date.group(0)

        # ── Extraction référence transaction ──────────────────────────────
        # Priorité 1 : patterns spécifiques à l'opérateur détecté
        _op = result["operateur"]
        patterns_ref_kw = []
        if _op == "Orange":
            patterns_ref_kw = [
                r"\b(PP\d{6}\.\d{4}\.[A-Z0-9]{4,8})\b",            # PP260623.1152.AD63N5
                r"\b(OM\d{8,12})\b",
                r"(?:R[EÉ]F[EÉ]RENCE?|TRANS(?:ACTION)?|ID)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        elif _op == "MTN":
            patterns_ref_kw = [
                r"\bTRANSACTION\s+ID\s*[:\-]?\s*(\d{8,15})\b",       # Cash in format MTN EN
                r"\bTXN?(\d{8,12})\b",
                r"(?:TRANSACTION\s*ID|TXN?|REF)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        elif _op == "SwitchN":
            patterns_ref_kw = [
                r"\bSWN?-?([A-Z0-9]{6,14})\b",             # SWN-XXXXXXXX ou SW-XXXXXXXX
                r"(?:ORDER|REF\.?|TRANSACTION|RECU)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        # Priorité 2 : patterns génériques (Switch + opérateur non détecté)
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
        # Priorité 3 : FALLBACK — OCR a mal lu "Référence"/"Transfert"
        # Cherche la première chaîne alphanumérique MAJUSCULE isolée 8-15 chars
        # commençant par une lettre (exclut les montants purement numériques)
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

        # ── Niveau de confiance ───────────────────────────────────────────
        score = 0
        if result["montant"]:   score += 2
        if result["operateur"] != "Inconnu": score += 1
        if result["type"] != "inconnu":      score += 1
        if result["date"]:      score += 1
        if result["reference"]: score += 1
        # Mots de confirmation présents
        if any(k in texte for k in ("SUCCES", "SUCCESS", "CONFIRME",
                                     "REUSSI", "EFFECTUE", "COMPLETED")):
            score += 2

        result["confiance"] = "haute" if score >= 5 else \
                              "moyenne" if score >= 3 else "faible"

        return result

    except ImportError:
        log.warning("pytesseract non installé — OCR désactivé. "
                    "Lancez : pip install pytesseract pillow")
        return {"ok": False, "raison": "pytesseract non installé"}
    except Exception as e:
        log.error(f"OCR erreur : {e}")
        return {"ok": False, "raison": str(e)[:80]}
