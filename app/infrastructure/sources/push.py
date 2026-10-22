"""
PUSH : reçoit les logs via POST /ingest (route dans presentation/api/routes/ingest.py).

Tampon mémoire thread-safe : la route (thread FastAPI) écrit, la boucle de détection
(autre thread) lit.
"""

import threading
from collections import deque
from collections.abc import Iterable

from app.infrastructure.sources.base import Source, extraire_timestamp


class PushSource(Source):
    """
    Mode push : les autres services envoient leurs logs.

    Deux protections de la mémoire :
      - rétention : un log plus vieux que `retention_s` (par rapport à la fenêtre lue) est oublié ;
      - plafond   : au plus `max_logs` logs gardés, les plus anciens sortent en premier
        (sinon un flood sur /ingest saturerait la RAM du conteneur).
    """

    def __init__(self, retention_s: int = 600, max_logs: int = 200_000):
        self.retention_s = retention_s
        # deque(maxlen=...) jette tout seul l'élément le plus ancien quand elle est pleine.
        self.tampon: deque = deque(maxlen=max_logs)  # éléments : (timestamp_s, log)
        self.verrou = threading.Lock()

    def ajouter(self, logs: Iterable[object]) -> None:
        """Ajoute des logs (appelé par /ingest). Les logs sans timestamp exploitable sont ignorés."""
        # On calcule le timestamp UNE fois ici, hors verrou, plutôt qu'à chaque lecture.
        a_garder = []
        for log in logs:
            if not isinstance(log, dict):
                continue
            ts = extraire_timestamp(log)
            if ts is not None:
                a_garder.append((ts, log))
        with self.verrou:
            self.tampon.extend(a_garder)

    def lire_logs(self, debut: float, fin: float) -> list[dict]:
        """Logs du tampon dans [debut, fin). Oublie au passage ceux plus vieux que la rétention."""
        limite = fin - self.retention_s
        with self.verrou:
            # On ne supprime QUE les trop vieux : les logs futurs (ts >= fin) restent pour
            # la prochaine lecture (le détecteur lit avec du retard, les logs arrivent en avance).
            self.tampon = deque((e for e in self.tampon if e[0] >= limite), maxlen=self.tampon.maxlen)
            return [log for ts, log in self.tampon if debut <= ts < fin]
