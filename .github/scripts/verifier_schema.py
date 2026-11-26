"""CI : construit le schéma du bot sur un PostgreSQL vierge, deux fois de suite.

Sans les tests (locaux, #61), c'est la seule preuve en CI que le schéma, les
migrations et les reprises de données uniques passent sur une base neuve et
peuvent être rejoués à chaque démarrage. init_db() journalise ses échecs au
lieu de lever : toute ligne de niveau ERROR pendant init_db() fait échouer le job.

Refuse de tourner sur une base dont le nom ne finit pas par « _ci ».
"""
import logging
import os
import sys


class _Collecteur(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.erreurs = []

    def emit(self, record):
        self.erreurs.append(f"{record.name}: {record.getMessage()}")


def main() -> int:
    base = os.environ.get("PG_DB", "")
    if not base.endswith("_ci"):
        print(f"Refus : PG_DB={base!r} ne finit pas par _ci")
        return 2

    sys.path.insert(0, os.getcwd())
    import barack_corp_v9_18 as bc            # les secrets absents sont journalisés à l'import : hors mesure
    from katechon.core.db import fetchall, fetchone, get_conn, release_conn
    from katechon.core.schema import _REPRISES_DONNEES

    collecteur = _Collecteur()
    logging.getLogger().addHandler(collecteur)
    logging.getLogger("BarackCorp").addHandler(collecteur)

    bc.init_pool()
    try:
        for passage in (1, 2):
            bc.init_db()
            if collecteur.erreurs:
                print(f"init_db() passage {passage} : {len(collecteur.erreurs)} erreur(s)")
                for e in collecteur.erreurs:
                    print("  " + e)
                return 1
            print(f"init_db() passage {passage} : OK")

        conn = get_conn()
        try:
            critiques = ["membres", "tontines", "adhesions", "admins_groupe", "cotisations_manuelles",
                         "screenshots_hash", "bouffages_manuels", "dettes_badf", "liste_passage",
                         "sanctions", "reprises_donnees"]
            manquantes = [t for t in critiques
                          if not fetchone(conn, "SELECT to_regclass(%s) AS t", (f"public.{t}",))["t"]]
            if manquantes:
                print(f"Tables manquantes : {manquantes}")
                return 1
            noms = sorted(r["nom"] for r in fetchall(conn, "SELECT nom FROM reprises_donnees"))
            attendus = sorted(nom for nom, _ in _REPRISES_DONNEES)
            if noms != attendus:
                print(f"Reprises enregistrées {noms}, attendues {attendus}")
                return 1
        finally:
            release_conn(conn)
        print(f"Schéma OK : {len(critiques)} tables critiques, {len(attendus)} reprise(s) notée(s) une fois")
        return 0
    finally:
        bc.fermer_pool()


if __name__ == "__main__":
    sys.exit(main())
