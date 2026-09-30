"""Backwards-compatible entry point for the original single reference parity case.

The comparison logic and the case definition now live in ``tools/verify_pcm_parity.py``
and ``data/pcm_parity_cases.json`` so that parity cases for the remaining commodities can
be added without editing Python. This module is retained because
``tests/test_pcm_reference.py`` and the documented command both refer to it; it delegates
rather than duplicating the logic, so the two can no longer drift apart.

Prefer ``python tools/verify_pcm_parity.py`` for new work - it runs every declared case
and reports which profiles still have no parity fixture.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.verify_pcm_parity import load_case, run_case  # noqa: E402

REFERENCE_CASE = "s302_bso_cover"


def reference_run(directory):
    """Run the S302 -> 2302 reference case.

    Returns ``(report, definition, masters, rows, results)``, unchanged from the
    original implementation. The report is also written to ``reference_parity.json``
    in ``directory`` for compatibility with the documented output location.
    """
    report, definition, masters, rows, results = run_case(
        load_case(REFERENCE_CASE), Path(directory), return_details=True
    )
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / "reference_parity.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report, definition, masters, rows, results


if __name__ == "__main__":
    report, *_ = reference_run(ROOT / "docs/pcm/verification")
    print(json.dumps(report, indent=2))
    raise SystemExit(bool(report["mismatches"] or report["errors"]))
