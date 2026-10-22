"""
SCHÉMA : normalise les logs bruts en DataFrame structuré.

Toutes les sources (Loki, push, fichier, simulation) convergeront ici.
Les logs entrent sous différents formats, on les normalise :
  - champs obligatoires : timestamp, service
  - champs optionnels : level, status_code, duration_ms, outcome
  - applique la correspondance des noms (config.CORRESPONDANCE_CHAMPS)
  - rejette les logs sans champ obligatoire ou service non surveillé
  - accepte timestamp en ISO 8601 UTC ou epoch (s/ms/ns)

Résultat : DataFrame [timestamp(s), service, level, status_code, duration_ms, outcome]
"""

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import pandas as pd

import config

COLONNES = ["timestamp", "service", "level", "status_code", "duration_ms", "outcome"]

# Noms usuels cherchés si CORRESPONDANCE_CHAMPS ne dit rien (ou si le champ relié manque).
NOMS_USUELS = {
    "timestamp": ["timestamp", "ts", "time", "t"],
    "service": ["service", "srv", "app", "application"],
    "level": ["level", "severity", "sev", "lvl"],
    "status_code": ["status_code", "status", "code", "http_status"],
    "duration_ms": ["duration_ms", "duration", "latency", "latency_ms"],
    # Résultat d'un événement applicatif (format des services Basecamp) : success / denied / ...
    "outcome": ["outcome", "result"],
}


def _vide() -> pd.DataFrame:
    # DataFrame vide MAIS avec les bonnes colonnes : le reste du code n'a pas de cas spécial.
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype=float),
            "service": pd.Series(dtype=object),
            "level": pd.Series(dtype=object),
            "status_code": pd.Series(dtype=float),
            "duration_ms": pd.Series(dtype=float),
            "outcome": pd.Series(dtype=object),
        }
    )


def _normaliser_timestamp(valeur) -> float | None:
    """
    Convertit un timestamp en secondes (float), ou None s'il est inutilisable.
    Accepte :
      - ISO 8601 : '2026-10-20T09:15:20.123Z' ou '2026-10-20T09:15:20+00:00'
        (sans fuseau, on suppose UTC : sinon Python utiliserait l'heure locale du PC !)
      - epoch secondes (1.79e9), millisecondes (1.79e12), nanosecondes (1.79e18),
        en nombre ou en texte (cas d'un CSV)
    """
    if valeur is None or isinstance(valeur, bool):
        return None

    if isinstance(valeur, str):
        texte = valeur.strip()
        try:
            valeur = float(texte)  # "1792404920" lu dans un CSV
        except ValueError:
            try:
                dt = datetime.fromisoformat(texte.replace("Z", "+00:00"))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.timestamp()

    try:
        valeur = float(valeur)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(valeur):  # NaN / inf (cellule vide d'un CSV)
        return None

    # Unité devinée par l'ordre de grandeur (pour les dates actuelles) :
    #   secondes ~ 1.8e9 ; millisecondes ~ 1.8e12 ; nanosecondes ~ 1.8e18
    if valeur > 1e17:
        return valeur / 1e9
    if valeur > 1e11:
        return valeur / 1e3
    return valeur


def _lire_champ(log_brut: dict, nom: str, correspondance: dict):
    """
    Cherche le champ `nom` dans un log brut. Retourne (trouve, valeur).

    Ordre : 1) le nom réel indiqué dans CORRESPONDANCE_CHAMPS, 2) à défaut les noms usuels.
    Choix volontaire du repli (2) : si la config relie "timestamp" à "ts" mais qu'un log
    arrive avec "timestamp", mieux vaut le garder que le jeter silencieusement
    (jeter = fenêtre faussement calme = anomalie ratée).
    """
    candidats = []
    if nom in correspondance:
        candidats.append(correspondance[nom])
    candidats += NOMS_USUELS[nom]
    for clef in candidats:
        if clef in log_brut:
            return True, log_brut[clef]
    return False, None


def normaliser(logs: Iterable[Any] | None) -> pd.DataFrame:
    """
    Normalise une liste de logs bruts en DataFrame [timestamp, service, level, status_code, duration_ms].

    Rejette (sans erreur) les logs sans timestamp valide, sans service, ou d'un service
    non surveillé : un log illisible ne doit pas faire tomber la boucle de détection.
    """
    correspondance = getattr(config, "CORRESPONDANCE_CHAMPS", {}) or {}
    lignes = []
    for log_brut in logs or []:
        if not isinstance(log_brut, dict):
            continue
        trouve_ts, brut_ts = _lire_champ(log_brut, "timestamp", correspondance)
        trouve_svc, service = _lire_champ(log_brut, "service", correspondance)
        if not (trouve_ts and trouve_svc):
            continue
        if not isinstance(service, str) or service not in config.SERVICES:
            continue
        ts = _normaliser_timestamp(brut_ts)
        if ts is None:
            continue

        _, level = _lire_champ(log_brut, "level", correspondance)
        _, status = _lire_champ(log_brut, "status_code", correspondance)
        _, duree = _lire_champ(log_brut, "duration_ms", correspondance)
        _, resultat = _lire_champ(log_brut, "outcome", correspondance)
        lignes.append(
            {
                "timestamp": ts,
                "service": service,
                "level": level.strip().upper() if isinstance(level, str) else "",
                "status_code": status,
                "duration_ms": duree,
                # minuscules : "Denied" et "denied" doivent compter pareil ; "" = champ absent
                "outcome": resultat.strip().lower() if isinstance(resultat, str) else "",
            }
        )

    if not lignes:
        return _vide()

    df = pd.DataFrame(lignes, columns=COLONNES)
    # str / None / NaN (CSV) -> nombre ou NaN ; jamais d'exception
    df["status_code"] = pd.to_numeric(df["status_code"], errors="coerce")
    df["duration_ms"] = pd.to_numeric(df["duration_ms"], errors="coerce")
    return df
