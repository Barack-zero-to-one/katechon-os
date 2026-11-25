"""
Socle partagé du bot : réglages métier (frais, barème IRA, anti-fraude,
webhook), normalisation des numéros, recherches admin/owner/membre,
bannissement après trois fraudes, calendrier de collecte, crédit de
communication, inscription et liste de passage.

Extrait de barack_corp_v9_18.py sans changement de comportement. Dépend des
modules déjà extraits (db, audit, whatsapp, messages, trust_graph) et des
modules purs (ira_engine, katechon.security.webhook_security), jamais du bot. Les menus, le
paiement et le webhook l'importent quand ils quittent le bot.
"""
import logging
import os
import re
from datetime import date as _date, datetime, time
from typing import Optional

from katechon.finance import ira_engine
from katechon.security import webhook_security as ws
from katechon.core.audit import log_audit
from katechon.core.db import fetchall, fetchone, get_conn, q, release_conn, verrou_ordre_tontine
from katechon.messaging.messages import msg_dissuasion
from katechon.finance.trust_graph import _update_score_confiance
from katechon.messaging.whatsapp import OWNER_WA, wa_admin, wa_owner, wa_prive

__all__ = ["BouffageARegler", "bouffage_a_regler", "msg_bouffage_a_regler", "FRAIS_FMP", "HEURE_LIMITE_DEF", "IRA_CLIFF_PCT", "IRA_DAILY_PCT", "IRA_HOURLY_PCT",
    "IRA_CAP_PCT", "IRA_FLOOR", "IRA_GRACE_MIN", "FRAIS_REACTIV", "FRAIS_CHGNUM",
    "NUMERO_BADF_MTN", "NUMERO_BADF_ORANGE", "MAX_TENTATIVES_FRAUDE", "GREENAPI_WEBHOOK_SECRET",
    "WEBHOOK_REPLAY_TTL", "_replay_guard", "MEDIA_MAX_BYTES", "SESSION_TIMEOUT",
    "enregistrer_liste_passage", "inscrire_dans_tontine", "normaliser_numero",
    "valider_numero_cameroun", "calculer_ira_mise", "get_membre_by_wa", "get_tontines_admin",
    "est_owner", "est_admin", "incrementer_tentatives_fraude", "_alerter_burst_fraude",
    "_JOURS_SEMAINE", "_est_jour_collecte", "_est_jour_cotisation", "_verifier_credit_comm",
    "PORT", "DELAI_SUSPENSION_HEURES", "DELAI_ALERTE_FUGUE", "DELAI_BLOCAGE_FUGUE", "BACKUP_ROTATION", "HEURE_BACKUP", "PG_HOST", "PG_PORT", "PG_DB", "PG_USER", "PG_PASS", "PG_BIN", "BACKUP_DIR", "NGROK_DOMAIN", "NGROK_TOKEN", "MONTANT_IRA", "GROUPE_ADMIN", "enregistrer_admins_groupe"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


# ── Frais BADF Ltd ────────────────────────────────────────────────────────
FRAIS_FMP        = 0.00    # Gratuit au lancement — modèle 2% activable (une ligne)
HEURE_LIMITE_DEF = time(18, 0)

# ── IRA v2 — Katechon comportemental (barème indexé sur la mise) ───────────
# Modèle : falaise 15% dès la 6e min + 2%/jour linéaire, plafond 50%, plancher 200,
# arrondi bas 100. Rampe horaire optionnelle (IRA_HOURLY_PCT>0). Voir ira_engine.py.
IRA_CLIFF_PCT    = 0.15    # Socle Schelling : % de la mise dès la 6e minute de retard
IRA_DAILY_PCT    = 0.02    # Accrual journalier linéaire
IRA_HOURLY_PCT   = 0.00    # Rampe intra-day (0 = falaise plate ; 0.01 = urgence horaire)
IRA_CAP_PCT      = 0.50    # Plafond anti-usure (optics ANIF/COBAC)
IRA_FLOOR        = 200     # Plancher aligné grille 100 (protège les petits groupes)
IRA_GRACE_MIN    = 5       # Tolérance réseau MTN/Orange (minutes)


FRAIS_REACTIV    = 1_000   # Réactivation après suspension
FRAIS_CHGNUM     = 500     # Changement de numéro Mobile Money

# ── Numéro collecteur BADF — reçoit FMP + adhésions (jamais l'IRA, qui
#    revient aux membres ponctuels du groupe) ──────────────────────────────
NUMERO_BADF_MTN    = os.getenv("NUMERO_BADF_MTN",    "+237683603113")  # MTN MoMo
NUMERO_BADF_ORANGE = os.getenv("NUMERO_BADF_ORANGE", "+237692100606")  # Orange Money

# ── Anti-fraude général ────────────────────────────────────────────────────
MAX_TENTATIVES_FRAUDE   = 3


GREENAPI_WEBHOOK_SECRET = os.getenv("GREENAPI_WEBHOOK_SECRET", "")  # Token secret webhook (256 bits)


# Anti-rejeu webhook : le token statique dans l'URL n'empêche pas de rejouer une
# requête capturée. On déduplique les idMessage sur une fenêtre glissante, borné.
WEBHOOK_REPLAY_TTL = int(os.getenv("WEBHOOK_REPLAY_TTL", "900"))  # secondes
_replay_guard = ws.ReplayGuard(ttl=WEBHOOK_REPLAY_TTL, max_size=50_000)

# Plafond de taille d'un média téléchargé (anti-DoS mémoire côté SSRF).
MEDIA_MAX_BYTES = int(os.getenv("MEDIA_MAX_BYTES", str(15 * 1024 * 1024)))  # 15 Mo


SESSION_TIMEOUT = 300


class BouffageARegler(Exception):
    """Un tour bloqué ou approuvé mais impayé interdit de reconstruire la liste.
    args[0] : les tours concernés, tels que renvoyés par bouffage_a_regler."""


def msg_bouffage_a_regler(tours, action: str = "modifier la liste") -> str:
    """Refus nommant chaque tour et l'issue qui lui correspond (bloqué ou approuvé)."""
    bloques   = [f"#{x['id']}" for x in tours if x["etat"] == "bloque"]
    approuves = [f"#{x['id']} approuvé" for x in tours if x["etat"] == "approuve"]
    echecs    = [f"#{x['id']} en échec technique" for x in tours if x["etat"] == "echec"]
    parties = []
    if bloques:
        parties.append("passage " + ", ".join(bloques) + " bloqué : DEBLOQUER, BOUFFAGE_COMPLET "
                       "ou interception à l'expiration du blocage")
    if approuves:
        parties.append("passage " + ", ".join(approuves) + " : versé à la prochaine heure de "
                       "bouffage, ou ANNULER_ACCORD")
    if echecs:
        parties.append("passage " + ", ".join(echecs) + " : réessayé à la prochaine heure de "
                       "bouffage, ou ANNULER_ACCORD pour abandonner sa reprise")
    return ("❌ Un bouffage bloqué ou approuvé attend son règlement (" + " ; ".join(parties)
            + f") : impossible de {action} avant.")


_SQL_A_REGLER = """
    SELECT lp.id, lp.bloque_suspect, lp.debloque_le
    FROM liste_passage lp JOIN tontines t ON t.id = lp.tontine_id
    WHERE lp.tontine_id=%s AND lp.cycle = t.cycle_actuel AND lp.statut='En_attente'
      AND (%s::int IS NULL OR lp.membre_id = %s::int)
      AND (lp.bloque_suspect = 1 OR lp.debloque_le IS NOT NULL OR lp.echec_le IS NOT NULL)
    ORDER BY lp.ordre"""

_SQL_A_REGLER_VERROU = """
    SELECT lp.id, lp.bloque_suspect, lp.debloque_le, lp.echec_le
    FROM liste_passage lp JOIN tontines t ON t.id = lp.tontine_id
    WHERE lp.tontine_id=%s AND lp.cycle = t.cycle_actuel AND lp.statut='En_attente'
      AND (%s::int IS NULL OR lp.membre_id = %s::int)
    ORDER BY lp.ordre
    FOR UPDATE OF lp"""


def bouffage_a_regler(conn, tontine_id: int, membre_id: int = None, verrouiller: bool = False) -> list:
    """Tours ({"id", "approuve"}) du cycle en cours bloqués (contrôle suspect) ou approuvés
    (DEBLOQUER, BOUFFAGE_COMPLET) sans avoir encore été versés.

    Tant qu'il y en a, ni une cession ni une nouvelle liste ne doivent changer le
    titulaire ou supprimer le tour : l'accord serait perdu, ou la sanction du job
    d'expiration frapperait un autre membre. Avec `verrouiller`, tous les tours en
    attente sont verrouillés (FOR UPDATE) dans la transaction de l'appelant : un
    blocage posé par le job de bouffage pendant la modification est attendu puis vu.
    """
    if not verrouiller:
        lignes = fetchall(conn, _SQL_A_REGLER, (tontine_id, membre_id, membre_id))
    else:
        lignes = [l for l in fetchall(conn, _SQL_A_REGLER_VERROU, (tontine_id, membre_id, membre_id))
                  if l["bloque_suspect"] or l["debloque_le"] or l["echec_le"]]
    # Un tour en échec technique compte aussi : sinon une nouvelle liste le supprimerait sans
    # qu'il soit jamais repris ; ANNULER_ACCORD permet d'abandonner sa reprise.
    return [{"id": l["id"],
             "etat": "bloque" if l["bloque_suspect"] else "approuve" if l["debloque_le"] else "echec"}
            for l in lignes]


def enregistrer_liste_passage(tontine_id: int, liste: list, wa_admin: str) -> tuple:
    """
    Enregistre la liste parsée dans liste_passage.
    Gère les places multiples : si un nickname apparaît N fois dans la liste,
    le membre a N places → il cotise N × montant_base par période.
    Retourne (nb_ok, nb_non_lies).
    """
    conn  = get_conn()
    verrou_ordre_tontine(conn, tontine_id)  # FORTERESSE : sérialise l'ordre de cette tontine
    a_regler = bouffage_a_regler(conn, tontine_id, verrouiller=True)
    if a_regler:
        conn.rollback()
        release_conn(conn)
        raise BouffageARegler(a_regler)
    cycle = fetchone(conn,
        "SELECT cycle_actuel FROM tontines WHERE id=%s", (tontine_id,))["cycle_actuel"]

    # Supprimer l'ancienne liste de ce cycle
    q(conn, """DELETE FROM liste_passage
               WHERE tontine_id=%s AND cycle=%s AND statut='En_attente'""",
      (tontine_id, cycle))

    nb_ok       = 0
    nb_non_lies = 0

    # Compter les occurrences de chaque nickname → nombre de places
    from collections import Counter
    occurrences = Counter(
        item["nickname"].upper().strip() for item in liste
    )

    # Mapper nickname → membre_id (cache pour éviter les doublons de requêtes)
    nickname_cache = {}

    for item in liste:
        nick_key = item["nickname"].upper().strip()

        if nick_key not in nickname_cache:
            membre = fetchone(conn, """
                SELECT m.id FROM membres m
                JOIN adhesions a ON a.membre_id = m.id
                WHERE a.tontine_id=%s AND a.statut='Actif'
                  AND UPPER(m.nom_complet) LIKE UPPER(%s)
                LIMIT 1
            """, (tontine_id, f"%{item['nickname']}%"))
            nickname_cache[nick_key] = membre["id"] if membre else None

        membre_id = nickname_cache[nick_key]
        if not membre_id:
            nb_non_lies += 1

        # Enregistrer la ligne de passage — upsert MANUEL (ON CONFLICT retiré :
        # incompatible avec la contrainte DEFERRABLE ; sérialisé par le verrou).
        cur_up = q(conn, """
            UPDATE liste_passage SET
                nickname      = %s,
                date_bouffage = %s,
                membre_id     = %s,
                soumis_par    = %s
            WHERE tontine_id=%s AND cycle=%s AND ordre=%s
        """, (item["nickname"], item["date_bouffage"], membre_id, wa_admin,
              tontine_id, cycle, item["ordre"]))
        if cur_up.rowcount == 0:
            q(conn, """
                INSERT INTO liste_passage
                    (tontine_id, membre_id, nickname, date_bouffage, cycle, ordre, soumis_par)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (tontine_id, membre_id,
                  item["nickname"], item["date_bouffage"],
                  cycle, item["ordre"], wa_admin))
        nb_ok += 1

    # Mettre à jour nombre_places dans adhesions selon les occurrences réelles
    places_par_membre = {}
    for nick_key, nb in occurrences.items():
        mid = nickname_cache.get(nick_key)
        if mid:
            if mid not in places_par_membre:
                places_par_membre[mid] = 0
            places_par_membre[mid] += nb

    for membre_id, nb_places in places_par_membre.items():
        q(conn, """UPDATE adhesions SET nombre_places=%s
                   WHERE membre_id=%s AND tontine_id=%s""",
          (nb_places, membre_id, tontine_id))

    conn.commit()
    release_conn(conn)

    # Construire résumé des places multiples pour log
    multi = {k: v for k, v in occurrences.items() if v > 1}
    if multi:
        log.info(f"Places multiples détectées : {multi}")
        log_audit("PLACES_MULTIPLES",
                  f"Tontine {tontine_id} — {multi}", wa_admin)

    log_audit("LISTE_PASSAGE",
              f"Tontine {tontine_id} — {nb_ok} entrées, {nb_non_lies} non liées",
              wa_admin)
    return nb_ok, nb_non_lies


def inscrire_dans_tontine(membre_id: int, tontine_id: int,
                          nb_places: int = 1) -> None:
    """
    Inscrit un membre dans une tontine et l'ajoute à la liste de passage.
    Vérifie : capacité max, doublon.

    Sous le verrou d'ordre de la tontine (verrou_ordre_tontine), la place
    suivante est libre par construction : l'insertion se fait sans ON CONFLICT,
    que PostgreSQL refuse sur la contrainte DEFERRABLE (tontine, cycle, ordre).
    La connexion est rendue au pool sur tous les chemins, erreur comprise.
    """
    conn = get_conn()
    try:
        # Premier statement de la transaction (règle de verrou_ordre_tontine) : le
        # cycle et la capacité lus ensuite ne peuvent plus changer sous nos pieds.
        verrou_ordre_tontine(conn, tontine_id)
        membre = fetchone(conn,
            "SELECT nom_complet FROM membres WHERE id=%s", (membre_id,))
        if not membre:
            raise ValueError(f"Membre {membre_id} introuvable.")

        tontine = fetchone(conn,
            "SELECT capacite_max, cycle_actuel, nom FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine:
            raise ValueError(f"Tontine {tontine_id} introuvable.")

        deja = fetchone(conn, """SELECT 1 AS x FROM adhesions a
                                 WHERE a.membre_id=%s AND a.tontine_id=%s AND a.statut='Actif'
                                   AND EXISTS (SELECT 1 FROM liste_passage lp
                                               WHERE lp.membre_id=a.membre_id AND lp.tontine_id=a.tontine_id
                                                 AND lp.cycle=%s)""",
                        (membre_id, tontine_id, tontine["cycle_actuel"]))
        if deja:
            raise ValueError(f"{membre['nom_complet']} est déjà inscrit dans {tontine['nom']}.")

        nb_actuel = fetchone(conn,
            "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
            (tontine_id,))["n"]
        if nb_actuel + nb_places > tontine["capacite_max"]:
            raise ValueError(
                f"Tontine pleine : {nb_actuel}/{tontine['capacite_max']} membres.")

        # Adhésion (UNIQUE ordinaire sur membre_id, tontine_id : ON CONFLICT accepté)
        q(conn, """
            INSERT INTO adhesions (membre_id, tontine_id, nombre_places)
            VALUES (%s,%s,%s)
            ON CONFLICT (membre_id, tontine_id)
            DO UPDATE SET nombre_places=EXCLUDED.nombre_places, statut='Actif'
        """, (membre_id, tontine_id, nb_places))

        # Ajouter à la liste de passage, après le dernier ordre du cycle courant
        cycle     = tontine["cycle_actuel"]
        ordre_max = fetchone(conn,
            "SELECT COALESCE(MAX(ordre),0) m FROM liste_passage WHERE tontine_id=%s AND cycle=%s",
            (tontine_id, cycle))["m"]
        for i in range(nb_places):
            ordre_max += 1
            q(conn, """
                INSERT INTO liste_passage (tontine_id, membre_id, cycle, ordre)
                VALUES (%s,%s,%s,%s)
            """, (tontine_id, membre_id, cycle, ordre_max))

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        release_conn(conn)
    log.info(f"✅ Membre {membre_id} → tontine {tontine_id} ({nb_places} place(s))")
    log_audit("INSCRIPTION_TONTINE",
              f"Membre {membre_id} → {tontine['nom']} ({nb_places}p)")


def normaliser_numero(numero: str) -> str:
    """
    Normalise un numéro WhatsApp au format +237XXXXXXXXX.
    Gère tous les formats envoyés par Meta :
      - Format Meta : 237690123456 → +237690123456
      - Device suffix: 237690123456:12@s.whatsapp.net → +237690123456
      - Préfixe 00   : 00237690123456               → +237690123456
      - Court 9 ch.  : 690123456                    → +237690123456
      - Déjà bon     : +237690123456                → +237690123456
    """
    if not numero:
        return numero
    n = numero.strip()
    # Extraire avant @ (au cas où JID complet)
    if "@" in n:
        n = n.split("@")[0]
    # Extraire avant : (device suffix éventuel)
    if ":" in n:
        n = n.split(":")[0]
    # Supprimer tout sauf chiffres et +
    n = re.sub(r"[^\d+]", "", n)
    # Préfixe 00 → +
    if n.startswith("00"):
        n = "+" + n[2:]
    # Ajouter + si absent
    if not n.startswith("+"):
        n = "+" + n
    # Numéro court camerounais sans indicatif (6/7/8/9XXXXXXXX)
    if re.match(r"^\+[6-9]\d{8}$", n):
        n = "+237" + n[1:]
    return n


def valider_numero_cameroun(numero: str) -> bool:
    return bool(re.match(r"^\+237[26789]\d{8}$", numero))


def calculer_ira_mise(montant_mise: int, dt_paiement: datetime,
                      dt_echeance: datetime) -> dict:
    """
    IRA v2 indexée sur la mise (falaise 15% + 2%/jour, plafond 50%, plancher 200).
    Barème pur délégué à ira_engine — calcul côté serveur, incontournable.

    Args:
        montant_mise : montant de la cotisation attendue (la mise), FCFA.
        dt_paiement  : datetime du paiement effectif (aware WAT).
        dt_echeance  : échéance DATÉE de la période due (heure limite du jour de
                       collecte, AVANT la grâce). Calculée par
                       ira_engine.echeance_periode — jamais reconstruite ici sur
                       le jour de soumission (c'était la racine des findings #1/#2).
    """
    return ira_engine.calculer_ira(
        montant_mise, dt_paiement, dt_echeance,
        cliff=IRA_CLIFF_PCT, daily=IRA_DAILY_PCT, hourly=IRA_HOURLY_PCT,
        cap=IRA_CAP_PCT, floor=IRA_FLOOR, grace_min=IRA_GRACE_MIN,
    )


def get_membre_by_wa(wa: str) -> Optional[dict]:
    wa   = normaliser_numero(wa)
    conn = get_conn()
    m    = fetchone(conn, "SELECT * FROM membres WHERE whatsapp=%s", (wa,))
    release_conn(conn)
    return m


def get_tontines_admin(wa: str) -> list:
    n    = normaliser_numero(wa)
    conn = get_conn()
    rows = fetchall(conn,
        "SELECT tontine_id FROM admins_groupe WHERE whatsapp=%s", (n,))
    release_conn(conn)
    return [r["tontine_id"] for r in rows]


def est_owner(wa: str) -> bool:
    return normaliser_numero(wa) == normaliser_numero(OWNER_WA)


def est_admin(wa: str) -> bool:
    return bool(get_tontines_admin(wa)) or est_owner(wa)


def incrementer_tentatives_fraude(membre_id: int, raison: str):
    conn = get_conn()
    try:
        q(conn,
          "UPDATE membres SET tentatives_fraude=tentatives_fraude+1 WHERE id=%s",
          (membre_id,))
        row = fetchone(conn,
            "SELECT tentatives_fraude, nom_complet, whatsapp FROM membres WHERE id=%s",
            (membre_id,))
        conn.commit()
        log_audit("TENTATIVE_FRAUDE",
                  f"{row['nom_complet']} — {raison} — #{row['tentatives_fraude']}",
                  row["whatsapp"])
        if row["tentatives_fraude"] >= MAX_TENTATIVES_FRAUDE:
            q(conn,
              "UPDATE membres SET statut_global='Banni', blackliste=1 WHERE id=%s",
              (membre_id,))
            _update_score_confiance(conn, membre_id, set_val=0, raison="Bannissement automatique — fraude")
            q(conn, """INSERT INTO sanctions (membre_id, type_sanction, notes)
                       VALUES (%s,'Blocage_permanent',%s)""",
              (membre_id, f"Auto-bannissement après {row['tentatives_fraude']} tentatives"))
            conn.commit()
            log_audit("BANNISSEMENT_AUTO", f"{row['nom_complet']} banni", row["whatsapp"])
            wa_admin(
                f"🚨 *MEMBRE BANNI — BADF Ltd*\n"
                f"👤 {row['nom_complet']} | {row['whatsapp']}\n"
                f"Raison : {row['tentatives_fraude']} tentatives de fraude\n"
                f"Dernière : {raison}"
            )
            wa_prive(row["whatsapp"], msg_dissuasion(row["whatsapp"]))
        _alerter_burst_fraude()
    finally:
        release_conn(conn)


def _alerter_burst_fraude():
    """DE — Alerte owner si ≥5 tentatives fraude sur n'importe quel membre en 1h (attaque coordonnée)."""
    try:
        conn = get_conn()
        row = fetchone(conn, """
            SELECT COUNT(*) as nb FROM audit_log
            WHERE type_event = 'TENTATIVE_FRAUDE'
            AND date_heure > NOW() - INTERVAL '1 hour'
        """)
        release_conn(conn)
        if row and row["nb"] >= 5:
            wa_owner(
                f"🚨 *ALERTE COORDONNÉE — BADF Ltd*\n"
                f"{row['nb']} tentatives de fraude détectées en 1h.\n"
                f"Vérifier audit_log immédiatement."
            )
            log_audit("ALERTE_BURST_FRAUDE", f"{row['nb']} tentatives en 1h")
    except Exception:
        pass


_JOURS_SEMAINE = {"Lundi":0,"Mardi":1,"Mercredi":2,"Jeudi":3,"Vendredi":4,"Samedi":5,"Dimanche":6}

def _est_jour_collecte(t: dict, d: _date) -> bool:
    """
    True si la date `d` est un jour de cotisation pour cette tontine.
    Source UNIQUE du calendrier de collecte (rappels + échéance IRA) :
      • Journaliere  → tout jour
      • Hebdomadaire → jour_semaine
      • Mensuelle    → jour_mois (1-28)
    Date-paramétrique : sert aussi à ira_engine.echeance_periode pour dater
    l'échéance d'un paiement en retard, pas seulement à tester "aujourd'hui".
    """
    tt = t.get("type_tontine", "Journaliere")
    if tt == "Journaliere":
        return True
    if tt == "Hebdomadaire":
        jour_cible = _JOURS_SEMAINE.get(t.get("jour_semaine"))
        if jour_cible is None:
            log.warning(f"jour_semaine invalide '{t.get('jour_semaine')}' tontine#{t.get('id')}")
            return False
        return d.weekday() == jour_cible
    if tt == "Mensuelle":
        return d.day == (t.get("jour_mois") or 1)
    return True


def _est_jour_cotisation(t: dict) -> bool:
    """True si aujourd'hui (tz de la tontine) est un jour de cotisation."""
    if t.get("type_tontine", "Journaliere") == "Journaliere":
        return True
    tz_name = t.get("timezone") or "Africa/Douala"
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        today = datetime.now().date()
    return _est_jour_collecte(t, today)


def _verifier_credit_comm(conn, tontine_id: int):
    """Débloque le crédit comm 1000 FCFA après 5 cotisations confirmées réelles.
    Prévient les faux groupes créés pour obtenir le bonus sans activité réelle."""
    try:
        tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine or tontine["credit_comm_statut"] != "Non_eligible":
            return
        nb_tx = fetchone(conn, """
            SELECT COUNT(*) AS n FROM transactions
            WHERE tontine_id=%s AND type_transaction='Cotisation' AND statut='Confirmee'
        """, (tontine_id,))["n"]
        if nb_tx >= 5:
            q(conn, "UPDATE tontines SET credit_comm_statut='Eligible' WHERE id=%s", (tontine_id,))
            conn.commit()
            log_audit("CREDIT_COMM_ELIGIBLE",
                      f"Tontine {tontine['nom']} — {nb_tx} tx confirmées", "system")
            try:
                wa_prive(OWNER_WA,
                    f"🎁 *CRÉDIT COMM À VERSER*\n\n"
                    f"Tontine : *{tontine['nom']}*\n"
                    f"Transactions confirmées : *{nb_tx}*\n\n"
                    f"Versez *1 000 FCFA* de crédit à l'admin de ce groupe.\n"
                    f"Tapez *CREDIT_VERSE {tontine_id}* pour confirmer le versement."
                )
            except Exception as _wa_e:
                log.warning(f"⚠️ CREDIT_COMM notif échouée — DB=Eligible, tontine#{tontine_id} : {_wa_e}. "
                            f"Tapez CREDIT_VERSE {tontine_id} pour confirmer manuellement.")
    except Exception as _e:
        log.warning(f"⚠️ _verifier_credit_comm tontine#{tontine_id} : {_e}")

# ── Réglages d'exploitation (port, sanctions, sauvegarde, PostgreSQL, webhook public) ──
# Extraits du bot avec les jobs et le scheduler (katechon/finance/jobs.py, katechon/core/scheduler.py).
PORT             = 5000

DELAI_SUSPENSION_HEURES = 72
DELAI_ALERTE_FUGUE      = 3
DELAI_BLOCAGE_FUGUE     = 7
BACKUP_ROTATION         = 7
HEURE_BACKUP           = 2

# ── PostgreSQL ────────────────────────────────────────────────────────────
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB   = os.getenv("PG_DB",   "barack_corp")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASS = os.getenv("PG_PASS", "")
PG_BIN  = os.getenv("PG_BIN",  r"C:\Program Files\PostgreSQL\18\bin")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")

# ── Webhook public (ngrok ou domaine) ────────────────────────────────────
NGROK_DOMAIN  = os.getenv("NGROK_DOMAIN", "lennox-unbiographical-jasmin.ngrok-free.dev")
NGROK_TOKEN   = os.getenv("NGROK_TOKEN",  "")


# ── Divers repris du bot : ancien montant IRA, groupe admin, enregistrement des admins ──
MONTANT_IRA      = 150     # DÉPRÉCIÉ — conservé le temps de migrer les anciens messages

GROUPE_ADMIN = "Admin Barack Corp"

def enregistrer_admins_groupe(tontine_id: int, admins: list) -> None:
    """
    Enregistre les admins WhatsApp d'un groupe en base.
    admins = [{"whatsapp": "+237...", "nom": "Jean"}, ...]
    Appelé automatiquement quand le bot rejoint un groupe.
    """
    if not admins:
        return
    conn = get_conn()
    for a in admins:
        wa  = normaliser_numero(a.get("whatsapp", ""))
        nom = a.get("nom", "")
        if not wa:
            continue
        q(conn, """
            INSERT INTO admins_groupe (tontine_id, whatsapp, nom)
            VALUES (%s,%s,%s)
            ON CONFLICT (tontine_id, whatsapp) DO UPDATE SET nom=%s
        """, (tontine_id, wa, nom, nom))
    conn.commit()
    release_conn(conn)
    log.info(f"Admins enregistrés tontine {tontine_id}: {[a.get('whatsapp') for a in admins]}")
    log_audit("ADMINS_ENREGISTRES",
              f"Tontine {tontine_id} — {len(admins)} admin(s)")
