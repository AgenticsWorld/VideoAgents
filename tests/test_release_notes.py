import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

SAMPLE = """# Changelog

## [Unreleased]

### Added

- pending thing

## [1.2.0] - 2026-09-16

### Added

- feature A

### Fixed

- bug B

## [1.1.0] - 2026-09-01

- older
"""


def test_extract_middle_section():
    body = release_notes.extract(SAMPLE, "1.2.0")
    assert body == "### Added\n\n- feature A\n\n### Fixed\n\n- bug B\n"


def test_extract_last_section():
    assert release_notes.extract(SAMPLE, "1.1.0") == "- older\n"


def test_missing_version_returns_none():
    assert release_notes.extract(SAMPLE, "9.9.9") is None


def test_real_changelog_has_current_version():
    from json import load

    version = load(open(ROOT / "package.json"))["version"]
    body = release_notes.extract((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    assert body and "## [" not in body
