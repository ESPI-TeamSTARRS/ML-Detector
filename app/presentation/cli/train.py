"""
TRAIN : entraîne le détecteur sur du trafic NORMAL uniquement (apprentissage non supervisé).
"""
import argparse
import sys

import config
from app.application.entrainer import ErreurDonnees, entrainer_et_sauvegarder

EXEMPLES = """exemples (PowerShell, depuis le dossier ml-detector) :
  python -m app.presentation.cli.train --data data/normal.csv --out models/detecteur.joblib
  python -m app.presentation.cli.train --data data/normal.csv --out models/lof.joblib --modele lof
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Entraîne le détecteur sur un CSV de trafic normal (produit par collect).",
        epilog=EXEMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True, help="CSV de trafic normal")
    p.add_argument("--out", default=config.MODEL_PATH, help="modèle .joblib (défaut : %(default)s)")
    p.add_argument("--modele", choices=["isolation_forest", "lof", "statistique"],
                   default=config.MODELE["type"], help="algorithme (défaut : %(default)s)")
    p.add_argument("--contamination", type=float, default=config.MODELE["params"].get("contamination"),
                   help="part de photos normales tolérées comme 'bizarres' (défaut : %(default)s ; "
                        "sans effet sur le modèle statistique)")
    args = p.parse_args(argv)

    try:
        entrainer_et_sauvegarder(args.data, args.out, type_modele=args.modele,
                                 contamination=args.contamination)
    except ErreurDonnees as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
