FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Brussels

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir "psycopg[binary]>=3.1"

COPY . .

EXPOSE 8000

# Migrations puis démarrage. Le jeu de démonstration se charge séparément :
#   docker compose exec app python scripts/seed_demo.py
# Respecte $PORT (Scalingo/PaaS) avec repli sur 8000 pour docker-compose local.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
