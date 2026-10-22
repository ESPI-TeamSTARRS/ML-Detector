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
from datetime import UTC, datetime

from app.application.contexte_llm import construire_message
from app.domaine.detecteur import AnomalyDetector, IncidentTracker
from app.domaine.schema import normaliser
from app.infrastructure.sources.base import Source

log = logging.getLogger("ml-detector")

HISTORIQUE_FENETRES = 6  # fenêtres lues par cycle (seule la dernière est analysée)


class Moteur:
    """Boucle temps réel du détecteur."""

    def __init__(
        self,
        detecteur: AnomalyDetector,
        source: Source,
        notificateur=None,
        pas: int = 10,
        retard: int = 0,
        n_consecutifs: int = 3,
        horloge=time.time,
        notificateur_rag=None,
        rag_fenetre_s: int = 60,
        rag_max_logs: int = 200,
        rag_asynchrone: bool = True,
    ):
        self.detecteur = detecteur
        self.source = source
        self.notificateur = notificateur
        # Envoi optionnel du contexte (service + logs) au LLM, UNIQUEMENT au début d'incident.
        self.notificateur_rag = notificateur_rag
        self.rag_fenetre_s = rag_fenetre_s
        self.rag_max_logs = rag_max_logs
        self.rag_asynchrone = rag_asynchrone
        self.pas = pas
        self.retard = retard
        self.horloge = horloge
        self.tracker = IncidentTracker(detecteur, n_consecutifs=n_consecutifs)

        self._verrou = threading.Lock()
        self.evenements: deque[dict] = deque(maxlen=50)
        self.compteurs: dict[str, int] = {
            "cycles": 0,
            "erreurs_source": 0,
            "alertes_envoyees": 0,
            "alertes_echouees": 0,
            "contexte_llm_envoyes": 0,
            "contexte_llm_echoues": 0,
        }
        self.dernier_ts: int | None = None
        self.derniere_maj: float | None = None
        self.derniere_erreur: str | None = None

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
                "horodatage": datetime.fromtimestamp(ts, UTC).isoformat(),
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
            # Le LLM passe APRÈS la remédiation : son éventuelle lenteur ne la retarde jamais.
            if self.notificateur_rag and evenement["evenement"] == "incident_debut":
                self._contexte_vers_llm(evenement, fin)
        return evenement

    def _contexte_vers_llm(self, evenement: dict, fin: float) -> None:
        """Lance l'envoi du contexte au LLM (dans un fil séparé par défaut)."""
        if self.rag_asynchrone:
            threading.Thread(target=self._envoyer_contexte, args=(evenement, fin), daemon=True).start()
        else:
            self._envoyer_contexte(evenement, fin)

    def _envoyer_contexte(self, evenement: dict, fin: float) -> None:
        """Relit les logs récents du service touché, les nettoie, les envoie. Ne lève jamais."""
        try:
            debut = fin - self.rag_fenetre_s
            logs = normaliser(self.source.lire_logs(debut, fin))
            message = construire_message(evenement, logs, debut, fin, self.rag_max_logs)
            envoye = self.notificateur_rag.envoyer(message)
        except Exception as e:  # le LLM est accessoire : aucune erreur ne doit remonter
            log.warning("Contexte LLM non envoyé : %s", type(e).__name__)
            envoye = False
        with self._verrou:
            self.compteurs["contexte_llm_envoyes" if envoye else "contexte_llm_echoues"] += 1

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
            scores: dict[str, float] = (
                {}
                if res is None
                else {k.removeprefix("score_"): round(float(v), 4) for k, v in res.items() if k.startswith("score_")}
            )
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
