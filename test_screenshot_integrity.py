# -*- coding: utf-8 -*-
"""
Tests de l'anti-recyclage renforcé (module pur screenshot_integrity).
Reproduit les attaques du PoC (1px, re-compression JPEG) sur le perceptual hash.
Lancer :  python -m pytest test_screenshot_integrity.py -v
"""
import io

import pytest

import screenshot_integrity as si


# ── normaliser_reference ────────────────────────────────────────────────────
def test_ref_normalise_upper_alnum():
    assert si.normaliser_reference("mp260814.0912.b44317") == "MP2608140912B44317"


def test_ref_espaces_et_tirets_retires():
    assert si.normaliser_reference("  MP 2608-14 A5 ") == "MP260814A5"


def test_ref_trop_courte_rejetee():
    assert si.normaliser_reference("A1B2") == ""      # < 6 alnum → non fiable
    assert si.normaliser_reference("") == ""
    assert si.normaliser_reference(None) == ""


def test_ref_variantes_ocr_convergent():
    # Le même reçu ré-encodé donne le même texte OCR → même référence normalisée.
    assert si.normaliser_reference("MP-260814-0912") == si.normaliser_reference("mp260814 0912")


# ── hamming / est_near_duplicate ────────────────────────────────────────────
def test_hamming_connu():
    assert si.hamming(0b1010, 0b1000) == 1
    assert si.hamming(0, 0xFFFFFFFFFFFFFFFF) == 64
    assert si.hamming(42, 42) == 0


def test_near_duplicate_seuil():
    assert si.est_near_duplicate(0, 0b111, seuil=6) is True     # 3 bits
    assert si.est_near_duplicate(0, 0x7F, seuil=6) is False     # 7 bits
    assert si.est_near_duplicate(None, 5) is False              # dhash indécodable


# ── dHash : robustesse 1px / re-compression (le cœur du fix #3) ─────────────
def _recu(ref="MP260814", teinte=(245, 245, 245)):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (480, 240), teinte)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 480, 40], fill=(255, 140, 0))
    d.text((12, 70), f"Montant : 50 000 FCFA", fill=(20, 20, 20))
    d.text((12, 110), f"Reference : {ref}", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _modifier_1px(png_bytes):
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    px = img.load()
    r, g, b = px[0, 0]
    px[0, 0] = ((r + 1) % 256, g, b)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _recompresser_jpeg(png_bytes, quality=90):
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def test_dhash_stable_sur_1px():
    original = _recu()
    var = _modifier_1px(original)
    h0, h1 = si.dhash(original), si.dhash(var)
    assert h0 is not None and h1 is not None
    assert si.hamming(h0, h1) == 0                     # 1px invisible → dHash identique
    assert si.est_near_duplicate(h0, h1) is True


def test_dhash_stable_sur_recompression_jpeg():
    original = _recu()
    var = _recompresser_jpeg(original)
    h0, h2 = si.dhash(original), si.dhash(var)
    assert si.hamming(h0, h2) <= si.SEUIL_NEAR_DUP     # re-JPEG → perceptuellement identique
    assert si.est_near_duplicate(h0, h2) is True


def test_dhash_distingue_images_vraiment_differentes():
    # dHash sépare deux images au CONTENU réellement différent (reçu vs bruit).
    from PIL import Image
    import os
    a = _recu()
    bruit = Image.frombytes("L", (240, 240), os.urandom(240 * 240))
    buf = io.BytesIO(); bruit.save(buf, format="PNG")
    ha, hb = si.dhash(a), si.dhash(buf.getvalue())
    assert si.hamming(ha, hb) > si.SEUIL_NEAR_DUP
    assert si.est_near_duplicate(ha, hb) is False


def test_dhash_reçus_meme_template_sont_proches():
    # GARDE-FOU DESIGN : deux paiements DIFFÉRENTS sur le même template de reçu
    # ont un dHash proche. Le perceptual hash ne doit donc JAMAIS bloquer seul —
    # il sert d'alerte admin ; le blocage dur repose sur la référence + byte-hash.
    a = _recu(ref="MP260814", teinte=(245, 245, 245))
    b = _recu(ref="ZZ999999", teinte=(20, 40, 90))
    assert si.hamming(si.dhash(a), si.dhash(b)) <= si.SEUIL_NEAR_DUP


def test_dhash_bytes_invalides():
    assert si.dhash(b"") is None
    assert si.dhash(b"pas une image") is None


def test_dhash_hex_roundtrip():
    original = _recu()
    hx = si.dhash_hex(original)
    assert len(hx) == 16
    assert si.hex_vers_int(hx) == si.dhash(original)
    assert si.hex_vers_int("") is None
    assert si.hex_vers_int("zzz") is None
