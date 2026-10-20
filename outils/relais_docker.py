"""
RELAIS DOCKER : lit `docker compose logs` sur la MACHINE HÔTE et envoie les logs au détecteur.

Usage typique (PowerShell, depuis la racine de ml-detector) :
    $env:INGEST_TOKEN = "..."            # ou --jeton-file chemin\\vers\\jeton.txt
    python outils\\relais_docker.py --url http://localhost:8000/ingest
    python outils\\relais_docker.py --vers-fichier data\\normal.jsonl    # jeu d'entraînement

POURQUOI UN SCRIPT SUR L'HÔTE ET PAS DANS LE CONTENEUR DU DÉTECTEUR ?
Pour lire les logs des autres conteneurs, il faudrait monter le socket Docker
(/var/run/docker.sock) dans le conteneur du détecteur. Or l'accès à ce socket équivaut à
être root sur l'hôte (on peut lancer n'importe quel conteneur privilégié). Cela contredirait
le durcissement exigé (non-root, cap_drop ALL, read_only...). Ici, c'est l'hôte, qui a déjà
Docker, qui lit les logs et les POSTe ; le détecteur reste sans aucun privilège.

SÉCURITÉ : le jeton n'est JAMAIS un argument de ligne de commande (visible dans la liste des
processus) : fichier (--jeton-file) ou variable INGEST_TOKEN. Il n'est jamais affiché.

Bibliothèque standard + requests uniquement. Aucun code exécuté à l'import.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

# Permet `python outils/relais_docker.py` depuis la racine du projet (config.py, app/...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.sources.docker_logs import parser_ligne_docker  # noqa: E402

TAILLE_LOT = 500          # logs par envoi
INTERVALLE_S = 2.0        # un lot partiel part au plus tard après 2 s
TAMPON_MAX = 50_000       # logs gardés si le détecteur est indisponible (les plus anciens sortent)
BACKOFF_MAX_S = 30.0


def construire_commande(compose_file: Optional[str] = None, projet: Optional[str] = None,
                        depuis: Optional[str] = None) -> list[str]:
    """
    Commande `docker compose logs`, sous forme de liste (pas de shell : marche pareil sous
    Windows et évite toute injection). --since évite de rejouer tout l'historique.
    """
    cmd = ["docker", "compose"]
    if compose_file:
        cmd += ["-f", compose_file]
    if projet:
        cmd += ["-p", projet]
    if depuis is None:
        depuis = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd += ["logs", "-f", "--no-color", "--timestamps", "--since", depuis]
    return cmd


def decouper_en_lots(logs: Iterable[dict], taille: int = TAILLE_LOT) -> list[list[dict]]:
    """Découpe en lots de `taille` logs au plus (le dernier peut être plus petit)."""
    logs = list(logs)
    return [logs[i:i + taille] for i in range(0, len(logs), taille)]


def delai_backoff(essais: int, maximum: float = BACKOFF_MAX_S) -> float:
    """Attente avant le prochain essai : 1, 2, 4, 8... s, plafonnée. Évite de marteler un détecteur en panne."""
    if essais <= 0:
        return 0.0
    return min(maximum, 2.0 ** (essais - 1))


class Emetteur:
    """
    Accumule les logs et les envoie par lots via `envoi(lot) -> bool`.

    - envoi réussi : le lot sort du tampon ;
    - envoi raté   : le lot reste, nouvel essai plus tard (backoff) ;
    - tampon borné : si le détecteur reste en panne, les plus anciens logs sont abandonnés
      plutôt que de saturer la mémoire.
    """

    def __init__(self, envoi: Callable[[list], bool], taille_lot: int = TAILLE_LOT,
                 intervalle_s: float = INTERVALLE_S, tampon_max: int = TAMPON_MAX):
        self.envoi = envoi
        self.taille_lot = taille_lot
        self.intervalle_s = intervalle_s
        self.tampon: deque = deque(maxlen=tampon_max)
        self.essais = 0
        self.prochain_essai = 0.0
        self.dernier_envoi = 0.0

    def ajouter(self, log: dict) -> None:
        self.tampon.append(log)

    def tick(self, maintenant: float) -> None:
        """À appeler souvent : envoie un lot si c'est le moment (lot plein ou 2 s écoulées)."""
        if not self.tampon or maintenant < self.prochain_essai:
            return
        if len(self.tampon) < self.taille_lot and maintenant - self.dernier_envoi < self.intervalle_s:
            return
        self._envoyer_un_lot(maintenant)

    def _envoyer_un_lot(self, maintenant: float) -> bool:
        lot = [self.tampon[i] for i in range(min(self.taille_lot, len(self.tampon)))]
        try:
            ok = self.envoi(lot)
        except Exception:  # un envoi ne doit jamais tuer le relais
            ok = False
        if ok:
            for _ in lot:
                self.tampon.popleft()
            self.essais = 0
            self.prochain_essai = 0.0
            self.dernier_envoi = maintenant
        else:
            self.essais += 1
            self.prochain_essai = maintenant + delai_backoff(self.essais)
        return ok

    def vider(self, maintenant: float) -> None:
        """Dernier envoi à l'arrêt (un seul essai par lot : on ne bloque pas Ctrl+C)."""
        while self.tampon:
            if not self._envoyer_un_lot(maintenant):
                break


