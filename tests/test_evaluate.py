import json

import pytest

from tests.conftest import DOC, ROOT, FakeClient
from validator import pipeline
from validator.evaluate import evaluate, main
from validator.export import report_to_dict

GOLDEN = ROOT / "tests" / "golden" / "requirements.golden.json"


def _golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_lines_exist_in_document():
    n = len(DOC.read_text(encoding="utf-8").splitlines())
    g = _golden()
    assert len(g["defects"]) == 25
    for d in g["defects"]:
        assert all(1 <= x <= n for x in d["lines"]), d["id"]


def test_evaluate_matching_rules():
    golden = {"defects": [
        {"id": "A", "title": "a", "severity": "critical", "defects": ["contradiction"], "lines": [10]},
        {"id": "B", "title": "b", "severity": "major", "defects": ["gap"], "lines": [20]},
        {"id": "C", "title": "c", "severity": "major", "defects": ["traceability"], "lines": []},
    ]}
    loc = lambda a, b: {"line_start": a, "line_end": b, "page": None}
    report = {"checks": [{"violations": [
        {"id": "V-1", "defect": "contradiction", "location": loc(5, 6), "location2": loc(9, 11)},
        {"id": "V-2", "defect": "ambiguity", "location": loc(20, 20)},  # wrong type for B
    ]}], "set_check": {"violations": [
        {"id": "V-3", "defect": "traceability", "location": loc(1, 1)},
        {"id": "V-4", "defect": "gap", "location": loc(99, 99)},  # not in golden
    ]}}
    r = evaluate(report, golden)
    assert [x["found"] for x in r["rows"]] == [True, False, True]
    assert r["recall"] == 0.667 and r["critical_recall"] == 1.0
    assert r["findings"] == 4 and r["precision_lower_bound"] == 0.5


@pytest.mark.facts
def test_code_checks_alone_cover_part_of_golden(document, passport, tmp_path, capsys, monkeypatch):
    """Baseline without any LLM finding: only code checks (facts) and the traceability rule."""
    def empty(rids):
        return {"checks": [{"rid": r, "characteristics": {}, "violations": []} for r in rids]}

    client = FakeClient(checks=empty, set_check={"characteristics": {}, "violations": []})
    report = pipeline.run(document, client, passport, log=lambda m: None)
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report_to_dict(report), ensure_ascii=False), encoding="utf-8")
    assert main([str(path), str(GOLDEN)]) == 0
    out = capsys.readouterr().out
    result = evaluate(json.loads(path.read_text(encoding="utf-8")), _golden())
    found = {r["id"] for r in result["rows"] if r["found"]}
    assert {"G01", "G02", "G05", "G06", "G08", "G09", "G10", "G19", "G15", "G23", "G25"} <= found
    assert result["precision_lower_bound"] == 1.0
    assert "Эталон: 25 дефектов" in out
    assert main([str(path), str(GOLDEN), "--min-recall", "0.99"]) == 1
