"""
ALERTER : envoie les événements au moteur de remédiation (HTTP POST + jeton).

Avec retry automatique (3 essais) et backoff exponentiel.
"""

import logging
import time

import requests

log = logging.getLogger("ml-detector")


class NotificateurRemediation:
    """Envoie les alertes au moteur de remédiation."""

    def __init__(self, url: str, jeton: str, tentatives: int = 3, timeout: float = 2.0):
        self.url = url
        self.jeton = jeton
        self.tentatives = tentatives
        self.timeout = timeout
        if url and not jeton:
            log.warning("REMEDIATION_URL défini sans jeton : la remédiation devrait refuser nos alertes")

    def envoyer(self, evenement: dict) -> bool:
        """Envoie un événement. Retourne True si succès."""
        if not self.url:
            log.info("Pas de REMEDIATION_URL : événement journalisé uniquement")
            return False

        entetes = {"Authorization": f"Bearer {self.jeton}"}
        for essai in range(1, self.tentatives + 1):
            try:
                r = requests.post(self.url, json=evenement, headers=entetes, timeout=self.timeout)
                if r.status_code < 300:
                    return True
                log.warning("Remédiation a répondu %s (essai %d/%d)", r.status_code, essai, self.tentatives)
            except requests.RequestException as e:
                log.warning("Remédiation injoignable (essai %d/%d) : %s", essai, self.tentatives, e)
            if essai < self.tentatives:
                time.sleep(0.5 * 2 ** (essai - 1))
        return False
