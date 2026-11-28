"""
Schéma PostgreSQL du bot : tables, migrations isolées par SAVEPOINT, index
et contrôle des tables critiques. init_db() est idempotent.

Extrait de barack_corp_v9_18.py sans changement ; dépend de db.py, jamais du
bot.
"""
import logging

from katechon.core.db import get_conn, release_conn, fetchone

__all__ = ["init_db"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


# Reprises à usage unique (voir init_db).
# Avant sanctions.levee_le, une réactivation ne marquait rien et deux jobs ne traçaient
# pas leurs suspensions : les anciennes données ne disent pas qui a levé quoi. Plutôt
# que de deviner, toutes les suspensions antérieures sont marquées levées une fois ;
# un membre déjà suspendu au déploiement se réactive comme avant (admin de l'une de
# ses tontines), et l'isolation par tontine vaut pour toute suspension postérieure.
_REPRISES_DONNEES = [
    ("2026-11-sanctions-levee", [
        """UPDATE sanctions SET levee_le = NOW()
           WHERE levee_le IS NULL
             AND type_sanction IN ('Suspension_72h','Interception_bouffage','Fugue_post_bouffage')""",
    ]),
]

def init_db():
    conn = get_conn()
    c    = conn.cursor()

    # ── MEMBRES ──────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS membres (
        id                  SERIAL PRIMARY KEY,
        nom_complet         TEXT    NOT NULL,
        kyc_hash            TEXT    NOT NULL UNIQUE,
        whatsapp            TEXT    NOT NULL UNIQUE,
        statut_global       TEXT    NOT NULL DEFAULT 'En_attente_kyc'
            CHECK(statut_global IN
                ('En_attente_kyc','Actif','Suspendu_global','Banni')),
        adhesion_payee      INTEGER NOT NULL DEFAULT 0,
        -- Onboarding (prénom seul)
        kyc_complet         INTEGER NOT NULL DEFAULT 0,
        kyc_etape           INTEGER NOT NULL DEFAULT 0,
        kyc_nom             TEXT,
        -- Finances
        solde_dette         INTEGER NOT NULL DEFAULT 0,
        dette_ira_total     INTEGER NOT NULL DEFAULT 0,
        crediteur_id        INTEGER REFERENCES membres(id),
        -- Anti-fraude
        tentatives_fraude   INTEGER NOT NULL DEFAULT 0,
        score_confiance     INTEGER NOT NULL DEFAULT 100,
        blackliste          INTEGER NOT NULL DEFAULT 0,
        -- Anti-fugue
        dernier_bouffage    TIMESTAMPTZ,
        nb_bouffages        INTEGER NOT NULL DEFAULT 0,
        -- Suspension pour retard cotisation
        suspendu_retard              INTEGER NOT NULL DEFAULT 0,
        date_suspension_retard       TIMESTAMPTZ,
        date_adhesion       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── TONTINES ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS tontines (
        id               SERIAL PRIMARY KEY,
        nom              TEXT    NOT NULL,
        type_tontine     TEXT    NOT NULL
            CHECK(type_tontine IN ('Journaliere','Hebdomadaire','Mensuelle')),
        montant_place    INTEGER NOT NULL DEFAULT 5000,
        whatsapp_groupe  TEXT    UNIQUE,  -- identifiant primaire du groupe WA
        statut           TEXT    NOT NULL DEFAULT 'Active'
            CHECK(statut IN ('Active','Suspendue','Terminee')),
        capacite_max     INTEGER NOT NULL DEFAULT 2000,
        cycle_actuel     INTEGER NOT NULL DEFAULT 1,
        heure_limite     TEXT    NOT NULL DEFAULT '18:00',
        heure_ouverture  TEXT    NOT NULL DEFAULT '05:00',  -- Rappel ouverture dépôts
        heure_rappel     TEXT    NOT NULL DEFAULT '14:00',  -- Rappel non-cotisants
        heure_bouffage   TEXT    NOT NULL DEFAULT '17:00',
        penalite_echec   INTEGER NOT NULL DEFAULT 500,
        penalite_desist  INTEGER NOT NULL DEFAULT 20000,
        caution_active   INTEGER NOT NULL DEFAULT 1,
        caution_pourcent INTEGER NOT NULL DEFAULT 10,
        credit_comm_statut TEXT NOT NULL DEFAULT 'Non_eligible',
        date_creation    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── ADHÉSIONS ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS adhesions (
        id            SERIAL PRIMARY KEY,
        membre_id     INTEGER NOT NULL REFERENCES membres(id) ON DELETE CASCADE,
        tontine_id    INTEGER NOT NULL REFERENCES tontines(id) ON DELETE CASCADE,
        nombre_places INTEGER NOT NULL DEFAULT 1,
        statut        TEXT    NOT NULL DEFAULT 'Actif'
            CHECK(statut IN ('Actif','Suspendu','Quitte','Pause','Exonere')),
        jours_avance                 INTEGER NOT NULL DEFAULT 0,
        nb_avertissements_retard     INTEGER NOT NULL DEFAULT 0,
        date_adhesion TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(membre_id, tontine_id)
    )""")

    # ── LISTE DE PASSAGE ──────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS liste_passage (
        id                SERIAL PRIMARY KEY,
        tontine_id        INTEGER NOT NULL REFERENCES tontines(id),
        membre_id         INTEGER REFERENCES membres(id),  -- NULL si nickname non encore lié
        nickname          TEXT,                            -- Nom tel que donné par l'admin
        date_bouffage     DATE,                            -- Date prévue du bouffage
        cycle             INTEGER NOT NULL DEFAULT 1,
        ordre             INTEGER NOT NULL,
        statut            TEXT    NOT NULL DEFAULT 'En_attente'
            CHECK(statut IN
                ('En_attente','Notifie','Paye','Intercepte','Cede')),
        numero_cashout    TEXT,
        operateur_cashout TEXT,
        montant_bouffage  INTEGER,
        date_paiement     TIMESTAMPTZ,
        soumis_par        TEXT,           -- admin qui a soumis la liste
        UNIQUE(tontine_id, cycle, ordre)
    )""")

    # ── TRANSACTIONS ──────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id               SERIAL PRIMARY KEY,
        membre_id        INTEGER NOT NULL REFERENCES membres(id),
        tontine_id       INTEGER REFERENCES tontines(id),
        montant_brut     INTEGER NOT NULL,
        frais_fmp        INTEGER NOT NULL DEFAULT 0,
        frais_ira        INTEGER NOT NULL DEFAULT 0,
        montant_net      INTEGER NOT NULL,
        type_transaction TEXT    NOT NULL
            CHECK(type_transaction IN (
                'Cotisation','Adhesion','Reactivation',
                'Changement_num','Bouffage','Remboursement','Penalite'
            )),
        statut           TEXT    NOT NULL DEFAULT 'Confirmee'
            CHECK(statut IN ('En_attente','Confirmee','Echouee','Rejetee')),
        reference        TEXT,
        periodes_payees  INTEGER NOT NULL DEFAULT 1,
        ip_source        TEXT,
        date_heure       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── COTISATIONS MANUELLES ─────────────────────────────────────────────
    # Enregistre chaque screenshot soumis dans le groupe
    c.execute("""CREATE TABLE IF NOT EXISTS cotisations_manuelles (
        id                SERIAL PRIMARY KEY,
        membre_id         INTEGER NOT NULL REFERENCES membres(id),
        tontine_id        INTEGER NOT NULL REFERENCES tontines(id),
        periode           INTEGER NOT NULL DEFAULT 1,
        montant_declare   INTEGER NOT NULL,
        fmp_du            INTEGER NOT NULL DEFAULT 0,
        screenshot_hash   TEXT,               -- SHA-256 anti-recyclage
        statut            TEXT NOT NULL DEFAULT 'En_attente'
            CHECK(statut IN ('En_attente','Confirme','Rejete')),
        confirme_par      TEXT,               -- whatsapp de l'admin
        date_soumission   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_confirmation TIMESTAMPTZ
    )""")

    # ── SCREENSHOTS HASH — Anti-recyclage ────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS screenshots_hash (
        id          SERIAL PRIMARY KEY,
        hash        TEXT NOT NULL UNIQUE,
        membre_id   INTEGER REFERENCES membres(id),
        tontine_id  INTEGER REFERENCES tontines(id),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── BOUFFAGES MANUELS ─────────────────────────────────────────────────
    # Suit chaque bouffage — l'admin vire manuellement
    c.execute("""CREATE TABLE IF NOT EXISTS bouffages_manuels (
        id                    SERIAL PRIMARY KEY,
        membre_id             INTEGER NOT NULL REFERENCES membres(id),
        tontine_id            INTEGER NOT NULL REFERENCES tontines(id),
        passage_id            INTEGER REFERENCES liste_passage(id),
        montant_brut          INTEGER NOT NULL,
        caution               INTEGER NOT NULL DEFAULT 0,
        montant_net           INTEGER NOT NULL,
        numero_mm             TEXT,               -- numéro Mobile Money du bénéficiaire
        statut                TEXT NOT NULL DEFAULT 'En_attente'
            CHECK(statut IN ('En_attente','Vire','Confirme','Expire')),
        confirme_par          TEXT,               -- whatsapp de l'admin
        date_declenchement    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_confirmation     TIMESTAMPTZ,
        expiration            TIMESTAMPTZ         -- 2h pour répondre
    )""")

    # ── DETTES BADF — FMP + adhésions dues par admin ──────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS dettes_badf (
        id          SERIAL PRIMARY KEY,
        admin_wa    TEXT NOT NULL,
        tontine_id  INTEGER REFERENCES tontines(id),
        type_dette  TEXT NOT NULL
            CHECK(type_dette IN ('FMP','Adhesion','IRA','Reactivation','Changement_num')),
        montant     INTEGER NOT NULL,
        statut      TEXT NOT NULL DEFAULT 'Due'
            CHECK(statut IN ('Due','Payee')),
        ref_cotis   INTEGER REFERENCES cotisations_manuelles(id),
        date_creation TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_paiement TIMESTAMPTZ,
        code_paiement TEXT
    )""")

    # ── ADMINS GROUPE ─────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS admins_groupe (
        id         SERIAL PRIMARY KEY,
        tontine_id INTEGER NOT NULL REFERENCES tontines(id) ON DELETE CASCADE,
        whatsapp   TEXT    NOT NULL,
        nom        TEXT,
        numero_collecte TEXT,   -- numéro MTN/Orange qui reçoit les cotisations
        date_ajout TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(tontine_id, whatsapp)
    )""")

    # ── SANCTIONS ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS sanctions (
        id               SERIAL PRIMARY KEY,
        membre_id        INTEGER NOT NULL REFERENCES membres(id),
        tontine_id       INTEGER REFERENCES tontines(id),
        type_sanction    TEXT    NOT NULL
            CHECK(type_sanction IN (
                'Retard_paiement','Suspension_72h','Blocage_permanent',
                'Interception_bouffage','Tentative_fraude',
                'Fugue_post_bouffage','Exclusion'
            )),
        montant_penalite INTEGER NOT NULL DEFAULT 0,
        date_sanction    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        notes            TEXT
    )""")

    # ── HISTORIQUE SCORE CONFIANCE ────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS historique_score_confiance (
        id         SERIAL PRIMARY KEY,
        membre_id  INTEGER NOT NULL REFERENCES membres(id) ON DELETE CASCADE,
        score_av   INTEGER NOT NULL,
        score_ap   INTEGER NOT NULL,
        delta      INTEGER NOT NULL,
        raison     TEXT    NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hsc_membre ON historique_score_confiance(membre_id, created_at DESC)")

    # ── CAUTIONS GARANTIE ─────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS cautions_garantie (
        id              SERIAL PRIMARY KEY,
        membre_id       INTEGER NOT NULL REFERENCES membres(id),
        tontine_id      INTEGER NOT NULL REFERENCES tontines(id),
        passage_id      INTEGER NOT NULL REFERENCES liste_passage(id),
        montant         INTEGER NOT NULL,
        pourcent        INTEGER NOT NULL DEFAULT 10,
        statut          TEXT    NOT NULL DEFAULT 'Bloquee'
            CHECK(statut IN ('Bloquee','Liberee','Saisie')),
        date_bouffage   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_liberation TIMESTAMPTZ,
        UNIQUE(membre_id, tontine_id, passage_id)
    )""")

    # ── DETTES IRA ────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS dettes_ira (
        id          SERIAL PRIMARY KEY,
        membre_id   INTEGER NOT NULL REFERENCES membres(id),
        tontine_id  INTEGER NOT NULL REFERENCES tontines(id),
        montant     INTEGER NOT NULL,
        statut      TEXT    NOT NULL DEFAULT 'Due'
            CHECK(statut IN ('Due','Prelevee')),
        motif       TEXT,
        date_echeance DATE,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        prelevee_le TIMESTAMPTZ
    )""")

    # ── PONCTUALITÉ / MULLIGAN (IRA v2) ───────────────────────────────────
    # 1 slip réseau toléré par cycle : le 1er retard préserve bonus+position,
    # le 2e éjecte. Pilote la priorité de rotation du cycle suivant (ira_engine).
    c.execute("""CREATE TABLE IF NOT EXISTS ponctualite_cycle (
        id          SERIAL PRIMARY KEY,
        membre_id   INTEGER NOT NULL REFERENCES membres(id),
        tontine_id  INTEGER NOT NULL REFERENCES tontines(id),
        cycle       INTEGER NOT NULL,
        slips       INTEGER NOT NULL DEFAULT 0,
        eligible    BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(membre_id, tontine_id, cycle)
    )""")

    # ── ALERTES FUGUE ─────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS alertes_fugue (
        id          SERIAL PRIMARY KEY,
        membre_id   INTEGER NOT NULL REFERENCES membres(id),
        tontine_id  INTEGER NOT NULL REFERENCES tontines(id),
        type_alerte TEXT    NOT NULL
            CHECK(type_alerte IN
                ('Avertissement_1','Avertissement_2','Blocage','Interception_caution')),
        jours_retard INTEGER NOT NULL DEFAULT 0,
        montant_du   INTEGER NOT NULL DEFAULT 0,
        traite       INTEGER NOT NULL DEFAULT 0,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── CAS DIFFICILES ────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS cas_difficiles (
        id          SERIAL PRIMARY KEY,
        membre_id   INTEGER NOT NULL REFERENCES membres(id),
        tontine_id  INTEGER NOT NULL REFERENCES tontines(id),
        type_cas    TEXT    NOT NULL
            CHECK(type_cas IN
                ('Pause','Echelonnement','Cession','Exoneration','Exclusion')),
        details     TEXT,
        nb_tranches  INTEGER,
        montant_tranche INTEGER,
        tranches_payees INTEGER NOT NULL DEFAULT 0,
        date_reprise DATE,
        cessionnaire_id INTEGER REFERENCES membres(id),
        statut      TEXT    NOT NULL DEFAULT 'Actif'
            CHECK(statut IN ('Actif','Termine','Annule')),
        admin_id    TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── AUDIT LOG ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id         SERIAL PRIMARY KEY,
        type_event TEXT NOT NULL,
        details    TEXT,
        whatsapp   TEXT,
        ip         TEXT,
        date_heure TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── COMMIT intermédiaire — sécurise les CREATE TABLE avant les migrations ──
    # En PostgreSQL, une migration échouée met la transaction en état ERROR.
    # Sans ce commit, un RENAME de colonne manquante annulerait TOUTES les tables.
    try:
        conn.commit()
    except Exception as e:
        log.error(f"❌ Commit intermédiaire init_db ERREUR : {e}")
        conn.rollback()
        release_conn(conn)
        return

    # ── MIGRATIONS v9.17 — chaque migration est isolée dans un SAVEPOINT ──────
    # Un SAVEPOINT garantit que l'échec d'une migration n'affecte pas les autres
    # et ne met PAS la transaction globale en état ERROR.
    for migration in [
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS nickname TEXT",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS date_bouffage DATE",
        "ALTER TABLE liste_passage ALTER COLUMN membre_id DROP NOT NULL",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS bot_est_admin INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS soumis_par TEXT",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS bloque_suspect INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS date_blocage TIMESTAMPTZ",
        # Levée d'une suspension par l'admin de SA tontine (réactivation isolée par tontine).
        "ALTER TABLE sanctions ADD COLUMN IF NOT EXISTS levee_le TIMESTAMPTZ",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS heure_ouverture TEXT NOT NULL DEFAULT '05:00'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS heure_rappel TEXT NOT NULL DEFAULT '14:00'",
        "ALTER TABLE admins_groupe ADD COLUMN IF NOT EXISTS numero_collecte TEXT",
        # ── Multi-pays ────────────────────────────────────────────────────
        "ALTER TABLE membres  ADD COLUMN IF NOT EXISTS pays_code TEXT NOT NULL DEFAULT 'CM'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS pays_code TEXT NOT NULL DEFAULT 'CM'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS devise    TEXT NOT NULL DEFAULT 'FCFA'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS langue    TEXT NOT NULL DEFAULT 'fr'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS timezone  TEXT NOT NULL DEFAULT 'Africa/Douala'",
        # ── Multi-cadence (v9.18) ──────────────────────────────────────────
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS jour_semaine TEXT NOT NULL DEFAULT 'Lundi' CHECK(jour_semaine IN ('Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche'))",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS jour_mois INTEGER NOT NULL DEFAULT 1 CHECK(jour_mois BETWEEN 1 AND 28)",
        "ALTER TABLE adhesions ADD COLUMN IF NOT EXISTS nb_avertissements_retard INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS credit_comm_statut TEXT NOT NULL DEFAULT 'Non_eligible'",
        # ── Trust Graph cross-tontine (PATCH 41) ──────────────────────────
        "ALTER TABLE alertes_fugue DROP CONSTRAINT IF EXISTS alertes_fugue_type_alerte_check",
        """ALTER TABLE alertes_fugue ADD CONSTRAINT alertes_fugue_type_alerte_check
           CHECK(type_alerte IN (
               'Avertissement_1','Avertissement_2','Blocage','Interception_caution',
               'TrustGraph_orange','TrustGraph_rouge'
           ))""",
        # ── Capture numéro Mobile Money bouffage (BOUFFAGE_VIRE) ────────────
        "ALTER TABLE bouffages_manuels ADD COLUMN IF NOT EXISTS operateur_cashout TEXT",
        # ── Lien dette IRA ↔ versement : on ne solde et ne comptabilise que les
        # dettes réellement déduites du versement. montant_ira NULL = versement
        # créé avant ce lien (ancienne règle conservée à la confirmation).
        "ALTER TABLE bouffages_manuels ADD COLUMN IF NOT EXISTS montant_ira INTEGER",
        "ALTER TABLE dettes_ira ADD COLUMN IF NOT EXISTS bouffage_id INTEGER REFERENCES bouffages_manuels(id)",
        # ── FORTERESSE concurrence : UNIQUE(tontine,cycle,ordre) DEFERRABLE ──
        # Une permutation d'ordre (reclassement, MODIF_ORDRE, dup cycle) crée des
        # doublons TRANSITOIRES ; la vérif differée au COMMIT les tolère tant que
        # l'état final est valide. Couplé à verrou_ordre_tontine (advisory lock).
        # Idempotent : ne reconstruit l'index qu'une fois (skip si déjà deferrable).
        """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint
                         WHERE conname = 'liste_passage_ordre_uk' AND condeferrable) THEN
            ALTER TABLE liste_passage
              DROP CONSTRAINT IF EXISTS liste_passage_tontine_id_cycle_ordre_key;
            ALTER TABLE liste_passage
              DROP CONSTRAINT IF EXISTS liste_passage_ordre_uk;
            ALTER TABLE liste_passage
              ADD CONSTRAINT liste_passage_ordre_uk
              UNIQUE (tontine_id, cycle, ordre) DEFERRABLE INITIALLY DEFERRED;
          END IF;
        END $$;""",
        # ── Anti-recyclage renforcé (finding #3) ────────────────────────────
        # Perceptual hash stocké par screenshot (TEXT hex, évite le débordement
        # BIGINT signé d'un dHash 64 bits).
        "ALTER TABLE screenshots_hash ADD COLUMN IF NOT EXISTS dhash TEXT",
        # Dédup FORTE sur la référence de transaction (unique par paiement réel),
        # insensible au 1px / re-encodage qui défait le byte-hash.
        """CREATE TABLE IF NOT EXISTS references_transaction (
            id             SERIAL PRIMARY KEY,
            tontine_id     INTEGER NOT NULL REFERENCES tontines(id),
            reference_norm TEXT    NOT NULL,
            membre_id      INTEGER REFERENCES membres(id),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(tontine_id, reference_norm)
        )""",
    ]:
        try:
            c.execute("SAVEPOINT mig")
            c.execute(migration)
            c.execute("RELEASE SAVEPOINT mig")
        except Exception as e:
            c.execute("ROLLBACK TO SAVEPOINT mig")
            log.error(f"❌ Migration en échec (ignorée, SAVEPOINT isolé) : {migration[:80]} : {e}")

    # ── REPRISES DE DONNÉES À USAGE UNIQUE ────────────────────────────────
    # Contrairement aux migrations (rejouées à chaque démarrage), une reprise ne
    # s'applique qu'une fois : rejouée, elle lèverait ou recréerait des suspensions
    # gérées depuis par les admins. reprises_donnees garde la trace de son passage.
    c.execute("""CREATE TABLE IF NOT EXISTS reprises_donnees (
        nom          TEXT PRIMARY KEY,
        appliquee_le TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    for nom, requetes in _REPRISES_DONNEES:
        try:
            c.execute("SAVEPOINT reprise")
            c.execute("INSERT INTO reprises_donnees (nom) VALUES (%s) ON CONFLICT DO NOTHING", (nom,))
            if c.rowcount:
                for requete in requetes:
                    c.execute(requete)
                log.info(f"✅ Reprise de données appliquée : {nom}")
            c.execute("RELEASE SAVEPOINT reprise")
        except Exception as e:
            c.execute("ROLLBACK TO SAVEPOINT reprise")
            log.error(f"❌ Reprise de données en échec (ignorée, SAVEPOINT isolé) : {nom} : {e}")


    # ── INDEX PERFORMANCES ────────────────────────────────────────────────
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_mbr_wa      ON membres(whatsapp)",
        "CREATE INDEX IF NOT EXISTS idx_mbr_stat    ON membres(statut_global)",
        "CREATE INDEX IF NOT EXISTS idx_adh_mbr     ON adhesions(membre_id)",
        "CREATE INDEX IF NOT EXISTS idx_adh_ton     ON adhesions(tontine_id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_mbr      ON transactions(membre_id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_pass_ton    ON liste_passage(tontine_id,cycle,ordre)",
        "CREATE INDEX IF NOT EXISTS idx_pass_stat   ON liste_passage(statut)",
        "CREATE INDEX IF NOT EXISTS idx_fugue       ON alertes_fugue(traite,created_at) WHERE traite=0",
        "CREATE INDEX IF NOT EXISTS idx_audit       ON audit_log(type_event,date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_dette_ira   ON dettes_ira(membre_id,statut) WHERE statut='Due'",
        "CREATE INDEX IF NOT EXISTS idx_suspendu    ON membres(suspendu_retard,date_suspension_retard) WHERE suspendu_retard=1",
        "CREATE INDEX IF NOT EXISTS idx_cotis_man   ON cotisations_manuelles(tontine_id,statut)",
        "CREATE INDEX IF NOT EXISTS idx_screenshot  ON screenshots_hash(hash)",
        "CREATE INDEX IF NOT EXISTS idx_bouf_man    ON bouffages_manuels(tontine_id,statut)",
        "CREATE INDEX IF NOT EXISTS idx_dette_badf  ON dettes_badf(admin_wa,statut) WHERE statut='Due'",
        # ── Index composites pour haute volumétrie (flux 50M+ FCFA) ──────
        "CREATE INDEX IF NOT EXISTS idx_tx_ton_type_date ON transactions(tontine_id,type_transaction,date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tx_mbr_ton_stat  ON transactions(membre_id,tontine_id,statut)",
        "CREATE INDEX IF NOT EXISTS idx_adh_ton_stat     ON adhesions(tontine_id,statut) WHERE statut='Actif'",
        "CREATE INDEX IF NOT EXISTS idx_pass_actif       ON liste_passage(tontine_id,cycle,statut) WHERE statut IN ('En_attente','Notifie')",
        "CREATE INDEX IF NOT EXISTS idx_caution_active   ON cautions_garantie(membre_id,tontine_id,statut) WHERE statut='Bloquee'",
        "CREATE INDEX IF NOT EXISTS idx_audit_date       ON audit_log(date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cotis_pending    ON cotisations_manuelles(tontine_id,date_soumission DESC) WHERE statut='En_attente'",
        # Trust Graph : heures de soumission confirmées d'un membre dans une tontine.
        "CREATE INDEX IF NOT EXISTS idx_cotis_mbr_conf   ON cotisations_manuelles(membre_id,tontine_id,date_soumission) WHERE statut='Confirme'",
        # Hash unique anti-recyclage déjà UNIQUE mais on confirme l'index
        "CREATE INDEX IF NOT EXISTS idx_screenshot_date  ON screenshots_hash(created_at DESC)",
        # Anti-recyclage renforcé (finding #3)
        "CREATE INDEX IF NOT EXISTS idx_screenshot_mbr   ON screenshots_hash(membre_id,created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ref_tx           ON references_transaction(tontine_id,reference_norm)",
        # Anti-TOCTOU double-submit : un screenshot_hash ne peut créer qu'UNE cotisation.
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cotis_screenshot ON cotisations_manuelles(screenshot_hash) WHERE screenshot_hash IS NOT NULL",
    ]
    # SAVEPOINT par index — sans ça, un seul CREATE INDEX en échec empoisonne
    # toute la transaction PostgreSQL en cours et le conn.commit() plus bas se
    # comporte comme un rollback silencieux, annulant TOUTES les migrations et
    # tous les index précédents sans lever la moindre exception Python.
    for idx in indexes:
        try:
            c.execute("SAVEPOINT idx")
            c.execute(idx)
            c.execute("RELEASE SAVEPOINT idx")
        except Exception as e:
            c.execute("ROLLBACK TO SAVEPOINT idx")
            log.error(f"❌ Index en échec (ignoré, SAVEPOINT isolé) : {idx[:80]} : {e}")

    try:
        conn.commit()
        # Vérifier que les tables critiques existent vraiment
        tables_critiques = [
            "membres", "tontines", "adhesions", "admins_groupe",
            "cotisations_manuelles", "screenshots_hash",
            "bouffages_manuels", "dettes_badf"
        ]
        manquantes = []
        for table in tables_critiques:
            row = fetchone(conn,
                "SELECT to_regclass(%s) AS t", (f"public.{table}",))
            if not row or not row["t"]:
                manquantes.append(table)

        if manquantes:
            log.error(f"❌ Tables manquantes après init_db : {manquantes}")
            log.error("   Exécutez create_db_v917.sql manuellement dans psql.")
        else:
            log.info("✅ PostgreSQL TontineBot Pro v9.18 — toutes les tables présentes.")
    except Exception as e:
        log.error(f"❌ init_db commit ERREUR : {e}")
        conn.rollback()
    finally:
        release_conn(conn)
