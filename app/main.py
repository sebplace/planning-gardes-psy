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
from .web.routers import api, ui

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=(
            "Prototype de planification des gardes psychiatriques. "
            "Données entièrement fictives. Aucune donnée patient. "
            "Ni outil institutionnel, ni logiciel de production."
        ),
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, same_site="lax", https_only=False
    )
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

    @app.on_event("startup")
    def _startup() -> None:  # pragma: no cover - initialisation
        create_all()

    return app


app = create_app()
