"""
DOCKER_LOGS : transforme une ligne de `docker compose logs` en log brut (dict).

Format d'une ligne (avec --timestamps) :
    api-1  | 2026-10-20T09:15:20.123456789Z {"level":"INFO","status_code":200,...}
Sans --timestamps :
    api-1  | {"timestamp":"...","level":"INFO",...}

Fonction PURE (pas de réseau, pas de fichier) : facile à tester, et utilisée par
deux endroits qui doivent voir exactement la même chose :
  - FichierSource (fichiers .log/.txt pour l'entraînement hors ligne)
  - outils/relais_docker.py (flux en direct sur la machine hôte)
"""

import json
import re
from collections.abc import Iterable

# "<préfixe>  | <reste>" : le préfixe n'a pas d'espace, le séparateur est " | " (espaces de
# remplissage variables : docker aligne les noms de conteneurs).
_RE_LIGNE = re.compile(r"^(?P<prefixe>\S+)\s+\|\s?(?P<reste>.*)$")
# Horodatage ISO 8601 que Docker met devant le message avec --timestamps.
_RE_HORODATAGE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\S+)\s+(?P<message>.*)$")
# Suffixe d'index de réplique : "-1" (Compose v2) ou "_1" (Compose v1).
_RE_INDEX = re.compile(r"[-_]\d+$")


def deduire_service(prefixe: str, services_connus: Iterable[str]) -> str:
    """
    Déduit le nom du service depuis le préfixe du conteneur.

    "api-1" -> "api" ; "projet-api-1" -> "api" (si "api" est un service connu).
    On cherche d'abord parmi les services connus (le plus long d'abord, pour que
    "oauth" ne soit pas pris pour "auth"), sinon on renvoie le préfixe nettoyé.
    """
    nettoye = _RE_INDEX.sub("", prefixe)
    connus = sorted(set(services_connus), key=len, reverse=True)
    for nom in connus:
        if nettoye == nom:
            return nom
    for nom in connus:
        # "projet-api" / "projet_api" : le service est ce qui suit le préfixe de projet
        if nettoye.endswith("-" + nom) or nettoye.endswith("_" + nom):
            return nom
    return nettoye


def parser_ligne_docker(ligne: str, services_connus: Iterable[str]) -> dict | None:
    """
    Renvoie le log brut (dict) contenu dans la ligne, ou None si elle n'est pas
    exploitable (ligne vide, message non-JSON, JSON qui n'est pas un objet...).

    On n'invente rien : on ne garde que le JSON de l'application, auquel on ajoute
    "service" (déduit du préfixe, sauf s'il est déjà dans le JSON) et "timestamp"
    (pris de Docker, sauf s'il est déjà dans le JSON).
    """
    if not ligne:
        return None
    ligne = ligne.strip().lstrip("﻿")  # BOM éventuel + fin de ligne Windows
    if not ligne:
        return None

    prefixe = None
    reste = ligne
    m = _RE_LIGNE.match(ligne)
    if m:
        prefixe, reste = m.group("prefixe"), m.group("reste")

    ts_docker = None
    m = _RE_HORODATAGE.match(reste)
    if m:
        ts_docker, reste = m.group("ts"), m.group("message")

    reste = reste.strip()
    if not reste.startswith("{"):
        return None  # pas du JSON (message de démarrage, stacktrace...) : on ignore
    try:
        log = json.loads(reste)
    except json.JSONDecodeError:
        return None
    if not isinstance(log, dict):
        return None

    if prefixe and "service" not in log:
        log["service"] = deduire_service(prefixe, services_connus)
    if ts_docker and "timestamp" not in log:
        log["timestamp"] = ts_docker
    return log
