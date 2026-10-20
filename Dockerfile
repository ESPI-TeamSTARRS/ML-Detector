# Image du détecteur ML. Python 3.12 comme le venv de développement (décision D10) :
# le modèle .joblib entraîné dans le venv doit se recharger à l'identique ici.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config.py .
COPY models/ ./models/

# Durcissement : le détecteur n'a besoin d'aucun privilège -> utilisateur non-root.
# (read_only, cap_drop ALL et no-new-privileges se règlent dans le docker-compose.)
RUN useradd --create-home --uid 10001 detecteur
USER detecteur

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"

CMD ["uvicorn", "app.presentation.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
