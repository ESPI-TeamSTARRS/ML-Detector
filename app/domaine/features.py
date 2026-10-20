"""
REGISTRE DE FEATURES : calcule les features à partir de logs normalisés.

Une feature = une métrique sur une fenêtre [t, t+pas). Par exemple :
  - req_count : nombre de requêtes
  - error_4xx : nombre d'erreurs 4xx
  - latency_avg_ms : latence moyenne

Chaque feature a une règle pour combler les trous (si aucun log dans la fenêtre) :
  - trou=0 : pour un compteur (aucun log = aucune requête)
  - trou="precedent" : pour une latence (on garde la dernière valeur connue)

Les features calculées ici sont EXACTEMENT les mêmes à l'entraînement et en direct,
peu importe la source (Loki, push, fichier, simulation).
"""
from typing import Callable

import numpy as np
import pandas as pd


# Registre global des features (nom -> fonction)
_REGISTRE: dict[str, dict] = {}


def feature(nom: str, trou: int | str):
    """Décorateur pour enregistrer une fonction comme feature."""
    def decorateur(func: Callable) -> Callable:
        _REGISTRE[nom] = {"fonction": func, "trou": trou}
        return func
    return decorateur


# --- FEATURES ---
# Chaque fonction reçoit un DataFrame normalisé et retourne une Series (index=timestamps).

@feature("req_count", trou=0)
def req_count(logs: pd.DataFrame) -> pd.Series:
    """
    Nombre de requêtes HTTP par fenêtre.

    On ne compte que les lignes qui ont un code HTTP : une ligne du journal d'erreurs du serveur
    web (ex. « [warn] upstream timed out ») n'est pas une requête, la compter fausserait le volume.
    """
    return logs[logs["status_code"].notna()].groupby("timestamp").size()


@feature("error_4xx", trou=0)
def error_4xx(logs: pd.DataFrame) -> pd.Series:
    """Nombre d'erreurs 4xx par fenêtre."""
    erreurs = logs[(logs["status_code"] >= 400) & (logs["status_code"] < 500)]
    return erreurs.groupby("timestamp").size()


@feature("error_5xx", trou=0)
def error_5xx(logs: pd.DataFrame) -> pd.Series:
    """Nombre d'erreurs 5xx par fenêtre."""
    erreurs = logs[logs["status_code"] >= 500]
    return erreurs.groupby("timestamp").size()


@feature("error_logs", trou=0)
def error_logs(logs: pd.DataFrame) -> pd.Series:
    """Nombre de logs au niveau ERROR ou plus grave."""
    niveaux_graves = ["ERROR", "CRITICAL", "FATAL"]
    graves = logs[logs["level"].isin(niveaux_graves)]
    return graves.groupby("timestamp").size()


@feature("latency_avg_ms", trou="precedent")
def latency_avg_ms(logs: pd.DataFrame) -> pd.Series:
    """Latence moyenne en millisecondes par fenêtre."""
    logs_avec_latence = logs[logs["duration_ms"].notna()]
    if logs_avec_latence.empty:
        return pd.Series(dtype=float)
    return logs_avec_latence.groupby("timestamp")["duration_ms"].mean()


@feature("latency_p95_ms", trou="precedent")
def latency_p95_ms(logs: pd.DataFrame) -> pd.Series:
    """Latence au 95e percentile en millisecondes par fenêtre."""
    logs_avec_latence = logs[logs["duration_ms"].notna()]
    if logs_avec_latence.empty:
        return pd.Series(dtype=float)
    return logs_avec_latence.groupby("timestamp")["duration_ms"].quantile(0.95)


def construire_tableau(logs: pd.DataFrame, debut: float, fin: float, pas: int = 10) -> pd.DataFrame:
    """
    À partir de logs normalisés, construit le DataFrame des features.

    Fenêtres [t, t+pas) : un log est rangé dans la fenêtre floor(timestamp / pas) * pas,
    donc un log à t+pas pile va dans la fenêtre SUIVANTE.
    La grille couvre [debut, fin) alignés sur le pas : la fenêtre qui commence à `fin`
    n'est pas incluse (elle est encore en train de se remplir, donc incomplète).

    Résultat : index = début de fenêtre (secondes), colonnes = <service>__<feature>.
    """
    import config

    debut_aligne = int(debut // pas * pas)
    fin_alignee = int(fin // pas * pas)
    grille = pd.Index(np.arange(debut_aligne, fin_alignee, pas), name="timestamp")

    # Fail fast : une feature de la config inconnue du registre = faute de frappe
    # qui produirait un modèle sans cette colonne, sans jamais le dire.
    inconnues = sorted({m for cfg in config.SERVICES.values() for m in cfg.get("features", [])
                        if m not in _REGISTRE})
    if inconnues:
        raise ValueError(f"Features inconnues dans config.SERVICES : {inconnues}. "
                         f"Disponibles : {sorted(_REGISTRE)}")

    df = pd.DataFrame(index=grille)
    if len(grille) == 0:
        return df

    # Rangement de chaque log dans sa fenêtre (on remplace le timestamp par le début de fenêtre)
    if not logs.empty:
        logs = logs.copy()
        logs["timestamp"] = (logs["timestamp"] // pas * pas).astype("int64")

    for service in sorted(config.SERVICES):
        logs_service = logs[logs["service"] == service] if not logs.empty else logs
        for mesure in config.SERVICES[service].get("features", []):
            trou = _REGISTRE[mesure]["trou"]
            if logs_service.empty:
                serie = pd.Series(dtype=float)
            else:
                serie = _REGISTRE[mesure]["fonction"](logs_service)
            serie = serie.reindex(grille)  # fenêtres sans log -> NaN

            if trou == "precedent":
                # Latence : sans requête on ne sait pas, on garde la dernière valeur connue
                serie = serie.ffill().fillna(0.0)
            else:
                serie = serie.fillna(float(trou))  # compteur : aucun log = 0
            df[f"{service}__{mesure}"] = serie.astype(float)

    return df
