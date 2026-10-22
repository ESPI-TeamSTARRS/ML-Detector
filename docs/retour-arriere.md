# Retour arrière

Le modèle, le code et les dépendances sont copiés dans l'image Docker : revenir en arrière, c'est ramener `main` à un état sain, puis reconstruire l'image à partir de cet état. Le déploiement sur la VM commune n'est pas encore décrit (voir [publier](publier.md)) ; cette page couvre le dépôt.

## Choisir la bonne voie

| Situation | Action |
|---|---|
| La PR n'est pas encore fusionnée | corriger la branche (nouveau commit) ou fermer la PR |
| Un changement fusionné est défectueux et le correctif est évident | PR de correctif courte (`fix: ...`) |
| Un changement fusionné est défectueux et il faut revenir vite à l'état précédent | PR de **revert** |

On ne réécrit jamais l'historique de `main` (pas de `reset --hard` suivi d'un push forcé).

## Revert d'une PR fusionnée

Les PR sont fusionnées en squash : une PR = un commit sur `main`, donc un seul commit à annuler.

```powershell
git switch main
git pull
git switch -c fix/revert-ma-modification
git revert <sha-du-commit-squash>
```

Le message proposé par Git (`Revert "..."`) doit être mis au format Conventional Commit, par exemple `revert: annule la règle silence sur les services lents`. Pousser la branche, ouvrir une PR, attendre `ci-ok`, fusionner en squash. En cas de conflit : le résoudre à la main, puis `uv run poe check`.

## Selon ce qui a changé

- **Code seul** : le revert suffit.
- **Dépendances** : le revert ramène `pyproject.toml`, `uv.lock` et `requirements.txt` ensemble. Vérifier que le job `python` confirme que `requirements.txt` est à jour.
- **Modèle** : le fichier `.joblib` précédent est dans l'historique Git ; le revert du commit de modèle le restaure. Ne jamais retoucher un `.joblib` à la main. Si c'est une mise à jour de scikit-learn, numpy, pandas ou joblib qui empêche de recharger le modèle, revenir sur la dépendance **ou** réentraîner, mais pas laisser un modèle illisible sur `main`.
- **Réglages de détection** (`config.py`, ex. contamination 0.03, règle silence) : le revert restaure les valeurs ; un modèle entraîné avec une autre contamination reste valable tel quel, c'est le seuil appliqué qui change.
- **Contrat de l'alerte** : prévenir l'équipe remédiation avant et après le revert, le format a pu être consommé entre-temps.
- **Dockerfile / `.dockerignore`** : le revert restaure le fichier ; le job `docker` valide que l'image démarre et passe Trivy.

## Exception Trivy devenue fausse

Une ligne de `.trivyignore` dont la date de réévaluation est dépassée se supprime (ou se justifie à nouveau) par une PR. Un correctif amont publié rend l'exception inutile : la retirer.

## Secret poussé par erreur

Retirer le secret du dépôt ne suffit pas : **le révoquer et en émettre un nouveau** d'abord. Ensuite seulement nettoyer, par une PR (et, si le secret est dans l'historique de `main`, en prévenir l'équipe avant toute réécriture).
