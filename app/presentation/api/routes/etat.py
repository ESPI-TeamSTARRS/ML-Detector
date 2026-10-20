"""Route /etat : état courant du détecteur."""


def creer_routes_etat(app):
    """Ajoute la route /etat à l'app FastAPI."""

    @app.get("/etat")
    def etat():
        return app.state.moteur.instantane()
