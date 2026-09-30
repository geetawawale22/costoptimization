import json
from pathlib import Path

from app.pcm.commodities import ALL_SERVICES
from app.pcm.commodities.status import MIGRATION_STATUSES, verified_profiles


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _parity_profiles():
    parity = json.loads((BACKEND_ROOT / "data" / "pcm_parity_cases.json").read_text())
    profiles = set()
    for case in parity["cases"]:
        if not case.get("enabled", True):
            continue
        profiles.update(case.get("profile_sheets", {}))
        profiles.update(case.get("profile_details", {}))
        if case.get("rollups"):
            profiles.add("end_item")
    return profiles


def test_every_commodity_service_has_migration_status():
    service_profiles = {service.profile for service in ALL_SERVICES}

    assert set(MIGRATION_STATUSES) == service_profiles


def test_verified_status_requires_parity_fixture_evidence():
    assert verified_profiles() <= _parity_profiles()


def test_pending_parity_list_matches_unverified_statuses():
    parity = json.loads((BACKEND_ROOT / "data" / "pcm_parity_cases.json").read_text())
    pending = set(parity["_pending_cases"]["profiles_without_parity_fixture"])
    blocked_or_unverified = {
        profile
        for profile, migration_status in MIGRATION_STATUSES.items()
        if migration_status.status in {"blocked", "unverified"}
    }

    assert blocked_or_unverified <= pending
