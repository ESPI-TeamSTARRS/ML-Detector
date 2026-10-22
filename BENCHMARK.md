# Benchmark du détecteur ML : trafic de 2 heures (22 octobre 2026)

Résultats mesurés sur la pile BaseCamp locale (Docker) avec le Traffic-Generator, pour les trois services **shop, auth et
payment**. Toutes les valeurs viennent de `notebooks/analyse_detecteur_4.ipynb` (réexécutable) et des suites de tests.

## 1. Résumé

- **Trafic** : 2 h de trafic normal réaliste (726 photos de 10 s). Aucun des quatre modèles ne déclenche d'alerte sur du
  trafic normal jamais vu.
- **Isolation Forest, réglage par défaut (contamination 0,01) : ne détecte pas un flood.** À 0,02–0,05 elle détecte tous les
  incidents en 20 s, sans fausse alerte. **Réglage conseillé : 0,03.**
- **Brute force sur auth** et **test de cartes sur payment** : détectés en 20 s, dans le bon service, par l'Isolation Forest,
  `lof_bruit` et la statistique. Le LOF du code ne détecte aucune des trois attaques (dégénéré).
- **Pannes de 60 s** : détectées **seulement pour le shop**, grâce à la règle silence (20 s, gravité critique).
- **Points faibles** : `payment` est à zéro dans 93 % des photos (modèle peu fiable) ; une baisse de trafic qui ne va pas à
  zéro, et une panne de 20 s, ne sont pas détectées.

## 2. Protocole

