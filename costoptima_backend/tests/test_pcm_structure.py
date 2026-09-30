from app.pcm.commodities import (
    ALL_SERVICES,
    SERVICES_BY_PROFILE,
)


EXPECTED_PROFILES = {
    "hard",
    "buy_std",
    "assy",
    "special_process",
    "pkg",
    "frt",
    "end_item",
    "soft",
    "gdc",
    "pdc",
    "sand",
    "stamping",
    "forging",
    "thermal",
    "thermal_make",
    "pcba",
    "buy_non_std",
    "consign",
    "seat",
}


def test_every_pcm_profile_has_a_dedicated_service():
    assert {service.profile for service in ALL_SERVICES} == EXPECTED_PROFILES
    assert set(SERVICES_BY_PROFILE) == EXPECTED_PROFILES
    assert len(ALL_SERVICES) == len(EXPECTED_PROFILES)


def test_services_have_stable_identity_metadata():
    for service in ALL_SERVICES:
        assert service.name
        assert service.profile
        assert service.scope
        assert SERVICES_BY_PROFILE[service.profile] is service
