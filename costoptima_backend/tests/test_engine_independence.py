"""The calculation engine must not depend on the HTTP layer.

``app/services/pcm_runtime.py`` used to import ``_read_bom_sheet`` from
``app/routers/boms.py``. That inverted the layering: a service depended on a router,
so the engine could not be imported, unit-tested or run offline without FastAPI, and
``tools/verify_pcm_parity.py`` inherited the same dependency. The reader now lives in
``app/services/bom_reader.py`` and the router re-exports it.

These tests fail if that regression is reintroduced.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "app" / "services"


def _imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", sorted(SERVICES.rglob("*.py")), ids=lambda p: p.name)
def test_no_service_imports_a_router(path):
    offenders = {m for m in _imported_modules(path) if m.startswith("app.routers")}
    assert not offenders, (
        f"{path.relative_to(ROOT)} imports {sorted(offenders)}. Services must not depend "
        "on the HTTP layer - move the shared helper into app/services/ instead."
    )


def test_engine_imports_without_fastapi():
    """Importing the engine must not pull in FastAPI.

    Run in a subprocess so the assertion holds even when the rest of the test session
    has already imported the web application.
    """
    code = (
        "import sys;"
        "import app.services.pcm_runtime, app.services.bom_reader;"
        "sys.exit(1 if 'fastapi' in sys.modules else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, (
        "Importing the calculation engine pulled in FastAPI.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_router_still_exposes_the_moved_helpers():
    """Existing imports of the private names must keep working."""
    from app.routers import boms

    for name in ("_read_bom_sheet", "_json_safe_row", "_json_safe_value",
                 "_resolve_commodity_code", "_norm", "COMMODITY_ALIASES"):
        assert hasattr(boms, name), f"app.routers.boms no longer exports {name}"


def test_commodity_aliases_default_preserves_legacy_hard_mapping():
    """With no configuration present the legacy hard-coded table must still apply.

    Returning an empty alias table would silently resolve every 'Hard' sheet row to no
    commodity and calculate Total=0, which is the bug the original table was added to fix.
    """
    from app.services.bom_reader import load_commodity_aliases

    aliases = load_commodity_aliases(None)
    assert aliases["hard"] == "trim"
    assert aliases["soft"] == "soft_trim"
