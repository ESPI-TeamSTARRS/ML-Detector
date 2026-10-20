"""
COLLECT : produit un CSV de features (une ligne par photo de STEP_SECONDS secondes).

Toutes les sources donnent des logs bruts ; les features sont calculées au même endroit,
donc le CSV a exactement le même format quelle que soit la source.
"""
import argparse
import sys
import time

import pandas as pd

import config
from app.application.entrainer import ErreurDonnees
from app.infrastructure.sources.fichier import FichierSource
from app.infrastructure.sources.loki import LokiSource
from app.infrastructure.sources.simulation import SimulationSource

EXEMPLES = """exemples (PowerShell, depuis le dossier ml-detector) :
  python -m app.presentation.cli.collect --source loki --minutes 60 --out data/normal.csv
  python -m app.presentation.cli.collect --source simule --minutes 60 --out data/normal_simule.csv
  python -m app.presentation.cli.collect --source simule --minutes 20 --out data/incident_simule.csv --incident api:10:5:dos
  python -m app.presentation.cli.collect --source fichier --fichier data/logs.jsonl --out data/normal.csv
"""


def lire_incident(texte: str) -> dict:
    """'api:10:5:dos' -> service api, début à 10 min, durée 5 min, type dos."""
    morceaux = texte.split(":")
    if len(morceaux) != 4:
        raise ErreurDonnees(f"Incident invalide '{texte}' : format attendu service:debut_min:duree_min:type")
    service, debut_min, duree_min, type_ = morceaux
    try:
        debut_s, duree_s = int(float(debut_min) * 60), int(float(duree_min) * 60)
    except ValueError:
        raise ErreurDonnees(f"Incident invalide '{texte}' : début et durée doivent être des nombres de minutes")
    if service not in config.SERVICES:
        raise ErreurDonnees(f"Incident invalide '{texte}' : service inconnu (connus : {', '.join(config.SERVICES)})")
    if type_ not in ("dos", "panne", "brute_force"):
        raise ErreurDonnees(f"Incident invalide '{texte}' : type inconnu (dos, panne ou brute_force)")
    return {"service": service, "debut_s": debut_s, "duree_s": duree_s, "type": type_}


def aligner(t: float, pas: int = config.STEP_SECONDS) -> int:
    """Arrondit t au pas inférieur : les photos tombent sur des multiples de 10 s."""
    return int(t // pas * pas)


def ajouter_verite(df: pd.DataFrame, incidents: list[dict], debut: int) -> pd.DataFrame:
    """
    Colonne _verite : le service réellement touché (vide sinon). Sert à évaluer, jamais à entraîner.
    Les incidents sont comptés depuis `debut`, la même origine que celle donnée à la simulation.
    """
    df = df.copy()
    df["_verite"] = ""
    for inc in incidents:
        d = debut + inc["debut_s"]
        df.loc[(df.index >= d) & (df.index < d + inc["duree_s"]), "_verite"] = inc["service"]
    return df


def collecter(args) -> pd.DataFrame:
    pas = config.STEP_SECONDS
    if args.source == "fichier":
        if not args.fichier:
            raise ErreurDonnees("--source fichier demande --fichier chemin")
        source = FichierSource(args.fichier)
        try:
            premier, dernier = source.etendue()
            if dernier <= premier:
                raise ErreurDonnees(f"Aucun log exploitable dans {args.fichier}")
            fin = dernier + 1  # +1 : [debut, fin) est ouvert à droite, on veut le dernier log inclus
            debut = premier if args.minutes is None else max(premier, fin - args.minutes * 60)
        except (FileNotFoundError, ValueError) as e:
            raise ErreurDonnees(f"Lecture de {args.fichier} impossible : {e}")
        return source.fetch(aligner(debut), fin)

    minutes = 60 if args.minutes is None else args.minutes
    if args.source == "loki":
        # Retard volontaire : les derniers logs mettent quelques secondes à arriver dans Loki.
        fin = aligner(time.time() - config.LAG_SECONDS)
        debut = aligner(fin - minutes * 60)
        try:
            return LokiSource().fetch(debut, fin)
        except Exception as e:  # réseau, Loki arrêté, requête refusée...
            raise ErreurDonnees(f"Loki injoignable ou en erreur ({config.LOKI_URL}) : {e}")

    # Simulation : origine FIXE = début de la fenêtre, donc "api:10:5:dos" tombe vraiment à +10 min.
    incidents = [lire_incident(t) for t in args.incident]
    fin = aligner(time.time())
    debut = aligner(fin - minutes * 60)
    source = SimulationSource(incidents=incidents, seed=args.seed, pas=pas, origine=debut)
    return ajouter_verite(source.fetch(debut, fin), incidents, debut)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Collecte des logs et écrit un CSV de features (une ligne par photo de 10 s).",
        epilog=EXEMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=["loki", "fichier", "simule"], required=True,
                   help="d'où viennent les logs")
    p.add_argument("--out", required=True, help="CSV de sortie")
    p.add_argument("--minutes", type=float, default=None,
                   help="durée de la fenêtre, finissant maintenant (loki, simule ; défaut 60). "
                        "Pour fichier : les N dernières minutes du fichier (défaut : tout le fichier)")
    p.add_argument("--fichier", help="(fichier) export de logs .jsonl, .ndjson ou .csv")
    p.add_argument("--incident", action="append", default=[], metavar="SERVICE:DEBUT_MIN:DUREE_MIN:TYPE",
                   help="(simule) incident à injecter, répétable. Types : dos, panne, brute_force")
    p.add_argument("--seed", type=int, default=42, help="(simule) graine du hasard, pour reproduire")
    args = p.parse_args(argv)

    if args.incident and args.source != "simule":
        p.error("--incident n'a de sens qu'avec --source simule")
    try:
        df = collecter(args)
        if df.empty:
            raise ErreurDonnees("Aucune photo produite : pas de logs sur cette période ?")
        from pathlib import Path
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out)
    except ErreurDonnees as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1
    mesures = [c for c in df.columns if not c.startswith("_")]
    print(f"{len(df)} photos x {len(mesures)} mesures -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
