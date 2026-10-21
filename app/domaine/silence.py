"""
RÈGLE SILENCE : filet de sécurité autour du modèle pour les pannes (chute à 0 requête).

Pourquoi une règle et pas le modèle ? Un compteur qui vaut 9,5 requêtes en moyenne (écart-type 6) et tombe à 0
reste à moins de 2 écarts-types : le modèle ne voit rien d'anormal, et un entraînement sur du trafic normal ne
contient jamais de panne. Mais un service habituellement actif ne tombe jamais à 0 requête sans cause grave
(chez nous le front l'interroge même au repos, donc 0 = service ou collecte arrêtés).

La règle : un service est SILENCIEUX quand son req_count est à 0 alors que son trafic habituel (moyenne apprise à
l'entraînement) vaut au moins `trafic_min` requêtes par photo. Un service naturellement calme n'est jamais signalé.

DetecteurAvecSilence enveloppe un AnomalyDetector sans le modifier : mêmes colonnes en sortie, donc l'IncidentTracker,
le moteur et le contrat d'alerte restent identiques (la règle de N photos d'affilée s'applique aussi au silence).
Interrupteur : config.SILENCE_ACTIF.
"""
import pandas as pd

import config
from app.domaine.detecteur import AnomalyDetector

SCORE_SILENCE = -1.0  # score conventionnel d'une photo silencieuse (< 0 = anormal)


class RegleSilence:
    """Décide, photo par photo, quels services sont silencieux."""

    def __init__(self, trafic_min: float = 3.0, intensite: float = 12.0):
        if trafic_min <= 0:
            raise ValueError("trafic_min doit être strictement positif")
        if intensite <= 0:
            raise ValueError("intensite doit être strictement positive")
        self.trafic_min = trafic_min
        self.intensite = intensite

    def services_silencieux(self, photos: pd.DataFrame, detecteur: AnomalyDetector) -> pd.DataFrame:
        """Booléens (une colonne par service concerné) : True = muet alors qu'il parle d'habitude."""
        silencieux = {}
        for service in detecteur.features_par_service:
            colonne = f"{service}__req_count"
            if colonne not in detecteur.moyenne.index or colonne not in photos.columns:
                continue  # service sans req_count appris (constante ou absente) : jamais signalé
            parle_d_habitude = bool(detecteur.moyenne[colonne] >= self.trafic_min)
            silencieux[service] = (photos[colonne] == 0) & parle_d_habitude
        return pd.DataFrame(silencieux, index=photos.index)


class DetecteurAvecSilence:
    """Même interface qu'AnomalyDetector (score, expliquer, fiche, features...), plus la règle silence."""

    def __init__(self, detecteur: AnomalyDetector, regle: RegleSilence):
        self._detecteur = detecteur
        self._regle = regle

    def __getattr__(self, nom: str):
        # Appelé seulement pour les attributs absents d'ici : tout le reste vient du détecteur enveloppé.
        if nom.startswith("_"):
            raise AttributeError(nom)
        return getattr(self._detecteur, nom)

    def score(self, df: pd.DataFrame) -> pd.DataFrame:
        resultat = self._detecteur.score(df).copy()
        silencieux = self._regle.services_silencieux(df, self._detecteur)
        # Du moins critique au plus critique : en cas de silence multiple, le plus critique est désigné en dernier.
        for service in sorted(silencieux.columns, key=self._criticite):
            muet = silencieux[service]
            if not muet.any():
                continue
            resultat.loc[muet, "score"] = resultat.loc[muet, "score"].clip(upper=SCORE_SILENCE)
            colonne = f"score_{service}"
            if colonne in resultat.columns:
                resultat.loc[muet, colonne] = resultat.loc[muet, colonne].clip(upper=SCORE_SILENCE)
            resultat.loc[muet, "anomalie"] = True
            resultat.loc[muet, "service_suspect"] = service
        return resultat

    def expliquer(self, lignes: pd.DataFrame, service: str) -> dict:
        """Comme le détecteur, mais un silence est une panne totale : intensité au maximum (gravité haute)."""
        explication = self._detecteur.expliquer(lignes, service)
        if self._est_muet(lignes, service):
            explication["intensite"] = max(explication["intensite"], self._regle.intensite)
        return explication

    def _est_muet(self, lignes: pd.DataFrame, service: str) -> bool:
        silencieux = self._regle.services_silencieux(lignes, self._detecteur)
        return service in silencieux.columns and silencieux[service].sum() * 2 > len(lignes)

    @staticmethod
    def _criticite(service: str) -> int:
        return config.SERVICES.get(service, {}).get("criticite", 0)


def avec_silence(detecteur: AnomalyDetector):
    """Enveloppe le détecteur avec la règle silence, sauf si config.SILENCE_ACTIF est désactivé."""
    if not config.SILENCE_ACTIF:
        return detecteur
    return DetecteurAvecSilence(detecteur, RegleSilence(config.SILENCE_TRAFIC_MIN, config.SILENCE_INTENSITE))
