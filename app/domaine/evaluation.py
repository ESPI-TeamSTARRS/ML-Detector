"""
ÉVALUATION : compare les trois modèles sur des données simulées avec incidents.

Métriques :
  - Taux de détection : % de photos pendant l'incident qui sont détectées comme anormales
  - Délai de détection : combien de secondes après le début de l'incident
  - Bon service : % de fois où le service correct est désigné
  - Fausses alertes : % de photos normales jugées anormales
"""
import pandas as pd
from app.domaine.detecteur import AnomalyDetector, IncidentTracker


def etiqueter(df: pd.DataFrame, incidents: list[dict] = None) -> pd.DataFrame:
    """
    Ajoute une colonne _verite (le service réellement touché, ou '').

    Utilisé pour évaluer sur des données simulées.
    """
    if "_verite" in df.columns:
        return df  # Déjà étiqueté
    if not incidents:
        df["_verite"] = ""
        return df

    df = df.copy()
    df["_verite"] = ""
    for inc in incidents:
        service = inc["service"]
        debut = inc["debut_s"]
        duree = inc["duree_s"]
        masque = (df.index >= debut) & (df.index < debut + duree)
        df.loc[masque, "_verite"] = service
    return df


def evaluer(df: pd.DataFrame, detecteur: AnomalyDetector, n_consecutifs: int = 3) -> dict:
    """
    Évalue un modèle sur un CSV avec colonne _verite.

    Retourne un dico avec les métriques.
    """
    if "_verite" not in df.columns or not (df["_verite"] != "").any():
        return {"type": "aucun_incident", "resultat": "pas d'incident de test"}

    tracker = IncidentTracker(detecteur, n_consecutifs=n_consecutifs)
    evenements = []
    for i in range(len(df)):
        ev = tracker.mettre_a_jour(df.iloc[[i]])
        if ev:
            evenements.append(ev)

    # Identifier l'incident réel
    verite = df["_verite"].fillna("")
    debuts_reels = verite[verite != ""].index
    if len(debuts_reels) == 0:
        return {"type": "aucun_incident", "resultat": "pas d'incident de test"}

    debut_reel = int(debuts_reels[0])
    service_reel = verite[debuts_reels[0]]
    fin_reelle = int(verite[verite != ""].index[-1]) + 1

    # Incidents détectés
    debuts_detectes = [e for e in evenements if e["evenement"] == "incident_debut"]

    # Métriques
    if not debuts_detectes:
        return {
            "type": "non_detecte",
            "service_reel": service_reel,
            "debut_reel": debut_reel,
            "fin_reelle": fin_reelle,
            "resultat": "incident non détecté",
        }

    e = debuts_detectes[0]
    delai = e["timestamp"] - debut_reel
    bon_service = e["service"] == service_reel
    taux_fausses_alertes = len([ev for ev in debuts_detectes if ev["timestamp"] < debut_reel])

    return {
        "type": "detecte",
        "service_reel": service_reel,
        "debut_reel": debut_reel,
        "fin_reelle": fin_reelle,
        "service_detecte": e["service"],
        "bon_service": bon_service,
        "delai_s": delai,
        "gravite": e["gravite"],
        "nb_fausses_alertes": taux_fausses_alertes,
    }


def comparer(df: pd.DataFrame, modeles: dict[str, AnomalyDetector], n_consecutifs: int = 3) -> pd.DataFrame:
    """
    Compare plusieurs modèles sur les mêmes données.

    Retourne un tableau récapitulatif.
    """
    resultats = []
    for nom_modele, detecteur in modeles.items():
        eval_res = evaluer(df, detecteur, n_consecutifs)
        eval_res["modele"] = nom_modele
        resultats.append(eval_res)

    return pd.DataFrame(resultats)
