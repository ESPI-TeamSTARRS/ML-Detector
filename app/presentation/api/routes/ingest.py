"""Route /ingest : recevoir des logs (MODE PUSH UNIQUEMENT)."""
from typing import List

from fastapi import Header, HTTPException
from pydantic import BaseModel

import config
from app.application.securite import jeton_valide

TAILLE_MAX_LOT = 10000


class LotDeLogs(BaseModel):
    logs: List[dict]


def creer_routes_ingest(app):
    """Ajoute la route /ingest UNIQUEMENT si SOURCE=push (sinon elle n'existe pas)."""
    if config.SOURCE != "push":
        return

    jeton_attendu = config.lire_secret("INGEST_TOKEN")  # lu une fois, jamais journalisé
    if not jeton_attendu:
        raise RuntimeError("SOURCE=push : INGEST_TOKEN (ou INGEST_TOKEN_FILE) doit être défini")

    @app.post("/ingest")
    def ingest(demande: LotDeLogs, authorization: str | None = Header(default=None)):
        if not jeton_valide(authorization, jeton_attendu):
            raise HTTPException(status_code=401, detail="Jeton invalide")
        if len(demande.logs) > TAILLE_MAX_LOT:
            raise HTTPException(status_code=413, detail="Lot trop grand")
        app.state.moteur.source.ajouter(demande.logs)
        return {"ok": True, "recu": len(demande.logs)}