| Élément | Valeur |
|---|---|
| Pile | BaseCamp en Docker (traefik, shop, auth, payment, bases, mailpit) |
| Génération | Traffic-Generator, fenêtre mesurée dans les logs : 07 h 27 → 09 h 27 UTC (2 h) |
| Trafic simulé | visiteurs invités, nouveaux clients, clients qui se reconnectent ; commandes et paiements ; refus, abandons, rafales |
| Données | 726 photos de 10 s (`data/normal_2h.csv`) |
| Découpe | dans le temps : 544 photos d'entraînement (75 %), 182 de test (25 %), jamais vues |
| Modèles | Isolation Forest, LOF du code, `lof_bruit` (LOF avec un bruit infime à l'entraînement), statistique ; **un modèle par service** |
| Règle d'alerte | 3 photos anormales d'affilée (donc 20 s au minimum entre le début d'un incident et l'alerte) |
| Règle silence | un service dont la moyenne apprise est d'au moins 3 requêtes par photo et qui tombe à 0 est anormal |
| Mesures apprises | `req_count`, `denied_count`, `non_success_count` ; `error_logs` et `latency_avg_ms` sont constantes à 0 et ignorées |
| Incidents | **injectés** dans le jeu de test (pas de vraies attaques), un seul service touché à chaque fois |

## 3. Trafic normal observé

| Service | Requêtes totales | Moyenne par photo | Max | Photos à 0 | Refus totaux |
|---|---|---|---|---|---|
| shop | 9 959 | 13,72 | 54 | 0 % | 428 |
| auth | 1 963 | 2,70 | 17 | 47 % | 81 |
| payment | 298 | 0,41 | 8 | 93 % | 5 |

Un paiement n'arrive que lorsqu'un client commande (environ 3 % des sessions) : `payment` est presque toujours à zéro.

## 4. Fausses alertes sur du trafic normal

Part des photos de test (182) jugées anormales, par service, et alertes déclenchées (règle des 3 photos) :

| Modèle | shop | auth | payment | Alertes sur le test |
|---|---|---|---|---|
| isolation_forest | 0,5 % | 2,7 % | 0,5 % | **0** |
| lof | 0,0 % | 1,1 % | 0,0 % | **0** |
| lof_bruit | 0,0 % | 1,6 % | 0,5 % | **0** |
| statistique | 1,1 % | 1,6 % | 2,2 % | **0** |

Sur les 2 h complètes (dont la partie d'entraînement) : 0 alerte pour tous les modèles, **sauf la statistique : 1 alerte**.
La règle silence n'ajoute aucune alerte.

## 5. Détection par incident

Délai entre le début de l'incident et l'alerte, avec la gravité. « non » = non détecté. Le **bon service** est désigné dans
toutes les détections.

| Modèle | flood (shop) | brute force (auth) | test de cartes (payment) | panne 60 s (shop) | panne 60 s (auth) | panne 60 s (payment) |
|---|---|---|---|---|---|---|
| isolation_forest | **non** | 20 s, critique | 20 s, élevée | non | non | non |
| isolation_forest + silence | **non** | 20 s, critique | 20 s, élevée | **20 s, critique** | non | non |
| lof | non | non | non | non | non | non |
| lof + silence | non | non | non | **20 s, critique** | non | non |
| lof_bruit | 20 s, critique | 20 s, critique | 20 s, élevée | non | non | non |
| lof_bruit + silence | 20 s, critique | 20 s, critique | 20 s, élevée | **20 s, critique** | non | non |
| statistique | 20 s, critique | 20 s, critique | 20 s, élevée | non | non | non |
| statistique + silence | 20 s, critique | 20 s, critique | 20 s, élevée | **20 s, critique** | non | non |

Incidents injectés (30 photos = 5 min, sauf panne) : flood = trafic du shop ×8 + 20 ; brute force = +25 requêtes dont 22 refus
sur auth ; test de cartes = +12 requêtes dont 10 refus sur payment ; panne = 0 requête pendant 6 photos.

## 6. Réglage de l'Isolation Forest : la contamination

La contamination est la part des photos d'entraînement acceptées comme « bizarres » : elle fixe le seuil. Les rafales légitimes
rendent les gros pics fréquents dans l'entraînement ; un seuil trop strict laisse alors passer un flood.

| Réglage | Alertes sur trafic normal | flood ×8 | flood ×3 | flood ×2 | brute force | test de cartes |
|---|---|---|---|---|---|---|
| 0,01 (par défaut) | 0 | **non** | **non** | **non** | 20 s | 20 s |
| 0,02 | 0 | 20 s | 20 s | 20 s | 20 s | 20 s |
| **0,03 (conseillé)** | 0 | 20 s | 20 s | 20 s | 20 s | 20 s |
| 0,05 | 0 | 20 s | 20 s | 20 s | 20 s | 20 s |
| 0,10 | **1 fausse alerte** | fausse alerte | fausse alerte | fausse alerte | fausse alerte | fausse alerte |

Pour le modèle entraîné sur les 726 photos, la part de photos d'entraînement jugées anormales (3 modèles réunis) est de 2,3 %
à 0,01 et de 7,3 % à 0,03 ; la règle des 3 photos d'affilée garde malgré tout 0 alerte en rejeu sur ces 2 h.

## 7. Effet de la règle silence

| | Sans la règle | Avec la règle |
|---|---|---|
| Panne de 60 s sur le shop | non détectée (score toujours positif) | **20 s, critique** |
| Panne de 60 s sur auth | non détectée | non détectée (moyenne 2,70 < 3 : règle non applicable) |
| Panne de 60 s sur payment | non détectée | non détectée (moyenne 0,41 < 3) |
| Flood, brute force, test de cartes | inchangés | **identiques** (scores strictement égaux) |
| Alertes sur trafic normal | 0 | 0 |

## 8. Tests logiciels

| Projet | Résultat |
|---|---|
| ML-Detector | **232 tests passés** (dont 22 pour la règle silence) |
| Traffic-Generator | **196 tests passés**, lint propre, couverture **99,24 %** (seuil 80 %) |

Vérifications sur la pile réelle (Traffic-Generator) :

| Essai | Résultat |
|---|---|
| 2 min, 20 sessions/min (avant l'ajout des refus variés) | 37 sessions, 174 requêtes (200 : 166, 201 : 4, 202 : 3, 409 : 1) |
| 3 min, 25 sessions/min, gestes maladroits 10 % | 65 sessions, 345 requêtes (200 : 315, 201 : 10, 202 : 10, 409 : 1, 422 : 9) |
| Contrôles sur ces essais | **aucun 429, aucune erreur réseau, aucun verrouillage de compte** (`auth.lockout_triggered` : 0) |

Simulation de pile (tests automatiques, mêmes limites de débit que Traefik et verrouillage d'auth) : 0 réponse 429 et au plus
1 échec de connexion consécutif par compte, jusqu'à 120 visiteurs par minute.

## 9. Limites

- Les incidents sont **injectés** dans du trafic réel : ils ne remplacent pas de vraies attaques sur la pile.
- **Un seul découpage** entraînement/test (2 h de données).
- `payment` est à zéro dans 93 % des photos : son modèle est peu fiable, et la règle silence ne s'y applique pas.
- Une panne de **20 s** (moins de 3 photos) et une **baisse de trafic** qui ne va pas à zéro ne sont détectées ni avec ni sans la règle.
- Les comptes du générateur ne vérifient jamais leur email : chaque action produit un refus `order.guest_link_skipped`
  (`email_not_verified`) qui n'existerait pas pour des clients vérifiés.
- Pas de cycle jour/nuit, une seule IP, pas d'erreur 5xx : `error_logs` reste à 0.
- Le conteneur `shop-expiration` de la pile était `unhealthy` pendant les essais (les commandes impayées n'expirent pas).

## 10. Recommandations

1. Entraîner l'Isolation Forest avec **`--contamination 0.03`** (c'est le modèle `models/detecteur.joblib`).
2. Pour `payment`, collecter plus longtemps ou le laisser hors surveillance tant que son volume reste aussi faible.
3. Ne pas utiliser le LOF du code tel quel (compteurs entiers) ; préférer `lof_bruit` ou la statistique comme comparaison.
4. Garder la règle silence activée, en sachant qu'elle ne couvre que les services à plus de 3 requêtes par photo.
5. Compléter par des healthchecks Docker et Prometheus pour les pannes courtes.

## 11. Reproduire

```powershell
# Génération (dans Traffic-Generator/)
uv run poe normal --minutes 120 --sessions-per-minute 15
# Export des logs, collecte et entraînement (dans ML-Detector/)
cmd /c "docker compose -f ..\BaseCamp\docker-compose.yml logs --no-color --timestamps --since 135m shop auth payment > data\logs_2h.log"
uv run python -m app.presentation.cli.collect --source fichier --fichier data/logs_2h.log --out data/normal_2h.csv
uv run python -m app.presentation.cli.train --data data/normal_2h.csv --out models/detecteur.joblib --contamination 0.03
# Analyse
uv run jupyter lab   # notebooks/analyse_detecteur_4.ipynb
```

Pour le jeu exact de ce benchmark, `collect` a été suivi d'un recadrage sur la fenêtre 07 h 27 → 09 h 28 UTC (les essais
courts du début de matinée sont exclus).

## 12. Fichiers

- Modèles (chacun avec sa fiche `.json`) : `models/detecteur.joblib` (**modèle par défaut**, trois services, contamination 0,03),
  `models/detecteur_2h.joblib` (trois services, contamination 0,01) et `models/detecteur_01.joblib` (ancien modèle, shop seul,
  388 photos, contamination 0,01).
- Notebooks : `notebooks/analyse_detecteur_4.ipynb` (ce benchmark), `_3` (effet de la règle silence sur le shop seul), `_2`, `_1`.
- Règle silence : `app/domaine/silence.py`, interrupteur `SILENCE_ACTIF` dans `config.py`.
