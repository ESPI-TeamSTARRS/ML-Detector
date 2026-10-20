"""Tests de app/domaine/detecteur.py : un modèle par service, explication, gravité, incidents, sauvegarde."""
import json
import warnings

import pandas as pd
import pytest

import config
from app.domaine.detecteur import AnomalyDetector, IncidentTracker, gravite, service_de
from app.domaine.modeles import IsolationForestModele, LOFModele


@pytest.fixture
def det(normal):
    return AnomalyDetector().fit(normal)


# --- Construction ---------------------------------------------------------------

def test_defauts_viennent_de_config():
    d = AnomalyDetector()
    assert d.type_modele == config.MODELE["type"]
    assert d.params_modele == config.MODELE["params"]


def test_type_explicite_n_herite_pas_des_params_d_un_autre_type():
    d = AnomalyDetector(type_modele="lof", contamination=0.02)
    assert d.type_modele == "lof"
    assert d.params_modele == {"contamination": 0.02}  # pas de n_estimators pour un LOF


def test_kwargs_l_emportent_sur_la_config():
    d = AnomalyDetector(n_estimators=10)
    assert d.params_modele["n_estimators"] == 10
    assert d.params_modele["random_state"] == config.MODELE["params"]["random_state"]


def test_service_de():
    assert service_de("api__error_4xx") == "api"


# --- Entraînement / score -------------------------------------------------------

def test_un_modele_par_service(det):
    assert sorted(det.modeles) == sorted(config.SERVICES)
    assert all(isinstance(m, IsolationForestModele) for m in det.modeles.values())
    for service, colonnes in det.features_par_service.items():
        assert all(service_de(c) == service for c in colonnes)
    assert det.nb_photos_entrainement == 300


def test_fit_avec_type_lof(normal):
    d = AnomalyDetector(type_modele="lof", contamination=0.02).fit(normal)
    assert all(isinstance(m, LOFModele) for m in d.modeles.values())


def test_colonne_commencant_par_underscore_jamais_donnee_au_modele(normal):
    d = AnomalyDetector().fit(normal.assign(_verite=""))
    assert "_verite" not in d.features


def test_colonne_constante_retiree(normal):
    d = AnomalyDetector().fit(normal.assign(front__error_5xx=0.0))
    assert "front__error_5xx" not in d.features


def test_score_colonnes_manquantes(det, normal):
    with pytest.raises(ValueError, match="absentes"):
        det.score(normal.drop(columns=["api__req_count"]))


def test_photo_typique_normale(det, ligne_normale):
    r = det.score(ligne_normale(0)).iloc[0]
    assert not r["anomalie"] and r["score"] > 0 and r["service_suspect"] == ""


@pytest.mark.parametrize("service", ["api", "auth", "front"])
def test_service_designe_correct(det, ligne_normale, service):
    ligne = ligne_normale(0)
    # Seul ce service dérive, sur TOUTES ses colonnes (comme une vraie panne). Une seule colonne
    # sur 4 déviante peut passer sous le radar d'une Isolation Forest : limite à citer au jury.
    for f in det.features_par_service[service]:
        ligne[f] = ligne[f] * 5 + 80.0
    r = det.score(ligne).iloc[0]
    assert r["anomalie"] and r["service_suspect"] == service
    assert r[f"score_{service}"] < 0
    autres = [s for s in config.SERVICES if s != service]
    assert all(r[f"score_{s}"] > 0 for s in autres)


# --- expliquer ------------------------------------------------------------------

def test_expliquer(det, ligne_anormale):
    e = det.expliquer(ligne_anormale(0, "auth"), "auth")
    assert e["service"] == "auth"
    assert e["intensite"] > 12
    haut = e["features_principales"][0]
    assert haut["feature"] in ("auth__error_4xx", "auth__latency_avg_ms")
    assert haut["ecart_types"] > 12
    assert all(f["feature"].startswith("auth__") for f in e["features_principales"])
    assert len(e["features_principales"]) <= 3


def test_expliquer_baisse_garde_le_signe(det, ligne_normale):
    ligne = ligne_normale(0)
    ligne["api__req_count"] = 0.0
    e = det.expliquer(ligne, "api")
    assert e["features_principales"][0]["feature"] == "api__req_count"
    assert e["features_principales"][0]["ecart_types"] < 0
    assert e["intensite"] > 3  # intensité = valeur absolue


