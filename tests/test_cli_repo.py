import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import DOC, ROOT, FakeClient
from validator import cli


def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "sub" / "report.md"
    code = cli.main([str(DOC), str(out), "--env-file", str(tmp_path / "none.env")],
                    client=FakeClient())
    assert code == 0
    md = out.read_text(encoding="utf-8")
    assert md.startswith("# Отчёт о проверке требований: Уведомление о расходах по карте")
    assert "| Модель | fake-model |" in md
    assert re.search(r"\| Версия агента \| \S+ \|", md)
    assert re.search(r"\| Дата \| \d{4}-\d{2}-\d{2}T", md)
    assert str(out) in capsys.readouterr().err


def test_cli_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    env = tmp_path / "empty.env"
    env.write_text("")
    assert cli.main([str(tmp_path / "missing.md"), str(tmp_path / "r.md"), "--env-file", str(env)]) == 2
    assert cli.main([str(DOC), str(tmp_path / "r.md"), "--env-file", str(env)]) == 2
    assert not (tmp_path / "r.md").exists()


def test_cli_api_failure_short_message(tmp_path, capsys):
    class Broken(FakeClient):
        def generate_json(self, *a, **k):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    code = cli.main([str(DOC), str(tmp_path / "r.md"), "--env-file", str(tmp_path / "n")],
                    client=Broken())
    assert code == 1
    assert "RESOURCE_EXHAUSTED" in capsys.readouterr().err


def test_module_entry_point_help():
    res = subprocess.run([sys.executable, "-m", "validator", "--help"], cwd=ROOT,
                         capture_output=True, text=True)
    assert res.returncode == 0 and "input" in res.stdout and "output" in res.stdout


def _tracked_files():
    res = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True)
    return [ROOT / f for f in res.stdout.split() if f]


def test_no_secrets_in_repo():
    pattern = re.compile(r"AIza[0-9A-Za-z_\-]{30,}")
    for f in _tracked_files():
        if f.is_file() and f.suffix not in {".pdf", ".png"}:
            text = f.read_text(encoding="utf-8", errors="ignore")
            assert not pattern.search(text), f
    assert ".env" in (ROOT / ".gitignore").read_text().split()
    assert ROOT / ".env" not in _tracked_files()


def test_env_example_has_no_key():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^GEMINI_API_KEY=\s*$", text, re.M)


def test_requirements_file():
    names = (ROOT / "requirements.txt").read_text().lower()
    for pkg in ("google-genai", "pypdf", "python-dotenv"):
        assert pkg in names


def test_readme_has_required_parts():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "aistudio.google.com" in text
    assert ".env" in text and "GEMINI_API_KEY" in text
    assert "python -m validator docs/requirements.md reports/report.md" in text
    assert "reports/report.md" in text
    assert "gemini-3.6-flash" in text


@pytest.mark.skipif(not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
                    reason="нет ключа Gemini: живой прогон пропущен")
def test_live_gemini(tmp_path):
    out = tmp_path / "live.md"
    assert cli.main([str(DOC), str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    for h in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6."):
        assert h in md
