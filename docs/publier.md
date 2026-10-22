# Publier un changement

Aucun push direct sur `main` : tout passe par une PR, vérifiée par `ci-ok`, fusionnée en squash.

## Pas à pas

1. Partir de `main` à jour :
   ```powershell
   git switch main
   git pull
   git switch -c feat/ma-modification
   ```
   Préfixes de branche usuels : `feat/`, `fix/`, `chore/`, `docs/`, `ci/`.
2. Modifier, puis vérifier en local :
   ```powershell
   uv run poe check
   ```
3. Committer. Message court, en français, au format Conventional Commit : `fix(silence): ignore les services sans logs depuis moins de 20 s`. Les hooks vérifient le message, les secrets, le lint et le formatage.
4. Pousser la branche (le hook `pre-push` lance gitleaks, les types et les tests) :
   ```powershell
   git push -u origin feat/ma-modification
   ```
5. Ouvrir une PR vers `main`.
   - **Le titre respecte Conventional Commits** : en squash, il devient le message du commit sur `main`, et le job `pr-title` le contrôle.
   - La description donne : quoi, pourquoi, comment tester, risque.
6. Attendre `ci-ok` au vert, faire relire, puis fusionner en **squash**.

## Petites PR, PR empilées

Une PR = un changement cohérent et relisable. Pour un travail en plusieurs tranches, on empile : chaque branche part de la précédente et sa PR vise la précédente. Quand la PR du bas est fusionnée, on change la base de la suivante vers `main` (GitHub le propose), on rebase si besoin, puis on continue.

## Cas particuliers du détecteur

### Dépendances

Ajouter ou mettre à jour une dépendance passe par uv, puis on régénère `requirements.txt` (l'image installe ce fichier, pas `uv.lock`). Le commit contient `pyproject.toml`, `uv.lock` **et** `requirements.txt` :

```powershell
uv add <paquet>==<version>
uv run poe requirements
uv run poe check
```

### Modèle entraîné

Les modèles sont des fichiers `.joblib` versionnés dans `models/` et copiés dans l'image. Un nouveau modèle se publie comme du code : entraîner, évaluer (voir `BENCHMARK.md`), committer le fichier, PR. Le modèle chargé par défaut est `models/detecteur.joblib`. Il doit être entraîné avec les versions de scikit-learn, numpy, pandas et joblib de `uv.lock`, sinon il ne se recharge pas à l'identique dans l'image.

### Contrat de l'alerte

Le format de l'alerte est partagé avec le service de remédiation. Le modifier demande de **prévenir l'équipe remédiation avant** la fusion, pas après.

### Image Docker

Le `Dockerfile` n'est construit que pour contrôle par la CI (job `docker`) : cette chaîne ne publie pas d'image. Le mode de publication et le déploiement sur la VM commune seront décrits ici quand ils auront été décidés avec l'équipe.

## Ce qu'on ne fait pas

- pas de secret dans le dépôt, dans un commit, une PR ou un message (utiliser les variables d'environnement, `.env` est ignoré par Git) ;
- pas de `--no-verify`, pas de test ignoré, pas de règle de lint ou de sécurité désactivée pour « faire passer » ;
- pas de push direct sur `main`, pas de `push --force` sur `main` ;
- pas de modification manuelle de `requirements.txt`.
