"""
Outils partagés par les tests du domaine.

On construit ici de petits jeux de données DÉTERMINISTES (graine fixe) : un test qui
change de résultat d'un jour à l'autre est inutilisable devant un jury.
"""

import numpy as np
import pandas as pd
import pytest

import config

T0 = 1_700_000_000 // 10 * 10  # un début de fenêtre de 10 s (multiple de 10)


SERVICES_TEST = {
    "api": {"criticite": 4, "features": ["req_count", "error_4xx", "error_5xx", "latency_avg_ms"]},
    "auth": {"criticite": 3, "features": ["req_count", "error_4xx", "error_5xx", "latency_avg_ms"]},
    "front": {"criticite": 2, "features": ["req_count", "error_5xx"]},
}

# Les VRAIS services de config.py, gardés de côté pour test_logs_basecamp.py.
SERVICES_REELS = {k: dict(v) for k, v in config.SERVICES.items()}

# Les tests ne doivent pas dépendre des vrais services de config.py (qui changent avec l'équipe) :
# on impose un jeu de services de test fixe, DÈS le chargement de ce fichier. Surtout pas dans une
# fixture : pytest crée d'abord les fixtures de portée "module" (ex. `detecteur`, `donnees`), qui
# verraient alors encore les vrais services.
config.SERVICES = {k: dict(v) for k, v in SERVICES_TEST.items()}


@pytest.fixture
def services_reels():
    """Les vrais services de config.py (pour les tests du format de logs réel)."""
    return SERVICES_REELS


@pytest.fixture
def services_mini(monkeypatch):
    """
    Remplace config.SERVICES par 2 petits services pour que les valeurs attendues
    des tests de features soient calculables à la main.
    """
    monkeypatch.setattr(
        "config.SERVICES",
        {
            "a": {
                "criticite": 4,
                "features": ["req_count", "error_4xx", "error_5xx", "error_logs", "latency_avg_ms", "latency_p95_ms"],
            },
            "b": {"criticite": 1, "features": ["req_count"]},
        },
    )
    return config.SERVICES


def _photos_normales(n: int, graine: int) -> pd.DataFrame:
    """Trafic « normal » synthétique pour les services api / auth / front de config.py."""
    rng = np.random.default_rng(graine)
    colonnes = {}
    for service, cfg in config.SERVICES.items():
        for f in cfg["features"]:
            if f == "req_count":
                colonnes[f"{service}__{f}"] = rng.poisson(20, n)
            elif f == "latency_avg_ms":
                colonnes[f"{service}__{f}"] = rng.normal(120, 4, n)
            else:  # error_4xx, error_5xx : compteurs presque toujours petits
                colonnes[f"{service}__{f}"] = rng.poisson(0.4, n)
    return pd.DataFrame(colonnes, index=pd.Index(T0 + 10 * np.arange(n), name="timestamp")).astype(float)


@pytest.fixture
def normal() -> pd.DataFrame:
    """300 photos normales (50 minutes) pour l'entraînement."""
    return _photos_normales(300, graine=1)


@pytest.fixture
def ligne_normale(normal):
    """Fabrique une photo parfaitement typique (médiane de chaque colonne) à un timestamp donné."""
    mediane = normal.median()

    def fabrique(ts: int) -> pd.DataFrame:
        return pd.DataFrame([mediane.values], columns=normal.columns, index=pd.Index([ts], name="timestamp"))

    return fabrique


@pytest.fixture
def ligne_anormale(ligne_normale):
    """Fabrique une photo où SEUL le service `service` dérive (4xx x100, latence x5)."""

    def fabrique(ts: int, service: str = "auth") -> pd.DataFrame:
        ligne = ligne_normale(ts)
        ligne[f"{service}__error_4xx"] = 60.0
        if f"{service}__latency_avg_ms" in ligne.columns:
            ligne[f"{service}__latency_avg_ms"] = 600.0
        return ligne

    return fabrique
