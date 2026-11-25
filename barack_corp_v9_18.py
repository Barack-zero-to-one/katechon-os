"""
TontineBot Pro — KATECHON OS (BADF Ltd) : point d'entrée.

Le code vit dans le package katechon/ (core, security, finance, messaging,
handlers). Ce fichier garde le nom que lancent watchdog.js et la CI :
`python barack_corp_v9_18.py` démarre le bot (pool, tables, sessions,
scheduler, Waitress) ; `import barack_corp_v9_18` expose la même surface
qu'avant la mise en package, réexportée depuis katechon.compat.
"""
from katechon.compat import *            # noqa: F401,F403 — surface historique du bot
from katechon.compat import __all__      # noqa: F401
from katechon.core.scheduler import demarrer

if __name__ == "__main__":
    demarrer()
