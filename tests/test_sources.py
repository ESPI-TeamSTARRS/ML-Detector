"""Tests des sources de logs : Loki (fausses réponses), push, fichier, logs Docker, relais."""

import json
import sys
import threading
from pathlib import Path

import pytest

from app.infrastructure.sources.docker_logs import parser_ligne_docker
from app.infrastructure.sources.fichier import FichierSource
from app.infrastructure.sources.loki import LokiSource
from app.infrastructure.sources.push import PushSource
from app.infrastructure.sources.simulation import SimulationSource

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "outils"))
import relais_docker  # noqa: E402

T0 = 1_700_000_000
SERVICES = ["api", "auth", "front"]


# ---------------------------------------------------------------- Loki ----
class FausseReponse:
    def __init__(self, corps=None, code=200):
        self.status_code, self._corps, self.text = code, corps, json.dumps(corps)

    def json(self):
        return self._corps


def flux(service, valeurs, label="service"):
    return {"stream": {label: service}, "values": [[str(ns), ligne] for ns, ligne in valeurs]}


def reponse(*flux_):
    return FausseReponse({"status": "success", "data": {"resultType": "streams", "result": list(flux_)}})


@pytest.fixture
def appels(monkeypatch):
    """Remplace requests.get ; les réponses à servir sont dans `pages`, les requêtes reçues dans `vus`."""

    class Etat:
        pages, vus = [], []

    etat = Etat()
    etat.pages, etat.vus = [], []

    def faux_get(url, params=None, timeout=None):
        etat.vus.append({"url": url, "params": params, "timeout": timeout})
        element = etat.pages.pop(0)
        if isinstance(element, Exception):
            raise element
        return element

    monkeypatch.setattr("app.infrastructure.sources.loki.requests.get", faux_get)
    return etat


def test_loki_streams_service_du_label_et_timestamp_de_loki(appels):
    ligne = json.dumps({"level": "INFO", "status_code": 200, "duration_ms": 12})
    appels.pages = [reponse(flux("api", [(T0 * 10**9 + 5, ligne)]))]
    logs = LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)
    assert len(logs) == 1
    assert logs[0]["service"] == "api"  # vient du label
    assert logs[0]["timestamp"] == pytest.approx(T0, rel=1e-9)  # vient de Loki
    assert logs[0]["status_code"] == 200


def test_loki_ne_remplace_pas_service_ni_timestamp_du_json(appels):
    ligne = json.dumps({"service": "auth", "timestamp": "2026-10-20T09:15:20Z", "level": "INFO"})
    appels.pages = [reponse(flux("api", [(T0 * 10**9, ligne)]))]
    log = LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)[0]
    assert log["service"] == "auth"
    assert log["timestamp"] == "2026-10-20T09:15:20Z"


def test_loki_label_service_configurable(appels, monkeypatch):
    monkeypatch.setattr("config.LOKI_LABEL_SERVICE", "container")
    appels.pages = [reponse(flux("front", [(T0 * 10**9, '{"level":"INFO"}')], label="container"))]
    assert LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)[0]["service"] == "front"


def test_loki_ligne_non_json_ignoree(appels):
    appels.pages = [
        reponse(
            flux(
                "api",
                [
                    (T0 * 10**9, "Server started on port 80"),
                    (T0 * 10**9 + 1, '{"level":"INFO"}'),
                    (T0 * 10**9 + 2, "[1,2]"),
                ],
            )
        )
    ]
    logs = LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)
    assert len(logs) == 1 and logs[0]["level"] == "INFO"


def test_loki_selecteur_et_parametres_envoyes(appels, monkeypatch):
    monkeypatch.setattr("config.LOKI_SELECTEUR", '{projet="resilience",service=~".+"}')
    appels.pages = [reponse()]
    LokiSource("http://loki:3100/", timeout=7).lire_logs(T0, T0 + 10)
    p = appels.vus[0]["params"]
    assert p["query"] == '{projet="resilience",service=~".+"}'
    assert p["direction"] == "forward" and p["limit"] == 5000
    assert p["start"] == T0 * 10**9 and isinstance(p["start"], int) and isinstance(p["end"], int)
    assert appels.vus[0]["url"] == "http://loki:3100/loki/api/v1/query_range"
    assert appels.vus[0]["timeout"] == 7


