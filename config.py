"""
CONFIGURATION DU DÉTECTEUR — le SEUL fichier de réglage.

Toutes les couches (domaine, application, infrastructure, presentation) lisent ce fichier.
C'est ici qu'on s'adapte à ce que l'équipe décidera (noms de services, champs JSON,
mode de transmission des logs...) SANS toucher au reste du code.

Les valeurs surchargeables par variable d'environnement le sont pour Docker :
on change le comportement dans le docker-compose, pas dans le code.
"""
import os

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
# Sélecteur LogQL des flux à lire. Par défaut : tous ceux qui ont le label service.
# Exemple si INFRA ajoute un label de projet : '{compose_project="resilience",service=~".+"}'
LOKI_SELECTEUR = os.getenv("LOKI_SELECTEUR", "") or '{%s=~".+"}' % LOKI_LABEL_SERVICE

# --- Format des logs (décision D8) -------------------------------------------
# Si l'équipe utilise d'autres noms de champs, les relier ici.
# Format : {"nom attendu par le ML": "nom réel dans les logs"}
# Exemple : {"timestamp": "ts", "service": "app", "status_code": "http_code"}
CORRESPONDANCE_CHAMPS = {}

# --- Services surveillés -----------------------------------------------------
# criticite : 1 (faible) à 4 (vital) — À ALIGNER avec l'analyse EBIOS de l'équipe GOUV.
# features  : celles qui ont du sens pour ce service (voir app/domaine/features.py).
# Les noms doivent être EXACTEMENT ceux des logs / du label Loki.
# Un service absent d'ici n'est jamais surveillé (limite à citer dans le dossier).
SERVICES = {
    "api":   {"criticite": 4, "features": ["req_count", "error_4xx", "error_5xx", "latency_avg_ms"]},
    "auth":  {"criticite": 3, "features": ["req_count", "error_4xx", "error_5xx", "latency_avg_ms"]},
    "front": {"criticite": 2, "features": ["req_count", "error_5xx"]},
}

# --- Modèle (décisions D2/D3) --------------------------------------------------
# type : "isolation_forest" (principal), "lof" ou "statistique" (comparaison)
MODELE = {
    "type": "isolation_forest",
    "params": {"contamination": 0.01, "n_estimators": 200, "random_state": 42},
}
MODEL_PATH = os.getenv("MODEL_PATH", "models/detecteur.joblib")

# --- Alertes (décision D5) -----------------------------------------------------
# Nombre de photos anormales d'affilée avant d'alerter (et normales avant de clore).
N_CONSECUTIFS = int(os.getenv("N_CONSECUTIFS", "3"))
# URL du moteur de remédiation. Vide = les alertes sont seulement journalisées.
REMEDIATION_URL = os.getenv("REMEDIATION_URL", "")


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
