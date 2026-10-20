"""
MOTEUR : boucle temps réel qui orchestre la détection.

À chaque cycle :
  1. Récupère la dernière fenêtre de logs
  2. Passe au détecteur + IncidentTracker
  3. Si événement (début/fin d'incident) : journalise + notifie remédiation
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from app.domaine.detecteur import AnomalyDetector, IncidentTracker
from app.infrastructure.sources.base import Source

log = logging.getLogger("ml-detector")

HISTORIQUE_FENETRES = 6  # fenêtres lues par cycle (seule la dernière est analysée)


class Moteur:
    """Boucle temps réel du détecteur."""

    def __init__(self, detecteur: AnomalyDetector, source: Source, notificateur=None,
                 pas: int = 10, retard: int = 0, n_consecutifs: int = 3, horloge=time.time):
        self.detecteur = detecteur
        self.source = source
        self.notificateur = notificateur
        self.pas = pas
        self.retard = retard
        self.horloge = horloge
        self.tracker = IncidentTracker(detecteur, n_consecutifs=n_consecutifs)

        self._verrou = threading.Lock()
        self.evenements = deque(maxlen=50)
        self.compteurs = {"cycles": 0, "erreurs_source": 0, "alertes_envoyees": 0, "alertes_echouees": 0}
        self.dernier_ts = None
        self.derniere_maj = None
        self.derniere_erreur = None

    def cycle(self, maintenant: float | None = None) -> dict | None:
        """Un tour de boucle. Retourne l'événement produit, ou None."""
        maintenant = self.horloge() if maintenant is None else maintenant
        fin = maintenant - self.retard
        try:
            # On lit quelques fenêtres de plus que nécessaire : si la dernière n'a aucun log,
            # la latence reprend la valeur précédente (sinon 0 ms => fausse anomalie).
            fin = fin // self.pas * self.pas
            photos = self.source.fetch(fin - self.pas * HISTORIQUE_FENETRES, fin)
        except Exception as e:
            with self._verrou:
                self.compteurs["erreurs_source"] += 1
                self.derniere_erreur = f"{type(e).__name__}: {e}"
            log.error("Lecture des données impossible : %s", e)
            return None

        if photos.empty:
            return None

        photo = photos.iloc[[-1]]
        ts = int(photo.index[0])
        if ts == self.dernier_ts:  # Photo déjà traitée
            return None

        evenement = self.tracker.mettre_a_jour(photo)
        with self._verrou:
            self.dernier_ts = ts
            self.derniere_maj = maintenant
            self.compteurs["cycles"] += 1
            self.derniere_erreur = None

        if evenement:
            evenement = {
                "id": str(uuid.uuid4()),
                "detecteur": "ml-detector",
                "horodatage": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                **evenement,
            }
            log.warning("EVENEMENT %s", json.dumps(evenement, ensure_ascii=False))
            envoye = False
            if self.notificateur:
                envoye = self.notificateur.envoyer(evenement)
            with self._verrou:
                self.evenements.append(evenement)
                if envoye:
                    self.compteurs["alertes_envoyees"] += 1
                else:
                    self.compteurs["alertes_echouees"] += 1
        return evenement

    def boucle(self, arret: threading.Event) -> None:
        """Tourne jusqu'à ce que `arret` soit levé. Un cycle au début de chaque fenêtre."""
        log.info("Boucle démarrée (pas=%ss, retard=%ss)", self.pas, self.retard)
        while not arret.is_set():
            try:
                self.cycle()
            except Exception:
                log.exception("Erreur inattendue pendant un cycle")
            attente = self.pas - (self.horloge() % self.pas)
            arret.wait(attente)

    def en_bonne_sante(self) -> bool:
        """Sain si un cycle a réussi récemment (moins de 3 pas)."""
        with self._verrou:
            return self.derniere_maj is not None and self.horloge() - self.derniere_maj < 3 * self.pas

    def instantane(self) -> dict:
        """État courant pour /etat, /health, /metrics."""
        with self._verrou:
            res = self.tracker.dernier_resultat
            scores = {} if res is None else {
                k.removeprefix("score_"): round(float(v), 4)
                for k, v in res.items() if k.startswith("score_")
            }
            return {
                "en_incident": self.tracker.en_incident,
                "service_touche": self.tracker.service if self.tracker.en_incident else None,
                "scores": scores,
                "dernier_timestamp": self.dernier_ts,
                "derniere_maj": self.derniere_maj,
                "derniere_erreur": self.derniere_erreur,
                "compteurs": dict(self.compteurs),
                "evenements": list(self.evenements)[-20:],
            }
