"""KATECHON OS — package du bot TontineBot Pro (BADF Ltd).

Sous-paquets, ajoutés étape par étape (security d'abord, puis core et
messaging, finance, handlers) :

core : base de données, schéma, état, réglages, santé, app Flask, scheduler.
security : durcissement du webhook, limiteur de débit, anti-recyclage.
finance : paiement, barème IRA, Trust Graph, supervision, OCR, jobs.
messaging : envoi WhatsApp, file d'envoi, textes.
handlers : webhook, menus admin, membre et owner, groupes, dashboard.

Les sous-paquets n'ont aucun effet de bord à l'import ; le point d'entrée
reste barack_corp_v9_18.py à la racine.
"""
