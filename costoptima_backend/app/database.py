"""
Database engine/session setup for CostOptima.
Uses MySQL/MariaDB by default via PyMySQL. Set DATABASE_URL in .env to point at
your own MySQL server; the default matches the local dev database created by
this project's setup instructions (see README.md).
"""
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app import config  # noqa: F401 — loads .env before DATABASE_URL is read below

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://costoptima:costoptima_pw@localhost:3306/costoptima",
)

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()



def ensure_schema_columns():
    """Small forward-only migration for installations without Alembic.
    create_all() does not add columns to an existing database, so the dynamic
    workbook metadata introduced by the modernized Excel engine is added here.
    """
    additions = {
        "workbook_tabs": {
            "header_rows": "JSON",
            "remarks_column_name": "VARCHAR(255)",
        },
        "workbook_sections": {
            "header_rows": "JSON",
        },
        "workbook_tab_columns": {
            "number_format": "VARCHAR(255)",
            "is_percentage": "BOOLEAN NOT NULL DEFAULT FALSE",
            "formula_config": "JSON",
        },
        "workbook_rows": {
            "version": "INTEGER NOT NULL DEFAULT 1",
        },
        "boms": {
            "extra_input_file_path": "VARCHAR(500)",
        },
        "users": {
            "employee_id": "VARCHAR(255)",
            "company": "VARCHAR(255)",
            "mobile": "VARCHAR(50)",
        },
        "app_config": {
            "`group`": "VARCHAR(100) NOT NULL DEFAULT 'GENERAL'",
            "content": "TEXT",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        if not inspector.has_table("app_config"):
            conn.execute(text("""
                CREATE TABLE app_config (
                    id INTEGER PRIMARY KEY AUTO_INCREMENT,
                    `group` VARCHAR(100) NOT NULL DEFAULT 'GENERAL',
                    `key` VARCHAR(255) NOT NULL,
                    value TEXT NULL,
                    content TEXT NULL,
                    type VARCHAR(50) NOT NULL DEFAULT 'string',
                    description TEXT NULL,
                    is_secret BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by INTEGER NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    modified_by INTEGER NULL,
                    modified_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE KEY uq_app_config_group_key (`group`, `key`),
                    INDEX ix_app_config_id (id),
                    INDEX ix_app_config_group (`group`),
                    INDEX ix_app_config_key (`key`)
                )
            """))

        if inspector.has_table("app_config"):
            existing_cols = {c["name"] for c in inspect(conn).get_columns("app_config")}
            if "group" in existing_cols:
                try:
                    conn.execute(text("UPDATE app_config SET `group` = 'GENERAL' WHERE `group` IS NULL OR `group` = ''"))
                except Exception:
                    pass
            try:
                indexes = {idx["name"] for idx in inspect(conn).get_indexes("app_config")}
                uniques = {uc["name"] for uc in inspect(conn).get_unique_constraints("app_config")}
                names = indexes | uniques
                if "uq_app_config_key" in names:
                    conn.execute(text("ALTER TABLE app_config DROP INDEX uq_app_config_key"))
                if "uq_app_config_group_key" not in names and "group" in existing_cols:
                    conn.execute(text("ALTER TABLE app_config ADD UNIQUE KEY uq_app_config_group_key (`group`, `key`)"))
            except Exception:
                pass
        for table, columns in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspect(conn).get_columns(table)}
            for name, ddl in columns.items():
                if name.strip("`") in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
