"""Route /simulation/incident : déclencher un incident (MODE SIMULÉ UNIQUEMENT)."""
from fastapi import HTTPException
from pydantic import BaseModel, Field
import time

import config


class IncidentSimule(BaseModel):
    service: str
    type: str = Field("dos", pattern="^(dos|panne|brute_force)$")
    duree_s: int = Field(120, ge=10, le=1800)


def creer_routes_simulation(app):
    """Ajoute la route /simulation/incident si SOURCE=simule."""
    if config.SOURCE != "simule":
        return

    @app.post("/simulation/incident")
    def declencher_incident(demande: IncidentSimule):
        try:
            return app.state.moteur.source.ajouter_incident(
                demande.service, demande.type, demande.duree_s, time.time())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
