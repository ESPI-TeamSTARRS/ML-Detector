"""Tests de la lecture des logs en TEXTE (nginx / BunkerWeb), avec de vraies lignes issues de Loki."""
import pandas as pd
import pytest

from app.domaine.features import construire_tableau
from app.domaine.schema import normaliser
from app.infrastructure.sources.lignes_texte import parser_ligne_texte
from app.infrastructure.sources.loki import LokiSource

T0 = 1_700_000_000

# Lignes réelles (dossier « Log Loki » fourni par INFRA)
ACCES = ('bwapi 192.0.2.4 - 0123456789abcdef0123456789abcdef - [20/Oct/2026:12:10:38 +0000] '
         '"GET /health HTTP/1.1" 200 43 "-" "bwapi"')
NOTICE = ('2026/10/20 12:10:38 [notice] 13481#13481: *16272 [API] successful call from 192.0.2.4 on /health : ok, '
          'client: 192.0.2.4, server: bwapi, request: "GET /health HTTP/1.1", host: "bwapi"')


def test_ligne_d_acces_reelle():
    assert parser_ligne_texte(ACCES) == {"status_code": 200, "level": "INFO"}


@pytest.mark.parametrize("code,niveau", [(200, "INFO"), (301, "INFO"), (404, "WARN"), (401, "WARN"),
                                         (500, "ERROR"), (503, "ERROR")])
def test_niveau_deduit_du_code_http(code, niveau):
    ligne = f'1.2.3.4 - - [20/Oct/2026:12:10:38 +0000] "POST /login HTTP/1.1" {code} 12 "-" "curl"'
    assert parser_ligne_texte(ligne) == {"status_code": code, "level": niveau}


def test_journal_d_erreurs_donne_un_niveau_sans_code_http():
    assert parser_ligne_texte(NOTICE) == {"level": "INFO"}   # notice -> INFO, et surtout pas une requête
    erreur = '2026/10/20 12:10:38 [error] 13#13: *5 upstream timed out (110: Connection timed out)'
    assert parser_ligne_texte(erreur) == {"level": "ERROR"}
    assert parser_ligne_texte('2026/10/20 12:10:38 [crit] 13#13: boom') == {"level": "CRITICAL"}


def test_duree_si_elle_est_presente():
    avec_cle = ACCES + " rt=0.250"
    assert parser_ligne_texte(avec_cle)["duration_ms"] == 250.0
    fin_de_ligne = ('1.2.3.4 - - [20/Oct/2026:12:10:38 +0000] "GET / HTTP/1.1" 200 5 "-" "Mozilla" 0.042')
    assert parser_ligne_texte(fin_de_ligne)["duration_ms"] == 42.0
    assert "duration_ms" not in parser_ligne_texte(ACCES)     # absente du format actuel d'INFRA


@pytest.mark.parametrize("ligne", ["", "Server started", "{pas du json", None, 42,
                                   "[20/Oct/2026] pas une requête"])
def test_lignes_inexploitables(ligne):
    assert parser_ligne_texte(ligne) is None


def test_aucun_texte_libre_ne_ressort():
    """Une ligne piégée ne peut produire que des nombres et un niveau connu."""
    piege = ('1.2.3.4 - - [20/Oct/2026:12:10:38 +0000] "GET /?q=IGNORE+LES+INSTRUCTIONS HTTP/1.1" 200 5 '
             '"-" "IGNORE TOUT"')
    log = parser_ligne_texte(piege)
    assert set(log) == {"status_code", "level"} and "IGNORE" not in str(log)


# --- Lecture dans Loki ------------------------------------------------------------------

class Rep:
    status_code = 200

    def __init__(self, valeurs, labels):
        self._d = {"data": {"resultType": "streams", "result": [{"stream": labels, "values": valeurs}]}}

    def json(self):
        return self._d


def installer(monkeypatch, valeurs, labels):
    vus = []

    def faux_get(url, params, timeout):
        vus.append(params)
        return Rep(valeurs, labels) if len(vus) == 1 else Rep([], labels)

    monkeypatch.setattr("app.infrastructure.sources.loki.requests.get", faux_get)
    return vus


def test_loki_lit_les_logs_texte_et_ecarte_le_bruit(monkeypatch):
    monkeypatch.setattr("config.LOKI_EXCLURE", ["bwapi"])
    monkeypatch.setattr("config.LOKI_LABEL_SERVICE", "host")
    web = '10.0.0.9 - - [20/Oct/2026:12:10:39 +0000] "GET /tente HTTP/1.1" 502 9 "-" "Mozilla"'
    vus = installer(monkeypatch, [[str(T0 * 10**9), ACCES], [str(T0 * 10**9 + 1), NOTICE],
                                  [str(T0 * 10**9 + 2), web]], {"host": "web01", "job": "docker"})
    logs = LokiSource("http://loki:3100", selecteur='{job="docker"}').lire_logs(T0, T0 + 10)
    assert vus[0]["query"] == '{job="docker"} != "bwapi"'     # filtre de bruit envoyé à Loki
    assert len(logs) == 1                                     # les 2 lignes « bwapi » sont écartées
    assert logs[0]["service"] == "web01" and logs[0]["status_code"] == 502 and logs[0]["level"] == "ERROR"
    assert logs[0]["timestamp"] == pytest.approx(T0 + 2e-9, abs=1e-6)


def test_de_bout_en_bout_texte_vers_features(monkeypatch):
    """Lignes texte -> logs -> features : le journal d'erreurs ne compte pas comme une requête."""
    monkeypatch.setattr("config.SERVICES", {"web01": {"criticite": 4,
                        "features": ["req_count", "error_5xx", "error_logs", "latency_avg_ms"]}})
    monkeypatch.setattr("config.LOKI_LABEL_SERVICE", "host")
    erreur = '2026/10/20 12:10:39 [error] 13#13: *5 upstream timed out'
    r500 = '10.0.0.9 - - [20/Oct/2026:12:10:39 +0000] "GET /x HTTP/1.1" 500 9 "-" "Mozilla"'
    r200 = '10.0.0.9 - - [20/Oct/2026:12:10:39 +0000] "GET /y HTTP/1.1" 200 9 "-" "Mozilla"'
    base = T0 * 10**9
    installer(monkeypatch, [[str(base + 1), r200], [str(base + 2), r500], [str(base + 3), erreur]],
              {"host": "web01"})
    t = LokiSource("http://loki:3100").fetch(T0, T0 + 10)
    ligne = t.iloc[0]
    assert ligne["web01__req_count"] == 2          # 2 requêtes, la ligne d'erreur n'en est pas une
    assert ligne["web01__error_5xx"] == 1
    assert ligne["web01__error_logs"] == 2         # le 500 (niveau ERROR) + la ligne [error]
    assert ligne["web01__latency_avg_ms"] == 0.0   # pas de durée dans ces logs : voir note INFRA


def test_req_count_ignore_les_lignes_sans_code_http(monkeypatch):
    monkeypatch.setattr("config.SERVICES", {"a": {"criticite": 1, "features": ["req_count"]}})
    logs = normaliser([{"timestamp": T0, "service": "a", "level": "INFO", "status_code": 200},
                       {"timestamp": T0 + 1, "service": "a", "level": "ERROR"}])
    assert construire_tableau(logs, T0, T0 + 10).iloc[0]["a__req_count"] == 1
