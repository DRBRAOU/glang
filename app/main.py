"""BRAOU Gondi Vaani - FastAPI application entry point."""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.routers import auth, catalog, expressions, imports, public, recordings, reports, users

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gondi_vaani")

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Expert-voiced Gondi listening and expression library - Dr. B. R. Ambedkar Open University.",
    docs_url="/api/docs" if settings.environment == "development" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.environment == "development" else None,
)

for r in (public.router, auth.router, expressions.router, recordings.router, imports.router,
          catalog.router, users.router, reports.router):
    app.include_router(r)


# --------------------------------------------------------------------- security headers
def _frame_ancestors() -> str:
    """Accept `self https://lms.example` or `'self' https://lms.example` from .env."""
    parts = []
    for tok in settings.frame_ancestors.replace('"', "").split():
        bare = tok.strip("'")
        parts.append(f"'{bare}'" if bare in ("self", "none") else bare)
    return " ".join(parts) or "'self'"


def _csp(admin: bool) -> str:
    frame = "'none'" if admin else _frame_ancestors()
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
        f"base-uri 'none'; form-action 'self'; frame-ancestors {frame}"
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    is_admin = path.startswith(("/admin", "/api/admin", "/api/auth"))
    response.headers.setdefault("Content-Security-Policy", _csp(is_admin))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if is_admin:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if path.startswith("/api/"):
        log.info("%s %s %s %.0fms", request.method, path, response.status_code,
                 (time.perf_counter() - start) * 1000)
    return response


# --------------------------------------------------------------------- errors
@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query", "path"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else e.get("msg"))
    return JSONResponse({"detail": "; ".join(msgs) or "Invalid request."}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover - safety net
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "An unexpected server error occurred. It has been logged."}, status_code=500)


# --------------------------------------------------------------------- pages & static
fe = settings.frontend_dir
app.mount("/css", StaticFiles(directory=fe / "css"), name="css")
app.mount("/js", StaticFiles(directory=fe / "js"), name="js")
app.mount("/fonts", StaticFiles(directory=fe / "fonts"), name="fonts")


@app.get("/", include_in_schema=False)
def learner_page():
    return FileResponse(fe / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/index.html", include_in_schema=False)
def learner_page_alias():
    return RedirectResponse("/", status_code=301)


@app.get("/admin", include_in_schema=False)
@app.get("/admin.html", include_in_schema=False)
def admin_page():
    return FileResponse(fe / "admin.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(fe / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz", include_in_schema=False)
def health():
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        raise HTTPException(503, "database unavailable") from exc
    return {"status": "ok"}
