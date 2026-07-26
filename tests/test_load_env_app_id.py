"""Tests for iOS APP_ID normalization."""

from infra.load_env import normalize_ios_app_id, resolve_app_id_for_platform


def test_normalize_ios_app_id_fixes_d_prefix_typo():
    assert normalize_ios_app_id("d1512793879") == "id1512793879"


def test_normalize_ios_app_id_accepts_id_prefix():
    assert normalize_ios_app_id("id1512793879") == "id1512793879"


def test_resolve_ios_app_id_normalizes_use_case_typo():
    assert resolve_app_id_for_platform("ios", "d1512793879") == "id1512793879"
