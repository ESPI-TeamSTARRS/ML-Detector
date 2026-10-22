"""
ENTRAINER : charge un CSV de trafic NORMAL et entraîne le détecteur.

Utilisé par app/presentation/cli/train.py (et evaluate.py pour charger les CSV).
"""

from pathlib import Path

import pandas as pd

import config
from app.domaine.detecteur import AnomalyDetector

MIN_PHOTOS = 200  # ~35 min de trafic à 10 s par photo : en dessous, le modèle apprend mal

# Paramètres que chaque type de modèle ACCEPTE. On ne passe à un modèle que ce qu'il connaît :
# "statistique" n'a pas de "contamination", donc la lui donner provoquerait une erreur.
PARAMETRES_ACCEPTES = {
    "isolation_forest": {"contamination", "n_estimators", "random_state"},
    "lof": {"contamination", "n_neighbors"},
    "statistique": {"seuil_ecarts_types"},
}


class ErreurDonnees(Exception):
    """Erreur ATTENDUE (fichier absent, CSV vide...) : les CLI l'affichent sans traceback."""


def charger_csv(chemin: str) -> pd.DataFrame:
    """
    Charge un CSV de photos (index = timestamp).

    Les colonnes qui commencent par '_' (ex. _verite) sont gardées ici : c'est à l'appelant
    de décider. Pour l'entraînement on les retire (voir charger_pour_entrainement).
    """
    if not Path(chemin).is_file():
        raise ErreurDonnees(f"Fichier introuvable : {chemin}")
    try:
        df = pd.read_csv(chemin, index_col="timestamp")
    except ValueError as err:
        raise ErreurDonnees(
            f"{chemin} n'a pas de colonne 'timestamp' : est-ce bien un CSV produit par collect ?"
        ) from err
    except pd.errors.EmptyDataError as err:
        raise ErreurDonnees(f"{chemin} est vide.") from err
    if df.empty:
        raise ErreurDonnees(f"{chemin} ne contient aucune photo.")
    return df


def charger_pour_entrainement(chemin: str) -> pd.DataFrame:
    """CSV -> uniquement les colonnes de mesures. On retire '_verite' : le modèle ne doit JAMAIS la voir."""
    df = charger_csv(chemin)
    return df[[c for c in df.columns if not c.startswith("_")]]


def parametres_modele(type_modele: str, contamination: float | None = None) -> dict:
    """
    Paramètres à donner au modèle : ceux de config.MODELE["params"], filtrés selon le type,
    avec éventuellement une contamination choisie en ligne de commande.

    Attention : N_CONSECUTIFS (photos d'affilée avant alerte) n'est PAS un paramètre du modèle,
    il concerne l'IncidentTracker. On ne le mélange donc pas ici.
    """
    if type_modele not in PARAMETRES_ACCEPTES:
        raise ErreurDonnees(f"Modèle inconnu : {type_modele} (choix : {', '.join(PARAMETRES_ACCEPTES)})")
    params = dict(config.MODELE["params"])
    if contamination is not None:
        params["contamination"] = contamination
    return {k: v for k, v in params.items() if k in PARAMETRES_ACCEPTES[type_modele]}


def entrainer_et_sauvegarder(
    chemin_donnees: str, chemin_modele: str, type_modele: str | None = None, contamination: float | None = None
) -> AnomalyDetector:
    """Entraîne le détecteur sur un CSV de trafic normal et le sauvegarde en .joblib."""
    type_modele = type_modele or config.MODELE["type"]
    params = parametres_modele(type_modele, contamination)

    df = charger_pour_entrainement(chemin_donnees)
    if len(df) < MIN_PHOTOS:
        print(f"[attention] Seulement {len(df)} photos (visez >= {MIN_PHOTOS}, soit ~35 min de trafic)")

    det = AnomalyDetector(type_modele=type_modele, **params).fit(df)

    # Contrôle : sur ses propres données NORMALES, le modèle devrait signaler très peu de photos.
    taux = float(det.score(df)["anomalie"].mean())
    print(f"Modèle : {type_modele} {params}")
    print(f"Entraîné sur {len(df)} photos, {len(det.features)} mesures, {len(det.modeles)} modèles (un par service)")
    print(f"Photos normales jugées anormales : {taux:.1%}")
    print(f"Rappel : une alerte n'est envoyée qu'après {config.N_CONSECUTIFS} photos anormales d'affilée")

    Path(chemin_modele).parent.mkdir(parents=True, exist_ok=True)
    det.save(chemin_modele)
    print(f"Modèle sauvegardé -> {chemin_modele}")
    return det
