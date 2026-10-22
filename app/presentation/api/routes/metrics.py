"""Route /metrics : métriques Prometheus."""

from fastapi import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest

import config


def creer_routes_metrics(app):
    """Ajoute la route /metrics à l'app FastAPI."""

    @app.get("/metrics")
    def metrics():
        etat = app.state.moteur.instantane()
        registre = CollectorRegistry()

        # Anomaly score par service
        score = Gauge("anomaly_score", "Score Isolation Forest (< 0 = anormal)", ["service"], registry=registre)
        for service, valeur in etat["scores"].items():
            score.labels(service=service).set(valeur)

        # Incident actif par service
        incident = Gauge("anomaly_incident_actif", "1 si un incident est en cours", ["service"], registry=registre)
        for service in config.SERVICES:
            incident.labels(service=service).set(1 if etat["service_touche"] == service else 0)

        # Dernière mise à jour
        Gauge("detector_derniere_maj_timestamp", "Heure du dernier cycle réussi", registry=registre).set(
            etat["derniere_maj"] or 0
        )

        # Compteurs
        for nom, valeur in etat["compteurs"].items():
            Counter(f"detector_{nom}", f"Compteur {nom}", registry=registre).inc(valeur)

        return Response(generate_latest(registre), media_type=CONTENT_TYPE_LATEST)
