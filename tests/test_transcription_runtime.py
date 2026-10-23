"""Transcription CLI runtime selection, without changing imported API behavior.

The interpreter switch itself is modules/host_python.ensure_host_python (see
tests/test_host_python.py); these cases cover the transcription entry points.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _cli_env(tmp_path, **extra):
    # Keep the probe cache (<data>/.videoagents/host-python-probe.json) out of the real data dir.
    env = {**os.environ, "VIDEOAGENTS_PYTHON": sys.executable,
           "VIDEOAGENTS_DATA_DIR": str(tmp_path), **extra}
    env.pop("VIDEOAGENTS_PYTHON_REEXEC", None)
    env.pop("VIDEOAGENTS_RUNTIME_DIR", None)
    return env


@pytest.fixture(scope="module")
def bare_python(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bare-python")
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(directory)
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@pytest.mark.parametrize("entry", [["modules/transcription.py"], ["-m", "modules.transcription"]])
def test_transcription_reexecs_before_dependency_imports(bare_python, entry, tmp_path):
    # Both venv executables can point to the same base binary. samefile() must
    # not suppress the switch to the environment containing the dependencies.
    result = subprocess.run(
        [str(bare_python), *entry, "doctor", "--model", "small"], cwd=ROOT,
        env=_cli_env(tmp_path), capture_output=True, text=True, timeout=90,
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
    result = subprocess.run(
        ["/bin/zsh", "-lc", "python3 modules/transcription.py doctor --model small"],
        cwd=ROOT, env=_cli_env(tmp_path, ZDOTDIR=str(tmp_path)),
        capture_output=True, text=True, timeout=90,
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


def test_reexec_preserves_cli_errors_and_exit_status(bare_python, tmp_path):
    result = subprocess.run(
        [str(bare_python), "-m", "modules.transcription", "transcribe"],
        cwd=ROOT, env=_cli_env(tmp_path), capture_output=True, text=True, timeout=90,
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
