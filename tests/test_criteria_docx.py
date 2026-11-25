import builtins
import re
import subprocess

import pytest

from tests.conftest import ROOT
from validator import cli
from validator.criteria import normalize_criterion
from validator.reader import read_document


@pytest.mark.parametrize("raw,defect,scope,expected", [
    ("ISO/IEC/IEEE 29148:2018 Consistent", "other", "req",
     "ISO/IEC/IEEE 29148 5.2.5 — Непротиворечивость"),
    ("ISO/IEC/IEEE 29148:2018 Complete", "gap", "set", "ISO/IEC/IEEE 29148 5.2.6 — Полнота"),
    ("IEEE 830-1998 Ranked", "other", "set", "IEEE 830 4.3.5 — Ранжирование"),
    ("IEEE 830-1998 Complete", "other", "set", "IEEE 830 4.3.3 — Полнота"),
    ("IEEE 830-1998 4.3.1 — Корректность", "other", "req", "IEEE 830 4.3.1 — Корректность"),
    ("ISO/IEC/IEEE 29148 5.2.5 — Однозначность", "ambiguity", "req",
     "ISO/IEC/IEEE 29148 5.2.5 — Однозначность"),
    ("", "unverifiable", "req", "ISO/IEC/IEEE 29148 5.2.5 — Проверяемость"),
    ("Traceability", "traceability", "set", "ISO/IEC/IEEE 29148 5.2.6 — Прослеживаемость"),
])
def test_normalize_criterion(raw, defect, scope, expected):
    assert normalize_criterion(raw, defect, scope) == expected


def test_no_latin_characteristics_in_report(document, fake, passport):
    from validator import pipeline
    from validator.report import render
    md = render(pipeline.run(document, fake, passport, log=lambda m: None))
    for line in re.findall(r"Критерий: (.+)", md):
        assert re.fullmatch(r"(ISO/IEC/IEEE 29148|IEEE 830) [\d.]+ — [А-Яа-яё ]+", line), line


def test_docx_reader(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("Документ требований", level=1)
    d.add_heading("Функции", level=2)
    d.add_paragraph("Ф-1. Система отправляет push.")
    path = tmp_path / "r.docx"
    d.save(str(path))
    doc = read_document(str(path))
    assert doc.title == "Документ требований"
    assert [l.text for l in doc.lines] == ["# Документ требований", "## Функции",
                                           "Ф-1. Система отправляет push."]
    assert [l.no for l in doc.lines] == [1, 2, 3]


def test_docx_without_library(tmp_path, monkeypatch):
    path = tmp_path / "r.docx"
    path.write_bytes(b"PK")
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "docx":
            raise ImportError("no docx")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ValueError, match="python-docx"):
        read_document(str(path))


def test_agent_version_has_commit_hash():
    version = cli._agent_version()
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    assert head[:7] in version
