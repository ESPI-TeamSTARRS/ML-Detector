"""
SERVICE HTTP : Lance la boucle temps réel et expose les routes FastAPI.

Lancement :
  uvicorn app.presentation.api.main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
from app.application.alerter import NotificateurRemediation
from app.application.contexte_llm import NotificateurRAG
from app.application.detecter import Moteur
from app.domaine.detecteur import AnomalyDetector
from app.domaine.silence import avec_silence
from app.infrastructure.sources.base import Source
from app.infrastructure.sources.loki import LokiSource
from app.infrastructure.sources.push import PushSource
from app.infrastructure.sources.simulation import SimulationSource
from app.presentation.api.routes import etat, health, ingest, metrics, simulation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ml-detector")


def charger_detecteur(source_simulee: bool) -> AnomalyDetector:
    """Charge le modèle entraîné ou en crée un temporaire en mode simulé."""
    if os.path.exists(config.MODEL_PATH):
        return AnomalyDetector.load(config.MODEL_PATH)
    if not source_simulee:
        raise RuntimeError(
            f"Modèle introuvable : {config.MODEL_PATH}. Lancer d'abord : python -m app.presentation.cli.train"
        )
    # Confort dev : entraînement automatique sur données simulées
    log.warning("Pas de modèle : entraînement automatique sur données simulées (mode dev)")
    fin = time.time()
    source = SimulationSource(seed=1)
    df = source.fetch(fin - 3600, fin)
    return AnomalyDetector().fit(df)  # type et paramètres pris dans config.MODELE


def construire_moteur() -> Moteur:
    """Fabrique le moteur avec la bonne source."""
    simule = config.SOURCE == "simule"
    source: Source
    if simule:
        source = SimulationSource(origine=time.time())
        retard = 0
    elif config.SOURCE == "loki":
        source = LokiSource()
        retard = config.LAG_SECONDS
    elif config.SOURCE == "push":
        source = PushSource()
        retard = 0
    else:
        raise ValueError(f"SOURCE inconnue : {config.SOURCE}")

    return Moteur(
        detecteur=avec_silence(charger_detecteur(simule)),
        source=source,
        notificateur=NotificateurRemediation(config.REMEDIATION_URL, config.lire_secret("ALERT_TOKEN")),
        notificateur_rag=(
            NotificateurRAG(config.RAG_URL, config.lire_secret("RAG_TOKEN"), config.RAG_TIMEOUT_S)
            if config.RAG_URL
            else None
        ),
        rag_fenetre_s=config.RAG_FENETRE_S,
        rag_max_logs=config.RAG_MAX_LOGS,
        pas=config.STEP_SECONDS,
        retard=retard,
        n_consecutifs=config.N_CONSECUTIFS,
    )


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    """Démarrage et arrêt du service."""
    app.state.moteur = construire_moteur()
    app.state.config = config
    arret = threading.Event()
    fil = threading.Thread(target=app.state.moteur.boucle, args=(arret,), daemon=True)
    if os.getenv("DETECTOR_AUTOSTART", "1") == "1":
        fil.start()
    yield
    arret.set()


# Créer l'app FastAPI
app = FastAPI(title="ml-detector", lifespan=cycle_de_vie)

# Enregistrer les routes
health.creer_routes_health(app)
etat.creer_routes_etat(app)
metrics.creer_routes_metrics(app)
simulation.creer_routes_simulation(app)
ingest.creer_routes_ingest(app)
