"""
Paiement manuel : anti-recyclage des captures et des références, saisie et
confirmation des cotisations (IRA, slips, reclassement), bouffage et
virement, fin et lancement de cycle, clôture, dettes BADF des admins,
cautions des fugitifs et leur libération.

Extrait de barack_corp_v9_18.py sans changement de comportement. Dépend de
socle, db, audit, health, whatsapp, ira_engine et screenshot_integrity,
jamais du bot ; le webhook, les menus et le scheduler du bot l'appellent.
"""
import logging
import hashlib
import re
from datetime import datetime, time, timedelta

import ira_engine
import psycopg2.extras
from katechon.security import screenshot_integrity as si
from audit import log_audit
from db import fetchall, fetchone, get_conn, q, release_conn, verrou_ordre_tontine
from health import healed
from socle import (
    FRAIS_FMP,
    HEURE_LIMITE_DEF,
    IRA_CLIFF_PCT,
    IRA_DAILY_PCT,
    IRA_GRACE_MIN,
    NUMERO_BADF_MTN,
    NUMERO_BADF_ORANGE,
    _est_jour_collecte,
    _verifier_credit_comm,
    calculer_ira_mise,
    normaliser_numero,
    valider_numero_cameroun,
)
from whatsapp import wa_admins_tontine, wa_groupe, wa_owner, wa_prive, wa_quitter_groupe