def test_loki_pagination_deux_pages(appels):
    page1 = [(T0 * 10**9 + i, '{"level":"INFO"}') for i in range(5000)]
    page2 = [(T0 * 10**9 + 5000 + i, '{"level":"WARN"}') for i in range(3)]
    appels.pages = [reponse(flux("api", page1)), reponse(flux("api", page2))]
    logs = LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)
    assert len(logs) == 5003
    assert len(appels.vus) == 2
    assert appels.vus[1]["params"]["start"] == T0 * 10**9 + 4999 + 1  # dernier ts + 1 ns


def test_loki_garde_fou_pages(appels):
    def page_pleine(n):
        return reponse(flux("api", [(T0 * 10**9 + n * 5000 + i, '{"level":"INFO"}') for i in range(5000)]))

    appels.pages = [page_pleine(n) for n in range(60)]
    LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)
    assert len(appels.vus) == 50


def test_loki_erreur_http_500(appels):
    appels.pages = [FausseReponse({"error": "boom"}, code=500)]
    with pytest.raises(RuntimeError, match="500"):
        LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)


def test_loki_erreur_reseau(appels):
    import requests

    appels.pages = [requests.ConnectionError("refusé")]
    with pytest.raises(RuntimeError, match="injoignable"):
        LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)


def test_loki_reponse_non_streams(appels):
    appels.pages = [FausseReponse({"data": {"resultType": "matrix", "result": []}})]
    with pytest.raises(RuntimeError, match="matrix"):
        LokiSource("http://loki:3100").lire_logs(T0, T0 + 10)


# ---------------------------------------------------------------- Push ----
def log(ts, **reste):
    return {"timestamp": ts, "service": "api", "level": "INFO", **reste}


def test_push_ajout_et_lecture_fenetre():
    s = PushSource()
    s.ajouter([log(T0), log(T0 + 5), log(T0 + 10)])
    assert [log["timestamp"] for log in s.lire_logs(T0, T0 + 10)] == [T0, T0 + 5]  # fin exclue


def test_push_ignore_logs_sans_timestamp_ou_invalides():
    s = PushSource()
    s.ajouter([{"service": "api"}, "texte", log("pas une date"), log(T0)])
    assert len(s.lire_logs(T0 - 1, T0 + 1)) == 1


def test_push_timestamp_iso():
    s = PushSource()
    s.ajouter([{"service": "api", "timestamp": "2026-10-20T09:15:20.123Z"}])
    assert len(s.lire_logs(1792487700, 1792487800)) == 1


def test_push_retention_oublie_les_vieux():
    s = PushSource(retention_s=60)
    s.ajouter([log(T0), log(T0 + 100)])
    s.lire_logs(T0 + 100, T0 + 110)  # limite = T0+50 : le log T0 est oublié
    assert [e[0] for e in s.tampon] == [T0 + 100]


def test_push_ne_perd_pas_les_logs_futurs():
    s = PushSource(retention_s=60)
    s.ajouter([log(T0 + 500)])
    assert s.lire_logs(T0, T0 + 10) == []  # lecture d'une fenêtre ancienne
    assert len(s.lire_logs(T0 + 500, T0 + 510)) == 1  # le log futur est toujours là


def test_push_plafond_du_tampon():
    s = PushSource(max_logs=100)
    s.ajouter([log(T0 + i) for i in range(250)])
    assert len(s.tampon) == 100
    assert min(e[0] for e in s.tampon) == T0 + 150  # les plus anciens sont sortis


