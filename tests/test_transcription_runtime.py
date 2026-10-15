"""Transcription CLI runtime selection, without changing imported API behavior."""
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

from modules.transcription import _reexec_with_app_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("selected", [None, sys.executable])
def test_no_reexec_without_selection_or_when_already_selected(monkeypatch, selected):
    monkeypatch.delenv("VIDEOAGENTS_PYTHON", raising=False)
    if selected:
        monkeypatch.setenv("VIDEOAGENTS_PYTHON", selected)
    monkeypatch.setattr(subprocess, "call", lambda *args, **kwargs: pytest.fail("unexpected reexec"))
    _reexec_with_app_python()


def test_missing_selected_runtime_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOAGENTS_PYTHON", str(tmp_path / "missing-python"))
    with pytest.raises(RuntimeError, match="应用 Python 不存在"):
        _reexec_with_app_python()


def test_successful_switch_clears_guard_for_later_child_commands(monkeypatch):
    monkeypatch.setenv("VIDEOAGENTS_PYTHON", sys.executable)
    monkeypatch.setenv("VIDEOAGENTS_TRANSCRIPTION_REEXEC", os.path.abspath(sys.executable))
    _reexec_with_app_python()
    assert "VIDEOAGENTS_TRANSCRIPTION_REEXEC" not in os.environ


def test_switch_does_not_loop_or_hide_configuration_error(monkeypatch, tmp_path):
    python = str(tmp_path / "wrapper-python")
    monkeypatch.setenv("VIDEOAGENTS_PYTHON", python)
    monkeypatch.setenv("VIDEOAGENTS_TRANSCRIPTION_REEXEC", python)
    with pytest.raises(RuntimeError, match="无法切换到应用 Python"):
        _reexec_with_app_python()


@pytest.fixture(scope="module")
def bare_python(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bare-python")
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(directory)
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@pytest.mark.parametrize("entry", [["modules/transcription.py"], ["-m", "modules.transcription"]])
def test_transcription_reexecs_before_dependency_imports(bare_python, entry, tmp_path):
    # Both venv executables can point to the same base binary. samefile() must
    # not suppress the switch to the environment containing the dependencies.
    env = {**os.environ, "VIDEOAGENTS_PYTHON": sys.executable}
    env["VIDEOAGENTS_DATA_DIR"] = str(tmp_path)
    env.pop("VIDEOAGENTS_TRANSCRIPTION_REEXEC", None)
    result = subprocess.run(
        [str(bare_python), *entry, "doctor", "--model", "small"], cwd=ROOT,
        env=env, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["python_executable"] == os.path.abspath(sys.executable)
    assert payload["python_prefix"] == sys.prefix
    assert payload["model"] == "small"


@pytest.mark.skipif(not Path("/bin/zsh").is_file(), reason="requires zsh")
def test_login_shell_overriding_path_still_uses_application_python(bare_python, tmp_path):
    # Simulate a user's login profile choosing another Python after dispatch.
    import shlex
    (tmp_path / ".zprofile").write_text(
        f"export PATH={shlex.quote(str(bare_python.parent))}:/usr/bin:/bin\n"
    )
    env = {**os.environ, "VIDEOAGENTS_PYTHON": sys.executable}
    env.update(ZDOTDIR=str(tmp_path), VIDEOAGENTS_DATA_DIR=str(tmp_path))
    env.pop("VIDEOAGENTS_TRANSCRIPTION_REEXEC", None)
    result = subprocess.run(
        ["/bin/zsh", "-lc", "python3 modules/transcription.py doctor --model small"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["python_executable"] == os.path.abspath(sys.executable)


def test_import_does_not_switch_interpreters_or_mutate_path(tmp_path):
    env = {**os.environ, "VIDEOAGENTS_PYTHON": str(tmp_path / "missing-python")}
    result = subprocess.run(
        [sys.executable, "-c", "import os; before = dict(os.environ); "
         "from modules import transcription; assert dict(os.environ) == before; print('ok')"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_reexec_preserves_cli_errors_and_exit_status(bare_python):
    env = {**os.environ, "VIDEOAGENTS_PYTHON": sys.executable}
    env.pop("VIDEOAGENTS_TRANSCRIPTION_REEXEC", None)
    result = subprocess.run(
        [str(bare_python), "-m", "modules.transcription", "transcribe"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "--audio" in result.stderr and "--output" in result.stderr


def test_doctor_reports_actual_interpreter_when_dependency_is_missing(monkeypatch):
    from modules import transcription
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    result = transcription.doctor("small")
    assert result["ok"] is False
    assert result["python_executable"] == sys.executable
    assert result["python_prefix"] == sys.prefix
    assert sys.executable in result["error"]
