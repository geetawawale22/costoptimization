from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.config import get_settings
from app.api import auth, users, access, settings

from contextlib import asynccontextmanager
from app.observability import technical


@asynccontextmanager
async def lifespan(app):
    from app.audit import signing_key
    signing_key()
    technical("application.startup")
    try:
        yield
    finally:
        technical("application.shutdown")


app = FastAPI(title="Cost Pilot", lifespan=lifespan)


@app.middleware("http")
async def secure_requests(request: Request, call_next):
    if request.url.path.startswith("/api") and request.url.path not in {"/api/auth/sso/callback", "/sso"} and request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("origin", "") not in get_settings().origins or request.headers.get("x-costoptima-request") != "1":
            return JSONResponse(status_code=403, content={"detail": "Untrusted request origin or missing request header"})
        if request.method in {"POST", "PUT", "PATCH"} and request.url.path != "/api/auth/logout" and request.headers.get("content-type", "").split(";", 1)[0].lower() not in ({"application/x-www-form-urlencoded"} if request.url.path == "/api/auth/login" else {"multipart/form-data"} if (request.url.path.startswith("/api/sources/") and request.url.path.endswith("/import/preview")) or request.url.path in {"/api/calculations/development/runs", "/api/converters/ftg/process"} else {"application/json"}):
            return JSONResponse(status_code=415, content={"detail": "Use application/json"})
    if request.url.path in {"/api/auth/sso/callback", "/api/auth/sso/login", "/sso"}:
        from urllib.parse import parse_qsl, urlencode, unquote
        raw_query = request.scope.get("query_string", b"").decode("utf-8", errors="replace")
        values = dict(parse_qsl(raw_query, keep_blank_values=True))
        # Original CC Services opaque tokens may contain literal plus signs.
        for pair in raw_query.split("&"):
            key, separator, value = pair.partition("=")
            if separator and unquote(key) in auth.TOKEN_KEYS:
                values[unquote(key)] = unquote(value)
        request.state.sso_query = values
        request.scope["query_string"] = urlencode({k: v for k, v in values.items() if k not in auth.TOKEN_KEYS}).encode()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(IntegrityError)
async def conflict(request: Request, exc):
    from app.audit import context
    technical("database.conflict", request_id=context().get("request_id"), error_category=type(exc).__name__)
    return JSONResponse(status_code=409, content={"detail": "This change conflicts with an existing record"})


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc):
    from app.audit import context
    technical("database.error", request_id=context().get("request_id"), error_category=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database operation unavailable. No automatic retry was performed."})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(access.router)
app.include_router(settings.router)
app.include_router(settings.config_router)
# Original CC Services callback alias; one shared callback implementation.
app.add_api_route("/sso", auth.sso_callback, methods=["GET", "POST"], include_in_schema=False)


# Added last so tracing surrounds origin checks and all API handlers.
from app.observability import RequestTracing, technical
from app.api import audit as audit_api
app.include_router(audit_api.router)
app.add_middleware(RequestTracing)


from app.api import masters as master_api
app.include_router(master_api.router)

from app.api import calculations as calculation_api
app.include_router(calculation_api.router)

from app.api import formulas as formulas_api
app.include_router(formulas_api.router)

from app.api import converters as converters_api
app.include_router(converters_api.router)
