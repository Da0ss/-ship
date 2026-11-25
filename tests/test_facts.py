import pytest

from tests.conftest import FakeClient
from validator import pipeline
from validator.facts import TAG, find_facts
from validator.models import Document, Line

pytestmark = pytest.mark.facts


def _doc(text):
    return Document("x.md", "x", [Line(i, t) for i, t in enumerate(text.splitlines(), 1)])


def _sig(vs):
    return sorted((v.defect, v.location.line_start, v.location2.line_start if v.location2 else None)
                  for v in vs)


def test_facts_on_task_document(document):
    assert _sig(find_facts(document)) == sorted([
        ("gap", 151, None),             # 201 used, not described
        ("gap", 189, None),             # 404 used, not described
        ("other", 165, None),           # "200 … Created"
        ("contradiction", 188, 189),    # 403 section says 404
        ("contradiction", 104, 103),    # GET for saving
        ("contradiction", 147, 148),    # Content-Type without body
        ("gap", 49, None),              # tiyn and tenge
        ("contradiction", 143, 156),    # amount integer vs string
        ("contradiction", 144, 157),    # enabled integer vs string
        ("gap", 109, None),             # card id and amount in the URL query string
    ])
    for v in find_facts(document):
        assert v.verified and v.explanation.startswith(TAG) and v.fix


def test_facts_quotes_are_found_in_text(document):
    from validator.citations import Locator
    loc = Locator(document)
    for v in find_facts(document):
        assert loc.locate(v.quote, v.location.line_start).line_start == v.location.line_start
        if v.quote2:
            assert loc.locate(v.quote2, v.location2.line_start).line_start == v.location2.line_start


def test_facts_clean_api_has_no_findings():
    doc = _doc("""# API
| Поле | Значение |
| :--- | :--- |
| **REST type** | POST |
| **Description** | Сохранение порога |
Content-Type: application/json
Тело содержит amount.
Если результат 200 — успешно, при ошибке 400.
### Статусы
#### 200. OK
#### 400. Bad Request
""")
    assert find_facts(doc) == []


def test_facts_ignore_money_amounts_that_look_like_codes():
    doc = _doc("""# Требования
Перевести 500 ₸ и вернуть результат.
### Статусы
#### 200. OK
""")
    assert find_facts(doc) == []


def test_facts_units_with_conversion_rule_ok():
    doc = _doc("Сумма в тиынах, порог в тенге.\n1 тенге = 100 тиынов.")
    assert find_facts(doc) == []


def test_pipeline_adds_facts_without_duplicates(document, passport):
    report = pipeline.run(document, FakeClient(), passport, log=lambda m: None)
    facts = [v for v in report.set_check.violations if v.explanation.startswith(TAG)]
    # FakeClient already reports "REST type | GET" (R-06, other) and the 403/404 contradiction
    # in the set check: the code findings of the same kind at the same place are skipped.
    sig = _sig(facts)
    assert ("contradiction", 188, 189) not in sig
    assert ("contradiction", 104, 103) not in sig  # LLM "other" at line 104 = same problem
    assert ("gap", 151, None) in sig and ("contradiction", 143, 156) in sig
    assert all(v.req_ids is not None for v in facts)
    assert report.set_check.characteristics["consistent"] == "fail"
