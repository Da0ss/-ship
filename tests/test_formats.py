"""The task document converted to PDF (tables with borders) and DOCX reads like Markdown."""
from pathlib import Path

import pytest

from validator.facts import find_facts
from validator.reader import read_document
from validator.review import find_document_checklist

FIX = Path(__file__).resolve().parent / "fixtures"


def _kinds(doc):
    return sorted(v.defect for v in find_facts(doc))


@pytest.mark.parametrize("name", ["requirements.pdf", "requirements.docx"])
def test_same_code_findings_as_markdown(name, document):
    pytest.importorskip("pdfplumber")
    doc = read_document(str(FIX / name))
    assert doc.title == "Уведомление о расходах по карте"
    assert _kinds(doc) == _kinds(document)
    assert len(find_document_checklist(doc)) == 14


def test_pdf_keeps_pages_and_ids():
    pytest.importorskip("pdfplumber")
    doc = read_document(str(FIX / "requirements.pdf"))
    assert all(l.page for l in doc.lines)
    f1 = next(l for l in doc.lines if "Ф-1." in l.text)
    assert f1.text.startswith("**Ф-1.**") and "уведомление выключено" in f1.text
    assert any(l.text.startswith("|") and "cardId" in l.text for l in doc.lines)


def test_code_rules_work_on_second_document():
    """Rules were written for the first task document; the autopay sample was written later
    with its own planted defects. The code rules must find them without tuning."""
    from validator.evaluate import _matches
    import json
    doc = read_document(str(FIX.parent.parent / "docs" / "samples" / "autopay.md"))
    golden = json.loads((FIX.parent / "golden" / "autopay.golden.json").read_text(encoding="utf-8"))
    found = set()
    for v in find_facts(doc):
        d = {"defect": v.defect, "location": {"line_start": v.location.line_start, "line_end": v.location.line_end},
             "location2": ({"line_start": v.location2.line_start, "line_end": v.location2.line_end}
                           if v.location2 else None)}
        found |= {g["id"] for g in golden["defects"] if _matches(d, g)}
    assert {"A04", "A07", "A08", "A09", "A06", "A15", "A16"} <= found
