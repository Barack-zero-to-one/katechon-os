"""
Couche canal : le code métier envoie et reçoit sans savoir par quelle
messagerie passe le message.

Un identifiant (le login d'un membre, l'adresse d'un groupe) porte son canal :
un préfixe enregistré (« tg: » pour Telegram, plus tard « ussd: »…) choisit le
canal correspondant, tout le reste est WhatsApp, le canal par défaut. Les
fonctions publiques d'envoi (wa_prive, wa_groupe, wa_owner…) gardent leur nom
et passent par canal_de() : leurs 113 appelants ne changent pas.

En entrée, chaque canal traduit son webhook en MessageEntrant, que
katechon.handlers.webhook.traiter_message route vers la logique métier.

Dépend de rien d'autre dans le package.
"""
import threading
from dataclasses import dataclass
from typing import Callable, Optional

__all__ = ["MessageEntrant", "Canal", "enregistrer", "retirer", "canal_de", "definir_defaut"]


@dataclass(frozen=True)
class MessageEntrant:
    """Un message reçu, quel que soit le canal.

    expediteur  : identifiant normalisé de la personne (+237…, tg:…)
    group_id    : adresse du groupe si le message vient d'un groupe, sinon ""
    type_media  : "" (texte), "image" ou "document"
    media_url   : référence du média à passer à telecharger()
    media_future: téléchargement déjà lancé (Future de bytes) ou None
    telecharger : fonction du canal qui rend les octets du média
    """
    canal: str
    expediteur: str
    group_id: str
    texte: str
    type_media: str
    media_url: str
    media_future: Optional[object]
    caption: str
    nom_affiche: str
    nom_groupe: str
    id_message: str
    telecharger: Callable[[str], bytes]


class Canal:
    """Interface d'un canal de messagerie. adresse() rend la forme canonique
    d'un identifiant ; c'est elle qui sert de clé à l'expéditeur (rythme par
    destinataire) et à l'outbox, et qu'envoyer() reçoit."""
    nom = ""

    def possede(self, identifiant: str) -> bool:
        raise NotImplementedError

    def adresse(self, identifiant: str) -> str:
        raise NotImplementedError

    def est_groupe(self, adresse: str) -> bool:
        raise NotImplementedError

    def envoyer(self, adresse: str, texte: str) -> bool:
        raise NotImplementedError


_verrou = threading.Lock()
_canaux = []
_defaut = None


def definir_defaut(canal):
    global _defaut
    with _verrou:
        _defaut = canal


def enregistrer(canal):
    """Ajoute un canal ; le dernier enregistré est consulté en premier."""
    with _verrou:
        if canal not in _canaux:
            _canaux.insert(0, canal)


def retirer(canal):
    with _verrou:
        if canal in _canaux:
            _canaux.remove(canal)


def canal_de(identifiant) -> Canal:
    ident = str(identifiant or "")
    with _verrou:
        for c in _canaux:
            if c.possede(ident):
                return c
        if _defaut is None:
            raise RuntimeError("aucun canal par défaut enregistré")
        return _defaut
