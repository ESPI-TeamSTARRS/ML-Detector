"""
Tests de app/domaine/features.py.

Jeu de logs écrit à la main (service "a", fenêtres de 10 s à partir de T=1000) :

  fenêtre 1000 : t=1000.0  200  INFO   100 ms
                 t=1004.5  404  WARN   200 ms
                 t=1009.99 503  ERROR  300 ms
  fenêtre 1010 : t=1010.0  200  INFO    50 ms     <- pile à t+10 : fenêtre SUIVANTE
  fenêtre 1020 : (aucun log)
  fenêtre 1030 : t=1031    500  ERROR  400 ms
                 t=1038    502  FATAL  600 ms
  service "b"  : un seul log en 1000.
"""

import pytest

from app.domaine import features
from app.domaine.features import construire_tableau
from app.domaine.schema import normaliser

T = 1000


def log(ts, code, level, ms, service="a"):
    return {"timestamp": ts, "service": service, "level": level, "status_code": code, "duration_ms": ms}


@pytest.fixture
def tableau(services_mini):
    logs = normaliser(
        [
            log(1000.0, 200, "INFO", 100),
            log(1004.5, 404, "WARN", 200),
            log(1009.99, 503, "ERROR", 300),
            log(1010.0, 200, "INFO", 50),
            log(1031, 500, "ERROR", 400),
            log(1038, 502, "FATAL", 600),
            log(1002, 200, "INFO", 10, service="b"),
        ]
    )
    return construire_tableau(logs, T, T + 40)


def test_forme_et_noms_de_colonnes(tableau):
    assert list(tableau.index) == [1000, 1010, 1020, 1030]
    assert list(tableau.columns) == [
        "a__req_count",
        "a__error_4xx",
        "a__error_5xx",
        "a__error_logs",
        "a__latency_avg_ms",
        "a__latency_p95_ms",
        "b__req_count",
    ]


@pytest.mark.parametrize(
    "colonne, attendu",
    [
        ("a__req_count", [3, 1, 0, 2]),
        ("a__error_4xx", [1, 0, 0, 0]),
        ("a__error_5xx", [1, 0, 0, 2]),
        ("a__error_logs", [1, 0, 0, 2]),  # ERROR + FATAL comptent, WARN non
        ("a__latency_avg_ms", [200, 50, 50, 500]),  # (100+200+300)/3 ; 50 ; précédent ; (400+600)/2
        ("a__latency_p95_ms", [290, 50, 50, 590]),  # 200+0.9*100 ; 50 ; précédent ; 400+0.95*200
        ("b__req_count", [1, 0, 0, 0]),
    ],
)
def test_valeurs_de_chaque_feature(tableau, colonne, attendu):
    obtenu = tableau[colonne].tolist()
    assert len(obtenu) == len(attendu)
    for o, a in zip(obtenu, attendu, strict=True):
        assert o == pytest.approx(a)


def test_compteur_sans_log_vaut_zero(tableau):
    assert tableau.loc[1020, "a__req_count"] == 0
    assert tableau.loc[1020, "a__error_5xx"] == 0


def test_latence_sans_log_reprend_la_valeur_precedente(tableau):
    assert tableau.loc[1020, "a__latency_avg_ms"] == tableau.loc[1010, "a__latency_avg_ms"]


def test_latence_sans_valeur_precedente_vaut_zero(services_mini):
    # Limite connue : au tout début de la plage, rien à reprendre -> 0.
    logs = normaliser([log(1015, 200, "INFO", 80)])
    t = construire_tableau(logs, T, T + 30)
    assert t["a__latency_avg_ms"].tolist() == [0.0, 80.0, 80.0]


def test_fenetre_semi_ouverte(services_mini):
    # 1009.99 -> fenêtre 1000 ; 1010.0 pile -> fenêtre 1010.
    logs = normaliser([log(1009.99, 200, "INFO", 1), log(1010.0, 200, "INFO", 1)])
    t = construire_tableau(logs, T, T + 20)
    assert t["a__req_count"].tolist() == [1, 1]


def test_logs_hors_plage_ignores(services_mini):
    logs = normaliser([log(999.9, 200, "INFO", 1), log(1020.0, 200, "INFO", 1), log(1005, 200, "INFO", 1)])
    t = construire_tableau(logs, T, T + 20)
    assert t["a__req_count"].tolist() == [1, 0]


def test_grille_alignee_et_sans_fenetre_incomplete(services_mini):
    # debut non aligné -> arrondi vers le bas ; la fenêtre qui commence à `fin` n'est pas incluse
    t = construire_tableau(normaliser([]), 1003, 1027)
    assert list(t.index) == [1000, 1010]
    t = construire_tableau(normaliser([]), 1000, 1030)
    assert list(t.index) == [1000, 1010, 1020]


def test_plage_vide(services_mini):
    t = construire_tableau(normaliser([]), 1000, 1000)
    assert t.empty


def test_aucun_log_donne_zeros(services_mini):
    t = construire_tableau(normaliser([]), T, T + 20)
    assert (t == 0).all().all() and t.shape == (2, 7)


def test_pas_different(services_mini):
    logs = normaliser([log(1004, 200, "INFO", 1), log(1005, 200, "INFO", 1)])
    t = construire_tableau(logs, T, T + 10, pas=5)
    assert list(t.index) == [1000, 1005]
    assert t["a__req_count"].tolist() == [1, 1]


def test_feature_inconnue_leve_une_erreur_claire(monkeypatch):
    monkeypatch.setattr("config.SERVICES", {"a": {"criticite": 1, "features": ["req_count", "latence_inventee"]}})
    with pytest.raises(ValueError, match="latence_inventee"):
        construire_tableau(normaliser([]), T, T + 10)


def test_registre_contient_les_six_features():
    assert set(features._REGISTRE) >= {
        "req_count",
        "error_4xx",
        "error_5xx",
        "error_logs",
        "latency_avg_ms",
        "latency_p95_ms",
    }
    assert features._REGISTRE["req_count"]["trou"] == 0
    assert features._REGISTRE["latency_avg_ms"]["trou"] == "precedent"


def test_logs_non_alignes_sont_ranges_dans_la_bonne_fenetre(services_mini):
    # Régression : avant correction, les timestamps non alignés (cas réel) n'étaient pas rangés
    logs = normaliser([log(1001.3, 200, "INFO", 1), log(1007.7, 200, "INFO", 1), log(1013.1, 200, "INFO", 1)])
    t = construire_tableau(logs, T, T + 20)
    assert t["a__req_count"].tolist() == [2, 1]