__all__ = ["hash_screenshot", "screenshot_deja_utilise", "enregistrer_screenshot",
    "reference_deja_utilisee", "enregistrer_reference", "screenshot_near_duplicate",
    "MontantAberrantError", "enregistrer_cotisation_manuelle", "_incrementer_slip_cycle",
    "_reclasser_en_dernier", "confirmer_cotisation", "rejeter_cotisation",
    "declencher_bouffage_manuel", "traiter_reponse_numero_bouffage", "_solder_versement",
    "confirmer_bouffage_vire", "_rapport_ponctualite_cycle", "_verifier_fin_cycle",
    "demarrer_nouveau_cycle", "cloturer_tontine", "rapport_dettes_badf_admin",
    "enregistrer_paiement_badf", "_get_retardataires", "saisir_caution_et_compenser_groupe",
    "verifier_et_liberer_cautions", "_verifier_liberation_caution"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


def hash_screenshot(image_bytes: bytes) -> str:
    """SHA-256 du contenu brut du screenshot — anti-recyclage."""
    return hashlib.sha256(image_bytes).hexdigest()


def screenshot_deja_utilise(conn, image_hash: str) -> bool:
    """Retourne True si ce screenshot a déjà été soumis."""
    row = fetchone(conn,
        "SELECT id FROM screenshots_hash WHERE hash=%s", (image_hash,))
    return row is not None


def enregistrer_screenshot(conn, image_hash: str,
                            membre_id: int, tontine_id: int, dhash_hex: str = ""):
    """Enregistre le hash brut + le perceptual hash (dHash) pour l'anti-recyclage.

    Écrit dans la transaction de l'appelant (enregistrer_cotisation_manuelle,
    avant son commit). Un échec est isolé dans un SAVEPOINT : il est logué sans
    annuler la cotisation en cours.
    """
    try:
        q(conn, "SAVEPOINT anti_recyclage_hash")
        q(conn,
          """INSERT INTO screenshots_hash (hash, membre_id, tontine_id, dhash)
             VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
          (image_hash, membre_id, tontine_id, dhash_hex or None))
        q(conn, "RELEASE SAVEPOINT anti_recyclage_hash")
    except Exception as e:
        # ON CONFLICT DO NOTHING absorbe déjà le cas bénin (hash déjà vu) sans
        # jamais lever — donc toute exception ici est anormale (DB down, FK
        # invalide, etc.) ET signifie que le hash n'a PAS été enregistré :
        # l'anti-recyclage est silencieusement désactivé pour ce screenshot.
        # Avaler l'erreur sans trace rendait ça indétectable — on logue.
        log.error(f"❌ enregistrer_screenshot échec (anti-recyclage NON enregistré) "
                  f"membre={membre_id} tontine={tontine_id}: {e}")
        try:
            q(conn, "ROLLBACK TO SAVEPOINT anti_recyclage_hash")
        except Exception:
            pass


def reference_deja_utilisee(conn, tontine_id: int, ref_norm: str) -> bool:
    """
    True si cette référence de transaction a déjà servi dans la tontine.
    Couche anti-recyclage FORTE : la référence est unique par paiement réel, donc
    insensible au re-encodage/1px qui défait le byte-hash (finding #3).
    """
    if not ref_norm:
        return False
    row = fetchone(conn,
        "SELECT id FROM references_transaction WHERE tontine_id=%s AND reference_norm=%s",
        (tontine_id, ref_norm))
    return row is not None


def enregistrer_reference(conn, tontine_id: int, ref_norm: str, membre_id: int):
    """Mémorise une référence de transaction (dédup ON CONFLICT via UNIQUE),
    dans la transaction de l'appelant ; un échec est isolé par SAVEPOINT."""
    if not ref_norm:
        return
    try:
        q(conn, "SAVEPOINT anti_recyclage_ref")
        q(conn,
          """INSERT INTO references_transaction (tontine_id, reference_norm, membre_id)
             VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
          (tontine_id, ref_norm, membre_id))
        q(conn, "RELEASE SAVEPOINT anti_recyclage_ref")
    except Exception as e:
        log.error(f"❌ enregistrer_reference échec (anti-recyclage NON enregistré) "
                  f"membre={membre_id} tontine={tontine_id}: {e}")
        try:
            q(conn, "ROLLBACK TO SAVEPOINT anti_recyclage_ref")
        except Exception:
            pass


def screenshot_near_duplicate(conn, membre_id: int, dh_int) -> bool:
    """
    Signal SOUPLE : True si un screenshot perceptuellement identique (dHash à
    <= SEUIL bits) existe déjà pour ce membre. Sert d'ALERTE admin, jamais de
    blocage dur — deux reçus MoMo distincts partagent le même template et sont
    donc naturellement proches en dHash (faux positif inacceptable en blocage).
    """
    if dh_int is None:
        return False
    rows = fetchall(conn,
        """SELECT dhash FROM screenshots_hash
           WHERE membre_id=%s AND dhash IS NOT NULL AND dhash <> ''
           ORDER BY created_at DESC LIMIT 200""",
        (membre_id,))
    for r in rows:
        autre = si.hex_vers_int(r["dhash"])
        if si.est_near_duplicate(dh_int, autre):
            return True
    return False


class MontantAberrantError(ValueError):
    """Levée quand le montant saisi est trop éloigné du montant attendu."""
    pass


def enregistrer_cotisation_manuelle(conn, membre_id: int, tontine_id: int,
                                     montant: int, screenshot_hash: str,
                                     admin_wa: str,
                                     force: bool = False,
                                     dhash_hex: str = "",
                                     ref_norm: str = "") -> int:
    """
    Enregistre une cotisation manuelle en attente de confirmation admin.
    Valide la cohérence du montant avant insertion (PATCH 2 v9.18).

    Args:
        force: Si True, accepte le montant même si écart 15-50%
               (admin confirme explicitement via commande FORCE).

    Raises:
        MontantAberrantError: Si écart > 50% du montant attendu, OU
                              si écart 15-50% sans force=True.
        ValueError: Si tontine introuvable ou montant <= 0.
        psycopg2.errors.UniqueViolation: Si screenshot_hash déjà utilisé
                                          (filet DB du PATCH 3).

    Returns:
        ID de la cotisation créée.
    """
    # ── Validations basiques ──────────────────────────────────────────
    if montant <= 0:
        raise ValueError(f"Montant invalide: {montant} (doit être > 0)")

    # ── Récupérer le montant attendu de la tontine ────────────────────
    tontine = fetchone(conn,
        "SELECT id, nom, montant_place FROM tontines WHERE id=%s",
        (tontine_id,))

    if not tontine:
        raise ValueError(f"Tontine #{tontine_id} introuvable")

    # Un membre peut détenir plusieurs places → l'attendu doit être mis à l'échelle,
    # sinon tout membre multi-places déclenche un écart artificiel (jusqu'à 100%)
    # et se fait rejeter à tort par MontantAberrantError.
    adhesion_places = fetchone(conn,
        "SELECT nombre_places FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
        (membre_id, tontine_id))
    nb_places = (adhesion_places["nombre_places"] if adhesion_places else 1) or 1
    montant_attendu = tontine["montant_place"] * nb_places

    # ── Calculer l'écart relatif ──────────────────────────────────────
    if montant_attendu > 0:
        ecart_pct = abs(montant - montant_attendu) / montant_attendu * 100
    else:
        ecart_pct = 0

    # ── Règles de validation ──────────────────────────────────────────
    if ecart_pct > 50:
        log_audit("COTISATION_REJETEE_ABERRANTE",
                  f"Membre#{membre_id} | Tontine#{tontine_id} ({tontine['nom']}) | "
                  f"Saisie:{montant:,} | Attendu:{montant_attendu:,} | "
                  f"Ecart:{ecart_pct:.1f}% | Admin:{admin_wa}")
        raise MontantAberrantError(
            f"❌ *Montant aberrant détecté.*\n\n"
            f"Saisie : *{montant:,} FCFA*\n"
            f"Attendu pour {tontine['nom']} : *{montant_attendu:,} FCFA*\n"
            f"Écart : {ecart_pct:.1f}%\n\n"
            f"_Vérifier la saisie. Si vraiment correct, contacter BADF._"
        )

    elif ecart_pct > 15 and not force:
        log_audit("COTISATION_ECART_IMPORTANT",
                  f"Membre#{membre_id} | Saisie:{montant:,} | "
                  f"Attendu:{montant_attendu:,} | Ecart:{ecart_pct:.1f}%")
        raise MontantAberrantError(
            f"⚠️ *Écart important détecté*\n\n"
            f"Saisie : *{montant:,} FCFA*\n"
            f"Attendu : *{montant_attendu:,} FCFA*\n"
            f"Écart : {ecart_pct:.1f}%\n\n"
            f"_Pour confirmer quand même, taper :_\n"
            f"*FORCE {membre_id} {montant}*"
        )

    elif ecart_pct > 5:
        log_audit("COTISATION_ECART_LEGER",
                  f"Membre#{membre_id} | Saisie:{montant:,} | "
                  f"Attendu:{montant_attendu:,} | Ecart:{ecart_pct:.1f}%")

    # ── Insertion (catch UniqueViolation du filet DB PATCH 3) ────────
    fmp = int(montant * FRAIS_FMP)
    try:
        cur = q(conn, """
            INSERT INTO cotisations_manuelles
                (membre_id, tontine_id, montant_declare, fmp_du, screenshot_hash)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (membre_id, tontine_id, montant, fmp, screenshot_hash))
        cotis_id = cur.fetchone()[0]

        # Créer la dette FMP pour l'admin → à reverser à BADF. Aucune dette
        # quand la FMP vaut 0 (lancement sans frais) : rien n'est dû.
        if fmp > 0:
            q(conn, """
                INSERT INTO dettes_badf (admin_wa, tontine_id, type_dette, montant, ref_cotis)
                VALUES (%s,%s,'FMP',%s,%s)
            """, (admin_wa, tontine_id, fmp, cotis_id))

        # Anti-recyclage dans la MÊME transaction que la cotisation : écrits
        # avant le commit, sinon release_conn les annule (rollback).
        if screenshot_hash:
            enregistrer_screenshot(conn, screenshot_hash, membre_id, tontine_id, dhash_hex)
        enregistrer_reference(conn, tontine_id, ref_norm, membre_id)

        conn.commit()
        return cotis_id

    except psycopg2.errors.UniqueViolation as e:
        conn.rollback()
        log_audit("DOUBLON_COTISATION_BLOQUE_DB",
                  f"Membre#{membre_id} | Tentative doublon: {str(e)[:100]}")
        raise ValueError(
            "❌ Cette cotisation existe déjà (doublon détecté par la base)."
        )


def _incrementer_slip_cycle(conn, membre_id: int, tontine_id: int, cycle: int) -> int:
    """
    Enregistre un slip (retard hors grâce) pour ce membre/cycle et retourne le
    nombre de slips AVANT celui-ci (0 => ce slip est le mulligan).

    Upsert atomique : la ligne ponctualite_cycle pilote l'éligibilité bonus et la
    priorité de rotation du cycle suivant (voir ira_engine.eligibilite_apres_slip).
    """
    row = fetchone(conn, """
        INSERT INTO ponctualite_cycle (membre_id, tontine_id, cycle, slips, eligible)
        VALUES (%s, %s, %s, 1, TRUE)
        ON CONFLICT (membre_id, tontine_id, cycle)
        DO UPDATE SET slips      = ponctualite_cycle.slips + 1,
                      eligible   = (ponctualite_cycle.slips + 1) <= 1,
                      updated_at = NOW()
        RETURNING slips
    """, (membre_id, tontine_id, cycle))
    slips_apres = row["slips"] if row else 1
    return slips_apres - 1


def _reclasser_en_dernier(conn, membre_id: int, tontine_id: int) -> dict:
    """
    Paiement après heure_limite → déplace le membre en dernière position
    dans liste_passage (cycle actif, statut En_attente ou Notifie).
    Retourne {"ok": True, "position_avant": N, "position_apres": M}
    ou {"ok": False} si déjà dernier / déjà bouffé.
    """
    # FORTERESSE : sérialise toute mutation d'ordre de cette tontine (tenu au commit)
    verrou_ordre_tontine(conn, tontine_id)

    passage = fetchone(conn, """
        SELECT id, ordre, cycle FROM liste_passage
        WHERE membre_id=%s AND tontine_id=%s
          AND statut IN ('En_attente','Notifie')
        ORDER BY cycle DESC LIMIT 1
    """, (membre_id, tontine_id))

    if not passage:
        return {"ok": False}

    cycle        = passage["cycle"]
    ordre_actuel = passage["ordre"]

    max_row = fetchone(conn,
        "SELECT MAX(ordre) AS max_ordre FROM liste_passage WHERE tontine_id=%s AND cycle=%s",
        (tontine_id, cycle))
    max_ordre = (max_row["max_ordre"] or ordre_actuel) if max_row else ordre_actuel

    if ordre_actuel >= max_ordre:
        return {"ok": False}  # déjà dernier

    # Sortir d'abord le membre vers une position sentinelle (-1) pour libérer son slot :
    # sans ça, le décalage `ordre-1` collisionne sur UNIQUE(tontine,cycle,ordre) tant que
    # le membre occupe encore ordre_actuel (doublon transitoire = UniqueViolation).
    q(conn, "UPDATE liste_passage SET ordre=-1 WHERE id=%s", (passage["id"],))

    # Décaler les membres entre l'ancienne position+1 et la dernière de -1 (slot libéré)
    q(conn, """
        UPDATE liste_passage SET ordre = ordre - 1
        WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND ordre <= %s
    """, (tontine_id, cycle, ordre_actuel, max_ordre))

    # Mettre le membre en dernière position (slot max_ordre désormais vacant)
    q(conn, "UPDATE liste_passage SET ordre=%s WHERE id=%s", (max_ordre, passage["id"]))
    return {"ok": True, "position_avant": ordre_actuel, "position_apres": max_ordre}


def confirmer_cotisation(conn, cotis_id: int, admin_wa: str) -> dict:
    """
    Admin confirme une cotisation manuelle.
    Met à jour le registre et notifie le membre.

    PATCH 1 v9.18 : SELECT FOR UPDATE pour verrou pessimiste PostgreSQL.
    Empêche la race condition si l'admin clique plusieurs fois rapidement.
    """
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # ── VERROU PESSIMISTE : SELECT FOR UPDATE ─────────────────────
        # La ligne est verrouillée pour les autres transactions jusqu'au COMMIT.
        # Si un autre admin essaye SELECT FOR UPDATE sur la même ligne,
        # il attend que cette transaction finisse.
        cur.execute(
            "SELECT * FROM cotisations_manuelles WHERE id=%s FOR UPDATE",
            (cotis_id,)
        )
        cotis = cur.fetchone()

        if not cotis:
            conn.rollback()
            return {"ok": False, "msg": "Cotisation introuvable."}

        if cotis["statut"] != "En_attente":
            # Idempotence : si déjà confirmée, on retourne un message clair
            conn.rollback()
            return {"ok": False, "msg": f"Déjà traitée ({cotis['statut']})."}

        # ── UPDATE (le verrou est toujours actif jusqu'au commit) ─────
        cur.execute("""
            UPDATE cotisations_manuelles
            SET statut='Confirme', confirme_par=%s, date_confirmation=NOW()
            WHERE id=%s
        """, (admin_wa, cotis_id))

        # ── INSERT dans transactions ──────────────────────────────────
        cur.execute("""
            INSERT INTO transactions
                (membre_id, tontine_id, montant_brut, frais_fmp,
                 montant_net, type_transaction, statut)
            VALUES (%s,%s,%s,%s,%s,'Cotisation','Confirmee')
        """, (cotis["membre_id"], cotis["tontine_id"],
              cotis["montant_declare"], cotis["fmp_du"],
              cotis["montant_declare"] - cotis["fmp_du"]))

        conn.commit()  # Le verrou est libéré ici

    except Exception as e:
        try:    conn.rollback()
        except: pass
        log.error(f"❌ confirmer_cotisation #{cotis_id} échec: {e}")
        return {"ok": False, "msg": f"Erreur technique: {str(e)[:80]}"}
    finally:
        cur.close()

    # ── Notifier le membre (hors transaction) ─────────────────────────
    membre = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s",
        (cotis["membre_id"],))
    tontine = fetchone(conn,
        "SELECT id, nom, heure_limite, heure_ouverture, montant_place, cycle_actuel, "
        "       type_tontine, jour_semaine, jour_mois "
        "FROM tontines WHERE id=%s", (cotis["tontine_id"],))

    if membre and tontine:
        wa_prive(membre["whatsapp"],
            f"✅ *COTISATION CONFIRMÉE — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Bonjour *{membre['nom_complet']}*,\n\n"
            f"Votre cotisation de *{cotis['montant_declare']:,} FCFA* "
            f"a été confirmée et enregistrée.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    # ── Reclassement : retard mesuré contre l'ÉCHÉANCE DATÉE de la période due ──
    #    (pas l'heure limite du jour de soumission — cf. ira_engine.echeance_periode).
    #    Gate ET calcul IRA partagent la MÊME échéance → impossible de diverger.
    try:
        from datetime import timezone, timedelta as _td
        WAT   = timezone(_td(hours=1))            # tz de collecte (Africa/Douala, +1)
        h_lim = (tontine or {}).get("heure_limite") or "18:00"
        h_ouv = (tontine or {}).get("heure_ouverture") or "05:00"
        try:
            _lh, _lm = map(int, h_lim.split(":")); t_limite = time(_lh, _lm)
        except Exception:
            t_limite = HEURE_LIMITE_DEF
        try:
            _oh, _om = map(int, h_ouv.split(":")); t_ouverture = time(_oh, _om)
        except Exception:
            t_ouverture = time(5, 0)

        soumis_dt    = cotis["date_soumission"].astimezone(WAT)
        soumis_local = soumis_dt.time()  # conservé pour l'affichage groupe/audit
        dt_echeance  = ira_engine.echeance_periode(
            soumis_dt,
            lambda d: _est_jour_collecte(tontine, d),
            t_ouverture, t_limite,
        )
        grace_fin = dt_echeance + _td(minutes=IRA_GRACE_MIN)

        if soumis_dt > grace_fin:
            # 1. IRA v2 indexée sur la mise — collecte À LA SOURCE (à ce paiement).
            #    La mise réelle inclut nombre_places : un membre à 3 places retient
            #    3× plus d'argent en retard, la pénalité doit suivre.
            _places = fetchone(conn,
                "SELECT nombre_places FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
                (cotis["membre_id"], cotis["tontine_id"]))
            mise    = tontine["montant_place"] * ((_places["nombre_places"] if _places else 1) or 1)
            cycle   = tontine["cycle_actuel"]
            calc_ira  = calculer_ira_mise(mise, soumis_dt, dt_echeance)
            montant_ira = calc_ira["ira"]
            motif_ira = (
                f"Retard cotisation — payé {soumis_dt.strftime('%d/%m %H:%M')} "
                f"> échéance {dt_echeance.strftime('%d/%m %H:%M')} "
                f"| J+{calc_ira['jours_retard']}"
                + (" | plafond 50%" if calc_ira["plafonnee"] else "")
            )
            q(conn, """INSERT INTO dettes_ira (membre_id, tontine_id, montant, motif)
                       VALUES (%s, %s, %s, %s)""",
              (cotis["membre_id"], cotis["tontine_id"], montant_ira, motif_ira))
            conn.commit()

            # 1bis. Tracking mulligan/éligibilité (1 slip réseau toléré/cycle).
            slips_avant = _incrementer_slip_cycle(conn, cotis["membre_id"],
                                                  cotis["tontine_id"], cycle)
            elig = ira_engine.eligibilite_apres_slip(slips_avant)
            conn.commit()

            # 2. Reclassement en dernière position — SEULEMENT à l'éjection (2e slip).
            #    Le mulligan (1er slip) préserve la position ; le cliff IRA suffit.
            reclasse = {"ok": False}
            if elig["ejecte"]:
                reclasse = _reclasser_en_dernier(conn, cotis["membre_id"], cotis["tontine_id"])
                if reclasse["ok"]:
                    conn.commit()

            # 3. Notification membre (IRA indexée + statut mulligan/éjection)
            if membre and tontine:
                pct_cliff = int(IRA_CLIFF_PCT * 100)
                msg_retard = (
                    f"⚠️ *COTISATION EN RETARD — {tontine['nom']}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Votre cotisation a été enregistrée ✅, mais elle est "
                    f"arrivée après l'heure limite (*{h_lim}*).\n\n"
                    f"📌 *Conséquences :*\n"
                    f"• Pénalité IRA : *{montant_ira:,} FCFA* ajoutée à votre dette "
                    f"({pct_cliff}% de la mise"
                    + (f" +{int(IRA_DAILY_PCT*100)}%/jour" if calc_ira["jours_retard"] else "")
                    + (", *plafond atteint*" if calc_ira["plafonnee"] else "") + ")\n"
                )
                if elig["mulligan_utilise"]:
                    msg_retard += (
                        f"• 🎟️ *Retard réseau toléré* (1/cycle) — votre bonus de "
                        f"ponctualité et votre position sont *préservés*. "
                        f"Un 2e retard ce cycle vous éjecte.\n"
                    )
                elif elig["ejecte"]:
                    msg_retard += (
                        f"• ❌ *2e retard du cycle* — éjecté du bonus de ponctualité.\n"
                    )
                if reclasse["ok"]:
                    msg_retard += (
                        f"• Reclassement : *position {reclasse['position_avant']}*"
                        f" → *position {reclasse['position_apres']}*\n"
                    )
                msg_retard += (
                    f"\n💡 Payez avant *{h_lim}* pour éviter ces pénalités.\n\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )
                wa_prive(membre["whatsapp"], msg_retard)

            # 4. Envoyer la liste de bouffage mise à jour au groupe
            tontine_full = fetchone(conn,
                "SELECT id, nom, whatsapp_groupe, cycle_actuel FROM tontines WHERE id=%s",
                (cotis["tontine_id"],))
            if tontine_full and tontine_full.get("whatsapp_groupe") and reclasse["ok"]:
                passages = fetchall(conn, """
                    SELECT lp.ordre, lp.statut, lp.nickname, lp.date_bouffage,
                           m.nom_complet
                    FROM liste_passage lp
                    LEFT JOIN membres m ON m.id = lp.membre_id
                    WHERE lp.tontine_id=%s AND lp.cycle=%s
                    ORDER BY lp.ordre
                """, (tontine_full["id"], tontine_full["cycle_actuel"]))
                lines = [
                    f"🔄 *LISTE DE BOUFFAGE MISE À JOUR — {tontine_full['nom']}*\n"
                    f"_(Reclassement retard — {soumis_local.strftime('%H:%M')} > {h_lim})_\n"
                ]
                for p in passages:
                    s   = {"Paye": "✅", "En_attente": "⏳", "Notifie": "🔔",
                           "Intercepte": "🚫", "Cede": "🔄"}.get(p["statut"], "❓")
                    nom = p["nom_complet"] or p["nickname"] or "???"
                    dt  = f"  📅 {p['date_bouffage']}" if p["date_bouffage"] else ""
                    lines.append(f"{str(p['ordre']).zfill(2)}- {s} {nom}{dt}")
                wa_groupe(tontine_full["whatsapp_groupe"], "\n".join(lines))

            pos_log = (f"position {reclasse['position_avant']} → position {reclasse['position_apres']}"
                       if reclasse["ok"] else "position préservée (mulligan)")
            log_audit("RETARD_COTISATION",
                      f"Membre#{cotis['membre_id']} | {pos_log} | IRA {montant_ira} FCFA "
                      f"(J+{calc_ira['jours_retard']}{', cap' if calc_ira['plafonnee'] else ''}) | "
                      f"slip#{elig['slips_apres']} {'ÉJECTÉ' if elig['ejecte'] else 'toléré'} | "
                      f"Tontine#{cotis['tontine_id']} | Soumis:{soumis_local.strftime('%H:%M')} > {h_lim}+5min")
    except Exception as _re:
        log.warning(f"⚠️ Reclassement retard non bloquant : {_re}")

    _verifier_credit_comm(conn, cotis["tontine_id"])
    log_audit("COTISATION_CONFIRMEE",
              f"Cotis#{cotis_id} | Membre#{cotis['membre_id']} | "
              f"Tontine#{cotis['tontine_id']} | Admin:{admin_wa}")
    return {"ok": True, "msg": "✅ Cotisation confirmée."}


def rejeter_cotisation(conn, cotis_id: int, admin_wa: str,
                        raison: str = "") -> dict:
    """Admin rejette une cotisation (screenshot incorrect ou non reçu).

    SELECT FOR UPDATE + vérif statut='En_attente' : même verrou pessimiste et
    idempotence que confirmer_cotisation. Sans ça, un admin qui rejette une
    cotisation déjà confirmée (double-tap, session admin périmée) écrase le
    statut sans toucher la ligne `transactions` déjà créée par la confirmation
    → contradiction permanente dans le grand livre.
    """
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM cotisations_manuelles WHERE id=%s FOR UPDATE",
            (cotis_id,))
        cotis = cur.fetchone()
        if not cotis:
            conn.rollback()
            return {"ok": False, "msg": "Cotisation introuvable."}

        if cotis["statut"] != "En_attente":
            # Idempotence : si déjà confirmée/rejetée, on retourne un message clair
            conn.rollback()
            return {"ok": False, "msg": f"Déjà traitée ({cotis['statut']})."}

        cur.execute("""UPDATE cotisations_manuelles
                   SET statut='Rejete', confirme_par=%s, date_confirmation=NOW()
                   WHERE id=%s""", (admin_wa, cotis_id))

        # Annuler la dette FMP correspondante
        cur.execute("""UPDATE dettes_badf SET statut='Payee'
                   WHERE ref_cotis=%s AND statut='Due'""", (cotis_id,))

        conn.commit()
    except Exception as e:
        try:    conn.rollback()
        except: pass
        log.error(f"❌ rejeter_cotisation #{cotis_id} échec: {e}")
        return {"ok": False, "msg": f"Erreur technique: {str(e)[:80]}"}
    finally:
        cur.close()

    membre = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s",
        (cotis["membre_id"],))
    if membre:
        wa_prive(membre["whatsapp"],
            f"❌ *COTISATION REJETÉE*\n\n"
            f"Bonjour *{membre['nom_complet']}*,\n\n"
            f"Votre soumission a été rejetée par l'admin.\n"
            f"{f'Raison : {raison}' if raison else ''}\n\n"
            f"Veuillez vérifier que vous avez :\n"
            f"• Payé le bon montant\n"
            f"• Payé sur le bon numéro\n"
            f"• Envoyé le bon screenshot\n\n"
            f"Puis soumettez à nouveau dans le groupe.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
    return {"ok": True, "msg": "❌ Cotisation rejetée."}


def declencher_bouffage_manuel(conn, membre_id: int, tontine_id: int,
                                passage_id: int, montant_brut: int,
                                caution: int,
                                deductions_detail: str = "",
                                caution_reelle: int = None,
                                dettes_ira: list = None) -> int:
    """
    Déclenche le flux de bouffage manuel.
    DM au bénéficiaire pour son numéro MM.
    DM à l'admin avec le montant à virer.
    Retourne l'ID du bouffage créé.

    `caution` = total des déductions (caution + IRA + cotisations manquantes),
    utilisé pour le calcul du montant net. `caution_reelle` = la VRAIE caution
    (celle qui sera restituée en fin de cycle) — si absente ou différente de
    `caution`, le reste (`caution - caution_reelle`) est une pénalité/dette
    NON remboursable et ne doit jamais être présenté comme telle au membre.

    `dettes_ira` = les lignes dettes_ira (id, montant) déduites de ce versement.
    Elles sont liées au versement dans la même transaction : la confirmation ne
    soldera qu'elles, jamais une dette apparue entre-temps.
    """
    if caution_reelle is None:
        caution_reelle = caution  # rétrocompatibilité : rien à distinguer
    caution_non_remboursable = max(0, caution - caution_reelle)
    montant_net = montant_brut - caution
    expiration  = datetime.now() + timedelta(hours=2)
    dettes_ira  = dettes_ira or []
    montant_ira = sum(d["montant"] for d in dettes_ira)

    cur = q(conn, """
        INSERT INTO bouffages_manuels
            (membre_id, tontine_id, passage_id, montant_brut,
             caution, montant_net, expiration, montant_ira)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (membre_id, tontine_id, passage_id,
          montant_brut, caution, montant_net, expiration, montant_ira))
    bouffage_id = cur.fetchone()[0]
    if dettes_ira:
        q(conn, "UPDATE dettes_ira SET bouffage_id=%s WHERE id = ANY(%s) AND statut='Due'",
          (bouffage_id, [d["id"] for d in dettes_ira]))
    conn.commit()

    membre  = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s", (membre_id,))
    tontine = fetchone(conn,
        "SELECT nom FROM tontines WHERE id=%s", (tontine_id,))

    if not membre or not tontine:
        return bouffage_id

    # DM au bénéficiaire — décompte honnête : seule caution_reelle est présentée
    # comme "retenue et restituable". Le reste (IRA, cotisations manquantes) est
    # une dette réglée, jamais un dépôt remboursable — ne jamais dire l'inverse.
    if caution_non_remboursable > 0:
        decompte = (
            f"  Cagnotte brute       : {montant_brut:>10,} FCFA\n"
            f"  Caution (restituable): {caution_reelle:>10,} FCFA\n"
            f"  Pénalités/dettes     : {caution_non_remboursable:>10,} FCFA (non remboursable)\n"
            f"  ─────────────────────────────────\n"
            f"  *MONTANT NET         : {montant_net:>10,} FCFA*\n\n"
            f"*II. DÉTAIL*\n\n"
            + (f"  Caution de *{caution_reelle:,} FCFA* : retenue par l'admin, "
               f"restituée en fin de cycle si vous continuez à cotiser normalement.\n"
               if caution_reelle > 0 else "")
            + f"  Pénalités/dettes de *{caution_non_remboursable:,} FCFA* : réglées sur "
              f"cette cagnotte, ne sont *pas* restituées (IRA de retard et/ou "
              f"cotisations manquantes).\n\n"
        )
    else:
        decompte = (
            f"  Cagnotte brute    : {montant_brut:>10,} FCFA\n"
            f"  Caution retenue   : {caution:>10,} FCFA\n"
            f"  ─────────────────────────────────\n"
            f"  *MONTANT NET      : {montant_net:>10,} FCFA*\n\n"
            f"*II. CAUTION*\n\n"
            f"  La caution de *{caution:,} FCFA* est retenue par l'admin.\n"
            f"  Elle vous sera restituée à la fin du cycle si vous\n"
            f"  continuez à cotiser normalement.\n\n"
        )
    wa_prive(membre["whatsapp"],
        f"🎉 *AVIS DE DÉBLOCAGE DE CAGNOTTE*\n"
        f"*{tontine['nom']}* — BADF Ltd\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*I. DÉCOMPTE FINANCIER*\n\n"
        f"{decompte}"
        f"*III. INSTRUCTIONS*\n\n"
        f"  Envoyez votre numéro Mobile Money, précédé de l'opérateur :\n\n"
        f"    *MTN +237690XXXXXX*\n"
        f"  ou\n"
        f"    *ORANGE +237699XXXXXX*\n\n"
        f"  ⏱ Vous avez *2 heures* pour répondre.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Réf. COBAC R-2019/01 — Barack & AI Development Facilities Ltd_"
    )

    # Notifier les admins
    detail = deductions_detail if deductions_detail else (
        f"  Cagnotte brute : *{montant_brut:,} FCFA*\n"
        f"  Caution        : *-{caution:,} FCFA*\n"
        f"  ─────────────────────────\n"
        f"  *NET À VIRER   : {montant_net:,} FCFA*"
    )
    wa_admins_tontine(tontine_id,
        f"🔔 *BOUFFAGE EN COURS — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Bénéficiaire : *{membre['nom_complet']}*\n\n"
        f"📊 *DÉCOMPTE FINANCIER :*\n"
        f"{detail}\n\n"
        f"En attente du numéro Mobile Money...\n"
        f"⏱ Délai : 2 heures\n\n"
        f"_TontineBot Pro — BADF Ltd_"
    )

    log_audit("BOUFFAGE_DECLENCHE",
              f"Membre#{membre_id} | Tontine#{tontine_id} | Net:{montant_net:,} FCFA")
    return bouffage_id


def traiter_reponse_numero_bouffage(wa: str, texte: str, membre: dict):
    """
    Intercepte la réponse d'un bénéficiaire de bouffage manuel à qui on a
    demandé son numéro Mobile Money (déclenché par declencher_bouffage_manuel).

    Volontairement SANS session en mémoire : le bénéficiaire a 2h pour
    répondre, largement au-delà de SESSION_TIMEOUT (300s) — l'état est lu
    directement dans bouffages_manuels.statut='En_attente'.

    Retourne None si rien à faire (laisser le dispatcher normal continuer),
    ou une chaîne de réponse sinon.
    """
    conn = get_conn()
    try:
        bouffage = fetchone(conn, """
            SELECT bm.*, t.nom AS tontine_nom
            FROM bouffages_manuels bm JOIN tontines t ON t.id = bm.tontine_id
            WHERE bm.membre_id=%s AND bm.statut='En_attente'
            ORDER BY bm.id DESC LIMIT 1
        """, (membre["id"],))
        if not bouffage:
            return None

        match = re.search(r"(\+?237[26789]\d{8}|\b[26789]\d{8}\b)", texte)
        if not match:
            # Ne ressemble pas à une tentative de numéro MM → laisser passer
            # (ex: le membre navigue son menu normalement).
            return None

        tu = texte.upper()
        operateur = None
        if "MTN" in tu:
            operateur = "cm.mtn"
        elif "ORANGE" in tu:
            operateur = "cm.orange"

        n      = match.group(1)
        numero = normaliser_numero(n if "237" in n else "237" + n)
        if not operateur or not valider_numero_cameroun(numero):
            return (
                "❌ Format non reconnu.\nPrécisez l'opérateur, exemples :\n"
                "• *MTN +237690123456*\n• *ORANGE +237699123456*"
            )

        import psycopg2.extras
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("SELECT * FROM bouffages_manuels WHERE id=%s FOR UPDATE", (bouffage["id"],))
            b2 = cur.fetchone()
            if not b2 or b2["statut"] != "En_attente":
                conn.rollback()
                return "ℹ️ Ce bouffage a déjà été traité."
            cur.execute("""UPDATE bouffages_manuels
                           SET statut='Vire', numero_mm=%s, operateur_cashout=%s
                           WHERE id=%s""", (numero, operateur, bouffage["id"]))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"traiter_reponse_numero_bouffage #{bouffage['id']} : {e}")
            return "❌ Erreur technique. Réessayez ou contactez votre admin."
        finally:
            cur.close()

        log_audit("BOUFFAGE_NUMERO_RECU",
                  f"Bouffage#{bouffage['id']} | Membre#{membre['id']} | {numero} ({operateur})", wa)

        wa_admins_tontine(bouffage["tontine_id"],
            f"💸 *NUMÉRO REÇU — {bouffage['tontine_nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Bénéficiaire : *{membre['nom_complet']}*\n"
            f"Montant net  : *{bouffage['montant_net']:,} FCFA*\n"
            f"Numéro MM    : *{numero}* ({'MTN' if operateur == 'cm.mtn' else 'Orange'})\n\n"
            f"Effectuez le virement, puis tapez :\n"
            f"*admin {bouffage['tontine_nom']}* puis *BOUFFAGE_VIRE {bouffage['id']}*\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        return (
            f"✅ Numéro enregistré : *{numero}*\n\n"
            f"Votre admin va effectuer le virement sous peu. "
            f"Vous recevrez une confirmation dès réception."
        )
    finally:
        release_conn(conn)


def _solder_versement(cur, bouffage: dict) -> int:
    """Règle un versement dont l'argent quitte la cagnotte : solde les dettes
    IRA qu'il a déduites et écrit la transaction Bouffage. Seul point d'écriture
    pour les deux chemins de versement (confirmation BOUFFAGE_VIRE et cashout
    forcé). `cur` est dans la transaction de l'appelant, ligne bouffage verrouillée.

    Les dettes soldées sont celles liées au versement au déclenchement
    (bouffage_id), jamais une dette apparue depuis ; frais_ira = montant_ira,
    ce qui a réellement été déduit. Un versement créé avant ce lien
    (montant_ira NULL) garde l'ancienne règle. Retourne frais_ira.
    """
    if bouffage.get("montant_ira") is None:
        cur.execute("""
            SELECT id, montant FROM dettes_ira
            WHERE membre_id=%s AND tontine_id=%s AND statut='Due'
            FOR UPDATE
        """, (bouffage["membre_id"], bouffage["tontine_id"]))
        dettes = cur.fetchall()
        total_ira = sum(d["montant"] for d in dettes)
    else:
        cur.execute("""
            SELECT id, montant FROM dettes_ira
            WHERE bouffage_id=%s AND statut='Due'
            FOR UPDATE
        """, (bouffage["id"],))
        dettes = cur.fetchall()
        total_ira = bouffage["montant_ira"]
    if dettes:
        cur.execute("""
            UPDATE dettes_ira SET statut='Prelevee', prelevee_le=NOW()
            WHERE id = ANY(%s)
        """, ([d["id"] for d in dettes],))

    cur.execute("""
        INSERT INTO transactions
            (membre_id, tontine_id, montant_brut, frais_ira, montant_net,
             type_transaction, statut)
        VALUES (%s,%s,%s,%s,%s,'Bouffage','Confirmee')
    """, (bouffage["membre_id"], bouffage["tontine_id"],
          bouffage["montant_brut"], total_ira, bouffage["montant_net"]))
    return total_ira


def confirmer_bouffage_vire(conn, bouffage_id: int, admin_wa: str) -> dict:
    """Admin confirme avoir viré le montant au bénéficiaire.

    SELECT FOR UPDATE + vérif statut='Vire' : verrou pessimiste et
    idempotence, même schéma que confirmer_cotisation (PATCH 1 v9.18) —
    empêche un double-tap ou un retry webhook de créer deux transactions.
    """
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM bouffages_manuels WHERE id=%s FOR UPDATE", (bouffage_id,))
        bouffage = cur.fetchone()
        if not bouffage:
            conn.rollback()
            return {"ok": False, "msg": "Bouffage introuvable."}
        if bouffage["statut"] != "Vire":
            conn.rollback()
            return {"ok": False, "msg": f"❌ Déjà traité ou pas encore prêt (statut : {bouffage['statut']})."}

        cur.execute("""UPDATE bouffages_manuels
                       SET statut='Confirme', confirme_par=%s, date_confirmation=NOW()
                       WHERE id=%s""", (admin_wa, bouffage_id))

        # Marquer le passage comme payé
        if bouffage["passage_id"]:
            cur.execute("""UPDATE liste_passage
                           SET statut='Paye', date_paiement=NOW(),
                               numero_cashout=%s, operateur_cashout=%s
                           WHERE id=%s""",
                        (bouffage["numero_mm"], bouffage.get("operateur_cashout"),
                         bouffage["passage_id"]))

        _solder_versement(cur, bouffage)
        conn.commit()
    except Exception as e:
        try: conn.rollback()
        except: pass
        log.error(f"❌ confirmer_bouffage_vire #{bouffage_id} échec: {e}")
        return {"ok": False, "msg": f"Erreur technique: {str(e)[:80]}"}
    finally:
        cur.close()

    # Notifier le bénéficiaire
    membre = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s",
        (bouffage["membre_id"],))
    tontine = fetchone(conn,
        "SELECT nom FROM tontines WHERE id=%s", (bouffage["tontine_id"],))

    if membre and tontine:
        wa_prive(membre["whatsapp"],
            f"✅ *VIREMENT EFFECTUÉ — {tontine['nom']}*\n\n"
            f"Bonjour *{membre['nom_complet']}*,\n\n"
            f"Votre bouffage de *{bouffage['montant_net']:,} FCFA* "
            f"a été viré sur votre numéro Mobile Money.\n\n"
            f"Vérifiez la réception sur votre téléphone.\n\n"
            f"Merci de continuer à cotiser normalement.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    log_audit("BOUFFAGE_CONFIRME",
              f"Bouffage#{bouffage_id} | Admin:{admin_wa} | "
              f"{bouffage['montant_net']:,} FCFA viré")

    # ── Détection fin de cycle ────────────────────────────────────────────
    _verifier_fin_cycle(conn, bouffage["tontine_id"])

    return {"ok": True, "msg": "✅ Bouffage confirmé et enregistré."}


def _rapport_ponctualite_cycle(conn, tontine_id: int, cycle: int) -> dict:
    """
    Construit le rapport de ponctualité d'un cycle + l'ordre PROPOSÉ pour le suivant.

    Bot = moteur de calcul + mémoire impartiale ; l'admin reste juge final. Ce rapport
    est de la situational awareness (qui parfait / mulligan / fugitif) + carburant Trust
    Graph, et sert de PROPOSITION que l'admin validera via NOUVEAU_CYCLE.

    Absence de ligne ponctualite_cycle = 0 slip = parfait.

    Returns: {"rapport_txt", "propose_txt", "stats": {parfaits, mulligan, ejectes, total}}
    """
    membres = fetchall(conn, """
        SELECT lp.membre_id, lp.ordre,
               COALESCE(m.nom_complet, lp.nickname, '???') AS nom,
               COALESCE(pc.slips, 0) AS slips
        FROM liste_passage lp
        LEFT JOIN membres m           ON m.id = lp.membre_id
        LEFT JOIN ponctualite_cycle pc ON pc.membre_id = lp.membre_id
                                      AND pc.tontine_id = lp.tontine_id
                                      AND pc.cycle = %s
        WHERE lp.tontine_id=%s AND lp.cycle=%s
        ORDER BY lp.ordre
    """, (cycle, tontine_id, cycle))

    # Un placeholder non lié (membre_id NULL) n'a pas de ponctualité mesurable :
    # il ne doit PAS compter comme "parfait" (sinon promu à tort en tête).
    lies      = [m for m in membres if m["membre_id"] is not None]
    parfaits  = [m for m in lies if m["slips"] == 0]
    mulligan  = [m for m in lies if m["slips"] == 1]
    ejectes   = [m for m in lies if m["slips"] >= 2]
    non_lies  = [m for m in membres if m["membre_id"] is None]

    def _bloc(titre, emoji, lst, suffixe=""):
        if not lst:
            return ""
        lignes = "\n".join(f"   {emoji} {x['nom']}{suffixe}" for x in lst)
        return f"*{titre}* ({len(lst)})\n{lignes}\n"

    rapport_txt = (
        _bloc("Parfaits — 0 retard", "🏅", parfaits)
        + _bloc("Mulligan consommé — 1 retard toléré", "🎟️", mulligan)
        + _bloc("Éjectés — 2+ retards", "❌", ejectes)
        + _bloc("Non liés (nickname seul)", "🔗", non_lies)
    ) or "   (aucune donnée de ponctualité ce cycle)\n"

    # Ordre proposé pour le prochain cycle (parfaits en tête ; non-liés en dernier tier).
    classement = ira_engine.ordre_priorite_next_cycle([
        {"membre_id": m["membre_id"], "nom": m["nom"],
         "slips": (m["slips"] if m["membre_id"] is not None else 99),
         "ordre_actuel": m["ordre"]}
        for m in membres
    ])
    propose_txt = "\n".join(
        f"   {m['ordre_nouveau']:>2}. {m['nom']}"
        + ("  🏅" if m["tier"] == 0 else "  🎟️" if m["tier"] == 1 else "  ❌")
        for m in classement
    ) or "   (liste vide)"

    return {
        "rapport_txt": rapport_txt,
        "propose_txt": propose_txt,
        "stats": {
            "parfaits": len(parfaits),
            "mulligan": len(mulligan),
            "ejectes": len(ejectes),
            "total": len(membres),
        },
    }


def _verifier_fin_cycle(conn, tontine_id: int):
    """
    Appelée après chaque bouffage confirmé.
    Vérifie si tous les passages du cycle actuel sont payés.
    Si oui → annonce fin de cycle + demande à l'admin si on repart.
    """
    tontine = fetchone(conn,
        "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
    if not tontine:
        return

    # Compter passages restants dans ce cycle
    restants = fetchone(conn, """
        SELECT COUNT(*) n FROM liste_passage
        WHERE tontine_id=%s AND cycle=%s AND statut NOT IN ('Paye','Intercepte','Cede')
    """, (tontine_id, tontine["cycle_actuel"]))["n"]

    if restants > 0:
        return  # Cycle pas encore terminé

    # Total du cycle
    nb_membres = fetchone(conn,
        "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
        (tontine_id,))["n"]
    nb_cycles  = tontine["cycle_actuel"]

    log_audit("FIN_CYCLE",
              f"Tontine:{tontine['nom']} | Cycle:{nb_cycles} terminé")

    # ── PHASE 2 — Dispatch Bonus de Ponctualité (compte marchand MoMo/OM) ──
    # INACTIF tant que pas d'API de mouvement de fonds (roadmap Q1 2027).
    # La récompense Phase 1 = priorité de rotation (appliquée dans demarrer_nouveau_cycle).
    # À l'arrivée du compte marchand, décommenter : le pool = Σ IRA COLLECTÉES
    # (statut='Prelevee', pas 'Due'), netté des frais MoMo/OM, réparti entre les
    # membres éligibles (0-1 slip). Invariant dur : Redistribué ≤ Collecté.
    # Voir mémoire projet : project_merchant_account_rubicon.
    #
    #   pool = fetchone(conn, "SELECT COALESCE(SUM(montant),0) t FROM dettes_ira "
    #                         "WHERE tontine_id=%s AND statut='Prelevee'",
    #                         (tontine_id,))["t"]
    #   n_parfaits = fetchone(conn, "SELECT COUNT(*) n FROM ponctualite_cycle "
    #                               "WHERE tontine_id=%s AND cycle=%s AND eligible",
    #                               (tontine_id, nb_cycles))["n"]
    #   dispatch = ira_engine.bonus_par_parfait(pool, n_parfaits,
    #                                           frais_dispatch=_frais_momo(pool, n_parfaits))
    #   # reliquat_rollover → pot du cycle suivant (dynamique jackpot)

    # Annonce dans le groupe
    if tontine.get("whatsapp_groupe"):
        wa_groupe(tontine["whatsapp_groupe"],
            f"🎉 *CYCLE {nb_cycles} TERMINÉ — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Félicitations à tous les *{nb_membres} membres* !\n\n"
            f"Le cycle {nb_cycles} est officiellement clôturé.\n"
            f"Tous les bouffages ont été effectués avec succès.\n\n"
            f"Votre administrateur vous informera prochainement "
            f"de la suite — nouveau cycle ou clôture définitive.\n\n"
            f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
        )

    # Rapport de ponctualité + ordre PROPOSÉ (bot propose, admin dispose).
    try:
        rap = _rapport_ponctualite_cycle(conn, tontine_id, nb_cycles)
    except Exception as _re:
        log.warning(f"⚠️ Rapport ponctualité non bloquant : {_re}")
        rap = None

    # DM aux admins — rapport + proposition + demande de décision
    admins = fetchall(conn,
        "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s", (tontine_id,))
    for adm in admins:
        msg = (
            f"🎉 *CYCLE {nb_cycles} TERMINÉ — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Tous les membres ont bouffé. Le cycle {nb_cycles} est clôturé.\n\n"
        )
        if rap:
            s = rap["stats"]
            msg += (
                f"📊 *PONCTUALITÉ DU CYCLE*\n"
                f"{rap['rapport_txt']}\n"
                f"🔄 *ORDRE PROPOSÉ — prochain cycle*\n"
                f"_(parfaits promus en tête — proposition, vous restez juge)_\n"
                f"{rap['propose_txt']}\n\n"
                f"💡 Le classement s'applique si vous lancez *NOUVEAU_CYCLE*. "
                f"Vous pouvez l'ajuster ensuite (option *modifier la liste*) — "
                f"toute modification est tracée.\n\n"
            )
        msg += (
            f"Que souhaitez-vous faire ?\n\n"
            f"  1. Tapez *admin {tontine['nom']}*\n"
            f"  2. Puis tapez *NOUVEAU_CYCLE* pour repartir avec les mêmes membres\n"
            f"     ou *CLOTURER* pour clôturer définitivement cette tontine\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        wa_prive(adm["whatsapp"], msg)

    # Notifier le owner
    wa_owner(
        f"✅ *FIN DE CYCLE — {tontine['nom']}*\n"
        f"Cycle {nb_cycles} | {nb_membres} membres | "
        f"En attente de décision admin."
    )

    # Score confiance × nombre d'or pour membres ayant honoré tout le cycle
    conn2 = get_conn()
    try:
        membres_cycle = fetchall(conn2, """
            SELECT DISTINCT m.id FROM membres m
            JOIN adhesions a ON a.membre_id = m.id
            WHERE a.tontine_id=%s AND a.statut='Actif'
        """, (tontine_id,))
        for mb in membres_cycle:
            q(conn2, """UPDATE membres
                       SET score_confiance = LEAST(100, ROUND(score_confiance * 1.618))
                       WHERE id=%s""", (mb["id"],))
        conn2.commit()
        log.info(f"✅ Score confiance × 1.618 appliqué à {len(membres_cycle)} membres — {tontine['nom']}")
    except Exception as e:
        log.error(f"Score confiance golden ratio : {e}")
    finally:
        release_conn(conn2)


def demarrer_nouveau_cycle(tontine_id: int, admin_wa: str) -> str:
    """
    Lance un nouveau cycle pour la tontine.
    - Incrémente cycle_actuel
    - Remet tous les passages en statut En_attente
    - Remet les adhesions en Actif
    - Libère les cautions restantes
    - Annonce dans le groupe
    """
    conn = get_conn()
    try:
        verrou_ordre_tontine(conn, tontine_id)  # FORTERESSE : sérialise l'ordre de cette tontine
        tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine:
            release_conn(conn)
            return "❌ Tontine introuvable."

        # Garde-fou : refuser si le cycle actuel n'est pas terminé — sinon
        # on libère les cautions de membres qui n'ont pas encore bouffé.
        restants = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s AND statut NOT IN ('Paye','Intercepte','Cede')
        """, (tontine_id, tontine["cycle_actuel"]))["n"]
        if restants > 0:
            release_conn(conn)
            return (
                f"❌ Le cycle {tontine['cycle_actuel']} n'est pas encore terminé "
                f"({restants} passage(s) restant(s)). Impossible de lancer un nouveau cycle."
            )

        ancien_cycle = tontine["cycle_actuel"]
        nouveau_cycle = ancien_cycle + 1

        # Incrémenter le cycle
        q(conn, "UPDATE tontines SET cycle_actuel=%s WHERE id=%s",
          (nouveau_cycle, tontine_id))

        # Remettre les passages en attente pour le nouveau cycle
        # (on garde la même liste, juste on remet les statuts)
        q(conn, """UPDATE liste_passage
                   SET statut='En_attente', date_paiement=NULL,
                       numero_cashout=NULL, operateur_cashout=NULL
                   WHERE tontine_id=%s AND cycle=%s""",
          (tontine_id, ancien_cycle))

        # Dupliquer la liste pour le nouveau cycle — REWARD : priorité de rotation.
        # Les membres parfaits (0 slip) sont promus en tête, mulligan (1 slip) ensuite,
        # éjectés (2+) en fin. Absence de ligne ponctualite = jamais en retard = parfait.
        passages = fetchall(conn, """
            SELECT lp.membre_id, lp.nickname, lp.ordre, lp.soumis_par,
                   COALESCE(pc.slips, 0) AS slips
            FROM liste_passage lp
            LEFT JOIN ponctualite_cycle pc
                   ON pc.membre_id = lp.membre_id
                  AND pc.tontine_id = lp.tontine_id
                  AND pc.cycle = %s
            WHERE lp.tontine_id=%s AND lp.cycle=%s
            ORDER BY lp.ordre
        """, (ancien_cycle, tontine_id, ancien_cycle))

        classement = ira_engine.ordre_priorite_next_cycle([
            {"membre_id": p["membre_id"], "nickname": p["nickname"],
             "soumis_par": p["soumis_par"],
             # Placeholder non lié (membre_id NULL) : jamais "parfait" — il n'a pas de
             # ligne ponctualite, donc slips=0 le hisserait à tort en tête. On le
             # classe en dernier tier (slips élevé) pour ne pas polluer la récompense.
             "slips": (p["slips"] if p["membre_id"] is not None else 99),
             "ordre_actuel": p["ordre"]}
            for p in passages
        ])
        nb_promus = sum(1 for m in classement if m["tier"] == 0)

        for m in classement:
            # Ordres 1..N distincts par construction + nouveau cycle vierge sous
            # verrou → INSERT direct (ON CONFLICT retiré : incompatible DEFERRABLE).
            q(conn, """
                INSERT INTO liste_passage
                    (tontine_id, membre_id, nickname, cycle, ordre, soumis_par)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (tontine_id, m["membre_id"], m["nickname"],
                  nouveau_cycle, m["ordre_nouveau"], m["soumis_par"]))

        # Remettre adhesions en Actif
        q(conn, """UPDATE adhesions SET statut='Actif', jours_avance=0
                   WHERE tontine_id=%s AND statut IN ('Suspendu','Pause')""",
          (tontine_id,))
        # Les pauses levées par le nouveau cycle ne laissent pas de cas ouvert.
        q(conn, """UPDATE cas_difficiles SET statut='Termine'
                   WHERE tontine_id=%s AND statut='Actif' AND type_cas='Pause'""",
          (tontine_id,))

        # Libérer les cautions bloquées
        cautions = fetchall(conn, """
            SELECT cg.id, cg.montant, m.whatsapp, m.nom_complet
            FROM cautions_garantie cg
            JOIN membres m ON m.id = cg.membre_id
            WHERE cg.tontine_id=%s AND cg.statut='Bloquee'
        """, (tontine_id,))

        for c in cautions:
            q(conn, "UPDATE cautions_garantie SET statut='Liberee', date_liberation=NOW() WHERE id=%s",
              (c["id"],))
            wa_prive(c["whatsapp"],
                f"🔓 *CAUTION LIBÉRÉE — {tontine['nom']}*\n\n"
                f"Fin du cycle {ancien_cycle}.\n"
                f"Votre caution de *{c['montant']:,} FCFA* est libérée.\n"
                f"Votre admin va vous la reverser.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )

        conn.commit()

        nb_membres = len(passages)
        log_audit("NOUVEAU_CYCLE",
                  f"Tontine:{tontine['nom']} | Cycle {ancien_cycle}→{nouveau_cycle} | Admin:{admin_wa}")

        # Traçabilité : le tri algorithmique de ponctualité est le DÉFAUT appliqué.
        # Toute déviation ultérieure de l'admin est loggée séparément (MODIF_ORDRE).
        nb_mulligan = sum(1 for m in classement if m["tier"] == 1)
        nb_ejectes  = sum(1 for m in classement if m["tier"] == 2)
        ordre_applique = " | ".join(
            f"{m['ordre_nouveau']}.{m.get('nickname') or ('#'+str(m['membre_id']) if m['membre_id'] else '?')}"
            for m in classement)
        log_audit("REORDER_PONCTUALITE",
                  f"Tontine:{tontine['nom']} | Cycle {nouveau_cycle} | "
                  f"parfaits:{nb_promus} mulligan:{nb_mulligan} éjectés:{nb_ejectes} | "
                  f"ordre appliqué (défaut algo) : {ordre_applique}", admin_wa)

        # Annonce dans le groupe
        if tontine.get("whatsapp_groupe"):
            wa_groupe(tontine["whatsapp_groupe"],
                f"🚀 *CYCLE {nouveau_cycle} LANCÉ — {tontine['nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Le cycle {nouveau_cycle} démarre maintenant !\n\n"
                f"👥 *{nb_membres} membres* repartent pour un nouveau cycle.\n"
                f"🏅 *{nb_promus} membre(s) parfait(s)* (zéro retard) promus en tête "
                f"de la liste de bouffage — la ponctualité paie.\n\n"
                f"Les cotisations reprennent normalement.\n"
                f"Envoyez vos screenshots dans ce groupe après chaque virement.\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )

        release_conn(conn)
        return (
            f"✅ *Cycle {nouveau_cycle} lancé — {tontine['nom']}*\n\n"
            f"▪ {nb_membres} membres dans le cycle\n"
            f"▪ {len(cautions)} caution(s) libérée(s)\n"
            f"▪ Groupe notifié\n\n"
            f"Les cotisations reprennent immédiatement."
        )

    except Exception as e:
        conn.rollback()
        release_conn(conn)
        log.error(f"demarrer_nouveau_cycle : {e}")
        return f"❌ Erreur : {str(e)[:80]}"


def cloturer_tontine(tontine_id: int, admin_wa: str) -> str:
    """Clôture définitive d'une tontine."""
    conn = get_conn()
    try:
        tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine:
            release_conn(conn)
            return "❌ Tontine introuvable."

        q(conn, "UPDATE tontines SET statut='Terminee' WHERE id=%s", (tontine_id,))
        conn.commit()

        if tontine.get("whatsapp_groupe"):
            wa_groupe(tontine["whatsapp_groupe"],
                f"🏁 *TONTINE CLÔTURÉE — {tontine['nom']}*\n\n"
                f"Cette tontine est officiellement terminée.\n"
                f"Merci à tous les membres pour leur sérieux et leur confiance.\n\n"
                f"TontineBot Pro se retire de ce groupe.\n"
                f"À bientôt sur le réseau BADF Ltd 🤝\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )
            import time as _t; _t.sleep(3)
            wa_quitter_groupe(tontine["whatsapp_groupe"])

        log_audit("TONTINE_CLOTUREE",
                  f"Tontine:{tontine['nom']} | Admin:{admin_wa}")
        release_conn(conn)
        return f"✅ Tontine *{tontine['nom']}* clôturée définitivement."

    except Exception as e:
        conn.rollback()
        release_conn(conn)
        return f"❌ Erreur : {str(e)[:80]}"


def rapport_dettes_badf_admin(admin_wa: str) -> str:
    """Rapport des dettes BADF dues par un admin (envoyé chaque soir à 20h)."""
    conn = get_conn()
    dettes = fetchall(conn, """
        SELECT type_dette, SUM(montant) as total
        FROM dettes_badf
        WHERE admin_wa=%s AND statut='Due'
        GROUP BY type_dette
    """, (admin_wa,))
    total = sum(d["total"] for d in dettes) if dettes else 0
    release_conn(conn)

    if total == 0:
        return ""

    lignes = []
    for d in dettes:
        lignes.append(f"  {d['type_dette']:<15} : {d['total']:>8,} FCFA")

    return (
        f"💰 *REVERSEMENT BADF DU JOUR*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lignes) +
        f"\n  ─────────────────────────────\n"
        f"  *TOTAL À REVERSER : {total:,} FCFA*\n\n"
        f"📱 Virement vers :\n"
        f"  MTN    : *{NUMERO_BADF_MTN}*\n"
        f"  Orange : *{NUMERO_BADF_ORANGE}*\n\n"
        f"Envoyez *PAIEMENT_BADF <code>* au bot après le virement.\n\n"
        f"_Barack & AI Development Facilities Ltd_"
    )


def enregistrer_paiement_badf(admin_wa: str, code_transaction: str) -> str:
    """Admin envoie son code de virement vers BADF — marque les dettes comme payées."""
    conn = get_conn()
    try:
        dettes = fetchall(conn, """
            SELECT id FROM dettes_badf
            WHERE admin_wa=%s AND statut='Due'
        """, (admin_wa,))

        if not dettes:
            return "Aucune dette en cours pour votre compte."

        ids = [d["id"] for d in dettes]
        q(conn, """UPDATE dettes_badf
                   SET statut='Payee', date_paiement=NOW(), code_paiement=%s
                   WHERE id = ANY(%s) AND statut='Due'""",
          (code_transaction, ids))

        # Rétablir les tontines suspendues pour dette
        tontines_suspendues = fetchall(conn, """
            SELECT id, nom, whatsapp_groupe
            FROM tontines
            WHERE statut='Suspendue'
              AND id IN (SELECT tontine_id FROM admins_groupe WHERE whatsapp=%s)
        """, (normaliser_numero(admin_wa),))

        groupes_retablis = []
        for t in tontines_suspendues:
            q(conn, "UPDATE tontines SET statut='Active' WHERE id=%s", (t["id"],))
            groupes_retablis.append(t["nom"])
            log_audit("TONTINE_RETABLIE",
                      f"Tontine:{t['nom']} rétablie après paiement dette BADF | Admin:{admin_wa}")

        conn.commit()

        log_audit("PAIEMENT_BADF_RECU",
                  f"Admin:{admin_wa} | Code:{code_transaction} | {len(ids)} dettes soldées")

        retabli_txt = ""
        if groupes_retablis:
            retabli_txt = (
                f"\n\n✅ *Service rétabli sur vos tontines :*\n"
                + "\n".join(f"  • {g}" for g in groupes_retablis)
                + f"\n\n_TontineBot Pro a été réintégré. "
                  f"Rajoutez le bot dans les groupes concernés._"
            )
            wa_owner(
                f"✅ *DETTE BADF SOLDÉE*\n"
                f"Admin : {admin_wa}\n"
                f"Code  : {code_transaction}\n"
                f"Dettes soldées : {len(ids)}\n"
                f"Tontines rétablies : {', '.join(groupes_retablis)}"
            )

        return (
            f"✅ *{len(ids)}* dettes soldées. Code : `{code_transaction}`"
            f"{retabli_txt}"
        )
    except Exception as e:
        try: conn.rollback()
        except: pass
        log.error(f"❌ enregistrer_paiement_badf Admin:{admin_wa} : {e}")
        return f"❌ Erreur technique : {str(e)[:80]}"
    finally:
        release_conn(conn)


def _get_retardataires(conn, tontine_id: int) -> list:
    """Membres actifs n'ayant pas cotisé aujourd'hui."""
    return fetchall(conn, """
        SELECT m.nom_complet, m.whatsapp, m.score_confiance
        FROM adhesions a JOIN membres m ON m.id=a.membre_id
        WHERE a.tontine_id=%s AND a.statut='Actif'
          AND m.id NOT IN (
              SELECT DISTINCT membre_id FROM transactions
              WHERE tontine_id=%s AND type_transaction='Cotisation'
                AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
          )
        ORDER BY m.score_confiance ASC
    """, (tontine_id, tontine_id))


def saisir_caution_et_compenser_groupe(conn, membre_id: int, tontine_id: int,
                                        raison: str = "Fugue post-bouffage") -> dict:
    """
    Saisit la caution d'un fugitif ET compense le groupe avec cet argent.

    Logique :
      1. Récupère la caution bloquée du membre
      2. Calcule combien de cotisations manquantes il doit encore
      3. Utilise la caution pour couvrir ces cotisations manquantes
      4. Le reliquat (si caution > dettes) est crédité à la réserve BADF
      5. Si caution < dettes → dette résiduelle enregistrée sur le membre

    Retourne un dict avec le détail de la compensation.
    """
    # VERROU PESSIMISTE : sans ça, deux déclencheurs concurrents (saisie manuelle
    # admin + job traiter_bouffages_suspects_expires) peuvent tous deux lire la
    # caution 'Bloquee' avant que l'un des deux ne commit son UPDATE → double
    # compensation, double crédit BADF. Même schéma que confirmer_cotisation.
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM cautions_garantie WHERE membre_id=%s AND tontine_id=%s "
        "AND statut='Bloquee' FOR UPDATE",
        (membre_id, tontine_id))
    caution = cur.fetchone()
    if not caution:
        conn.rollback()
        cur.close()
        return {"ok": False, "msg": "Aucune caution bloquée pour ce membre."}

    tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
    membre  = fetchone(conn, "SELECT nom_complet, whatsapp FROM membres WHERE id=%s", (membre_id,))
    if not tontine or not membre:
        conn.rollback()
        cur.close()
        return {"ok": False, "msg": "Membre ou tontine introuvable."}

    # `cur` a fait son travail (acquérir + lire la ligne verrouillée) : le verrou
    # reste tenu par la TRANSACTION jusqu'au commit() plus bas, pas par le curseur
    # lui-même — on peut donc le fermer ici, le reste utilise q()/fetchone() sur conn.
    cur.close()

    # Cotisations manquantes depuis le bouffage
    mon_pass = fetchone(conn, """
        SELECT ordre FROM liste_passage
        WHERE membre_id=%s AND tontine_id=%s AND cycle=%s AND statut='Paye'
        ORDER BY date_paiement DESC LIMIT 1
    """, (membre_id, tontine_id, tontine["cycle_actuel"]))

    if mon_pass:
        passages_restants = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND statut='En_attente'
        """, (tontine_id, tontine["cycle_actuel"], mon_pass["ordre"]))["n"]
    else:
        passages_restants = 0

    cotis_deja_faites = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
          AND statut='Confirmee'
          AND date_heure > (SELECT date_bouffage FROM cautions_garantie WHERE id=%s)
    """, (membre_id, tontine_id, caution["id"]))["n"]

    cotis_manquantes  = max(0, passages_restants - cotis_deja_faites)
    montant_du        = cotis_manquantes * tontine["montant_place"]
    montant_caution   = caution["montant"]

    # Saisir la caution
    q(conn, "UPDATE cautions_garantie SET statut='Saisie', date_liberation=NOW() WHERE id=%s",
      (caution["id"],))

    if montant_du == 0:
        # Fugitif a déjà tout cotisé — caution va à BADF
        reliquat = montant_caution
        compense = 0
        note = "Toutes cotisations honorées — caution intégralement reversée à BADF"
    elif montant_caution >= montant_du:
        # Caution couvre toutes les cotisations manquantes
        compense = montant_du
        reliquat = montant_caution - montant_du
        note = f"Caution couvre {cotis_manquantes} cotisation(s) manquante(s)"
    else:
        # Caution partielle — ne couvre pas tout
        compense = montant_caution
        reliquat = 0
        dette_residuelle = montant_du - montant_caution
        # Enregistrer la dette résiduelle sur le membre
        q(conn, """INSERT INTO dettes_ira (membre_id, tontine_id, montant, motif)
                   VALUES (%s,%s,%s,%s)""",
          (membre_id, tontine_id, dette_residuelle,
           f"Dette résiduelle post-fugue — caution insuffisante"))
        note = (f"Caution partielle — couvre {montant_caution:,} FCFA sur "
                f"{montant_du:,} FCFA dus. Dette résiduelle : {dette_residuelle:,} FCFA")

    # Créditer le groupe si compensation
    if compense > 0:
        # Injecter dans les transactions du groupe comme compensation
        q(conn, """INSERT INTO transactions
                   (membre_id, tontine_id, montant_brut, montant_net,
                    type_transaction, statut, reference)
                   VALUES (%s,%s,%s,%s,'Remboursement','Confirmee',%s)""",
          (membre_id, tontine_id, compense, compense,
           f"Compensation caution fugitif — {membre['nom_complet']}"))

    # Reliquat → dettes BADF (crédite BADF)
    if reliquat > 0:
        admin = fetchone(conn,
            "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
            (tontine_id,))
        if admin:
            q(conn, """INSERT INTO dettes_badf
                       (admin_wa, tontine_id, type_dette, montant, ref_cotis)
                       VALUES (%s,%s,'FMP',%s,NULL)""",
              (admin["whatsapp"], tontine_id, reliquat))

    conn.commit()
    log_audit("CAUTION_SAISIE_COMPENSATION",
              f"{membre['nom_complet']} | Caution:{montant_caution:,} | "
              f"Compensé:{compense:,} | Reliquat BADF:{reliquat:,} | {note}",
              membre["whatsapp"])

    # Notifier les admins
    wa_admins_tontine(tontine_id,
        f"🔒 *CAUTION SAISIE — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Membre    : *{membre['nom_complet']}*\n"
        f"Caution   : *{montant_caution:,} FCFA*\n"
        f"Motif     : _{raison}_\n\n"
        f"Utilisation :\n"
        f"  ▪ Compensation groupe : *{compense:,} FCFA*\n"
        f"  ▪ Reversé à BADF      : *{reliquat:,} FCFA*\n\n"
        f"_{note}_\n\n"
        f"_TontineBot Pro — BADF Ltd_"
    )

    # Notifier le fugitif
    wa_prive(membre["whatsapp"],
        f"🔴 *CAUTION SAISIE — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Votre caution de *{montant_caution:,} FCFA* a été saisie.\n"
        f"Motif : _{raison}_\n\n"
        f"*Utilisation :*\n"
        f"  ▪ Cotisations manquantes couvertes : *{compense:,} FCFA*\n"
        f"  ▪ Reversé à BADF Ltd : *{reliquat:,} FCFA*\n\n"
        + (f"⚠️ Il reste *{montant_du - montant_caution:,} FCFA* non couverts "
           f"enregistrés comme dette à votre charge.\n\n"
           if montant_du > montant_caution else "") +
        f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
    )

    return {
        "ok":        True,
        "compense":  compense,
        "reliquat":  reliquat,
        "caution":   montant_caution,
        "note":      note,
        "nom":       membre["nom_complet"],
    }


@healed()
def verifier_et_liberer_cautions():
    """
    Lancé quotidiennement à 9h00.
    Pour chaque membre ayant une caution bloquée :
      - Si cotisations post-bouffage = passages restants → libère la caution
      - Notifie le membre + l'admin
    """
    conn     = get_conn()
    cautions = fetchall(conn, """
        SELECT cg.id, cg.membre_id, cg.tontine_id, cg.montant,
               m.nom_complet, m.whatsapp,
               t.nom AS tontine_nom, t.cycle_actuel
        FROM cautions_garantie cg
        JOIN membres m  ON m.id  = cg.membre_id
        JOIN tontines t ON t.id  = cg.tontine_id
        WHERE cg.statut = 'Bloquee'
    """)

    liberees = 0
    for c in cautions:
        mon_pass = fetchone(conn, """
            SELECT ordre FROM liste_passage
            WHERE membre_id=%s AND tontine_id=%s AND cycle=%s AND statut='Paye'
            ORDER BY date_paiement DESC LIMIT 1
        """, (c["membre_id"], c["tontine_id"], c["cycle_actuel"]))

        if not mon_pass:
            continue

        restants = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND statut='En_attente'
        """, (c["tontine_id"], c["cycle_actuel"], mon_pass["ordre"]))["n"]

        cotis_post = fetchone(conn, """
            SELECT COUNT(*) n FROM transactions
            WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
              AND statut='Confirmee'
              AND date_heure > (SELECT date_bouffage FROM cautions_garantie WHERE id=%s)
        """, (c["membre_id"], c["tontine_id"], c["id"]))["n"]

        if cotis_post >= restants and restants == 0:
            # Fin de cycle — libérer automatiquement
            q(conn, """UPDATE cautions_garantie
                       SET statut='Liberee', date_liberation=NOW()
                       WHERE id=%s""", (c["id"],))
            conn.commit()
            liberees += 1

            # Notifier le membre
            wa_prive(c["whatsapp"],
                f"🔓 *CAUTION LIBÉRÉE — {c['tontine_nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Félicitations *{c['nom_complet']}* !\n\n"
                f"Vous avez honoré toutes vos cotisations jusqu'à la fin du cycle.\n"
                f"Votre caution de *{c['montant']:,} FCFA* est libérée.\n\n"
                f"Votre admin va vous la reverser.\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )

            # Notifier l'admin
            wa_admins_tontine(c["tontine_id"],
                f"🔓 *CAUTION À LIBÉRER — {c['tontine_nom']}*\n\n"
                f"Le membre *{c['nom_complet']}* a honoré toutes ses cotisations.\n"
                f"Veuillez lui reverser sa caution : *{c['montant']:,} FCFA*\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )

            log_audit("CAUTION_LIBEREE_AUTO",
                      f"{c['nom_complet']} | {c['tontine_nom']} | {c['montant']:,} FCFA")

    release_conn(conn)
    if liberees > 0:
        log.info(f"✅ {liberees} caution(s) libérée(s) automatiquement")


def _verifier_liberation_caution(conn, membre_id: int, tontine_id: int):
    """Libère la caution si le membre a cotisé pour tous les membres restants."""
    caution = fetchone(conn,
        "SELECT id, montant FROM cautions_garantie "
        "WHERE membre_id=%s AND tontine_id=%s AND statut='Bloquee'",
        (membre_id, tontine_id))
    if not caution:
        return
    tontine   = fetchone(conn, "SELECT cycle_actuel FROM tontines WHERE id=%s", (tontine_id,))
    mon_pass  = fetchone(conn, """
        SELECT ordre FROM liste_passage
        WHERE membre_id=%s AND tontine_id=%s AND cycle=%s AND statut='Paye'
        ORDER BY date_paiement DESC LIMIT 1
    """, (membre_id, tontine_id, tontine["cycle_actuel"]))
    if not mon_pass:
        return
    restants = fetchone(conn, """
        SELECT COUNT(*) n FROM liste_passage
        WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND statut='En_attente'
    """, (tontine_id, tontine["cycle_actuel"], mon_pass["ordre"]))["n"]
    cotis_post = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
          AND statut='Confirmee'
          AND date_heure > (SELECT date_bouffage FROM cautions_garantie WHERE id=%s)
    """, (membre_id, tontine_id, caution["id"]))["n"]
    if cotis_post >= restants:
        q(conn, "UPDATE cautions_garantie SET statut='Liberee', date_liberation=NOW() WHERE id=%s",
          (caution["id"],))
        conn.commit()
        m = fetchone(conn, "SELECT whatsapp, nom_complet FROM membres WHERE id=%s", (membre_id,))
        if m:
            wa_prive(m["whatsapp"],
                f"🔓 *CAUTION LIBÉRÉE !*\n"
                f"Félicitations {m['nom_complet']} !\n"
                f"Votre caution de *{caution['montant']:,} FCFA* est libérée.\n"
                f"Merci de votre sérieux 🙏")
