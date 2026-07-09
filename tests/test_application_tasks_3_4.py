from pathlib import Path

from infra.application.app_validator import validate_application, detect_platform


def test_detect_ios_project(tmp_path: Path):
    (tmp_path / "Podfile").write_text("pod 'AppsFlyerFramework'", encoding="utf-8")
    (tmp_path / "Demo.xcodeproj").mkdir()

    assert detect_platform(tmp_path) == "ios"

    result = validate_application(tmp_path)

    assert result["status"] == "OK"
    assert result["platform"] == "ios"
    assert result["markers"]["has_podfile"] is True
    assert result["markers"]["has_xcodeproj"] is True


def test_detect_android_project(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text("pluginManagement {}", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins {}", encoding="utf-8")

    assert detect_platform(tmp_path) == "android"

    result = validate_application(tmp_path)

    assert result["status"] == "OK"
    assert result["platform"] == "android"
    assert result["markers"]["has_settings_gradle"] is True
    assert result["markers"]["has_build_gradle"] is True


def test_unknown_project_fails(tmp_path: Path):
    (tmp_path / "README.md").write_text("not a mobile app", encoding="utf-8")

    result = validate_application(tmp_path)

    assert result["status"] == "FAILED"
    assert result["platform"] == "unknown"


def test_empty_app_file_fails(tmp_path: Path):
    app_file = tmp_path / "banana.app"
    app_file.write_text("", encoding="utf-8")

    result = validate_application(app_file)

    assert result["status"] == "FAILED"
    assert result["platform"] == "ios_app_bundle"
