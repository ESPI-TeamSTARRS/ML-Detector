"""
DÉTECTEUR : le cœur de la détection d'anomalies.

  1. AnomalyDetector : un modèle PAR SERVICE, entraîné sur du trafic normal
  2. expliquer() : dit QUELLES MESURES ont dérivé (écarts-types)
  3. gravite() : criticité x intensité = niveau d'alerte
  4. IncidentTracker : filtre les alertes (3 photos anormales d'affilée)
"""

from __future__ import annotations

import json
import warnings
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pandas as pd
import sklearn

import config
from app.domaine.modeles import ModeleBase, creer_modele


def colonnes_features(df: pd.DataFrame) -> list[str]:
    """Les colonnes commençant par '_' (ex. _verite) ne sont jamais données au modèle."""
    return [c for c in df.columns if not c.startswith("_")]


def service_de(feature: str) -> str:
    """'api__error_4xx' -> 'api'. Sert à savoir quel service une colonne décrit."""
    return feature.split("__")[0]


class AnomalyDetector:
    """
    Détecteur avec UN MODÈLE PAR SERVICE.

    Pourquoi ? Testé sur données simulées : quand une panne touche 2 colonnes sur 11,
    un modèle global la détecte sur 60 % des photos. Avec un modèle par service : 100 %.
    Bonus : le modèle qui alerte désigne directement le service touché.
    """

    def __init__(self, type_modele: str | None = None, **kwargs):
        # Sans argument : on prend le réglage de config.py (un seul endroit à modifier).
        # Les paramètres de config ne valent que pour le type de config : on ne donne pas
        # n_estimators à un LOF. Les kwargs explicites l'emportent toujours.
        self.type_modele: str = type_modele if type_modele is not None else config.MODELE["type"]
        params = dict(config.MODELE["params"]) if self.type_modele == config.MODELE["type"] else {}
        params.update(kwargs)
        self.params_modele = params
        self.nb_photos_entrainement = 0
        self.modeles: dict[str, ModeleBase] = {}
        self.features_par_service: dict[str, list[str]] = {}
        self.features: list[str] = []
        self.moyenne: pd.Series | None = None
        self.ecart: pd.Series | None = None

    def fit(self, normal: pd.DataFrame) -> AnomalyDetector:
        """Entraîner sur du trafic normal uniquement."""
        X = normal[colonnes_features(normal)]
        self.nb_photos_entrainement = len(X)
        constantes = [c for c in X.columns if X[c].std() == 0]
        if constantes:
            print(f"[info] colonnes ignorées car constantes : {constantes}")
        self.features = [c for c in X.columns if c not in constantes]
        X = X[self.features]

        for service in sorted({service_de(c) for c in self.features}):
            colonnes = [c for c in self.features if service_de(c) == service]
            self.features_par_service[service] = colonnes
            self.modeles[service] = creer_modele(self.type_modele, **self.params_modele)
            self.modeles[service].fit(X[colonnes])

        # Statistiques du normal pour expliquer les anomalies
        # Plancher à 1 : compteur presque à 0 aurait écart-type minuscule
        self.moyenne = X.mean()
        self.ecart = X.std().clip(lower=1.0)
        return self

    def score(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Score chaque photo :
          score_<service> : score du modèle
          score : le pire des scores
          anomalie : au moins un service anormal
          service_suspect : le service au score le plus bas
        """
        manquantes = set(self.features) - set(df.columns)
        if manquantes:
            raise ValueError(f"Colonnes absentes : {sorted(manquantes)}")

        scores = pd.DataFrame(
            {f"score_{s}": m.decision_function(df[self.features_par_service[s]]) for s, m in self.modeles.items()},
            index=df.index,
        )

        resultat = scores.copy()
        resultat["score"] = scores.min(axis=1)
        resultat["anomalie"] = resultat["score"] < 0
        pire = scores.idxmin(axis=1).str.removeprefix("score_")
        resultat["service_suspect"] = pire.where(resultat["anomalie"], "")
        return resultat

    def expliquer(self, lignes: pd.DataFrame, service: str) -> dict:
        """Quelles colonnes du service s'écartent le plus ?"""
        if self.moyenne is None or self.ecart is None:
            raise RuntimeError("Détecteur non entraîné")
        moyenne_lignes = lignes[self.features].mean()
        z = (moyenne_lignes - self.moyenne) / self.ecart
        z_service = z[self.features_par_service[service]]
        top = z_service.abs().sort_values(ascending=False).head(3).index
        return {
            "service": service,
            "intensite": round(float(z_service.abs().max()), 1),
            "features_principales": [{"feature": f, "ecart_types": round(float(z[f]), 1)} for f in top],
        }

    def fiche(self) -> dict:
        """
        Fiche de traçabilité (pour le jury) : avec quoi ce modèle a été entraîné.
        Ne contient AUCUN secret : uniquement des réglages et des statistiques.
        """
        if self.moyenne is None or self.ecart is None:
            raise RuntimeError("Détecteur non entraîné")
        return {
            "date_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "type_modele": self.type_modele,
            "params_modele": self.params_modele,
            "features_par_service": self.features_par_service,
            "nb_photos_entrainement": self.nb_photos_entrainement,
            "version_sklearn": sklearn.__version__,
            "moyenne": {f: float(v) for f, v in self.moyenne.items()},
            "ecart_type": {f: float(v) for f, v in self.ecart.items()},
        }

    def save(self, chemin: str) -> None:
        """Sauvegarde le détecteur (.joblib) et, à côté, sa fiche lisible (.json)."""
        joblib.dump({"detecteur": self, "sklearn": sklearn.__version__}, chemin)
        Path(chemin).with_suffix(".json").write_text(
            json.dumps(self.fiche(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def load(chemin: str) -> AnomalyDetector:
        """Charge le détecteur et vérifie la version sklearn."""
        contenu = joblib.load(chemin)
        if contenu["sklearn"] != sklearn.__version__:
            warnings.warn(
                f"Modèle entraîné avec sklearn {contenu['sklearn']}, "
                f"chargé avec {sklearn.__version__} : aligner les versions !",
                stacklevel=2,
            )
        return contenu["detecteur"]


def gravite(service: str, intensite: float) -> str:
    """
    Gravité = criticité du service (EBIOS 1-4) x niveau d'intensité (1-3).

    Seuils provisoires : à calibrer avec les vraies données.
    """
    criticite = config.SERVICES[service]["criticite"]
    niveau = 1 if intensite < 6 else 2 if intensite < 12 else 3
    points = criticite * niveau  # 1 à 12
    if points >= 9:
        return "critique"
    if points >= 6:
        return "elevee"
    if points >= 3:
        return "moyenne"
    return "faible"


class IncidentTracker:
    """Filtre les alertes : alerte après N photos anormales d'affilée."""

    def __init__(self, detecteur: AnomalyDetector, n_consecutifs: int = 3):
        self.det = detecteur
        self.n = n_consecutifs
        self.en_incident = False
        self.compteur = 0
        self.tampon: deque[tuple[pd.DataFrame, str]] = deque(maxlen=n_consecutifs)
        self.debut: int | None = None
        self.service: str | None = None
        self.dernier_resultat = None

    def mettre_a_jour(self, ligne: pd.DataFrame) -> dict | None:
        """
        Reçoit UNE ligne. Retourne un événement (début/fin d'incident) ou None.
        """
        resultat = self.det.score(ligne).iloc[0]
        self.dernier_resultat = resultat
        ts = int(ligne.index[0])
        anomal = bool(resultat["anomalie"])
        self.tampon.append((ligne, resultat["service_suspect"]))

        # Compter les photos consécutives qui contredisent l'état actuel
        self.compteur = self.compteur + 1 if anomal != self.en_incident else 0
        if self.compteur < self.n:
            return None

        self.compteur = 0
        self.en_incident = anomal
        if anomal:
            # Service touché = celui désigné le plus souvent sur les N dernières photos
            suspects = pd.Series([svc for _, svc in self.tampon if svc])
            service = suspects.mode().iloc[0]
            explication = self.det.expliquer(pd.concat([ligne for ligne, _ in self.tampon]), service)
            self.debut, self.service = ts, explication["service"]
            return {
                "evenement": "incident_debut",
                "timestamp": ts,
                "score": round(float(resultat["score"]), 3),
                "gravite": gravite(explication["service"], explication["intensite"]),
                **explication,
            }
        return {
            "evenement": "incident_fin",
            "timestamp": ts,
            "service": self.service,
            "duree_s": ts - self.debut if self.debut is not None else 0,
        }
