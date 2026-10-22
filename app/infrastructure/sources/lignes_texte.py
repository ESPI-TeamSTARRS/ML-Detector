"""
LOGS EN TEXTE (non JSON) : transforme une ligne de journal de serveur web en log brut exploitable.

Pourquoi : les vrais logs de la plateforme dans Loki ne sont pas du JSON mais des lignes de texte
au format nginx / BunkerWeb. Exemple de ligne d'accès (une requête) :

  bwapi 192.0.2.4 - 0123456... - [20/Oct/2026:12:10:38 +0000] "GET /health HTTP/1.1" 200 43 "-" "bwapi"

et de ligne du journal d'erreurs du serveur :

  2026/10/20 12:10:38 [warn] 13481#13481: *16272 upstream timed out ...

On n'en extrait QUE des nombres et un niveau (code HTTP, durée, niveau) : jamais de texte libre.
L'heure et le service viennent de Loki (horodatage de réception et labels), pas de la ligne.
Cette fonction est PURE (pas de réseau) : elle se teste avec de simples chaînes.
"""

from __future__ import annotations

import re

# Ligne d'accès : [date] "METHODE chemin protocole" code octets   (nginx combined et variantes)
_ACCES = re.compile(r'\[[^\]]+\]\s+"(?P<methode>[A-Z]+)\s+\S+[^"]*"\s+(?P<code>\d{3})\s+(?:\d+|-)')
# Durée éventuelle, si INFRA ajoute $request_time au format de log : rt=0.123 / request_time=0.123
_DUREE_CLE = re.compile(r"\b(?:rt|request_time|urt)=(?P<s>\d+(?:\.\d+)?)")
# ... ou un nombre décimal collé à la fin de la ligne, après le user-agent entre guillemets
_DUREE_FIN = re.compile(r'"\s+(?P<s>\d+\.\d{1,6})\s*$')
# Journal d'erreurs nginx : 2026/10/20 12:10:38 [warn] ...
_ERREUR = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} \[(?P<niveau>[a-z]+)\]")

_NIVEAUX_ERREUR = {
    "debug": "DEBUG",
    "info": "INFO",
    "notice": "INFO",
    "warn": "WARN",
    "error": "ERROR",
    "crit": "CRITICAL",
    "alert": "CRITICAL",
    "emerg": "CRITICAL",
}


def _niveau_http(code: int) -> str:
    """Niveau déduit du code HTTP (même règle que la simulation)."""
    return "ERROR" if code >= 500 else "WARN" if code >= 400 else "INFO"


def parser_ligne_texte(ligne: object) -> dict | None:
    """
    Renvoie un log brut partiel {level, status_code[, duration_ms]} ou None si la ligne
    n'est ni une ligne d'accès ni une ligne du journal d'erreurs.

    Ligne d'accès         -> status_code + level (+ duration_ms si présent)
    Journal d'erreurs     -> level seulement (pas de status_code : ce n'est pas une requête)
    """
    if not isinstance(ligne, str):
        return None

    acces = _ACCES.search(ligne)
    if acces:
        code = int(acces.group("code"))
        log = {"status_code": code, "level": _niveau_http(code)}
        duree = _DUREE_CLE.search(ligne) or _DUREE_FIN.search(ligne)
        if duree:
            log["duration_ms"] = round(float(duree.group("s")) * 1000, 3)  # secondes -> millisecondes
        return log

    erreur = _ERREUR.match(ligne)
    if erreur:
        return {"level": _NIVEAUX_ERREUR.get(erreur.group("niveau"), "INFO")}

    return None
