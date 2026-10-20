"""Tests des CLI : chaîne complète collect -> train -> replay -> evaluate sur données simulées."""
import contextlib
import io

import pandas as pd
import pytest

from app.presentation.cli import collect, evaluate, replay, train


def lancer(module, argv):
    """Exécute main(argv) en capturant stdout/stderr. Retourne (code, sortie, erreurs)."""
    sortie, erreurs = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreurs):
        code = module.main(argv)
    return code, sortie.getvalue(), erreurs.getvalue()


@pytest.fixture(scope="module")
def donnees():
    import tempfile, pathlib
    dossier = pathlib.Path(tempfile.mkdtemp())
    normal, incident = dossier / "normal.csv", dossier / "incident.csv"
    assert collect.main(["--source", "simule", "--minutes", "40", "--out", str(normal)]) == 0
    assert collect.main(["--source", "simule", "--minutes", "20", "--out", str(incident),
                         "--incident", "api:8:5:panne", "--seed", "7"]) == 0
    return dossier, normal, incident


def test_collect_simule_ecrit_verite_au_bon_endroit(donnees):
    _, normal, incident = donnees
    n = pd.read_csv(normal, index_col="timestamp")
    assert len(n) >= 200 and "_verite" in n.columns
    assert (n["_verite"].fillna("") == "").all()
    df = pd.read_csv(incident, index_col="timestamp")
    touchees = df[df["_verite"].fillna("") == "api"]
    assert len(touchees) == 30                      # 5 min / 10 s
    assert touchees.index[0] - df.index[0] == 8 * 60  # démarre bien à +8 min
    assert all(int(t) % 10 == 0 for t in df.index)    # aligné sur le pas


@pytest.mark.parametrize("modele", ["isolation_forest", "lof", "statistique"])
def test_train_trois_modeles(donnees, modele):
    dossier, normal, _ = donnees
    sortie = dossier / f"{modele}.joblib"
    assert train.main(["--data", str(normal), "--out", str(sortie), "--modele", modele]) == 0
    assert sortie.exists()


def test_replay_detecte_l_incident(donnees):
    dossier, _, incident = donnees
    modele = dossier / "isolation_forest.joblib"
    code, sortie, _ = lancer(replay, ["--data", str(incident), "--modele", str(modele)])
    assert code == 0
    assert "incident_debut" in sortie and "=== Évaluation ===" in sortie
    assert "Détecté         : oui" in sortie


def test_evaluate_tableau_et_csv(donnees, tmp_path):
    _, normal, incident = donnees
    res = tmp_path / "res.csv"
    assert evaluate.main(["--normal", str(normal), "--incident-data", str(incident),
                          "--csv-sortie", str(res)]) == 0
    tableau = pd.read_csv(res)
    assert list(tableau["modele"]) == ["isolation_forest", "lof", "statistique"]
    assert (tableau["incidents_reels"] == 1).all()
    # isolation_forest et statistique doivent voir l'incident (LOF : voir README, pas d'assertion)
    assert tableau.loc[0, "incidents_detectes"] == 1
    assert tableau.loc[2, "incidents_detectes"] == 1


def test_evaluate_incident_manuel_sur_csv_sans_verite(donnees, tmp_path):
    _, normal, incident = donnees
    df = pd.read_csv(incident, index_col="timestamp").drop(columns="_verite")
    sans = tmp_path / "sans.csv"
    df.to_csv(sans)
    d = int(df.index[0]) + 480
    assert evaluate.main(["--normal", str(normal), "--incident-data", str(sans),
                          "--incident", f"api,{d},{d + 300}"]) == 0
    # sans _verite ni --incident : erreur claire
    assert evaluate.main(["--normal", str(normal), "--incident-data", str(sans)]) == 1


def test_erreurs_attendues_sans_traceback(donnees, tmp_path):
    _, normal, incident = donnees
    code, _, err = lancer(replay, ["--data", str(incident), "--modele", str(tmp_path / "absent.joblib")])
    assert code == 1 and "Modèle introuvable" in err
    code, _, err = lancer(train, ["--data", str(tmp_path / "absent.csv"), "--out", str(tmp_path / "m.joblib")])
    assert code == 1 and "introuvable" in err
    vide = tmp_path / "vide.csv"
    vide.write_text("timestamp,api__req_count\n")
    assert train.main(["--data", str(vide), "--out", str(tmp_path / "m.joblib")]) == 1
    assert collect.main(["--source", "simule", "--out", str(tmp_path / "x.csv"),
                         "--incident", "inconnu:1:1:dos"]) == 1
    assert collect.main(["--source", "fichier", "--out", str(tmp_path / "x.csv")]) == 1
