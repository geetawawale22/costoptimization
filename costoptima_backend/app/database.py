from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, echo=False, hide_parameters=True, isolation_level="READ COMMITTED")
class ApplicationSession(Session):
    """Only this application's transactions receive audit hooks."""


SessionLocal = sessionmaker(bind=engine, class_=ApplicationSession, expire_on_commit=False)
Base = declarative_base()


from fastapi import Request


def get_db(request: Request):
    from app.audit import context
    context()["route"] = getattr(request.scope.get("route"), "path", "[unmatched]")
    with SessionLocal() as db:
        try:
            yield db
        except Exception:
            db.rollback()
            raise
from app.audit import install
install(ApplicationSession)
