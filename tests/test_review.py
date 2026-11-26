import pytest

from tests.conftest import FakeClient
from validator import pipeline, review
from validator.citations import Locator
from validator.models import Location, RequirementCheck, SetCheck, Violation
from validator.report import render


def _v(defect, a, b=None, fix="x", severity="major", req_ids=None):
    return Violation(defect, "c", severity, "q", "e", fix, list(req_ids or []),
                     quote2="q2" if b else None, location=Location(a, a),
                     location2=Location(b, b) if b else None, verified=True)


def test_exact_quote_fixes_model_typo(document):
    loc = Locator(document)
    q = "Для чужой карты возвращается 404, как и для отсутствиющей карты."
    found = loc.locate(q)
    assert found.line_start == 189
    assert loc.exact_quote(q, found) == "Для чужой карты возвращается 404, как и для отсутствующей карты."


def test_dedupe_merges_mirror_contradiction():
    a = RequirementCheck("R-07", {"consistent": "fail"}, [_v("contradiction", 39, 40, severity="critical")])
    b = RequirementCheck("R-08", {"consistent": "fail"}, [_v("contradiction", 40, 39)])
    s = SetCheck({}, [_v("contradiction", 39, 40)])
    assert review.dedupe([a, b], s) == 2
    assert len(a.violations) == 1 and not b.violations and not s.violations
    assert a.violations[0].req_ids == ["R-07", "R-08"]
    assert b.see_also == ["R-07"]
    assert b.characteristics["consistent"] == "fail"  # evidence kept, only the text is merged


def test_dedupe_keeps_different_defects_at_same_place():
    a = RequirementCheck("R-16", {}, [_v("contradiction", 104, 103), _v("other", 104)])
    assert review.dedupe([a], SetCheck({}, [])) == 0


def test_invented_number_becomes_question(document):
    v = _v("gap", 37, fix="При первом включении порог 75 000 ₸.")
    w = _v("gap", 37, fix="Порог от 1 000 до 1 000 000 ₸.")
    c = RequirementCheck("R-05", {}, [v, w])
    assert review.guard_invented_values(document, [c], SetCheck({}, [])) == 1
    assert v.fix_type == "question" and w.fix_type == "edit"


def test_document_checklist_found(document):
    rows = review.find_document_checklist(document)
    assert len(rows) == 14
    assert rows[0][1].startswith("Техническое требование зафиксировано")
    assert any("отката" in t for _, t in rows)


def test_domain_findings_and_checklist_in_report(document, passport):
    domain = {"findings": [
        {"defect": "gap", "criterion": "ISO/IEC/IEEE 29148 5.2.5 — Полнота", "severity": "major",
         "quote": "В расходы входят проведённые покупки.", "quote2": None,
         "explanation": "Не сказано, считается ли авторизация или только клиринг.",
         "fix": "Какое событие увеличивает расходы?", "fix_type": "question",
         "options": ["Авторизация", "Клиринг"], "req_ids": [], "topic": "operation_type",
         "tag": "fintech"},
        {"defect": "gap", "criterion": "x", "severity": "major", "quote": "Этой цитаты нет в тексте",
         "explanation": "e", "fix": "f", "req_ids": [], "topic": "rollback", "tag": "fintech"},
    ]}

    def checklist(items):
        out = []
        for i in items:
            st = "no" if "отката" in i["item"] else "yes"
            out.append({"cid": i["cid"], "status": st, "evidence": "есть раздел",
                        "quote": "Сервис передаёт push на отправку в течение минуты" if st == "yes" else None,
                        "refs": ["R-99"]})
        return {"items": out}

    fake = FakeClient(domain=domain, checklist=checklist)
    report = pipeline.run(document, fake, passport, log=lambda m: None)
    tagged = [v for v in report.set_check.violations if v.tag == "fintech"]
    assert len(tagged) == 1 and tagged[0].location.line_start == 38
    assert report.checklist_source == "document" and len(report.checklist) == 14
    rollback = next(c for c in report.checklist if "отката" in c.item)
    assert rollback.status == "no"
    assert all(not c.refs for c in report.checklist)  # unknown IDs dropped
    md = render(report)
    assert md.index("## Кратко") < md.index("## 1. Паспорт запуска")
    assert "## 7. Чек-лист ревью требований" in md and "❌ Нет" in md
    assert "## 8. Вопросы к владельцу продукта" in md
    assert "А) Авторизация" in md and "🏦 банковская специфика" in md
    assert "❌" not in md.split("### Самопроверка отчёта")[1]


def test_stages_can_be_switched_off(document, passport, monkeypatch):
    monkeypatch.setenv("VALIDATOR_DOMAIN", "off")
    monkeypatch.setenv("VALIDATOR_CHECKLIST", "off")
    fake = FakeClient()
    report = pipeline.run(document, fake, passport, log=lambda m: None)
    assert not report.checklist and "## 7." not in render(report)
    assert not any("банковский аналитик" in s for s, _ in fake.calls)


@pytest.mark.segmenter
def test_fast_client_runs_light_stages(document, passport):
    strong, fast = FakeClient(), FakeClient()
    pipeline.run(document, strong, passport, log=lambda m: None, fast_client=fast)
    fast_stages = [s for s, _ in fast.calls]
    strong_stages = [s for s, _ in strong.calls]
    assert any("Код уже нарезал документ" in s for s in fast_stages)
    assert any("аудитор процесса ревью" in s for s in strong_stages)
    assert any("вердикт" in s for s in fast_stages)
    assert any("строгий ревьюер" in s for s in strong_stages)
    assert any("независимый критик" in s for s in strong_stages)
    assert not any("строгий ревьюер" in s for s in fast_stages)


def test_held_fact_comes_back_when_llm_twin_is_gone():
    from validator.facts import TAG
    fact = _v("contradiction", 143, 157)
    fact.explanation = f"{TAG} типы различаются"
    s = SetCheck({"consistent": "pass"}, [])
    assert pipeline.restore_held_facts([fact], [], s) == 1
    assert s.violations == [fact] and s.characteristics["consistent"] == "fail"
    twin = _v("contradiction", 143, 157)
    s2 = SetCheck({}, [twin])
    assert pipeline.restore_held_facts([fact], [], s2) == 0


def test_bank_finding_not_merged_into_iso_finding():
    iso = _v("gap", 38)
    bank = _v("gap", 38)
    bank.tag = "fintech"
    s = SetCheck({}, [iso, bank])
    assert review.dedupe([], s) == 0 and len(s.violations) == 2


def test_coverage_gaps_grouped_into_one_finding():
    gaps = [_v("gap", n, fix=f"Добавить критерий приёмки: случай {n}", req_ids=[f"R-{n:02d}"])
            for n in (40, 37, 150, 160)]
    crit = _v("gap", 39, fix="Добавить критерий приёмки: главный", severity="critical")
    other = _v("ambiguity", 46)
    s = SetCheck({}, gaps + [crit, other])
    grouped = review.group_coverage(s)
    assert [g.location.line_start for g in grouped] == [37, 39, 40, 150, 160]
    assert crit not in s.violations and other in s.violations and len(s.violations) == 2
    one = s.violations[-1]
    assert one.severity == "critical" and "критичных 1" in one.explanation
    assert one.location.line_start == 37 and [l.line_start for l in one.also_at] == [39, 40, 150, 160]


def test_grouped_finding_matches_golden_by_any_place():
    from validator.evaluate import _matches
    v = {"location": {"line_start": 37, "line_end": 37}, "also_at": [{"line_start": 150, "line_end": 150}],
         "defect": "gap"}
    assert _matches(v, {"defects": ["gap"], "lines": [150]})
