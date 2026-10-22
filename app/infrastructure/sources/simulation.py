"""
SIMULATION : fausse plateforme pour développer et tester.

Génère des LOGS BRUTS réalistes + incidents injectables.
Ajoute une colonne _verite (service réellement touché) pour l'évaluation.
"""

from typing import Any

import numpy as np
import pandas as pd

import config
from app.infrastructure.sources.base import Source


def _resultat(status_code) -> str:
    """Résultat applicatif déduit du code HTTP (même vocabulaire que les logs Basecamp)."""
    return "error" if status_code >= 500 else "denied" if status_code >= 400 else "success"


class SimulationSource(Source):
    """
    Simule une plateforme avec logs réalistes et incidents injectables.

    incidents : liste de dicts {"service", "debut_s", "duree_s", "type"}
      - type "dos" : afflux de requêtes, latence explose, erreurs 5xx
      - type "panne" : erreurs 5xx, latence élevée
      - type "brute_force" : rafale d'erreurs 4xx
    """

    PROFILS = {  # (moyenne, écart-type) en fonctionnement normal
        "req_count": (20, 4),
        "error_4xx": (0.5, 0),
        "error_5xx": (0.1, 0),
        "latency_avg_ms": (120, 15),
        "latency_p95_ms": (200, 30),
    }

    def __init__(
        self, incidents: list[dict] | None = None, seed: int = 42, pas: int = 10, origine: float | None = None
    ):
        """
        origine : instant de référence (secondes). Les incidents (debut_s) sont comptés depuis ce point.
                 Par défaut : None (début de chaque fetch). Fixé à time.time() pour temps réel.
        """
        self.incidents = list(incidents or [])
        self.rng = np.random.default_rng(seed)
        self.pas = pas
        self.origine = origine

    def ajouter_incident(self, service: str, type_: str, duree_s: int, maintenant: float) -> dict:
        """Déclenche un incident simulé."""
        if self.origine is None:
            raise RuntimeError("ajouter_incident nécessite une origine fixe")
        if service not in config.SERVICES:
            raise ValueError(f"Service inconnu : {service}")
        incident = {
            "service": service,
            "type": type_,
            "duree_s": int(duree_s),
            "debut_s": int(maintenant - self.origine),
        }
        self.incidents.append(incident)
        return incident

    def _instant(self, debut_fenetre) -> float:
        """Instant aléatoire DANS la fenêtre : de vrais logs ne tombent pas tous pile sur t."""
        return float(debut_fenetre) + float(self.rng.uniform(0, self.pas))

    def lire_logs(self, debut: float, fin: float) -> list[dict]:
        """Génère des logs bruts réalistes."""

        # Fenêtres alignées sur le pas, fin EXCLUE (même convention que construire_tableau).
        grille = pd.Index(
            np.arange(int(debut // self.pas * self.pas), int(np.ceil(fin / self.pas) * self.pas), self.pas)
        )
        if len(grille) == 0:
            return []

        origine = grille[0] if self.origine is None else self.origine
        decalage = np.asarray(grille - origine)
        # Variation sinusoïdale du trafic (monte et descend)
        vague = 1 + 0.3 * np.sin(2 * np.pi * (decalage / self.pas) / 120)

        logs: list[dict[str, Any]] = []
        for ts in grille:
            idx = int((ts - grille[0]) / self.pas)
            for service in config.SERVICES:
                # Nombre de requêtes pour cette fenêtre
                nb_req = int(self.rng.poisson(20 * vague[idx]))

                # Générer des logs individuels
                for _ in range(nb_req):
                    status_code = 200  # normal par défaut
                    if self.rng.random() < 0.02:  # 2% d'erreurs 4xx
                        status_code = 400 + int(self.rng.integers(0, 100))
                    elif self.rng.random() < 0.005:  # 0.5% d'erreurs 5xx
                        status_code = 500 + int(self.rng.integers(0, 10))

                    duration_ms = max(0, self.rng.normal(120, 15))

                    log = {
                        "timestamp": self._instant(ts),
                        "service": service,
                        "level": "ERROR" if status_code >= 500 else "WARN" if status_code >= 400 else "INFO",
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                        "outcome": _resultat(status_code),
                    }
                    logs.append(log)

        # Appliquer les incidents
        for inc in self.incidents:
            m = (decalage >= inc["debut_s"]) & (decalage < inc["debut_s"] + inc["duree_s"])
            indices = np.where(m)[0]
            if len(indices) == 0:
                continue

            # Modifier les logs pendant l'incident
            service = inc["service"]
            type_inc = inc["type"]

            if type_inc == "dos":
                # Afflux de requêtes + latence élevée + erreurs 5xx
                for i, _ in enumerate(indices):
                    # Ajouter des requêtes
                    ts_fenetre = grille[indices[i]]
                    for _ in range(int(self.rng.poisson(150))):
                        logs.append(
                            {
                                "timestamp": self._instant(ts_fenetre),
                                "service": service,
                                "level": "ERROR" if self.rng.random() < 0.1 else "INFO",
                                "status_code": 500 if self.rng.random() < 0.1 else 200,
                                "duration_ms": max(0, self.rng.normal(500, 100)),
                            }
                        )
                        logs[-1]["outcome"] = _resultat(logs[-1]["status_code"])

            elif type_inc == "panne":
                # Erreurs 5xx + latence élevée
                for i, _ in enumerate(indices):
                    ts_fenetre = grille[indices[i]]
                    for _ in range(int(self.rng.poisson(20))):
                        logs.append(
                            {
                                "timestamp": self._instant(ts_fenetre),
                                "service": service,
                                "level": "ERROR",
                                "status_code": 500,
                                "duration_ms": max(0, self.rng.normal(300, 50)),
                                "outcome": "error",
                            }
                        )

            elif type_inc == "brute_force":
                # Rafale d'erreurs 4xx
                for i, _ in enumerate(indices):
                    ts_fenetre = grille[indices[i]]
                    for _ in range(int(self.rng.poisson(50))):
                        logs.append(
                            {
                                "timestamp": self._instant(ts_fenetre),
                                "service": service,
                                "level": "WARN",
                                "status_code": 401 + self.rng.integers(0, 3),
                                "duration_ms": max(0, self.rng.normal(50, 10)),
                                "outcome": "denied",
                            }
                        )

        # Garde uniquement [debut, fin) : la première fenêtre peut commencer avant `debut`.
        return [log for log in logs if debut <= log["timestamp"] < fin]
