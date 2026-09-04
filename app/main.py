"""Point d'entrée de l'application — monolithe modulaire.

PROTOTYPE de démonstration : données entièrement fictives, aucun envoi réel,
aucune donnée patient.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import create_all
from .services import environment as envsvc
from .web.routers import api, ui

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    # Fail-closed : une valeur d'environnement inconnue arrête l'application ici,
    # avec un message lisible, plutôt que de retomber sur « démonstration ».
    try:
        demo = envsvc.is_demonstration()
        deployed = envsvc.is_deployed()
    except envsvc.EnvironmentError_ as exc:
        raise SystemExit(f"Démarrage refusé : {exc}") from None
    app = FastAPI(
        title=settings.app_name,
        description=(
            "Prototype de planification des gardes psychiatriques. "
            "Données entièrement fictives. Aucune donnée patient. "
            "Ni outil institutionnel, ni logiciel de production."
        ),
        version="0.1.0",
        # Swagger/OpenAPI public uniquement en démonstration.
        docs_url="/api/docs" if demo else None,
        redoc_url="/api/redoc" if demo else None,
        openapi_url="/api/openapi.json" if demo else None,
    )
    # Cookie de session Secure dès qu'on est derrière un proxy TLS (staging/prod).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=deployed,
    )

    # Transport : redirection HTTP -> HTTPS (via X-Forwarded-Proto derrière Scalingo)
    # et en-têtes de sécurité. Défini après SessionMiddleware pour être le plus externe.
    @app.middleware("http")
    async def _transport_security(request: Request, call_next):
        proto = request.headers.get("x-forwarded-proto")
        if deployed and proto == "http":
            https_url = request.url.replace(scheme="https")
            return RedirectResponse(str(https_url), status_code=308)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if deployed or proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/api/") or request.cookies.get("session"):
            response.headers.setdefault("Cache-Control", "no-store, private")
        return response

    app.mount(
        "/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static"
    )
    app.include_router(api.router)
    app.include_router(ui.router)

    ui.templates.env.filters["fromjson"] = lambda value: json.loads(value or "{}")

    @app.exception_handler(403)
    async def forbidden(request: Request, exc):  # pragma: no cover - rendu simple
        from fastapi.responses import JSONResponse

        detail = getattr(exc, "detail", "Accès refusé.")
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": detail}, status_code=403)
        return HTMLResponse(
            f"<p>Accès refusé : {detail}</p>"
            "<p><a href='/tableau-de-bord'>Retour au tableau de bord</a></p>",
            status_code=403,
        )

    @app.get("/health/live")
    def health_live():  # pragma: no cover - trivial
        # Vivacité : aucun détail sensible.
        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready():
        # Disponibilité : vérifie l'accès à la base, sans divulguer de détail.
        from fastapi.responses import JSONResponse
        from sqlalchemy import text

        from .db import engine

        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.on_event("startup")
    def _startup() -> None:  # pragma: no cover - initialisation
        # Garde-fou : refuse de démarrer en staging/production avec un secret faible
        # ou des artefacts de démonstration (comptes .invalid) en production.
        envsvc.assert_startup_safe()
        # En démonstration SQLite, on crée le schéma directement. Sur une base
        # gérée (PostgreSQL), le schéma est géré par les migrations Alembic
        # (exécutées une seule fois), afin d'éviter toute course entre workers
        # ou conteneurs lors de la création des types enum.
        if settings.database_url.startswith("sqlite"):
            create_all()

    return app


app = create_app()
