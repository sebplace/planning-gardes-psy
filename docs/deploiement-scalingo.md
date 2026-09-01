# Déploiement Scalingo — planning des gardes psychiatriques

Statut : guide. Données fictives uniquement. Ce projet gère du **personnel et de
l'organisation de service** (pas de données patients) : le régime HDS n'est
a priori PAS requis, la région standard `osc-fr1` convient. À faire confirmer
par le DPO.

## Voie retenue : buildpack Python (déploiement par archive)
La plateforme construit l'app à partir de `requirements.txt` + `Procfile`.
Un `Dockerfile` est présent pour l'usage local (docker-compose) ; il est
**exclu de l'archive de déploiement** pour que Scalingo utilise le buildpack
Python et non le builder Docker.

### Fichiers ajoutés pour Scalingo
- `Procfile` : `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT` et
  `release: alembic upgrade head` (migrations jouées au déploiement).
- `.python-version` : `3.12`.
- `requirements.txt` : ajout de `psycopg[binary]` (pilote PostgreSQL, roues
  précompilées, aucun compilateur requis).
- `app/config.py` : reprise automatique de `SCALINGO_POSTGRESQL_URL` /
  `DATABASE_URL`, normalisation du schéma en `postgresql+psycopg://` et ajout de
  `sslmode=require`.

### Pré-requis côté client
- App Scalingo (région `osc-fr1`) + addon **PostgreSQL** (un plan Starter suffit
  pour la démo).
- Variables d'environnement : `GARDES_SECRET_KEY` (secret aléatoire long),
  `GARDES_ENVIRONMENT=production`. `SCALINGO_POSTGRESQL_URL` est fourni par l'addon.

### Étapes (une fois le CLI authentifié)
```powershell
# 1. Variables
scalingo --region osc-fr1 --app <app> env-set GARDES_ENVIRONMENT=production GARDES_SECRET_KEY=<secret>

# 2. Archive SANS Dockerfile, .env, base locale, venv (l'app dans un dossier de
#    premier niveau unique 'app-src/'), puis :
scalingo --region osc-fr1 --app <app> deploy app-src.tar.gz v1
#    -> phase release = alembic upgrade head ; phase web = uvicorn sur $PORT

# 3. Données fictives (une fois)
scalingo --region osc-fr1 --app <app> run "python scripts/seed_demo.py"

# 4. Vérifs
#    Ouvrir https://<app>.osc-fr1.scalingo.io  (page d'accueil + /api/docs)
```

### Rappels
- La navigation par URL ne donne aucun droit : l'autorisation reste serveur.
- Aucune donnée réelle avant validation DPO. La bannière « aucune donnée patient »
  reste affichée.
- SQLite n'est utilisé qu'en local ; en production c'est l'addon PostgreSQL
  (persistant), jamais un fichier SQLite (éphémère sur PaaS).
- Durcissement production à prévoir : cookies de session `https_only`, secret
  fort, journalisation, sauvegardes testées.