def test_expliquer_plancher_ecart_type(det, ligne_normale):
    # error_5xx est presque toujours 0 : sans plancher à 1, +1 erreur donnerait un écart énorme
    assert det.ecart.min() >= 1.0
    ligne = ligne_normale(0)
    ligne["api__error_5xx"] = det.moyenne["api__error_5xx"] + 1
    assert det.expliquer(ligne, "api")["intensite"] < 2


# --- gravité --------------------------------------------------------------------

@pytest.mark.parametrize("service, intensite, attendu", [
    ("api", 3, "moyenne"),       # 4 x 1 = 4
    ("api", 7, "elevee"),        # 4 x 2 = 8
    ("api", 15, "critique"),     # 4 x 3 = 12
    ("auth", 3, "moyenne"),      # 3 x 1 = 3
    ("auth", 7, "elevee"),       # 3 x 2 = 6
    ("auth", 15, "critique"),    # 3 x 3 = 9
    ("front", 3, "faible"),      # 2 x 1 = 2
    ("front", 7, "moyenne"),     # 2 x 2 = 4
    ("front", 15, "elevee"),     # 2 x 3 = 6
    ("api", 5.99, "moyenne"),    # frontière : < 6 -> niveau 1
    ("api", 6, "elevee"),        # >= 6 -> niveau 2
    ("api", 11.99, "elevee"),
    ("api", 12, "critique"),     # >= 12 -> niveau 3
])
def test_gravite(service, intensite, attendu):
    assert gravite(service, intensite) == attendu


def test_gravite_service_inconnu():
    with pytest.raises(KeyError):
        gravite("inconnu", 5)


# --- IncidentTracker -------------------------------------------------------------

def rejouer(tracker, lignes):
    return [e for e in (tracker.mettre_a_jour(l) for l in lignes) if e]


def test_pas_d_alerte_avant_n_photos_anormales(det, ligne_normale, ligne_anormale):
    t = IncidentTracker(det, n_consecutifs=3)
    lignes = [ligne_normale(0), ligne_anormale(10), ligne_anormale(20)]  # seulement 2 anormales
    assert rejouer(t, lignes) == []
    assert not t.en_incident


def test_alerte_a_la_n_ieme_photo_anormale(det, ligne_normale, ligne_anormale):
    t = IncidentTracker(det, n_consecutifs=3)
    lignes = [ligne_normale(0), ligne_anormale(10), ligne_anormale(20)]
    assert rejouer(t, lignes) == []
    ev = t.mettre_a_jour(ligne_anormale(30))
    assert ev["evenement"] == "incident_debut" and ev["timestamp"] == 30
    assert ev["service"] == "auth"
    assert ev["gravite"] in ("faible", "moyenne", "elevee", "critique")
    assert ev["gravite"] == "critique"  # auth (3) x niveau 3 (écart énorme)
    assert ev["score"] < 0 and ev["intensite"] > 12 and ev["features_principales"]
    assert t.en_incident


def test_pic_isole_ne_declenche_rien(det, ligne_normale, ligne_anormale):
    t = IncidentTracker(det, n_consecutifs=3)
    # N A N N A A N N : jamais 3 anormales d'affilée
    motif = "NANNAANN"
    lignes = [ligne_anormale(i * 10) if c == "A" else ligne_normale(i * 10) for i, c in enumerate(motif)]
    assert rejouer(t, lignes) == []


def test_n_photos_anormales_non_consecutives_ne_declenchent_pas(det, ligne_normale, ligne_anormale):
    t = IncidentTracker(det, n_consecutifs=3)
    lignes = [ligne_anormale(0), ligne_anormale(10), ligne_normale(20), ligne_anormale(30),
              ligne_anormale(40), ligne_normale(50)]
    assert rejouer(t, lignes) == []


def test_service_touche_majoritaire(det, ligne_normale, ligne_anormale):
    # 2 photos désignent api, 1 désigne auth -> api (le plus fréquent sur les 3 dernières)
    t = IncidentTracker(det, n_consecutifs=3)
    events = rejouer(t, [ligne_anormale(0, "api"), ligne_anormale(10, "auth"), ligne_anormale(20, "api")])
    assert len(events) == 1 and events[0]["service"] == "api"


