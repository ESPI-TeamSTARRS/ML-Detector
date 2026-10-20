"""
FICHIER : lit des logs bruts depuis un fichier (entraînement hors ligne).

Formats acceptés :
  .jsonl / .ndjson : un objet JSON par ligne
  .csv             : une colonne par champ (timestamp, service, level, ...)
  .log / .txt      : sortie de `docker compose logs` (voir docker_logs.py)

Utilisé par la CLI collect.py.
"""
import bisect
import json
from typing import Optional

import pandas as pd

import config
from app.infrastructure.sources.base import Source, extraire_timestamp
from app.infrastructure.sources.docker_logs import parser_ligne_docker


def _lire_lignes(chemin: str):
    """
    Lit les lignes d'un fichier texte. PowerShell 5 écrit `>` en UTF-16 : on détecte le BOM,
    sinon on lit en UTF-8 en remplaçant les octets invalides (un caractère bizarre ne doit
    pas faire échouer tout le chargement).
    """
    with open(chemin, "rb") as f:
        debut = f.read(2)
    encodage = "utf-16" if debut in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    with open(chemin, "r", encoding=encodage, errors="replace") as f:
        yield from f


class FichierSource(Source):
    """Lit les logs depuis un fichier (CSV, JSONL, ou sortie docker compose logs)."""

    def __init__(self, chemin_fichier: str):
        self.chemin = chemin_fichier
        # Chargé une fois : liste de (timestamp_s, log) TRIÉE par timestamp.
        self._entrees: Optional[list] = None
        self._instants: list[float] = []

    def _charger_fichier(self) -> list:
        if self._entrees is not None:
            return self._entrees

        chemin = self.chemin.lower()
        if chemin.endswith(".csv"):
            df = pd.read_csv(self.chemin)
            # to_dict met NaN pour les cases vides : on retire ces clés (champ absent).
            logs = [{k: v for k, v in ligne.items() if not pd.isna(v)}
                    for ligne in df.to_dict("records")]
        elif chemin.endswith((".jsonl", ".ndjson")):
            logs = []
            for ligne in _lire_lignes(self.chemin):
                if not ligne.strip():
                    continue
                try:
                    log = json.loads(ligne)
                except json.JSONDecodeError:
                    continue            # ligne corrompue : on passe, on ne plante pas
                if isinstance(log, dict):
                    logs.append(log)
        elif chemin.endswith((".log", ".txt")):
            services = list(config.SERVICES)
            logs = [log for log in (parser_ligne_docker(ligne, services)
                                    for ligne in _lire_lignes(self.chemin)) if log]
        else:
            raise ValueError(f"Format non supporté : {self.chemin}. "
                             "Accepte .csv, .jsonl, .ndjson, .log, .txt")

        # Timestamps convertis UNE seule fois ici (pas à chaque lecture de fenêtre).
        entrees = []
        for log in logs:
            ts = extraire_timestamp(log)
            if ts is not None:
                entrees.append((ts, log))
        entrees.sort(key=lambda e: e[0])
        self._entrees = entrees
        self._instants = [e[0] for e in entrees]
        return entrees

    def lire_logs(self, debut: float, fin: float) -> list[dict]:
        """Logs du fichier dans [debut, fin). Recherche dichotomique : rapide même sur de gros fichiers."""
        entrees = self._charger_fichier()
        i = bisect.bisect_left(self._instants, debut)
        j = bisect.bisect_left(self._instants, fin)
        return [log for _, log in entrees[i:j]]

    def etendue(self) -> tuple[float, float]:
        """(timestamp_min, timestamp_max) du fichier, ou (0, 0) s'il est vide."""
        self._charger_fichier()
        if not self._instants:
            return 0.0, 0.0
        return self._instants[0], self._instants[-1]
