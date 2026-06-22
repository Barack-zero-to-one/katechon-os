-- ════════════════════════════════════════════════════════════════════════
-- Migration v9.18 — Contraintes UNIQUE anti-doublons (PATCH 3)
-- BADF Ltd — TontineBot Pro
--
-- À exécuter UNE FOIS sur la base de production :
--   psql -h localhost -U postgres -d barack_corp -f migration_v9_18.sql
--
-- Idempotent : peut être ré-exécuté sans erreur grâce à IF NOT EXISTS
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── ÉTAPE 1 : Vérifier qu'il n'y a pas déjà des doublons ─────────────────
-- Si la requête détecte des doublons, la migration s'arrête (RAISE EXCEPTION)
-- et tu dois nettoyer manuellement avant de réessayer.

DO $$
DECLARE
    nb_doublons INTEGER;
BEGIN
    SELECT COUNT(*) INTO nb_doublons FROM (
        SELECT screenshot_hash, COUNT(*) as nb
        FROM cotisations_manuelles
        WHERE statut = 'Confirme'
        GROUP BY screenshot_hash
        HAVING COUNT(*) > 1
    ) doublons;

    IF nb_doublons > 0 THEN
        RAISE EXCEPTION 'ABORT: % doublons screenshot_hash dans cotisations confirmées. Nettoyer manuellement avant migration.', nb_doublons;
    END IF;
END $$;


DO $$
DECLARE
    nb_doublons INTEGER;
BEGIN
    SELECT COUNT(*) INTO nb_doublons FROM (
        SELECT ref_cotis, COUNT(*) as nb
        FROM dettes_badf
        WHERE type_dette = 'FMP' AND ref_cotis IS NOT NULL
        GROUP BY ref_cotis
        HAVING COUNT(*) > 1
    ) doublons;

    IF nb_doublons > 0 THEN
        RAISE EXCEPTION 'ABORT: % doublons FMP dans dettes_badf. Nettoyer avant migration.', nb_doublons;
    END IF;
END $$;


-- ── ÉTAPE 2 : Créer les index UNIQUE partiels ───────────────────────────
-- Index UNIQUE sur cotisations_manuelles : empêche d'avoir 2 cotisations
-- confirmées avec le même hash de screenshot.

CREATE UNIQUE INDEX IF NOT EXISTS idx_cotis_man_screenshot_confirmees
ON cotisations_manuelles (screenshot_hash)
WHERE statut = 'Confirme';


-- Index UNIQUE sur dettes_badf : une seule dette FMP par cotisation
CREATE UNIQUE INDEX IF NOT EXISTS idx_dettes_badf_unique_fmp
ON dettes_badf (ref_cotis)
WHERE type_dette = 'FMP' AND ref_cotis IS NOT NULL;


-- ── ÉTAPE 3 : Validation finale ─────────────────────────────────────────
DO $$
DECLARE
    nb_index INTEGER;
BEGIN
    SELECT COUNT(*) INTO nb_index FROM pg_indexes
    WHERE indexname IN (
        'idx_cotis_man_screenshot_confirmees',
        'idx_dettes_badf_unique_fmp'
    );

    IF nb_index = 2 THEN
        RAISE NOTICE 'OK: Migration v9.18 reussie, 2 index UNIQUE crees';
    ELSE
        RAISE EXCEPTION 'ECHEC: Seulement % index crees sur 2', nb_index;
    END IF;
END $$;

COMMIT;
