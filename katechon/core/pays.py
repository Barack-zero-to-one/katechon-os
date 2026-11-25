"""
Configuration multi-pays (Cameroun, Sénégal, Côte d'Ivoire...) : indicatif,
devise, fuseau, régulateur, références légales, vocabulaire local, et les
helpers get_pays et format_montant.

Extrait de barack_corp_v9_18.py sans changement de comportement ; module pur.
"""



__all__ = ["COUNTRY_CONFIG", "DEFAULT_COUNTRY", "get_pays", "format_montant"]



# ── Configuration multi-pays (i18n + payments rails) ─────────────────────
# Chaque tontine peut avoir son country_code → on lit la config ici.
# Pour ajouter un pays : ajouter une entrée + traduire les messages clés.
COUNTRY_CONFIG = {
    "CM": {
        "name":           "Cameroon",
        "flag":           "🇨🇲",
        "phone_prefix":   "+237",
        "phone_local":    "237",
        "currency":       "FCFA",
        "currency_full":  "Franc CFA",
        "timezone":       "Africa/Douala",
        "language":       "fr",
        "mobile_money":   ["MTN MoMo", "Orange Money"],
        "regulator":      "COBAC",
        "regulator_ref":  "Règlement COBAC R-2019/01",
        "aml_agency":     "ANIF",
        "penal_ref":      "Articles 318 à 323 du Code Pénal Camerounais",
        "tontine_name":   "tontine",
        "tontine_admin":  "président de tontine",
        "decimal_sep":    ",",
        "thousand_sep":   " ",
    },
    "SN": {
        "name":           "Senegal",
        "flag":           "🇸🇳",
        "phone_prefix":   "+221",
        "phone_local":    "221",
        "currency":       "FCFA",
        "timezone":       "Africa/Dakar",
        "language":       "fr",
        "mobile_money":   ["Wave", "Orange Money", "Free Money"],
        "regulator":      "BCEAO",
        "tontine_name":   "tontine",
    },
    "CI": {
        "name":           "Côte d'Ivoire",
        "flag":           "🇨🇮",
        "phone_prefix":   "+225",
        "currency":       "FCFA",
        "timezone":       "Africa/Abidjan",
        "language":       "fr",
        "mobile_money":   ["Wave", "MTN MoMo", "Orange Money", "Moov"],
        "regulator":      "BCEAO",
        "tontine_name":   "tontine",
    },
}

DEFAULT_COUNTRY = "CM"  # Cameroun par défaut


def get_pays(code: str = "CM") -> dict:
    """Retourne la config pays depuis COUNTRY_CONFIG (plus de table DB)."""
    cfg = COUNTRY_CONFIG.get(code, COUNTRY_CONFIG[DEFAULT_COUNTRY])
    return {
        "code":         code,
        "nom":          cfg.get("name",         "Cameroun"),
        "devise":       cfg.get("currency",      "FCFA"),
        "indicatif":    cfg.get("phone_prefix",  "+237"),
        "longueur_num": 9,
        "langue":       cfg.get("language",      "fr"),
        "timezone":     cfg.get("timezone",      "Africa/Douala"),
        "momo_ops":     [],
        "regulateur":   cfg.get("regulator",     "COBAC"),
    }


def format_montant(montant: int, pays_code: str = "CM") -> str:
    """
    Formate un montant avec la devise du pays.
    format_montant(5000, "CM")  → "5 000 FCFA"
    format_montant(5000, "SN")  → "5 000 FCFA"
    format_montant(5000, "CI")  → "5 000 FCFA"
    """
    devise = get_pays(pays_code).get("devise", "FCFA")
    return f"{montant:,} {devise}".replace(",", " ")
