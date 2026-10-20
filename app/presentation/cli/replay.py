"""
REPLAY : rejoue un CSV photo par photo, comme en direct, et affiche les événements (JSON).
Si le CSV contient la colonne _verite (données simulées), affiche aussi une évaluation.
"""
import argparse
import json
import sys

import pandas as pd

import config
from app.application.entrainer import ErreurDonnees, charger_csv
from app.domaine.detecteur import AnomalyDetector, IncidentTracker

EXEMPLES = """exemple (PowerShell, depuis le dossier ml-detector) :
  python -m app.presentation.cli.replay --data data/incident_simule.csv --modele models/detecteur.joblib
"""


def charger_detecteur(chemin: str) -> AnomalyDetector:
    try:
        return AnomalyDetector.load(chemin)
    except FileNotFoundError:
        raise ErreurDonnees(f"Modèle introuvable : {chemin} (lancer d'abord le CLI train)")
    except Exception as e:  # fichier corrompu, mauvaise version...
        raise ErreurDonnees(f"Modèle illisible ({chemin}) : {e}")


def evaluer(df: pd.DataFrame, evenements: list[dict]) -> None:
    """Compare les alertes à la vérité terrain (_verite)."""
    verite = df["_verite"].fillna("")
    debuts = [e for e in evenements if e["evenement"] == "incident_debut"]
    print("\n=== Évaluation ===")
    reels = verite[verite != ""]
    if reels.empty:
        print(f"Aucun incident réel. Fausses alertes : {len(debuts)}")
        return
    debut_reel, service_reel = int(reels.index[0]), reels.iloc[0]
    vrais = [e for e in debuts if verite.get(e["timestamp"], "") != ""]
    print(f"Incident réel   : {service_reel} à t+{debut_reel - int(df.index[0])} s")
    if vrais:
        e = vrais[0]
        print(f"Détecté         : oui, avec {e['timestamp'] - debut_reel} s de délai")
        print(f"Service désigné : {e['service']} ({'correct' if e['service'] == service_reel else 'FAUX'})")
        print(f"Gravité         : {e['gravite']}")
    else:
        print("Détecté         : NON")
    print(f"Fausses alertes : {len(debuts) - len(vrais)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Rejoue un CSV dans le détecteur, photo par photo.",
        epilog=EXEMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True, help="CSV de photos (produit par collect)")
    p.add_argument("--modele", default=config.MODEL_PATH, help="modèle .joblib (défaut : %(default)s)")
    p.add_argument("--n", type=int, default=config.N_CONSECUTIFS,
                   help="photos anormales d'affilée avant alerte (défaut : %(default)s)")
    args = p.parse_args(argv)

    try:
        df = charger_csv(args.data)
        detecteur = charger_detecteur(args.modele)
        manquantes = set(detecteur.features) - set(df.columns)
        if manquantes:
            raise ErreurDonnees(f"Le CSV n'a pas les colonnes attendues par le modèle : {sorted(manquantes)}")
        tracker = IncidentTracker(detecteur, n_consecutifs=args.n)
        evenements = []
        for i in range(len(df)):
            ev = tracker.mettre_a_jour(df.iloc[[i]])
            if ev:
                evenements.append(ev)
                print(json.dumps(ev, ensure_ascii=False, indent=2))
    except ErreurDonnees as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1

    print(f"\n{len(df)} photos rejouées, {len(evenements)} événement(s)")
    if "_verite" in df.columns:
        evaluer(df, evenements)
    return 0


if __name__ == "__main__":
    sys.exit(main())
