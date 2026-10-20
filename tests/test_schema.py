"""Tests de app/domaine/schema.py : normalisation des logs bruts."""
import pandas as pd
import pytest

import config
from app.domaine.schema import normaliser

COLONNES = ["timestamp", "service", "level", "status_code", "duration_ms"]
# 2026-10-20T09:15:20Z en secondes depuis 1970
EPOCH = 1792487720.0


def un_log(**champs) -> dict:
    base = {"timestamp": EPOCH, "service": "api", "level": "INFO", "status_code": 200, "duration_ms": 12.5}
    base.update(champs)
    return base


@pytest.mark.parametrize("valeur", [
    "2026-10-20T09:15:20Z",
    "2026-10-20T09:15:20.000Z",
    "2026-10-20T09:15:20+00:00",
    "2026-10-20T11:15:20+02:00",   # même instant, autre fuseau
    "2026-10-20T09:15:20",         # sans fuseau : on suppose UTC (pas l'heure locale du PC)
])
def test_timestamp_iso(valeur):
    df = normaliser([un_log(timestamp=valeur)])
    assert df["timestamp"].iloc[0] == EPOCH


@pytest.mark.parametrize("valeur", [
    EPOCH,                  # secondes
    int(EPOCH),
    int(EPOCH * 1000),      # millisecondes
    int(EPOCH * 10**9),     # nanosecondes
    str(int(EPOCH)),        # texte (CSV)
])
def test_timestamp_epoch(valeur):
    df = normaliser([un_log(timestamp=valeur)])
    assert df["timestamp"].iloc[0] == pytest.approx(EPOCH, abs=1e-3)


def test_colonnes_et_valeurs():
    df = normaliser([un_log()])
    assert list(df.columns) == COLONNES
    ligne = df.iloc[0]
    assert ligne["service"] == "api" and ligne["level"] == "INFO"
    assert ligne["status_code"] == 200 and ligne["duration_ms"] == 12.5


def test_liste_vide_donne_dataframe_vide_avec_colonnes():
    for entree in ([], None):
        df = normaliser(entree)
        assert df.empty and list(df.columns) == COLONNES


def test_tout_rejete_donne_dataframe_vide_avec_colonnes():
    df = normaliser([un_log(service="inconnu")])
    assert df.empty and list(df.columns) == COLONNES


def test_rejet_sans_service():
    log = un_log()
    del log["service"]
    assert normaliser([log, un_log()]).shape[0] == 1


def test_rejet_sans_timestamp_ou_timestamp_illisible():
    sans_ts = un_log()
    del sans_ts["timestamp"]
    logs = [sans_ts, un_log(timestamp="pas une date"), un_log(timestamp=None),
            un_log(timestamp=float("nan")), un_log()]
    assert normaliser(logs).shape[0] == 1


def test_rejet_service_non_surveille_ou_invalide():
    logs = [un_log(service="base_secrete"), un_log(service=None), un_log(service={"x": 1}), un_log(service="auth")]
    df = normaliser(logs)
    assert list(df["service"]) == ["auth"]


def test_log_non_dict_ignore():
    assert normaliser(["texte", 42, un_log()]).shape[0] == 1


def test_correspondance_champs(monkeypatch):
    monkeypatch.setattr("config.CORRESPONDANCE_CHAMPS", {
        "timestamp": "ts_reel", "service": "app_reelle", "status_code": "http_code",
        "duration_ms": "temps", "level": "niveau"})
    brut = {"ts_reel": EPOCH, "app_reelle": "api", "http_code": 503, "temps": 30, "niveau": "error"}
    ligne = normaliser([brut]).iloc[0]
    assert ligne["service"] == "api" and ligne["status_code"] == 503
    assert ligne["duration_ms"] == 30 and ligne["level"] == "ERROR"  # level mis en majuscules


def test_correspondance_prioritaire_sur_noms_usuels(monkeypatch):
    # Si les deux existent, le nom indiqué dans la config gagne.
    monkeypatch.setattr("config.CORRESPONDANCE_CHAMPS", {"status_code": "http_code"})
    ligne = normaliser([un_log(http_code=500, status_code=200)]).iloc[0]
    assert ligne["status_code"] == 500


def test_correspondance_cle_absente_repli_sur_noms_usuels(monkeypatch):
    # Choix documenté : si le champ relié manque dans UN log mais que le nom usuel est là,
    # on garde le log (le jeter créerait une fenêtre faussement calme).
    monkeypatch.setattr("config.CORRESPONDANCE_CHAMPS", {"timestamp": "ts_reel", "service": "app_reelle"})
    df = normaliser([un_log()])
    assert df.shape[0] == 1 and df["service"].iloc[0] == "api"


def test_correspondance_cle_absente_et_pas_de_repli_possible(monkeypatch):
    monkeypatch.setattr("config.CORRESPONDANCE_CHAMPS", {"service": "app_reelle"})
    log = un_log()
    del log["service"]
    assert normaliser([log]).empty


def test_champs_optionnels_absents():
    df = normaliser([{"timestamp": EPOCH, "service": "api"}])
    ligne = df.iloc[0]
    assert ligne["level"] == ""
    assert pd.isna(ligne["status_code"]) and pd.isna(ligne["duration_ms"])


def test_status_et_duree_en_texte_ou_nan_deviennent_numeriques():
    # Cas d'un CSV : tout arrive en texte, les cellules vides en NaN
    logs = [un_log(status_code="503", duration_ms="12.5"),
            un_log(status_code="n/a", duration_ms=""),
            un_log(status_code=float("nan"), duration_ms=None)]
    df = normaliser(logs)
    assert pd.api.types.is_numeric_dtype(df["status_code"])
    assert pd.api.types.is_numeric_dtype(df["duration_ms"])
    assert df["status_code"].iloc[0] == 503 and df["duration_ms"].iloc[0] == 12.5
    assert df["status_code"].iloc[1:].isna().all() and df["duration_ms"].iloc[1:].isna().all()
