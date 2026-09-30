from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine, ensure_schema_columns
from app import models  # noqa: F401 — registers all models on Base.metadata
from app.config import settings
import logging
import os
import sys
import traceback
from fastapi import Request
from fastapi.responses import JSONResponse
from app.routers import auth, users, workbooks, commodity_config, boms, calculations, misc, pdf_converter, app_config

def _configure_logging():
    log_path = os.path.abspath(os.path.join(settings.LOG_DIR, settings.LOG_FILE))
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not any(getattr(handler, "_costoptima_console", False) for handler in root.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        console._costoptima_console = True
        root.addHandler(console)

    if not any(getattr(handler, "_costoptima_file", None) == log_path for handler in root.handlers):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._costoptima_file = log_path
        root.addHandler(file_handler)

    logging.getLogger("costoptima").info("Logging initialized. Terminal logging enabled. Log file: %s", log_path)


_configure_logging()
logger = logging.getLogger("costoptima")
app = FastAPI(
    title="CostOptima API",
    description="CostOptima — modernized cost-calculation backend replacing the "
                "Excel/SharePoint-based cost calculation workflow. "
                "Session-cookie authentication; calculations run synchronously on demand.",
    version="1.0.0",
)

# allow_credentials=True requires explicit origins (not "*") so the browser will
# send/accept the session cookie cross-origin. allow_origin_regex additionally accepts
# any localhost/127.0.0.1/LAN-IP origin on any port, so accessing the app from another
# machine on the network (e.g. http://192.168.1.5:5173) works without editing
# FRONTEND_ORIGIN per client. Tighten this regex before deploying outside a LAN.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|(\d{1,3}\.){3}\d{1,3})(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    ensure_schema_columns()

@app.exception_handler(TypeError)
async def type_error_handler(request: Request, exc: TypeError):
    """Converts an uncaught TypeError — almost always a pydantic response-serialization
    edge case where a field declared `int` actually holds a `float` somewhere in the
    response — into a clean JSON 500 naming the exact request path, instead of letting
    the ASGI connection die mid-response (which is what shows up in the browser as the
    unhelpful "Failed to fetch" rather than a real error). Full traceback is still
    logged server-side so the exact model/field can be pinpointed."""
    logger.error("Unhandled TypeError on %s %s:\n%s", request.method, request.url.path, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "internal_type_error",
            "message": f"Internal error handling {request.method} {request.url.path}. "
                       f"Please share this path plus the server console output.",
            "path": str(request.url.path),
        },
    )

app.include_router(auth.sso_redirect_router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(workbooks.router)
app.include_router(commodity_config.router)
app.include_router(boms.router)
app.include_router(calculations.router)
app.include_router(misc.router)
app.include_router(pdf_converter.router)
app.include_router(app_config.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "CostOptima API", "docs": "/docs"}


