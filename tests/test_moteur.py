"""Tests du moteur temps réel et de l'alerte (sans FastAPI, sans réseau)."""

import pytest

import config
from app.application.alerter import NotificateurRemediation
from app.application.detecter import Moteur
from app.application.securite import jeton_valide
from app.domaine.detecteur import AnomalyDetector
from app.infrastructure.sources.simulation import SimulationSource

T0 = 1_700_000_000


class FauxNotificateur:
    def __init__(self, reussit=True):
        self.recus, self.reussit = [], reussit

    def envoyer(self, evenement):
        self.recus.append(evenement)
        return self.reussit


@pytest.fixture(scope="module")
def detecteur():
    return AnomalyDetector().fit(SimulationSource(seed=1).fetch(T0 - 3600, T0))


def nouveau_moteur(detecteur, source, notif):
    return Moteur(detecteur, source, notif, pas=10, retard=0, n_consecutifs=3, horloge=lambda: T0)


def test_incident_injecte_alerte_puis_retour_normal(detecteur):
    source = SimulationSource(seed=5, origine=T0)
    notif = FauxNotificateur()
    moteur = nouveau_moteur(detecteur, source, notif)
    for i in range(60):  # 10 min de fonctionnement, un cycle toutes les 10 s
        if i == 20:  # à t+200 s, DoS de 2 min sur l'API
            source.ajouter_incident("api", "dos", 120, T0 + 200)
        moteur.cycle(T0 + i * 10)
    types = [e["evenement"] for e in notif.recus]
    assert types == ["incident_debut", "incident_fin"]
    debut = notif.recus[0]
    assert debut["service"] == "api" and debut["id"] and debut["horodatage"]
    assert debut["detecteur"] == "ml-detector"
    assert moteur.instantane()["compteurs"]["alertes_envoyees"] == 2
    assert moteur.instantane()["en_incident"] is False


def test_contrat_alerte_champs_attendus(detecteur):
    """Le contrat est partagé avec la remédiation : on vérifie que les champs ne bougent pas."""
    source = SimulationSource(seed=5, origine=T0)
    notif = FauxNotificateur()
    moteur = nouveau_moteur(detecteur, source, notif)
    source.ajouter_incident("auth", "brute_force", 120, T0)
    for i in range(12):
        moteur.cycle(T0 + i * 10)
    debut = notif.recus[0]
    for champ in [
        "id",
        "detecteur",
        "horodatage",
        "evenement",
        "timestamp",
        "score",
        "gravite",
        "service",
        "intensite",
        "features_principales",
    ]:
        assert champ in debut, champ
    assert debut["evenement"] == "incident_debut" and debut["service"] == "auth"


def test_panne_de_la_source_ne_fait_pas_planter(detecteur):
    class SourceEnPanne:
        def fetch(self, debut, fin):
            raise ConnectionError("Loki injoignable")

    moteur = nouveau_moteur(detecteur, SourceEnPanne(), FauxNotificateur())
    assert moteur.cycle(T0) is None
    etat = moteur.instantane()
    assert etat["compteurs"]["erreurs_source"] == 1 and "Loki" in etat["derniere_erreur"]
    assert not moteur.en_bonne_sante()


def test_meme_photo_traitee_une_seule_fois(detecteur):
    moteur = nouveau_moteur(detecteur, SimulationSource(origine=T0), FauxNotificateur())
    moteur.cycle(T0)
    moteur.cycle(T0 + 3)  # même fenêtre de 10 s
    assert moteur.instantane()["compteurs"]["cycles"] == 1


def test_alerte_non_remise_est_comptee(detecteur):
    source = SimulationSource(seed=5, origine=T0)
    moteur = nouveau_moteur(detecteur, source, FauxNotificateur(reussit=False))
    source.ajouter_incident("auth", "brute_force", 120, T0)
    for i in range(10):
        moteur.cycle(T0 + i * 10)
    assert moteur.instantane()["compteurs"]["alertes_echouees"] == 1


def test_notificateur_jeton_et_nouvel_essai(monkeypatch):
    appels = []

    class Reponse:
        def __init__(self, code):
            self.status_code = code

    def faux_post(url, json, headers, timeout):
        appels.append(headers)
        return Reponse(503 if len(appels) == 1 else 200)  # échoue une fois, puis réussit

    monkeypatch.setattr("app.application.alerter.requests.post", faux_post)
    monkeypatch.setattr("app.application.alerter.time.sleep", lambda s: None)
    assert NotificateurRemediation("http://remediation/alertes", "secret").envoyer({"x": 1})
    assert len(appels) == 2 and appels[0]["Authorization"] == "Bearer secret"


def test_secret_lu_depuis_un_fichier(tmp_path, monkeypatch):
    fichier = tmp_path / "jeton"
    fichier.write_text("depuis-fichier\n")
    monkeypatch.setenv("ALERT_TOKEN_FILE", str(fichier))
    assert config.lire_secret("ALERT_TOKEN") == "depuis-fichier"


def test_jeton_ingest():
    assert jeton_valide("Bearer abc", "abc")
    assert not jeton_valide("Bearer faux", "abc")
    assert not jeton_valide(None, "abc")
    assert not jeton_valide("abc", "abc")  # pas de préfixe Bearer
    assert not jeton_valide("Bearer ", "")  # aucun jeton configuré => tout refusé
