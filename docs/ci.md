# Intégration continue : runbook

Ce document décrit ce que contrôle la CI de ML-Detector, comment la rejouer en local et que faire quand elle échoue.

## Principe

- Un seul workflow : `.github/workflows/ci.yml`. Il se déclenche sur chaque PR vers `main` (ouverture, nouveau commit, réouverture, titre modifié) et sur chaque push sur `main`.
- La CI rejoue les tâches `poe` du projet, jamais un outil en direct : ce qui passe en local passe en CI.
- Python 3.12 et dépendances figées sur `uv.lock` (`UV_FROZEN`, roues uniquement).
- Toutes les actions GitHub sont épinglées par SHA complet (la version est en commentaire). Les permissions sont vides par défaut, `contents: read` par job.
- Cette CI ne publie rien : elle construit l'image pour la contrôler, sans la pousser nulle part.

## Les jobs

| Job | Ce qu'il fait | Rejouer en local |
|---|---|---|
| `python` | `requirements.txt` à jour avec `uv.lock`, `poe quality` (ruff, ruff format --check, mypy), `poe test` (pytest, couverture ≥ 80 %), `poe security` (pip-audit) | `uv run poe requirements` puis `git diff requirements.txt`, et `uv run poe check` |
| `secrets-scan` | gitleaks 8.30.1 (archive vérifiée par SHA-256) sur les seuls commits apportés par la PR ou le push | `gitleaks git --redact -v --log-opts="origin/main..HEAD" .` |
| `pr-title` | le titre de la PR respecte Conventional Commits (même script que le hook `commit-msg`) | `python scripts/hooks/check_commit_msg.py <fichier contenant le titre>` |
| `docker` | construit l'image, la démarre avec les réglages de durcissement (lecture seule, `--cap-drop ALL`, `no-new-privileges`, source simulée) jusqu'à l'état `healthy`, puis scan Trivy bloquant | voir ci-dessous |
| `ci-ok` | agrège les jobs ci-dessus : vert si tous sont `success` ou `skipped` | — |

`ci-ok` est le **seul check à rendre obligatoire** dans la protection de la branche `main`. Ajouter un job bloquant, c'est l'ajouter à `needs` de `ci-ok`.

### Rejouer le job `docker` en local

```powershell
docker build -t ml-detector:ci .
docker run -d --name ml-detector --read-only --cap-drop ALL --security-opt no-new-privileges:true -e DETECTOR_SOURCE=simule ml-detector:ci
docker inspect --format '{{.State.Health.Status}}' ml-detector   # attendre « healthy »
docker logs ml-detector --tail 100
docker rm -f ml-detector
```

## Quand un job échoue

- **`python` / `requirements.txt` n'est pas à jour** : `uv run poe requirements`, puis committer le fichier. Il ne se modifie jamais à la main : c'est lui que l'image installe, il doit refléter `uv.lock`.
- **`python` / lint ou format** : `uv run poe format` corrige le formatage ; `uv run poe lint` indique le reste. Pas de `# noqa` sans motif écrit à côté.
- **`python` / types** : `uv run poe typecheck`.
- **`python` / tests ou couverture** : `uv run poe test`. Le seuil de 80 % ne se baisse pas ; on ajoute des tests. Les tests de modèle utilisent les fichiers de `models/` : ne pas les modifier sans réentraîner (voir [retour arrière](retour-arriere.md)).
- **`python` / security** : `pip-audit` signale une dépendance vulnérable. `uv add <paquet>==<version>` (ou `uv lock --upgrade-package <paquet>`), `uv run poe requirements`, puis `uv run poe check`. scikit-learn, numpy, pandas et joblib sont figés car ils conditionnent le chargement du modèle : toute mise à jour de l'un d'eux impose de recharger et de revalider le modèle.
- **`secrets-scan`** : un secret (ou ce qui y ressemble) est dans un commit de la PR. Le retirer **de l'historique de la branche** avant fusion, puis **révoquer** le secret s'il était réel. Un faux positif se traite dans `.gitleaks.toml` (règle et chemin précis), jamais par une exception large.
- **`pr-title`** : corriger le titre de la PR (le contrôle se relance tout seul). Format : `<type>(<portée>)?: <description>`, types `feat, fix, docs, chore, refactor, test, ci, build, perf, style, revert`.
- **`docker` / démarrage** : le job affiche les logs du conteneur. Le système de fichiers est en lecture seule : tout code qui écrit hors d'un volume dédié échoue ici comme en production. L'état `healthy` repose sur `GET /health`.
- **`docker` / Trivy** : le scan bloque sur les failles HIGH et CRITICAL **qui ont un correctif** et sur les secrets présents dans l'image.
  - Faille de l'image de base : reconstruire sur une base à jour (le tag `python:3.12-slim` se met à jour ; relancer le job).
  - Faille d'une dépendance Python : voir `security` ci-dessus.
  - Aucune correction possible et risque réellement nul : exception dans `.trivyignore`, **une ligne par identifiant, précédée d'un commentaire** avec le motif, l'auteur et une date de réévaluation (3 mois maximum). Une exception sans motif ni date est refusée en relecture.

Il est interdit de contourner un contrôle : pas de `--no-verify`, pas de test désactivé ou ignoré, pas de règle retirée pour faire passer la CI.

## Hooks Git

Les hooks rejouent une partie de la CI avant le commit et le push (voir le README, section « Hooks Git »). Installation, une fois :

```powershell
uv run pre-commit install --install-hooks
```

`gitleaks` 8.30.1 doit être installé (`winget install gitleaks.gitleaks`), la même version que la CI. Le `pre-push` lance les tests sans couverture (environ 1 minute) ; la couverture est imposée par la CI.
