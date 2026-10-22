"""Route /health : vérifier que la boucle tourne."""

from fastapi import Response


def creer_routes_health(app):
    """Ajoute la route /health à l'app FastAPI."""

    @app.get("/health")
    def health(response: Response):
        moteur = app.state.moteur
        ok = moteur.en_bonne_sante()
        if not ok:
            response.status_code = 503
        etat = moteur.instantane()
        return {
            "statut": "ok" if ok else "degrade",
            "source": app.state.config.SOURCE,
            "derniere_maj": etat["derniere_maj"],
            "derniere_erreur": etat["derniere_erreur"],
        }
