"""Install PCM definition without modifying users, permissions or master rates.

python -m app.seed_pcm                  # validate and print summary, no DB connection
python -m app.seed_pcm --sql data/pcm_reference_seed.sql
python -m app.seed_pcm --apply          # insert only; refuses to overwrite configuration
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, default=Path(__file__).parent.parent / "data/pcm_reference_definition.json")
    parser.add_argument("--sql", type=Path, help="Write idempotent MySQL/MariaDB INSERT (no connection)")
    parser.add_argument("--apply", action="store_true", help="Insert into the configured database")
    args = parser.parse_args()
    from app.pcm.engine import validate_definition, digest
    definition = json.loads(args.definition.read_text(encoding="utf-8"))
    validate_definition(definition)
    serialized = json.dumps(definition, ensure_ascii=False)
    print(f"Validated {len(definition['profiles'])} profiles; SHA256 {digest(definition)}")
    if args.sql:
        # UTF-8 hex avoids SQL-mode-dependent quote/backslash interpretation.
        payload = serialized.encode("utf-8").hex()
        sql = ("-- PCM formulas/configuration only. Existing configuration is never overwritten.\n"
               "-- Requires the existing CostOptima app_config table.\nSTART TRANSACTION;\n"
               "INSERT INTO app_config (`group`, `key`, value, type, description, is_secret, is_active, is_deleted, created_at, modified_at)\n"
               f"SELECT 'PCM_ENGINE', 'definition', CONVERT(0x{payload} USING utf8mb4), 'json', 'PCM database calculation definition', FALSE, TRUE, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP\n"
               "WHERE NOT EXISTS (SELECT 1 FROM app_config WHERE `group`='PCM_ENGINE' AND `key`='definition');\nCOMMIT;\n")
        args.sql.parent.mkdir(parents=True, exist_ok=True)
        args.sql.write_text(sql, encoding="utf-8")
        print(f"Wrote {args.sql}")
    if args.apply:
        from app.database import SessionLocal, engine
        from app.models.app_config import AppConfig
        from app.models.pcm_run import PcmRunSnapshot
        PcmRunSnapshot.__table__.create(bind=engine, checkfirst=True)
        with SessionLocal() as db:
            existing = db.query(AppConfig).filter(AppConfig.group == "PCM_ENGINE", AppConfig.key == "definition").first()
            if existing:
                print("Existing definition preserved. Edit through Settings > App Config to publish a revision.")
                return
            db.add(AppConfig(group="PCM_ENGINE", key="definition", value=serialized, type="json",
                             description="PCM formulas, dispatch, source bindings, parameters and output configuration",
                             is_active=True, is_secret=False))
            db.commit()
            print("Installed PCM_ENGINE.definition. No master rates were generated or changed.")


if __name__ == "__main__":
    main()
