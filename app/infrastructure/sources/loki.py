"""
LOKI : lit les logs BRUTS depuis Loki via /loki/api/v1/query_range (lecture seule).

Points clés :
  - on lit les logs, pas des métriques : les features sont calculées ailleurs, en Python
    (app/domaine/features.py), pour que l'entraînement et le direct voient les mêmes chiffres ;
  - pagination : Loki renvoie au plus `limit` lignes par requête, on repart donc du
    dernier horodatage + 1 ns tant qu'une page est pleine ;
  - le service vient du label Loki (config.LOKI_LABEL_SERVICE) quand le JSON ne le contient pas ;
  - une ligne qui n'est pas du JSON est lue comme une ligne de serveur web (nginx / BunkerWeb)
    par lignes_texte.py ; les lignes contenant un motif de config.LOKI_EXCLURE (bruit) sont écartées.
"""
import json
import logging
from typing import Optional

import requests

import config
from app.infrastructure.sources.base import Source, extraire_timestamp
from app.infrastructure.sources.lignes_texte import parser_ligne_texte

journal = logging.getLogger(__name__)

PAGE_TAILLE = 5000   # limite par requête (plafond usuel de Loki)
MAX_PAGES = 50       # garde-fou : jamais de boucle infinie, max 250 000 logs par lecture


class LokiSource(Source):
    """Lit les logs JSON des conteneurs dans Loki."""

    def __init__(self, loki_url: Optional[str] = None, pas: int = 10, timeout: float = 15,
                 selecteur: Optional[str] = None):
        self.loki_url = (loki_url or config.LOKI_URL).rstrip("/")
        self.pas = pas
        self.timeout = timeout  # secondes, par requête
        self.selecteur = selecteur or config.LOKI_SELECTEUR
        self.exclure = tuple(config.LOKI_EXCLURE)   # ex. ("bwapi",) : vérifications de santé du WAF

    def _requete_page(self, debut_ns: int, fin_ns: int) -> list:
        """Une requête HTTP vers Loki. Renvoie la liste 'result' (flux). Lève RuntimeError si échec."""
        url = f"{self.loki_url}/loki/api/v1/query_range"
        params = {
            # Le filtre de bruit est appliqué par Loki lui-même (moins de données transférées) :
            # {sélecteur} != "motif1" != "motif2"
            "query": self.selecteur + "".join(f' != "{m}"' for m in self.exclure),
            "start": int(debut_ns),   # entiers en nanosecondes (pas de float : perte de précision)
            "end": int(fin_ns),
            "direction": "forward",   # du plus ancien au plus récent : nécessaire pour paginer
            "limit": PAGE_TAILLE,
        }
        try:
            reponse = requests.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            # Le moteur compte ces erreurs et continue : un message clair suffit.
            raise RuntimeError(f"Loki injoignable ({type(e).__name__}) : {e}") from e
        if reponse.status_code != 200:
            raise RuntimeError(f"Loki a répondu HTTP {reponse.status_code} : {reponse.text[:200]}")
        try:
            donnees = reponse.json()
            data = donnees["data"]
            resultat = data["result"]
        except (ValueError, KeyError, TypeError) as e:
            raise RuntimeError("Réponse de Loki illisible (JSON inattendu)") from e
        if data.get("resultType", "streams") != "streams":
            # "matrix"/"vector" = requête de métrique, pas de logs : le sélecteur est mal formé.
            raise RuntimeError(f"Loki a renvoyé un résultat '{data.get('resultType')}' au lieu de 'streams'")
        return resultat

    @staticmethod
    def _lire_ligne(ligne) -> dict | None:
        """JSON si possible (logs applicatifs), sinon ligne de serveur web (nginx / BunkerWeb)."""
        try:
            log = json.loads(ligne)
        except (json.JSONDecodeError, TypeError):
            return parser_ligne_texte(ligne)
        return log if isinstance(log, dict) else None

    def lire_logs(self, debut: float, fin: float) -> list[dict]:
        """Logs bruts de la plage [debut, fin) (secondes), dans l'ordre chronologique."""
        debut_ns = int(round(debut * 1e9))
        fin_ns = int(round(fin * 1e9))
        logs: list[dict] = []

        for _ in range(MAX_PAGES):
            if debut_ns >= fin_ns:
                break
            flux = self._requete_page(debut_ns, fin_ns)
            nb_lignes = 0
            dernier_ns = debut_ns
            for element in flux:
                labels = element.get("stream", {}) or {}   # "stream" = labels (et non "metric")
                service = labels.get(config.LOKI_LABEL_SERVICE, "")
                for ts_ns, ligne in element.get("values", []):
                    nb_lignes += 1
                    ts_ns = int(ts_ns)
                    dernier_ns = max(dernier_ns, ts_ns)
                    if any(motif in ligne for motif in self.exclure):
                        continue            # deuxième filet de sécurité si Loki n'a pas filtré
                    log = self._lire_ligne(ligne)
                    if log is None:
                        continue            # ligne inexploitable (démarrage, stacktrace...) : ignorée
                    if service and "service" not in log:
                        log["service"] = service      # on n'écrase jamais le JSON
                    if extraire_timestamp(log) is None:
                        log["timestamp"] = ts_ns / 1e9   # sinon, l'heure de réception par Loki
                    logs.append(log)
            if nb_lignes < PAGE_TAILLE:
                break                        # page incomplète = plus rien à lire
            debut_ns = dernier_ns + 1        # page pleine : on repart juste après la dernière ligne
        else:
            journal.warning("Loki : %d pages lues, lecture tronquée (trop de logs sur la fenêtre)", MAX_PAGES)

        return logs