def test_push_thread_safe():
    s = PushSource(retention_s=100_000)  # grande rétention : ce test ne vérifie pas l'oubli

    def ecrire():
        for i in range(200):
            s.ajouter([log(T0 + i)])

    def lire():
        for _ in range(200):
            s.lire_logs(T0, T0 + 1000)

    ts = [threading.Thread(target=ecrire) for _ in range(4)] + [threading.Thread(target=lire) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(s.lire_logs(T0, T0 + 1000)) == 800


# ------------------------------------------------------------- Fichier ----
def test_fichier_jsonl(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text("\n".join([json.dumps(log(T0 + 5)), "pas du json", "", json.dumps(log(T0 + 1))]), encoding="utf-8")
    src = FichierSource(str(f))
    assert [log["timestamp"] for log in src.lire_logs(T0, T0 + 10)] == [T0 + 1, T0 + 5]  # trié
    assert src.etendue() == (T0 + 1, T0 + 5)


def test_fichier_csv_cases_vides(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text(f"timestamp,service,level,status_code\n{T0},api,INFO,200\n{T0 + 1},auth,ERROR,\n", encoding="utf-8")
    logs = FichierSource(str(f)).lire_logs(T0, T0 + 10)
    assert len(logs) == 2
    assert "status_code" not in logs[1]  # case vide = champ absent (pas NaN)


def test_fichier_log_docker(tmp_path):
    f = tmp_path / "x.log"
    f.write_text(
        'api-1  | 2026-10-20T09:15:20.123456789Z {"level":"INFO","status_code":200}\n'
        'auth-1  | 2026-10-20T09:15:21Z {"level":"ERROR","status_code":500}\n'
        "api-1  | 2026-10-20T09:15:22Z Server started\n",
        encoding="utf-8",
    )
    src = FichierSource(str(f))
    debut, fin = src.etendue()
    logs = src.lire_logs(debut, fin + 1)
    assert [log["service"] for log in logs] == ["api", "auth"]
    assert fin - debut == pytest.approx(1 - 0.123456, abs=1e-3)


def test_fichier_log_docker_utf16(tmp_path):
    f = tmp_path / "x.txt"  # ce que produit `>` sous PowerShell 5
    f.write_text('api-1  | {"timestamp": 1700000000, "level":"INFO"}\n', encoding="utf-16")
    assert len(FichierSource(str(f)).lire_logs(T0 - 1, T0 + 1)) == 1


def test_fichier_format_inconnu(tmp_path):
    f = tmp_path / "x.xml"
    f.write_text("<a/>")
    with pytest.raises(ValueError):
        FichierSource(str(f)).lire_logs(0, 1)


def test_fichier_vide(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text("")
    assert FichierSource(str(f)).etendue() == (0.0, 0.0)


# --------------------------------------------------- parser_ligne_docker ----
JSON = '{"level":"INFO","status_code":200,"duration_ms":12}'


@pytest.mark.parametrize(
    "ligne,service,ts",
    [
        (f"api-1  | 2026-10-20T09:15:20.123456789Z {JSON}", "api", "2026-10-20T09:15:20.123456789Z"),
        (f"api-1  | {JSON}", "api", None),
        (f"resilience-auth-2  | 2026-10-20T09:15:20Z {JSON}", "auth", "2026-10-20T09:15:20Z"),
        (f"projet_front_1  | {JSON}\r\n", "front", None),
        (f"oauth-1  | {JSON}", "oauth", None),  # service inconnu : préfixe nettoyé
        (f"monprojet-db-1  | {JSON}", "monprojet-db", None),
    ],
)
def test_parser_docker_cas_valides(ligne, service, ts):
    log = parser_ligne_docker(ligne, SERVICES)
    assert log["service"] == service
    assert log.get("timestamp") == ts
    assert log["status_code"] == 200


def test_parser_docker_ne_remplace_pas_le_json():
    ligne = 'api-1  | 2026-10-20T09:15:20Z {"service":"paiement","timestamp":1700000000}'
    log = parser_ligne_docker(ligne, SERVICES)
    assert log["service"] == "paiement" and log["timestamp"] == 1700000000


@pytest.mark.parametrize(
    "ligne",
    [
        "",
        "   ",
        "\n",
        "api-1  | Server started",
        "api-1  | 2026-10-20T09:15:20Z oups",
        "api-1  | [1,2,3]",
        'api-1  | {"cassé":',
        "texte sans préfixe",
    ],
)
def test_parser_docker_lignes_inexploitables(ligne):
    assert parser_ligne_docker(ligne, SERVICES) is None


# ------------------------------------------------------- relais_docker ----
def test_relais_decoupage_en_lots():
    lots = relais_docker.decouper_en_lots([{"i": i} for i in range(1201)], 500)
    assert [len(lot) for lot in lots] == [500, 500, 201]
    assert relais_docker.decouper_en_lots([], 500) == []


def test_relais_commande():
    cmd = relais_docker.construire_commande("dc.yml", "proj", "2026-10-20T09:00:00Z")
    assert cmd == [
        "docker",
        "compose",
        "-f",
        "dc.yml",
        "-p",
        "proj",
        "logs",
        "-f",
        "--no-color",
        "--timestamps",
        "--since",
        "2026-10-20T09:00:00Z",
    ]
    cmd = relais_docker.construire_commande()
    assert "-f" not in cmd[:3] and cmd[2] == "logs" and cmd[-2] == "--since"


def test_relais_backoff():
    assert [relais_docker.delai_backoff(n) for n in (0, 1, 2, 3, 10)] == [0.0, 1.0, 2.0, 4.0, 30.0]


def test_relais_emetteur_lot_plein_puis_partiel():
    envoyes = []
    e = relais_docker.Emetteur(lambda lot: envoyes.append(len(lot)) or True, taille_lot=3, intervalle_s=2)
    for i in range(4):
        e.ajouter({"i": i})
    e.tick(0.5)  # lot plein (3) part tout de suite
    assert envoyes == [3]
    e.tick(1.0)  # 1 log restant, < 2 s : attend
    assert envoyes == [3]
    e.tick(3.0)
    assert envoyes == [3, 1] and not e.tampon


def test_relais_emetteur_indisponible_backoff_et_tampon_borne():
    etat = {"ok": False, "appels": 0}

    def envoi(lot):
        etat["appels"] += 1
        return etat["ok"]

    e = relais_docker.Emetteur(envoi, taille_lot=2, intervalle_s=0, tampon_max=5)
    for i in range(8):
        e.ajouter({"i": i})
    assert len(e.tampon) == 5 and e.tampon[0] == {"i": 3}  # les plus anciens sont sortis
    e.tick(10.0)  # échec -> prochain essai à 11
    e.tick(10.5)  # trop tôt : pas d'appel
    assert etat["appels"] == 1
    etat["ok"] = True
    e.tick(11.0)
    assert etat["appels"] == 2 and len(e.tampon) == 3 and e.essais == 0


def test_relais_vers_fichier(tmp_path):
    chemin = tmp_path / "out.jsonl"
    envoi = relais_docker.creer_envoi_fichier(str(chemin))
    assert envoi([{"a": 1}, {"b": "é"}]) is True
    assert envoi([{"c": 3}]) is True
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    assert [json.loads(log) for log in lignes] == [{"a": 1}, {"b": "é"}, {"c": 3}]


def test_relais_jeton_jamais_en_argument(tmp_path, monkeypatch):
    args = relais_docker.lire_arguments(["--url", "http://x/ingest", "--jeton-file", "j.txt"])
    assert not hasattr(args, "jeton")
    f = tmp_path / "jeton.txt"
    f.write_text("secret\n")
    assert relais_docker.lire_jeton(str(f)) == "secret"
    monkeypatch.setenv("INGEST_TOKEN", "depuis-env")
    assert relais_docker.lire_jeton(None) == "depuis-env"


# ---------------------------------------------------------- Simulation ----
def test_simulation_incident_aux_bons_instants_et_fenetre_respectee():
    inc = {"service": "api", "debut_s": 600, "duree_s": 300, "type": "dos"}
    src = SimulationSource(incidents=[inc], seed=2, origine=T0)
    logs = src.lire_logs(T0 + 3, T0 + 1203)
    assert all(T0 + 3 <= log["timestamp"] < T0 + 1203 for log in logs)
    df = src.fetch(T0, T0 + 1200)
    assert len(df) == 120
    col = df["api__req_count"]
    assert col.loc[T0 + 590] < 40 and col.loc[T0 + 600] > 100  # début pile à 600 s
    assert col.loc[T0 + 890] > 100 and col.loc[T0 + 900] < 40  # fin pile à 900 s


def test_simulation_erreurs_5xx_coherentes():
    inc = {"service": "api", "debut_s": 0, "duree_s": 100, "type": "panne"}
    logs = SimulationSource(incidents=[inc], seed=1, origine=T0).lire_logs(T0, T0 + 100)
    for log in logs:
        assert (log["status_code"] >= 500) == (log["level"] == "ERROR")