def test_fin_apres_n_photos_normales_d_affilee(det, ligne_normale, ligne_anormale):
    t = IncidentTracker(det, n_consecutifs=3)
    debut = rejouer(t, [ligne_anormale(i * 10) for i in range(3)])
    assert debut[0]["timestamp"] == 20
    # 2 normales puis une anormale : l'incident continue
    assert rejouer(t, [ligne_normale(30), ligne_normale(40), ligne_anormale(50)]) == []
    assert t.en_incident
    # 3 normales d'affilée : fin
    fin = rejouer(t, [ligne_normale(60), ligne_normale(70), ligne_normale(80)])
    assert len(fin) == 1
    assert fin[0] == {"evenement": "incident_fin", "timestamp": 80, "service": "auth", "duree_s": 60}
    assert not t.en_incident


def test_nouvel_incident_apres_la_fin(det, ligne_normale, ligne_anormale):
    t = IncidentTracker(det, n_consecutifs=3)
    lignes = ([ligne_anormale(i * 10) for i in range(3)] + [ligne_normale(100 + i * 10) for i in range(3)]
              + [ligne_anormale(200 + i * 10, "api") for i in range(3)])
    evs = rejouer(t, lignes)
    assert [e["evenement"] for e in evs] == ["incident_debut", "incident_fin", "incident_debut"]
    assert evs[2]["service"] == "api"


def test_n_consecutifs_configurable(det, ligne_anormale):
    t = IncidentTracker(det, n_consecutifs=1)
    assert t.mettre_a_jour(ligne_anormale(0))["evenement"] == "incident_debut"


# --- Sauvegarde / chargement ------------------------------------------------------

def test_save_load_aller_retour(det, normal, tmp_path):
    chemin = tmp_path / "detecteur.joblib"
    det.save(str(chemin))
    charge = AnomalyDetector.load(str(chemin))
    avant, apres = det.score(normal), charge.score(normal)
    assert (avant["score"].values == apres["score"].values).all()
    assert charge.features_par_service == det.features_par_service
    assert charge.nb_photos_entrainement == 300


def test_save_ecrit_la_fiche_json(det, tmp_path):
    chemin = tmp_path / "detecteur.joblib"
    det.save(str(chemin))
    fiche_chemin = tmp_path / "detecteur.json"
    assert fiche_chemin.exists()
    fiche = json.loads(fiche_chemin.read_text(encoding="utf-8"))
    import sklearn
    assert fiche["type_modele"] == "isolation_forest"
    assert fiche["nb_photos_entrainement"] == 300
    assert fiche["version_sklearn"] == sklearn.__version__
    assert fiche["date_utc"].endswith("+00:00")
    assert fiche["features_par_service"] == det.features_par_service
    for f in det.features:
        assert fiche["moyenne"][f] == pytest.approx(float(det.moyenne[f]))
        assert fiche["ecart_type"][f] == pytest.approx(float(det.ecart[f]))
    texte = fiche_chemin.read_text(encoding="utf-8").lower()
    assert "token" not in texte and "password" not in texte and "secret" not in texte


def test_load_avertit_si_version_sklearn_differente(det, tmp_path):
    import joblib
    chemin = tmp_path / "m.joblib"
    joblib.dump({"detecteur": det, "sklearn": "0.0.1"}, chemin)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        AnomalyDetector.load(str(chemin))
    assert any("sklearn" in str(x.message) for x in w)


def test_chaine_complete_avec_la_simulation():
    """Logs bruts simulés -> features -> détecteur -> incident désigné sur le bon service."""
    from app.infrastructure.sources.simulation import SimulationSource
    t0 = 1_700_000_000 // 10 * 10
    d = AnomalyDetector().fit(SimulationSource(seed=1).fetch(t0, t0 + 1800))
    incident = {"service": "auth", "debut_s": 600, "duree_s": 300, "type": "brute_force"}
    photos = SimulationSource(incidents=[incident], seed=2, origine=t0).fetch(t0, t0 + 1200)
    evs = rejouer(IncidentTracker(d), [photos.iloc[[i]] for i in range(len(photos))])
    assert evs[0]["evenement"] == "incident_debut" and evs[0]["service"] == "auth"
