"""Tests de app/domaine/modeles.py : les 3 modèles respectent decision_function < 0 <=> anormal."""

import numpy as np
import pandas as pd
import pytest

from app.domaine.modeles import MODELES_DISPONIBLES, creer_modele

COLS = ["s__x", "s__y"]
TYPES = ["isolation_forest", "lof", "statistique"]


@pytest.fixture
def X_normal():
    rng = np.random.default_rng(0)
    return pd.DataFrame({"s__x": rng.normal(50, 5, 400), "s__y": rng.normal(100, 10, 400)})


def photo(x, y):
    return pd.DataFrame({"s__x": [x], "s__y": [y]})


def test_les_trois_types_existent():
    assert sorted(MODELES_DISPONIBLES) == sorted(TYPES)


def test_type_inconnu():
    with pytest.raises(ValueError, match="inconnu"):
        creer_modele("reseau_de_neurones")


@pytest.mark.parametrize("type_modele", TYPES)
def test_non_entraine_leve_une_erreur(type_modele):
    with pytest.raises(RuntimeError):
        creer_modele(type_modele).decision_function(photo(1, 1))


@pytest.mark.parametrize("type_modele", TYPES)
def test_point_typique_positif_point_extreme_negatif(type_modele, X_normal):
    m = creer_modele(type_modele).fit(X_normal)
    typique = m.decision_function(photo(50, 100))[0]
    extreme = m.decision_function(photo(500, 1000))[0]
    assert typique > 0, "un point typique doit être normal (score > 0)"
    assert extreme < 0, "un point très éloigné doit être anormal (score < 0)"
    assert extreme < typique


@pytest.mark.parametrize("type_modele", TYPES)
def test_sortie_un_score_par_ligne(type_modele, X_normal):
    m = creer_modele(type_modele).fit(X_normal)
    assert m.decision_function(X_normal.head(7)).shape == (7,)


@pytest.mark.parametrize("type_modele", TYPES)
def test_taux_de_faux_positifs_faible_sur_le_normal(type_modele, X_normal):
    m = creer_modele(type_modele).fit(X_normal)
    assert (m.decision_function(X_normal) < 0).mean() < 0.05


def test_statistique_detecte_la_hausse_et_la_baisse_brutale(X_normal):
    m = creer_modele("statistique", seuil_ecarts_types=3.0).fit(X_normal)
    assert m.decision_function(photo(50 + 6 * 5, 100))[0] < 0  # +6 écarts-types
    assert m.decision_function(photo(50 - 6 * 5, 100))[0] < 0  # -6 écarts-types (panne : trafic qui chute)
    assert m.decision_function(photo(0, 100))[0] < 0  # plus aucune requête
    assert m.decision_function(photo(50 + 2 * 5, 100))[0] > 0  # +2 : sous le seuil, normal


def test_statistique_seuil_exact(X_normal):
    m = creer_modele("statistique", seuil_ecarts_types=3.0).fit(X_normal)

    def z(v):
        return (v - X_normal["s__x"].mean()) / X_normal["s__x"].std()

    haut = X_normal["s__x"].mean() + 3.5 * X_normal["s__x"].std()
    assert m.decision_function(photo(haut, 100))[0] == pytest.approx(3.0 - abs(z(haut)), abs=0.2)


@pytest.mark.parametrize("type_modele", TYPES)
def test_colonne_constante_ignoree(type_modele, X_normal):
    X = X_normal.assign(s__z=0.0)
    m = creer_modele(type_modele).fit(X)
    score = m.decision_function(X.head(3).assign(s__z=0.0))
    assert score.shape == (3,)


def test_score_est_un_alias_de_decision_function(X_normal):
    m = creer_modele("isolation_forest").fit(X_normal)
    assert np.array_equal(m.score(X_normal.head(5)), m.decision_function(X_normal.head(5)))