def lire_jeton(fichier: Optional[str]) -> str:
    """Jeton depuis un fichier, sinon depuis la variable INGEST_TOKEN. Jamais depuis la ligne de commande."""
    if fichier:
        with open(fichier, encoding="utf-8") as f:
            return f.read().strip()
    return os.getenv("INGEST_TOKEN", "").strip()


def creer_envoi_http(url: str, jeton: str, timeout: float = 10.0) -> Callable[[list], bool]:
    """Fonction d'envoi POST {"logs": [...]} avec Authorization: Bearer. N'affiche jamais le jeton."""
    import requests
    session = requests.Session()
    entetes = {"Authorization": f"Bearer {jeton}"}

    def envoi(lot: list) -> bool:
        try:
            r = session.post(url, json={"logs": lot}, headers=entetes, timeout=timeout)
        except requests.RequestException as e:
            print(f"[relais] détecteur injoignable ({type(e).__name__}), nouvel essai plus tard", file=sys.stderr)
            return False
        if r.status_code in (401, 403):
            print(f"[relais] jeton refusé (HTTP {r.status_code}) : vérifier INGEST_TOKEN", file=sys.stderr)
            return False
        if r.status_code != 200:
            print(f"[relais] HTTP {r.status_code} du détecteur", file=sys.stderr)
            return False
        return True

    return envoi


def creer_envoi_fichier(chemin: str) -> Callable[[list], bool]:
    """Fonction d'envoi qui AJOUTE les logs au fichier JSONL (jeu d'entraînement hors ligne)."""
    def envoi(lot: list) -> bool:
        with open(chemin, "a", encoding="utf-8", newline="\n") as f:
            for log in lot:
                f.write(json.dumps(log, ensure_ascii=False) + "\n")
        return True

    return envoi


def _lecteur(proc: subprocess.Popen, file: "queue.Queue") -> None:
    """Thread : pousse chaque ligne de stdout dans la file (None = fin du flux)."""
    try:
        for ligne in proc.stdout:
            try:
                file.put_nowait(ligne)
            except queue.Full:
                pass  # saturé : on abandonne la ligne plutôt que de bloquer docker
    finally:
        file.put(None)


def executer(args, services: list[str], envoi: Callable[[list], bool]) -> None:
    """Boucle principale : (re)lance docker compose logs et relaie jusqu'à Ctrl+C."""
    emetteur = Emetteur(envoi)
    proc = None
    depuis = None  # première fois : "maintenant" ; après un redémarrage : l'instant de l'arrêt
    try:
        while True:
            cmd = construire_commande(args.compose_file, args.projet, depuis)
            print("[relais] lancement : " + " ".join(cmd), file=sys.stderr)
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"[relais] impossible de lancer docker : {e}", file=sys.stderr)
                proc = None
            if proc is not None:
                file: queue.Queue = queue.Queue(maxsize=50_000)
                threading.Thread(target=_lecteur, args=(proc, file), daemon=True).start()
                fini = False
                while not fini:
                    try:
                        ligne = file.get(timeout=0.5)
                    except queue.Empty:
                        ligne = ""
                    if ligne is None:
                        fini = True
                    elif ligne:
                        log = parser_ligne_docker(ligne, services)
                        if log:
                            emetteur.ajouter(log)
                    emetteur.tick(time.monotonic())
                proc.wait()
            # docker compose logs s'est arrêté (conteneurs recréés, daemon relancé...) : on repart.
            depuis = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print("[relais] flux interrompu, relance dans 3 s", file=sys.stderr)
            fin_attente = time.monotonic() + 3
            while time.monotonic() < fin_attente:
                emetteur.tick(time.monotonic())
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[relais] arrêt demandé", file=sys.stderr)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        emetteur.vider(time.monotonic())


def lire_arguments(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Relaie `docker compose logs` vers le détecteur (à lancer sur l'hôte).")
    p.add_argument("--compose-file", help="fichier docker-compose.yml (sinon celui du dossier courant)")
    p.add_argument("--projet", help="nom du projet Compose (-p)")
    p.add_argument("--url", help="URL du détecteur, ex. http://localhost:8000/ingest")
    p.add_argument("--jeton-file", help="fichier contenant le jeton (sinon variable INGEST_TOKEN)")
    p.add_argument("--vers-fichier", help="écrit les logs en JSONL dans ce fichier au lieu de les envoyer")
    p.add_argument("--services", nargs="*", help="noms de services connus (défaut : config.SERVICES)")
    args = p.parse_args(argv)
    if not args.url and not args.vers_fichier:
        p.error("il faut --url ou --vers-fichier")
    return args


def main(argv: Optional[list] = None) -> int:
    args = lire_arguments(argv)
    if args.services:
        services = args.services
    else:
        import config
        services = list(config.SERVICES)

    if args.vers_fichier:
        envoi = creer_envoi_fichier(args.vers_fichier)
    else:
        jeton = lire_jeton(args.jeton_file)
        if not jeton:
            print("[relais] aucun jeton : utiliser --jeton-file ou la variable INGEST_TOKEN", file=sys.stderr)
            return 2
        envoi = creer_envoi_http(args.url, jeton)
    executer(args, services, envoi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
