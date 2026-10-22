"""
REGISTRE DE MODÈLES : trois modèles, interface commune.

Tous respectent : decision_function(X) < 0  <=>  anormal

  1. isolation_forest : modèle principal (D2)
  2. lof : Local Outlier Factor avec StandardScaler (D3)
  3. statistique : référence simple (écarts-types) (D3)
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler


class ModeleBase:
    """Interface commune pour tous les modèles."""

    def fit(self, X: pd.DataFrame) -> "ModeleBase":
        raise NotImplementedError

    def decision_function(self, X: pd.DataFrame) -> np.ndarray:
        """Score pour chaque instance. Négatif = anormal."""
        raise NotImplementedError

    def score(self, X: pd.DataFrame) -> np.ndarray:
        """Alias de decision_function."""
        return self.decision_function(X)


class IsolationForestModele(ModeleBase):
    """Isolation Forest : entraîné sur du normal, puis signale ce qui s'en écarte."""

    def __init__(self, contamination: float = 0.01, n_estimators: int = 200, random_state: int = 42):
        self.params = {
            "contamination": contamination,
            "n_estimators": n_estimators,
            "random_state": random_state,
        }
        self.modele: IsolationForest | None = None

    def fit(self, X: pd.DataFrame) -> "IsolationForestModele":
        # Filtrer les colonnes constantes
        colonnes = [c for c in X.columns if X[c].std() > 0]
        self.colonnes = colonnes
        self.modele = IsolationForest(**self.params).fit(X[colonnes])
        return self

    def decision_function(self, X: pd.DataFrame) -> np.ndarray:
        if self.modele is None:
            raise RuntimeError("Modèle non entraîné")
        return self.modele.decision_function(X[self.colonnes])


class LOFModele(ModeleBase):
    """Local Outlier Factor : détecte les anomalies basées sur la densité locale."""

    def __init__(self, n_neighbors: int = 20, contamination: float = 0.01):
        self.params = {"n_neighbors": n_neighbors, "contamination": contamination, "novelty": True}
        self.modele: LocalOutlierFactor | None = None
        self.scaler: StandardScaler | None = None
        self.colonnes: list[str] = []

    def fit(self, X: pd.DataFrame) -> "LOFModele":
        # Filtrer les colonnes constantes
        colonnes = [c for c in X.columns if X[c].std() > 0]
        self.colonnes = colonnes

        # LOF mesure des distances : besoin de normaliser
        self.scaler = StandardScaler().fit(X[colonnes])
        X_normalisé = self.scaler.transform(X[colonnes])

        self.modele = LocalOutlierFactor(**self.params).fit(X_normalisé)
        return self

    def decision_function(self, X: pd.DataFrame) -> np.ndarray:
        if self.modele is None or self.scaler is None:
            raise RuntimeError("Modèle non entraîné")
        X_normalisé = self.scaler.transform(X[self.colonnes])
        # decision_function de sklearn suit DÉJÀ la convention commune (négatif = anormal,
        # seuil réglé par contamination) : surtout ne pas l'inverser (bug corrigé :
        # le signe inversé donnait "normal" aux points extrêmes et "anormal" aux typiques).
        return self.modele.decision_function(X_normalisé)


class StatistiqueModele(ModeleBase):
    """Modèle « statistique » : alerte si une colonne s'écarte de plus de N écarts-types (en plus OU en moins).

    Référence simple.
    """

    def __init__(self, seuil_ecarts_types: float = 3.0):
        self.seuil = seuil_ecarts_types
        self.moyenne: pd.Series | None = None
        self.ecart: pd.Series | None = None
        self.colonnes: list[str] = []

    def fit(self, X: pd.DataFrame) -> "StatistiqueModele":
        colonnes = [c for c in X.columns if X[c].std() > 0]
        self.colonnes = colonnes
        self.moyenne = X[colonnes].mean()
        # Plancher à 1 : un compteur presque à 0 aurait un écart-type minuscule
        self.ecart = X[colonnes].std().clip(lower=1.0)
        return self

    def decision_function(self, X: pd.DataFrame) -> np.ndarray:
        if self.moyenne is None or self.ecart is None:
            raise RuntimeError("Modèle non entraîné")
        # Écart de chaque colonne à sa moyenne normale, en écarts-types.
        # On prend la valeur ABSOLUE : une attaque fait monter les compteurs (DoS)
        # mais une panne peut les faire chuter (plus aucune requête) : les deux sont anormaux.
        z = (X[self.colonnes] - self.moyenne) / self.ecart
        # Score négatif <=> au moins une colonne dépasse le seuil (convention commune)
        return (self.seuil - z.abs().max(axis=1)).values


# Registre des modèles disponibles
MODELES_DISPONIBLES = {
    "isolation_forest": IsolationForestModele,
    "lof": LOFModele,
    "statistique": StatistiqueModele,
}


def creer_modele(type_modele: str, **kwargs) -> ModeleBase:
    """Factory pour créer un modèle par son type."""
    if type_modele not in MODELES_DISPONIBLES:
        raise ValueError(f"Type de modèle inconnu : {type_modele}. Disponibles : {list(MODELES_DISPONIBLES.keys())}")
    return MODELES_DISPONIBLES[type_modele](**kwargs)
