"""SÉCURITÉ : vérification des jetons (testable sans FastAPI)."""

import hmac


def jeton_valide(entete: str | None, attendu: str) -> bool:
    """
    Compare le jeton reçu au jeton attendu.
    hmac.compare_digest : comparaison à temps constant, pour qu'un attaquant ne puisse pas
    deviner le jeton caractère par caractère en mesurant les temps de réponse.
    """
    if not attendu or not entete or not entete.startswith("Bearer "):
        return False
    return hmac.compare_digest(entete[len("Bearer ") :].encode(), attendu.encode())
