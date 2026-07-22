"""Regression tests for relocatable desktop Python runtime packaging."""

from __future__ import annotations

import zipfile

from apps.desktop.scripts import build_runtime, package_runtime


def test_remove_python_aliases_handles_windows_junction(tmp_path, monkeypatch):
    concrete = tmp_path / "cpython-3.12.13-windows-x86_64-none"
    alias = tmp_path / "cpython-3.12-windows-x86_64-none"
    concrete.mkdir()
    alias.mkdir()
    monkeypatch.setattr(build_runtime, "is_junction", lambda path: path == alias)

    build_runtime.remove_python_aliases(tmp_path)

    assert concrete.is_dir()
    assert not alias.exists()


def test_runtime_paths_does_not_descend_into_junction(tmp_path, monkeypatch):
    concrete = tmp_path / "python" / "concrete"
    alias = tmp_path / "python" / "alias"
    concrete.mkdir(parents=True)
    alias.mkdir()
    (concrete / "python.exe").write_bytes(b"python")
    (alias / "broken-after-relocation.exe").write_bytes(b"alias")
    monkeypatch.setattr(package_runtime, "is_junction", lambda path: path == alias)

    paths = list(package_runtime.runtime_paths(tmp_path))

    assert concrete / "python.exe" in paths
    assert alias in paths
    assert alias / "broken-after-relocation.exe" not in paths


def test_add_path_skips_junction(tmp_path, monkeypatch):
    alias = tmp_path / "cpython-3.12-windows-x86_64-none"
    alias.mkdir()
    monkeypatch.setattr(package_runtime, "is_junction", lambda path: path == alias)
    archive_path = tmp_path / "runtime.zip"

    with zipfile.ZipFile(archive_path, "w") as archive:
        package_runtime.add_path(archive, alias, alias.relative_to(tmp_path))

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == []
