import pytest

from tests.conftest import FakeClient
from validator import pipeline


def _by_quote(views, start):
    return next(v["vid"] for v in views if v["quote"].startswith(start))


def critic_script(views):
    """Reject the NFR finding, mark one set finding as a duplicate, downgrade GET to major."""
    nfr = _by_quote(views, "в течение минуты")
    get = _by_quote(views, "**REST type**")
    gap = _by_quote(views, "заполняются тестировщиками")
    return {"reviews": [
        {"vid": nfr, "decision": "reject", "reason": "метрика задана: одна минута"},
        {"vid": get, "decision": "adjust", "severity": "major", "defect": "contradiction",
         "criterion": "IEEE 830 4.3.1 — Корректность", "reason": "уточнено"},
        {"vid": gap, "decision": "confirm", "duplicate_of": views[0]["vid"], "reason": "то же"},
    ]}


def test_critic_applies_decisions(document, passport):
    client = FakeClient(critic=critic_script)
    report = pipeline.run(document, client, passport, log=lambda m: None)
    assert report.critic_ran
    reasons = [r for _, r in report.critic_rejected]
    assert len(report.critic_rejected) == 2
    assert "метрика задана: одна минута" in reasons
    assert any(r.startswith("повтор замечания V-001") for r in reasons)
    by = {c.rid: c for c in report.checks}
    assert by["R-04"].violations == []
    assert by["R-04"].characteristics["verifiable"] == "pass"  # evidence removed
    get = by["R-06"].violations[0]
    # "other" → "contradiction" refused: the finding has no second quote.
    assert (get.severity, get.defect, get.criterion) == ("major", "other",
                                                         "IEEE 830 4.3.1 — Корректность")
    assert report.critic_adjusted == 1
    # Verdict prompt reports the critic numbers.
    verdict_prompt = next(p for s, p in client.calls if "вердикт" in s)
    assert "отклонено как ложные\n2" in verdict_prompt or "отклонено как ложные 2" in verdict_prompt


def test_critic_guard_against_mass_reject(document, passport, monkeypatch):
    def reject_all(views):
        return {"reviews": [{"vid": v["vid"], "decision": "reject", "reason": "нет"}
                            for v in views]}

    before = pipeline.run(document, FakeClient(), passport, log=lambda m: None)
    after = pipeline.run(document, FakeClient(critic=reject_all), passport, log=lambda m: None)
    total = lambda r: sum(len(c.violations) for c in r.checks) + len(r.set_check.violations)
    assert total(after) == total(before) and after.critic_rejected == []


def test_critic_missing_decisions_keep_findings(document, passport):
    report = pipeline.run(document, FakeClient(critic=lambda views: {"reviews": []}), passport,
                          log=lambda m: None)
    assert report.critic_rejected == [] and report.critic_adjusted == 0
    assert sum(len(c.violations) for c in report.checks) == 3


def test_critic_can_be_disabled(document, passport, monkeypatch):
    monkeypatch.setenv("VALIDATOR_CRITIC", "off")
    client = FakeClient()
    report = pipeline.run(document, client, passport, log=lambda m: None)
    assert not report.critic_ran
    assert not any("независимый критик" in s for s, _ in client.calls)


def test_critic_removes_last_contradiction_sets_consistent_pass(document, passport):
    def reject_contradictions(views):
        return {"reviews": [{"vid": v["vid"], "decision": "reject", "reason": "совместимы"}
                            for v in views if v["defect"] == "contradiction"]}

    report = pipeline.run(document, FakeClient(critic=reject_contradictions), passport,
                          log=lambda m: None)
    assert report.critic_rejected  # 3 of 8 rejected, under the 60 % guard
    assert report.set_check.characteristics["consistent"] == "pass"


def test_report_shows_critic_and_self_check(document, passport):
    from validator.report import render
    md = render(pipeline.run(document, FakeClient(critic=critic_script), passport,
                             log=lambda m: None))
    sec5 = md[md.index("## 5. Итог"):md.index("## 6. Вердикт")]
    assert "Критик проверил замечания: подтверждено 6, исправлено 1" in sec5
    assert "отклонено 2." in sec5
    assert "причина: метрика задана: одна минута" in sec5
    tail = md[md.index("### Самопроверка отчёта"):]
    assert tail.count("✅") == 8 and "❌" not in tail


def test_self_check_detects_defects(document, fake, passport):
    from validator.report import render
    from validator.selfcheck import self_check
    report = pipeline.run(document, fake, passport, log=lambda m: None)
    md = render(report)
    body = md[:md.index("### Самопроверка отчёта")]
    assert all(ok for _, ok, _ in self_check(report, body))
    report.checks[1].violations[0].location = None
    report.checks[1].violations[0].explanation = "Требование неполное."
    report.verdict.ready_for_development = True
    res = {rule: (ok, detail) for rule, ok, detail in self_check(report, body)}
    assert res["У каждого нарушения есть цитата и место"][0] is False
    assert "R-02" in res["У каждого нарушения есть цитата и место"][1]
    assert res["Нет общих утверждений"][0] is False
    assert res["Вердикт согласован с критичными"][0] is False
    res2 = {r: ok for r, ok, _ in self_check(report, body.replace("## 4. ", "## 4x. "))}
    assert res2["Все 6 разделов на месте"] is False
