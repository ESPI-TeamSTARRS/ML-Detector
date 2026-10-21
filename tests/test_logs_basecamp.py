"""
Tests sur le VRAI format de logs de l'équipe DEV (services basecamp-*).

Ces logs n'ont pas de code HTTP : le résultat est dans `outcome` (success / denied...), la durée
n'existe que sur certains événements, et `level` est en minuscules ("info", "warning").
Les exemples ci-dessous reprennent ceux envoyés par DEV (valeurs fictives).
"""
import json

import pytest

import config
from app.domaine.features import construire_tableau
from app.domaine.schema import normaliser

T0 = 1_792_515_720  # multiple de 10, tombe dans la fenêtre du log shop ci-dessous

SHOP_OK = ('{"timestamp":"2026-10-20T17:02:11.482Z","service":"basecamp-shop","level":"info",'
           '"event":"cart.quoted","correlation_id":"7c1f0e52","outcome":"success","duration_ms":12,'
           '"attributes":{"line_count":3,"total_cents":18970}}')
SHOP_REFUSE = ('{"timestamp":"2026-10-20T17:02:40.019Z","service":"basecamp-shop","level":"warning",'
               '"event":"cart.quote_rejected","correlation_id":"b3a9d7e1","outcome":"denied",'
               '"reason":"quantity_out_of_range","attributes":{"line_count":2}}')
PAIEMENT_REFUSE = ('{"timestamp":"2026-10-20T17:03:05.730Z","service":"basecamp-payment","level":"warning",'
                   '"event":"payment.intent_rejected","correlation_id":"e4d2a1c8","outcome":"denied",'
                   '"reason":"amount_exceeds_limit","actor_type":"service","actor_id":"shop",'
                   '"resource_type":"payment_intent","attributes":{"amount_cents":2500000,"currency":"EUR"}}')
AUTH_ECHEC = ('{"timestamp":"2026-10-20T17:04:22.208Z","service":"basecamp-auth","level":"warning",'
              '"event":"auth.login_failed","correlation_id":"1f8b6c3d","outcome":"denied",'
              '"reason":"invalid_credentials","attributes":{"email_hash":"9f2c1ab07d3e4c55"}}')
LOGS = [json.loads(x) for x in (SHOP_OK, SHOP_REFUSE, PAIEMENT_REFUSE, AUTH_ECHEC)]


@pytest.fixture(autouse=True)
def vrais_services(monkeypatch, services_reels):
    """Remet les VRAIS services de config.py (le conftest impose sinon un jeu de services de test)."""
    monkeypatch.setattr("config.SERVICES", {k: dict(v) for k, v in services_reels.items()})


def test_les_services_de_config_sont_ceux_des_logs(services_reels):
    # Garde-fou : config.py doit nommer les services exactement comme le champ "service" des logs de DEV.
    assert set(services_reels) == {"basecamp-shop", "basecamp-payment", "basecamp-auth"}


def test_normalisation_des_quatre_exemples():
    df = normaliser(LOGS)
    assert len(df) == 4
    assert list(df["service"]) == ["basecamp-shop", "basecamp-shop", "basecamp-payment", "basecamp-auth"]
    assert list(df["level"]) == ["INFO", "WARNING", "WARNING", "WARNING"]   # mis en majuscules
    assert list(df["outcome"]) == ["success", "denied", "denied", "denied"]
    assert df["status_code"].isna().all()                                   # pas de code HTTP
    assert df["duration_ms"].iloc[0] == 12 and df["duration_ms"].iloc[1:].isna().all()


def test_aucun_champ_libre_conserve():
    # correlation_id, event, reason, attributes (email_hash...) ne doivent pas entrer dans le tableau.
    df = normaliser(LOGS)
    assert set(df.columns) == {"timestamp", "service", "level", "status_code", "duration_ms", "outcome"}


def test_features_comptent_evenements_refus_et_non_succes():
    # Les 2 logs shop tombent dans des fenêtres différentes ; on regroupe sur une grille couvrant tout.
    df = normaliser(LOGS)
    debut = int(df["timestamp"].min() // 10 * 10)
    fin = int(df["timestamp"].max() // 10 * 10) + 10
    tab = construire_tableau(df, debut, fin)
    assert tab["basecamp-shop__req_count"].sum() == 2
    assert tab["basecamp-shop__denied_count"].sum() == 1
    assert tab["basecamp-shop__non_success_count"].sum() == 1
    assert tab["basecamp-payment__denied_count"].sum() == 1
    assert tab["basecamp-auth__denied_count"].sum() == 1
    assert tab["basecamp-auth__req_count"].sum() == 1


def test_error_logs_ne_compte_que_les_niveaux_graves():
    logs = [dict(LOGS[0], level="error"), dict(LOGS[1], level="warning"), dict(LOGS[0], level="critical")]
    df = normaliser(logs)
    tab = construire_tableau(df, int(df["timestamp"].min() // 10 * 10), int(df["timestamp"].max() // 10 * 10) + 10)
    assert tab["basecamp-shop__error_logs"].sum() == 2


def test_valeur_outcome_inattendue_est_vue_comme_non_succes():
    logs = [dict(LOGS[0], outcome="timeout")]
    df = normaliser(logs)
    tab = construire_tableau(df, int(df["timestamp"].min() // 10 * 10), int(df["timestamp"].max() // 10 * 10) + 10)
    assert tab["basecamp-shop__non_success_count"].sum() == 1
    assert tab["basecamp-shop__denied_count"].sum() == 0


def test_outcome_absent_ne_plante_pas():
    sans = {k: v for k, v in LOGS[0].items() if k != "outcome"}
    df = normaliser([sans])
    assert df["outcome"].iloc[0] == ""
    tab = construire_tableau(df, int(df["timestamp"].min() // 10 * 10), int(df["timestamp"].max() // 10 * 10) + 10)
    assert tab["basecamp-shop__req_count"].sum() == 0   # ni code HTTP ni outcome : pas un événement


def test_une_ligne_nginx_sans_outcome_n_est_pas_un_evenement_applicatif():
    # Une ligne d'accès nginx a un status_code : elle compte toujours comme requête.
    df = normaliser([{"timestamp": LOGS[0]["timestamp"], "service": "basecamp-shop", "status_code": 200}])
    tab = construire_tableau(df, int(df["timestamp"].min() // 10 * 10), int(df["timestamp"].max() // 10 * 10) + 10)
    assert tab["basecamp-shop__req_count"].sum() == 1
