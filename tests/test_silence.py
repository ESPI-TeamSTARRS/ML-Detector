"""Tests de app/domaine/silence.py : la règle silence (panne = chute à 0 requête) autour du détecteur."""

from types import SimpleNamespace

import pandas as pd
import pytest

import config
from app.domaine.detecteur import AnomalyDetector, IncidentTracker
from app.domaine.silence import SCORE_SILENCE, DetecteurAvecSilence, RegleSilence, avec_silence

T = 1_700_000_000 // 10 * 10


@pytest.fixture
def det(normal):
    return AnomalyDetector().fit(normal)


@pytest.fixture
def enveloppe(det):
    return DetecteurAvecSilence(det, RegleSilence(trafic_min=3.0, intensite=12.0))


@pytest.fixture
def muette(ligne_normale):
    """Fabrique une photo où les services de `muets` sont à 0 requête (le reste est typique)."""

    def fabrique(ts: int, muets=("api",)):
        ligne = ligne_normale(ts)
        for service in muets:
            ligne[f"{service}__req_count"] = 0.0
        return ligne

    return fabrique


# --- RegleSilence ---------------------------------------------------------------


def faux_detecteur(moyennes: dict):
    return SimpleNamespace(
        features_par_service={s: [f"{s}__req_count"] for s in moyennes},
        moyenne=pd.Series({f"{s}__req_count": m for s, m in moyennes.items()}),
    )


def photos(valeurs: dict) -> pd.DataFrame:
    return pd.DataFrame(valeurs, index=pd.Index([T, T + 10], name="timestamp"))


def test_service_habituellement_actif_a_zero_est_silencieux():
    res = RegleSilence(3.0).services_silencieux(photos({"a__req_count": [0.0, 5.0]}), faux_detecteur({"a": 9.5}))
    assert res["a"].tolist() == [True, False]


def test_service_calme_n_est_jamais_silencieux():
    res = RegleSilence(3.0).services_silencieux(photos({"a__req_count": [0.0, 0.0]}), faux_detecteur({"a": 0.4}))
    assert not res["a"].any()


def test_un_seul_service_silencieux_parmi_deux():
    res = RegleSilence(3.0).services_silencieux(
        photos({"a__req_count": [0.0, 0.0], "b__req_count": [7.0, 7.0]}), faux_detecteur({"a": 9.0, "b": 9.0})
    )
    assert res["a"].all() and not res["b"].any()


def test_service_sans_colonne_req_count_est_ignore():
    res = RegleSilence(3.0).services_silencieux(photos({"autre": [0.0, 0.0]}), faux_detecteur({"a": 9.0}))
    assert list(res.columns) == []


@pytest.mark.parametrize("kwargs", [{"trafic_min": 0}, {"trafic_min": -1}, {"intensite": 0}])
def test_regle_refuse_les_valeurs_invalides(kwargs):
    with pytest.raises(ValueError):
        RegleSilence(**kwargs)


# --- DetecteurAvecSilence : score -----------------------------------------------


def test_photo_silencieuse_est_anormale_et_designe_le_service(enveloppe, muette):
    res = enveloppe.score(muette(T)).iloc[0]
    assert bool(res["anomalie"]) is True
    assert res["service_suspect"] == "api"
    assert res["score"] <= SCORE_SILENCE and res["score_api"] <= SCORE_SILENCE


def test_le_modele_seul_ne_voyait_pas_la_panne(det, muette):
    """Justifie la règle : sans elle, la chute à 0 d'un service à 20 req/photo reste « normale » (score > 0)."""
    res = det.score(muette(T)).iloc[0]
    assert bool(res["anomalie"]) is False
    assert res["score_api"] > 0


def test_photos_normales_gardent_exactement_les_scores_du_modele(det, enveloppe, normal):
    assert enveloppe.score(normal).equals(det.score(normal))


def test_anomalie_du_modele_reste_detectee(det, enveloppe, ligne_anormale):
    ligne = ligne_anormale(T, "auth")
    assert enveloppe.score(ligne).equals(det.score(ligne))
    assert bool(enveloppe.score(ligne).iloc[0]["anomalie"]) is True


def test_silence_multiple_designe_le_service_le_plus_critique(enveloppe, muette):
    # api : criticité 4, auth : criticité 3 (conftest)
    res = enveloppe.score(muette(T, muets=("auth", "api"))).iloc[0]
    assert res["service_suspect"] == "api"


# --- DetecteurAvecSilence : explication et incidents ----------------------------


def test_expliquer_force_l_intensite_d_un_silence(enveloppe, det, muette):
    ligne = muette(T)
    assert det.expliquer(ligne, "api")["intensite"] < 12  # le modèle seul sous-estime la panne
    assert enveloppe.expliquer(ligne, "api")["intensite"] == 12.0


def test_expliquer_ne_change_rien_hors_silence(enveloppe, det, ligne_anormale):
    ligne = ligne_anormale(T, "auth")
    assert enveloppe.expliquer(ligne, "auth") == det.expliquer(ligne, "auth")


def rejouer(detecteur, lignes):
    tracker = IncidentTracker(detecteur, n_consecutifs=3)
    return [ev for ev in (tracker.mettre_a_jour(ligne) for ligne in lignes) if ev]


def test_trois_photos_silencieuses_declenchent_un_incident_critique(enveloppe, muette):
    evenements = rejouer(enveloppe, [muette(T + 10 * i) for i in range(3)])
    assert [e["evenement"] for e in evenements] == ["incident_debut"]
    assert evenements[0]["service"] == "api"
    assert evenements[0]["gravite"] == "critique"  # criticité 4 x niveau 3


def test_deux_photos_silencieuses_ne_declenchent_rien(enveloppe, muette, ligne_normale):
    lignes = [muette(T), muette(T + 10)] + [ligne_normale(T + 10 * (2 + i)) for i in range(4)]
    assert rejouer(enveloppe, lignes) == []


def test_l_incident_se_termine_quand_le_trafic_revient(enveloppe, muette, ligne_normale):
    lignes = [muette(T + 10 * i) for i in range(3)] + [ligne_normale(T + 10 * (3 + i)) for i in range(3)]
    evenements = rejouer(enveloppe, lignes)
    assert [e["evenement"] for e in evenements] == ["incident_debut", "incident_fin"]
    assert evenements[1]["service"] == "api"


def test_sans_la_regle_la_panne_n_est_pas_signalee(det, muette):
    """Contrôle de non-régression : c'est bien l'enveloppe qui apporte la détection."""
    assert rejouer(det, [muette(T + 10 * i) for i in range(6)]) == []


# --- Enveloppe transparente et interrupteur --------------------------------------


def test_enveloppe_transmet_les_attributs_du_detecteur(enveloppe, det):
    assert enveloppe.features == det.features
    assert enveloppe.features_par_service == det.features_par_service
    assert enveloppe.fiche()["nb_photos_entrainement"] == det.nb_photos_entrainement


def test_attribut_inexistant_leve_attributerror(enveloppe):
    with pytest.raises(AttributeError):
        _ = enveloppe.attribut_qui_n_existe_pas


def test_avec_silence_enveloppe_quand_actif(det, monkeypatch):
    monkeypatch.setattr(config, "SILENCE_ACTIF", True)
    assert isinstance(avec_silence(det), DetecteurAvecSilence)


def test_avec_silence_rend_le_detecteur_tel_quel_quand_desactive(det, monkeypatch):
    monkeypatch.setattr(config, "SILENCE_ACTIF", False)
    assert avec_silence(det) is det
