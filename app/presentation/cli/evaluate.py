"""
EVALUATE : compare les trois modèles (isolation_forest, lof, statistique) sur le MÊME jeu.

  1. chaque modèle est entraîné sur le CSV de trafic normal (--normal) ;
  2. chaque modèle est rejoué sur le CSV avec incidents (--incident-data) ;
  3. on compare ses alertes à la vérité (colonne _verite) : c'est le tableau du dossier.

Adaptation par rapport à app/domaine/evaluation.py : evaluer() n'y regarde que le PREMIER
incident et ne donne pas les pourcentages de photos. Le calcul est donc fait ici, pour
plusieurs incidents, avec les mêmes briques (AnomalyDetector, IncidentTracker).
"""

import argparse
import sys

import pandas as pd

import config
from app.application.entrainer import ErreurDonnees, charger_csv, charger_pour_entrainement, parametres_modele
from app.domaine.detecteur import AnomalyDetector, IncidentTracker

MODELES = ["isolation_forest", "lof", "statistique"]

EXEMPLES = """exemples (PowerShell, depuis le dossier ml-detector) :
  # jeu simulé : le CSV d'incident contient déjà la colonne _verite
  python -m app.presentation.cli.evaluate --normal data/normal_simule.csv --incident-data data/incident_simule.csv
  # vrai CSV sans _verite : on indique l'incident (secondes epoch ou dates ISO)
  python -m app.presentation.cli.evaluate --normal data/normal.csv --incident-data data/attaque.csv \\
      --incident "api,2026-10-22T10:00:00Z,2026-10-22T10:05:00Z" --csv-sortie data/resultats.csv
"""


def lire_instant(texte: str) -> int:
    """Secondes epoch ('1792404920') ou date ISO ('2026-10-22T10:00:00Z') -> secondes epoch."""
    try:
        return int(float(texte))
    except ValueError:
        pass
    try:
        t = pd.Timestamp(texte)
    except ValueError as err:
        raise ErreurDonnees(f"Date illisible : '{texte}' (attendu : secondes epoch ou ISO 8601)") from err
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return int(t.timestamp())


def ajouter_verite(df: pd.DataFrame, incidents: list[str]) -> pd.DataFrame:
    """Construit _verite depuis des textes 'service,debut,fin' (fin exclue)."""
    df = df.copy()
    df["_verite"] = ""
    for texte in incidents:
        morceaux = [m.strip() for m in texte.split(",")]
        if len(morceaux) != 3:
            raise ErreurDonnees(f"Incident invalide '{texte}' : format attendu service,debut,fin")
        service, debut, fin = morceaux[0], lire_instant(morceaux[1]), lire_instant(morceaux[2])
        if fin <= debut:
            raise ErreurDonnees(f"Incident invalide '{texte}' : la fin doit suivre le début")
        masque = (df.index >= debut) & (df.index < fin)
        if not masque.any():
            raise ErreurDonnees(f"Incident '{texte}' : aucune photo du CSV dans cette période")
        df.loc[masque, "_verite"] = service
    return df


def incidents_reels(verite: pd.Series) -> list[dict]:
    """Découpe _verite en incidents : une suite de photos consécutives avec le même service."""
    pas = config.STEP_SECONDS
    reels, courant = [], None
    for ts, service in verite.items():
        ts = int(ts)
        if service == "":
            courant = None
        elif courant and courant["service"] == service and ts - courant["fin"] <= pas:
            courant["fin"] = ts
        else:
            courant = {"service": service, "debut": ts, "fin": ts}
            reels.append(courant)
    return reels


