"""
SOURCE : interface commune pour toutes les sources de logs.

Contrat :
  - lire_logs(debut, fin) -> list[dict] : logs bruts (peuvent être vides ou partiels)
  - fetch(debut, fin) -> DataFrame : logs normalisés + features calculées
"""
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

import config
from app.domaine.schema import normaliser, _normaliser_timestamp
from app.domaine.features import construire_tableau


def extraire_timestamp(log: dict) -> Optional[float]:
    """
    Donne l'instant d'un log brut en secondes (float), ou None s'il n'en a pas d'exploitable.

    Pourquoi ici : push, fichier et Loki ont tous besoin de dater un log AVANT la
    normalisation (pour filtrer par fenêtre). On respecte donc la même table de
    correspondance que schema.py (config.CORRESPONDANCE_CHAMPS), sinon un log dont
    le champ s'appelle "ts" serait jeté à tort.
    """
    noms = [config.CORRESPONDANCE_CHAMPS.get("timestamp"), "timestamp", "ts", "time", "t"]
    for nom in noms:
        if nom and log.get(nom) is not None:
            return _normaliser_timestamp(log[nom])
    return None


class Source(ABC):
    """Interface commune pour toutes les sources."""

    @abstractmethod
    def lire_logs(self, debut: float, fin: float) -> list[dict]:
        """Lit les logs bruts entre debut et fin (secondes)."""
        raise NotImplementedError

    def fetch(self, debut: float, fin: float, pas: int = 10) -> pd.DataFrame:
        """
        Lit les logs bruts, les normalise, et calcule les features.

        Résultat : DataFrame [timestamp (index), <service>__<feature> (colonnes)]
        """
        logs_bruts = self.lire_logs(debut, fin)
        logs_normalisés = normaliser(logs_bruts)
        return construire_tableau(logs_normalisés, debut, fin, pas=pas)
