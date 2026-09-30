"""Run app.services.pcm_master_normalize against a Master Rate Data workbook and
print the normalized {"Net Consumption base": [...], "Blank Consumption base": [...]}
rows as JSON.

This is a read-only, offline tool: it does not touch the database. Pipe its
output into whatever loads app_config/workbook_rows in your environment (e.g.
adapt app/import_pcm_master.py's install_master(), or hand-review the JSON
first -- these rows directly become the Condition/Expression text that
app.services.pcm_runtime.configured_rule() will execute, so review is
warranted before loading into a production database).

Usage:
    python -m tools.normalize_pcm_consumption_masters "Master_Rate_Data.xlsx" > normalized_consumption.json
"""
import json
import sys

sys.path.insert(0, ".")
from app.pcm.master_data.normalize import normalize_master_workbook, NOT_YET_CONVERTED


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    path = sys.argv[1]
    result = normalize_master_workbook(path)
    if NOT_YET_CONVERTED:
        print(
            f"NOTE: not converted (needs manual layout inspection first): {NOT_YET_CONVERTED}",
            file=sys.stderr,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