def evaluer_modele(det: AnomalyDetector, df: pd.DataFrame, n_consecutifs: int) -> dict:
    """Rejoue df dans le détecteur et calcule les chiffres du tableau."""
    verite = df["_verite"].fillna("")
    reels = incidents_reels(verite)

    # 1) Alertes réelles : on rejoue photo par photo, comme en direct (avec la règle des N d'affilée)
    tracker = IncidentTracker(det, n_consecutifs=n_consecutifs)
    debuts = []
    for i in range(len(df)):
        ev = tracker.mettre_a_jour(df.iloc[[i]])
        if ev and ev["evenement"] == "incident_debut":
            debuts.append(ev)

    # 2) Une alerte est "bonne" si elle tombe PENDANT un incident réel ; la 1re de chaque incident
    #    compte comme détection, les suivantes (et celles hors incident) sont des fausses alertes.
    delais, bons_services, detectes = [], 0, 0
    utilisees = set()
    for inc in reels:
        dedans = [e for e in debuts if inc["debut"] <= e["timestamp"] <= inc["fin"]]
        if dedans:
            e = dedans[0]
            detectes += 1
            utilisees.add(id(e))
            delais.append(e["timestamp"] - inc["debut"])
            bons_services += e["service"] == inc["service"]
    fausses = len([e for e in debuts if id(e) not in utilisees])

    # 3) Photo par photo (sans la règle des N d'affilée) : mesure brute du modèle
    anomalie = det.score(df)["anomalie"]
    en_attaque = verite != ""
    return {
        "incidents_reels": len(reels),
        "incidents_detectes": detectes,
        "delai_moyen_s": round(sum(delais) / len(delais), 1) if delais else None,
        "bon_service": bons_services,
        "fausses_alertes": fausses,
        "pct_photos_attaque_signalees": round(100 * float(anomalie[en_attaque].mean()), 1)
        if en_attaque.any()
        else None,
        "pct_photos_normales_signalees": round(100 * float(anomalie[~en_attaque].mean()), 1)
        if (~en_attaque).any()
        else None,
    }


def afficher(tableau: pd.DataFrame) -> None:

    def f(v, suffixe=""):
        return "-" if pd.isna(v) else f"{v}{suffixe}"

    print(
        f"\n{'modèle':<18}{'détectés':>10}{'délai moy.':>12}{'bon service':>13}"
        f"{'fausses al.':>13}{'% attaque':>11}{'% normal':>10}"
    )
    for _, r in tableau.iterrows():
        print(
            f"{r['modele']:<18}{r['incidents_detectes']:>5}/{r['incidents_reels']:<4}"
            f"{f(r['delai_moyen_s'], ' s'):>12}{r['bon_service']:>8}/{r['incidents_detectes']:<4}"
            f"{r['fausses_alertes']:>13}{f(r['pct_photos_attaque_signalees'], ' %'):>11}"
            f"{f(r['pct_photos_normales_signalees'], ' %'):>10}"
        )
    print("\n% attaque = photos d'incident signalées anormales (plus haut = mieux)")
    print("% normal  = photos normales faussement signalées (plus bas = mieux)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Compare les trois modèles sur un jeu avec incidents.",
        epilog=EXEMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--normal", required=True, help="CSV de trafic normal (entraînement)")
    p.add_argument(
        "--incident-data",
        required=True,
        dest="incident_data",
        help="CSV avec incidents (colonne _verite, ou voir --incident)",
    )
    p.add_argument(
        "--incident",
        action="append",
        default=[],
        metavar="SERVICE,DEBUT,FIN",
        help="pour un CSV réel SANS _verite : incident réel, répétable. DEBUT et FIN en secondes epoch ou ISO 8601",
    )
    p.add_argument("--contamination", type=float, default=None, help="contamination des modèles (défaut : config.py)")
    p.add_argument(
        "--n",
        type=int,
        default=config.N_CONSECUTIFS,
        help="photos anormales d'affilée avant alerte (défaut : %(default)s)",
    )
    p.add_argument("--csv-sortie", help="écrit le tableau dans ce CSV (chiffres du dossier)")
    args = p.parse_args(argv)

    try:
        normal = charger_pour_entrainement(args.normal)
        test = charger_csv(args.incident_data)
        if args.incident:
            test = ajouter_verite(test, args.incident)
        elif "_verite" not in test.columns:
            raise ErreurDonnees(
                "Pas de colonne _verite dans le CSV d'incident : "
                'indiquer l\'incident avec --incident "service,debut,fin"'
            )
        if not (test["_verite"].fillna("") != "").any():
            raise ErreurDonnees("Le CSV d'incident ne contient aucune photo d'incident (_verite vide).")

        lignes = []
        for type_modele in MODELES:
            params = parametres_modele(type_modele, args.contamination)
            det = AnomalyDetector(type_modele=type_modele, **params).fit(normal)
            lignes.append({"modele": type_modele, **evaluer_modele(det, test, args.n)})
    except ErreurDonnees as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1
    except ValueError as e:  # colonnes du test absentes du modèle, etc.
        print(f"Erreur : {e}", file=sys.stderr)
        return 1

    tableau = pd.DataFrame(lignes)
    print(f"Entraînement : {len(normal)} photos normales | Test : {len(test)} photos")
    afficher(tableau)
    if args.csv_sortie:
        from pathlib import Path

        Path(args.csv_sortie).parent.mkdir(parents=True, exist_ok=True)
        tableau.to_csv(args.csv_sortie, index=False)
        print(f"\nTableau écrit -> {args.csv_sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
