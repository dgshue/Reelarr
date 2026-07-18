"""Config robustness — blank env vars must not crash startup.

Regression guard: a stack that declares RADARR_QUALITY_PROFILE_ID= (blank,
because the user hasn't picked a profile in the UI yet) used to raise a
pydantic int_parsing ValidationError at import time, which crash-looped the
whole container instead of just leaving the field unset.
"""

from __future__ import annotations

import pytest

from reelarr.config import AppConfig


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_quality_profile_ids_are_treated_as_unset(blank: str) -> None:
    cfg = AppConfig(
        radarr_quality_profile_id=blank,
        sonarr_quality_profile_id=blank,
    )
    assert cfg.radarr_quality_profile_id is None
    assert cfg.sonarr_quality_profile_id is None


def test_real_quality_profile_ids_still_parse() -> None:
    cfg = AppConfig(radarr_quality_profile_id="4", sonarr_quality_profile_id=6)
    assert cfg.radarr_quality_profile_id == 4
    assert cfg.sonarr_quality_profile_id == 6


def test_garbage_quality_profile_id_still_rejected() -> None:
    """Blank means "unset"; genuinely invalid input should still be an error."""
    with pytest.raises(Exception):
        AppConfig(radarr_quality_profile_id="not-a-number")
