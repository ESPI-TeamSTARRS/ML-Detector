"""
CONFIGURATION DU DÉTECTEUR — le SEUL fichier de réglage.

Toutes les couches (domaine, application, infrastructure, presentation) lisent ce fichier.
C'est ici qu'on s'adapte à ce que l'équipe décidera (noms de services, champs JSON,
mode de transmission des logs...) SANS toucher au reste du code.

Les valeurs surchargeables par variable d'environnement le sont pour Docker :
on change le comportement dans le docker-compose, pas dans le code.
"""

import os
from typing import Any

# Développement : si un fichier .env existe, ses valeurs sont chargées. Les VRAIES variables
# d'environnement (celles que Docker injecte en production) restent prioritaires (override=False).
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # python-dotenv absent : on lit simplement l'environnement
    pass

# --- Rythme -----------------------------------------------------------------
# Une "photo" du système = une fenêtre de STEP_SECONDS secondes (décision D4).
STEP_SECONDS = 10

# --- Source des logs (décision D9) -------------------------------------------
#   "loki"   : le détecteur lit les logs bruts dans Loki (lecture seule)
#   "push"   : les autres envoient leurs logs sur POST /ingest
#   "simule" : fausse plateforme (développement, tests)
# (le mode "fichier" n'est pas temps réel : il sert aux scripts collect/train)
SOURCE = os.getenv("DETECTOR_SOURCE", "simule")

# Retard volontaire : Loki met quelques secondes à recevoir les logs. Lire "maintenant"
# donnerait une fenêtre incomplète, donc faussement calme -> fausse anomalie.
LAG_SECONDS = int(os.getenv("LAG_SECONDS", "5"))

# --- Loki -------------------------------------------------------------------
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
# Nom du label Loki qui porte le nom du service (dépend de la config Promtail/Alloy d'INFRA).
LOKI_LABEL_SERVICE = os.getenv("LOKI_LABEL_SERVICE", "service")
# Motifs à écarter (bruit), séparés par des virgules. Ex. "bwapi" : les vérifications de santé
# de BunkerWeb toutes les 30 s, qui ne reflètent pas le trafic réel.
LOKI_EXCLURE = [m.strip() for m in os.getenv("LOKI_EXCLURE", "").split(",") if m.strip()]
# Sélecteur LogQL des flux à lire. Par défaut : tous ceux qui ont le label service.
# Exemple si INFRA ajoute un label de projet : '{compose_project="resilience",service=~".+"}'
LOKI_SELECTEUR = os.getenv("LOKI_SELECTEUR", "") or f'{{{LOKI_LABEL_SERVICE}=~".+"}}'

# --- Format des logs (décision D8) -------------------------------------------
# Si l'équipe utilise d'autres noms de champs, les relier ici.
# Format : {"nom attendu par le ML": "nom réel dans les logs"}
# Exemple : {"timestamp": "ts", "service": "app", "status_code": "http_code"}
CORRESPONDANCE_CHAMPS: dict[str, str] = {}

# --- Services surveillés -----------------------------------------------------
# criticite : 1 (faible) à 4 (vital) — À ALIGNER avec l'analyse EBIOS de l'équipe GOUV.
# features  : celles qui ont du sens pour ce service (voir app/domaine/features.py).
# Les noms doivent être EXACTEMENT ceux des logs / du label Loki.
# Un service absent d'ici n'est jamais surveillé (limite à citer dans le dossier).
SERVICES: dict[str, dict[str, Any]] = {
    # Noms EXACTS du champ "service" des logs JSON de l'équipe DEV (préfixe "basecamp-").
    # Ces logs n'ont pas de code HTTP : on compte les événements (req_count), les refus
    # (denied_count : outcome = "denied") et tout ce qui n'est pas un succès (non_success_count).
    # Criticités PROVISOIRES : à remplacer par celles de l'analyse EBIOS.
    "basecamp-payment": {"criticite": 4, "features": ["req_count", "denied_count", "non_success_count", "error_logs"]},
    "basecamp-auth": {"criticite": 3, "features": ["req_count", "denied_count", "non_success_count", "error_logs"]},
    # Seul le shop envoie duration_ms (sur les devis acceptés) : les latences n'ont de sens que là.
    "basecamp-shop": {
        "criticite": 3,
        "features": ["req_count", "denied_count", "non_success_count", "error_logs", "latency_avg_ms"],
    },
}

# --- Modèle (décisions D2/D3) --------------------------------------------------
# type : "isolation_forest" (principal), "lof" ou "statistique" (comparaison)
MODELE: dict[str, Any] = {
    "type": "isolation_forest",
    # contamination 0.03 : mesuré sur 2 h de trafic (voir BENCHMARK.md). À 0.01, un flood n'est pas détecté ;
    # de 0.02 à 0.05, tous les incidents testés le sont en 20 s sans fausse alerte ; à 0.10, fausses alertes.
    "params": {"contamination": 0.03, "n_estimators": 200, "random_state": 42},
}
MODEL_PATH = os.getenv("MODEL_PATH", "models/detecteur.joblib")

# --- Alertes (décision D5) -----------------------------------------------------
# Nombre de photos anormales d'affilée avant d'alerter (et normales avant de clore).
N_CONSECUTIFS = int(os.getenv("N_CONSECUTIFS", "3"))
# --- Règle « silence » (pannes : chute à 0 requête, que le modèle ne voit pas) ------------------
# Un service habituellement actif (au moins SILENCE_TRAFIC_MIN requêtes par photo en moyenne à l'entraînement)
# qui tombe à 0 requête est signalé, avec les mêmes N photos d'affilée. Mettre SILENCE_ACTIF=0 pour désactiver.
SILENCE_ACTIF = os.getenv("SILENCE_ACTIF", "1") == "1"
SILENCE_TRAFIC_MIN = float(os.getenv("SILENCE_TRAFIC_MIN", "3"))
# Intensité d'un silence (écarts-types) : une panne totale vaut le niveau maximal de gravité (>= 12).
SILENCE_INTENSITE = float(os.getenv("SILENCE_INTENSITE", "12"))

# URL du moteur de remédiation. Vide = les alertes sont seulement journalisées.
REMEDIATION_URL = os.getenv("REMEDIATION_URL", "")

# --- Contexte envoyé au LLM / RAG (début d'incident uniquement) -------------------
# URL du RAG. Vide = fonction désactivée. Jeton : RAG_TOKEN (ou RAG_TOKEN_FILE).
RAG_URL = os.getenv("RAG_URL", "")
RAG_FENETRE_S = int(os.getenv("RAG_FENETRE_S", "60"))  # logs des N dernières secondes
RAG_MAX_LOGS = int(os.getenv("RAG_MAX_LOGS", "200"))  # plafond de logs par envoi
RAG_TIMEOUT_S = float(os.getenv("RAG_TIMEOUT_S", "3"))  # délai max : le LLM ne doit pas ralentir le détecteur


def lire_secret(nom: str) -> str:
    """
    Lit un secret sans jamais l'écrire dans le code :
      1. fichier pointé par <NOM>_FILE (secret Docker, fichier déchiffré par SOPS, agent Vault...)
      2. sinon variable d'environnement <NOM>
    Ne jamais journaliser la valeur retournée.
    """
    chemin = os.getenv(f"{nom}_FILE")
    if chemin:
        with open(chemin, encoding="utf-8") as f:
            return f.read().strip()
    return os.getenv(nom, "")
