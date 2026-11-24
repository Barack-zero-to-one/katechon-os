"""
Journal d'audit du bot : masquage des numéros, ligne dans la table audit_log,
journal append-only logs/audit_immutable.log et logger "Audit".

Extrait de barack_corp_v9_18.py sans changement de comportement ; dépend de
db.py, jamais du bot. Le handler fichier du logger "Audit"
(audit_securite.log) reste configuré par le bot.
"""
import logging
from datetime import datetime

from db import get_conn, q, release_conn

__all__ = ["log_audit", "_masquer_wa"]

audit = logging.getLogger("Audit")   # même logger que le bot


def _masquer_wa(wa: str) -> str:
    if len(wa) > 6:
        return wa[:4] + "****" + wa[-2:]
    return "****"


def log_audit(type_event: str, details: str, whatsapp: str = "", ip: str = ""):
    wa_masque = _masquer_wa(whatsapp) if whatsapp else ""
    audit.warning(f"[{type_event}] {details} | WA:{wa_masque} | IP:{ip}")
    try:
        conn = get_conn()
        q(conn,
          "INSERT INTO audit_log (type_event, details, whatsapp, ip) VALUES (%s,%s,%s,%s)",
          (type_event, details, wa_masque, ip))
        conn.commit()
        release_conn(conn)
        # Journal immuable append-only
        with open("logs/audit_immutable.log", "a", encoding="utf-8") as _f:
            _f.write(f"{datetime.now().isoformat()}|{type_event}|{details}|{wa_masque}\n")
    except Exception:
        pass
