# -*- coding: utf-8 -*-
"""
screenshot_integrity.py — Anti-recyclage screenshot renforcé (module pur).

Le SHA-256 des bytes bruts ne détecte QUE le doublon strictement identique :
1 pixel modifié ou une re-compression JPEG produit un hash neuf (PoC confirmé,
finding #3). Ce module ajoute deux couches robustes :

  1. Référence de transaction (normaliser_reference) : identifiant unique par
     paiement réel, lu par l'OCR puis — dans le code historique — jeté. La
     dédupliquer tue le recyclage par ré-encodage, quel que soit le nombre de
     pixels modifiés.
  2. Perceptual hash (dhash + hamming) : empreinte perceptuelle 64 bits, stable
     au 1px / à la re-compression (distance de Hamming ~0). Filet de sécurité
     quand l'OCR ne parvient pas à lire une référence fiable.

Module PUR : Pillow importé paresseusement dans dhash() ; le reste (référence,
hamming, seuil) est sans dépendance et testable partout.
"""
from __future__ import annotations

import io
from typing import Optional

REFERENCE_MIN_LEN = 6      # en-deçà, OCR trop court/peu fiable → on ne déduplique pas
SEUIL_NEAR_DUP    = 6      # Hamming <= 6/64 → images perceptuellement identiques


def normaliser_reference(ref) -> str:
    """
    Normalise une référence de transaction MoMo/OM pour la déduplication :
    majuscules + caractères alphanumériques seuls (retire espaces, points, tirets
    que l'OCR place de façon instable). Renvoie "" si le résultat est trop court
    (< REFERENCE_MIN_LEN) — signal OCR peu fiable, on ne risque pas un faux positif.
    """
    if not ref:
        return ""
    norm = "".join(ch for ch in str(ref).upper() if ch.isalnum())
    return norm if len(norm) >= REFERENCE_MIN_LEN else ""


def hamming(a: int, b: int) -> int:
    """Distance de Hamming entre deux perceptual hashes (nombre de bits différents)."""
    return bin(int(a) ^ int(b)).count("1")


def est_near_duplicate(h1, h2, seuil: int = SEUIL_NEAR_DUP) -> bool:
    """True si deux dHash sont à <= seuil bits l'un de l'autre → même image (recyclage)."""
    if h1 is None or h2 is None:
        return False
    return hamming(h1, h2) <= seuil


def dhash(image_bytes: bytes, taille: int = 8) -> Optional[int]:
    """
    Difference hash (dHash) sur `taille*taille` bits (64 par défaut), robuste au
    1px et à la re-compression. Renvoie None si l'image est indécodable (Pillow
    absent ou bytes invalides) — l'appelant retombe alors sur le byte-hash.
    """
    if not image_bytes:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize(
            (taille + 1, taille), Image.LANCZOS)
    except Exception:
        return None
    px = img.load()
    bits = 0
    for y in range(taille):
        for x in range(taille):
            bits = (bits << 1) | (1 if px[x, y] > px[x + 1, y] else 0)
    return bits


def dhash_hex(image_bytes: bytes, taille: int = 8) -> str:
    """dHash en hexadécimal pour stockage TEXT (évite le débordement BIGINT signé).
    "" si indécodable."""
    h = dhash(image_bytes, taille)
    return "" if h is None else format(h, "016x")


def hex_vers_int(h: str) -> Optional[int]:
    """Reconvertit un dHash hexadécimal stocké en entier. None si vide/invalide."""
    if not h:
        return None
    try:
        return int(h, 16)
    except (ValueError, TypeError):
        return None
