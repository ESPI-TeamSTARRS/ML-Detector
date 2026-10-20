# Détecteur d'anomalies — Resilience-Alpha (pilier IA/ML)

Détecte qu'un service de la plateforme se comporte anormalement, **désigne le service touché**
et envoie une alerte JSON au moteur de remédiation.

## Principe

```
Logs bruts (Loki | push | fichier | simulation)
      │   toutes les sources donnent des LOGS BRUTS
[1] SCHÉMA     (domaine/schema.py)    → logs normalisés : timestamp, service, level, status_code, duration_ms
[2] FEATURES   (domaine/features.py)  → 1 « photo » / 10 s, colonnes <service>__<feature>  (calculées à UN seul endroit)
[3] DÉTECTION  (domaine/detecteur.py) → 1 Isolation Forest PAR SERVICE, entraîné sur du trafic NORMAL
[4] INCIDENT   (IncidentTracker)      → alerte après 3 photos anormales d'affilée, fin après 3 normales
[5] ALERTE     (application/alerter) → POST JSON + jeton vers la remédiation (3 essais)
```

Pourquoi calculer les features à un seul endroit : le modèle voit exactement les mêmes chiffres à
l'entraînement et en direct, quelle que soit la source.

## Architecture (Clean Architecture)

Les dépendances ne vont que vers l'intérieur : `presentation → application → domaine`,
`infrastructure → domaine`. Le domaine ne connaît ni FastAPI ni Loki.

| Dossier | Contenu |
|---|---|
| `config.py` | **Seul fichier de réglage** : services, criticité, features, modèle, source, URLs |
| `app/domaine/` | schema, features, modeles (3 modèles), detecteur, evaluation |
| `app/application/` | entrainer, detecter (moteur temps réel), alerter, securite |
| `app/infrastructure/sources/` | loki, push, fichier, simulation, docker_logs |
| `app/presentation/api/` | FastAPI : `/health`, `/etat`, `/metrics`, `/ingest` (push), `/simulation/incident` (simulé) |
| `app/presentation/cli/` | collect, train, evaluate, replay |
| `outils/relais_docker.py` | relais `docker compose logs` → détecteur (tourne sur l'HÔTE) |
| `tests/` | tests automatiques |

## Installation (Windows / PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Utilisation

```powershell
# 1. Données (simulées pour développer, loki ou fichier pour le réel)
python -m app.presentation.cli.collect --source simule --minutes 60 --out data/normal.csv
python -m app.presentation.cli.collect --source simule --minutes 20 --out data/incident.csv --incident api:10:5:dos
# 2. Entraînement (trafic NORMAL uniquement)
python -m app.presentation.cli.train --data data/normal.csv --out models/detecteur.joblib
# 3. Rejeu comme en direct
python -m app.presentation.cli.replay --data data/incident.csv --modele models/detecteur.joblib
# 4. Comparaison des 3 modèles (chiffres du dossier)
python -m app.presentation.cli.evaluate --normal data/normal.csv --incident-data data/incident.csv --csv-sortie data/resultats.csv
```

Réel : `--source loki --minutes 60`, ou `--source fichier --fichier data/logs.jsonl` (JSONL, CSV,
ou sortie de `docker compose logs` en `.log`).

## Récupérer les logs avec `docker compose logs`

Le détecteur ne lit pas lui-même Docker : lui donner le socket Docker reviendrait à lui donner les
droits root de l'hôte, ce qui contredit le durcissement exigé. À la place, un relais tourne **sur
l'hôte**, lit `docker compose logs -f --no-color --timestamps` et agit de deux façons :

```powershell
# a) Constituer un jeu d'entraînement hors ligne
python outils/relais_docker.py --compose-file ..\docker-compose.yml --vers-fichier data/logs.jsonl
python -m app.presentation.cli.collect --source fichier --fichier data/logs.jsonl --out data/normal.csv

# b) Alimenter le détecteur en direct (DETECTOR_SOURCE=push, jeton dans un fichier)
python outils/relais_docker.py --compose-file ..\docker-compose.yml --url http://ml-detector:8000/ingest --jeton-file secrets\ingest_token
```

Le jeton n'est jamais passé en argument. Si Loki est déjà alimenté par INFRA, préférer `DETECTOR_SOURCE=loki`
(rien à lancer en plus).

## Service temps réel

```powershell
$env:DETECTOR_SOURCE="simule"; uvicorn app.presentation.api.main:app --port 8000
curl http://localhost:8000/health ; curl http://localhost:8000/etat
curl -X POST http://localhost:8000/simulation/incident -H "Content-Type: application/json" -d '{"service":"api","type":"dos","duree_s":120}'
```

Docker : `docker build -t ml-detector .` (le dossier `models/` doit contenir `detecteur.joblib` hors mode simulé).
Extrait de compose : `deploiement/docker-compose.extrait.yml`.

## Contrat de l'alerte

`POST REMEDIATION_URL`, en-tête `Authorization: Bearer <ALERT_TOKEN>`. Événements `incident_debut`
(`id`, `detecteur`, `horodatage`, `evenement`, `timestamp`, `score`, `gravite`, `service`, `intensite`,
`features_principales`) et `incident_fin` (`service`, `duree_s`). Contrat partagé : ne pas le modifier sans prévenir.

## Sécurité

Aucun secret dans le dépôt (`<NOM>_FILE` ou variable d'environnement) ; conteneur non-root, `read_only`,
`cap_drop: ALL`, `no-new-privileges`, aucun port publié, pas de socket Docker ; `/ingest` et `/simulation`
n'existent que dans leur mode ; jeton comparé à temps constant ; jetons jamais journalisés.

## Limites (à citer dans le dossier)

Entraînement sur peu de trafic « normal » peu réaliste ; le normal dérive (réentraînement à prévoir) ;
une seule attaque testée ; le modèle dit « anormal » sans nommer le type d'attaque ; seuils de gravité
provisoires ; un service non listé dans `config.py` n'est jamais surveillé ; une attaque de moins de
30 s n'est pas signalée (règle des 3 photos) ; un seul champ qui dévie peut passer inaperçu.
