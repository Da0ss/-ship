import re

from tests.conftest import FakeClient
from validator import pipeline
from validator.citations import Locator
from validator.report import counts, render

HEADINGS = ["## 1. Паспорт запуска", "## 2. Таблица требований", "## 3. Проверка каждого требования",
            "## 4. Проверка набора целиком", "## 5. Итог", "## 6. Вердикт"]


def test_extraction_order_ids_and_quote_checks(document, fake):
    reqs = pipeline.extract_requirements(document, fake, Locator(document), log=lambda m: None)
    assert [r.rid for r in reqs] == [f"R-{i:02d}" for i in range(1, 8)]
    assert [r.doc_id for r in reqs[:3]] == ["Ф-1", "Ф-2", "Ф-3"]
    starts = [r.location.line_start for r in reqs]
    assert starts == sorted(starts) == [37, 38, 39, 46, 64, 104, 124]
    # Paraphrase replaced by the real text of the hinted line.
    assert reqs[-1].quote == "1. Целое число тенге, обе границы включены."
    assert all("биткоин" not in r.quote for r in reqs)


def test_full_run(document, fake, passport):
    report = pipeline.run(document, fake, passport, log=lambda m: None)
    by = {c.rid: c for c in report.checks}
    assert len(report.checks) == len(report.requirements) == 7
    # Evidence rules.
    assert by["R-02"].characteristics["consistent"] == "fail"
    assert by["R-04"].characteristics["verifiable"] == "fail"
    assert by["R-07"].characteristics["complete"] == "pass"
    assert len(by["R-04"].violations) == 1  # fake quote dropped
    v = by["R-02"].violations[0]
    assert v.location.line_start == 38 and v.location2.line_start == 64
    # Set check: 3 of 6 survive (no-quote2 contradiction, duplicate, R-99 gap? see below).
    set_quotes = [x.quote for x in report.set_check.violations]
    assert "Сохранение порога" not in set_quotes
    assert "в течение минуты после получения события" not in set_quotes
    assert report.set_check.characteristics["consistent"] == "fail"
    assert report.set_check.characteristics["complete"] == "fail"
    for x in report.set_check.violations:
        assert "R-99" not in x.req_ids
    forbidden = next(x for x in report.set_check.violations if x.quote.startswith("403"))
    assert forbidden.location.line_start == 188 and forbidden.location2.line_start == 189
    assert report.dropped_violations == 2
    # Verdict computed by rule.
    assert report.verdict.ready_for_development is False
    assert report.verdict.ready_for_testing is False
    assert report.verdict.fix_first[0].startswith("(R-02)")
    # Calls: extract + 1 check batch + set + coverage + critic + verdict.
    assert len(fake.calls) == 6


def test_batching_and_retry_of_skipped(document, passport):
    seen = []

    def checks(rids):
        seen.append(rids)
        # First call skips the last requirement of every batch.
        drop = rids[-1] if len(seen) <= 3 and len(rids) > 1 else None
        from tests.conftest import default_checks
        out = default_checks([r for r in rids if r != drop])
        return out

    client = FakeClient(checks=checks)
    report = pipeline.run(document, client, passport, log=lambda m: None, batch_size=3)
    assert seen[:3] == [["R-01", "R-02", "R-03"], ["R-04", "R-05", "R-06"], ["R-07"]]
    assert ["R-03"] in seen and ["R-06"] in seen
    assert all(c.characteristics for c in report.checks)


def test_verdict_ready_without_critical(document, passport):
    def clean(rids):
        from tests.conftest import ALL_PASS
        return {"checks": [{"rid": r, "characteristics": dict(ALL_PASS), "violations": []}
                           for r in rids]}

    client = FakeClient(checks=clean, set_check={"characteristics": {}, "violations": []},
                        verdict=RuntimeError("quota"))
    report = pipeline.run(document, client, passport, log=lambda m: None)
    assert report.verdict.ready_for_development and report.verdict.ready_for_testing
    assert report.verdict.summary  # fallback text
    # Only the deterministic traceability finding remains: 6 of 7 requirements have no doc ID.
    assert counts(report)["total"] == 1
    assert report.set_check.violations[0].defect == "traceability"
    assert report.set_check.characteristics["traceable"] == "fail"


def test_candidate_pairs_cover_known_conflicts(document, fake):
    reqs = pipeline.extract_requirements(document, fake, Locator(document), log=lambda m: None)
    pairs = {tuple(p["pair"]) for p in pipeline.candidate_pairs(reqs)}
    assert ("R-02", "R-05") in pairs  # month date vs acceptance criterion
    assert ("R-01", "R-07") in pairs  # threshold range vs integer rule


def test_weak_word_hints(document, fake):
    reqs = pipeline.extract_requirements(document, fake, Locator(document), log=lambda m: None)
    hints = pipeline.weak_word_hints(reqs)
    assert any(h.startswith("в течение [Неопределённое время]") for h in hints["R-04"])
    assert any(h.startswith("максимум [Неопределённое количество]") for h in hints["R-03"])
    assert "R-01" not in hints or not any("держатель" in h for h in hints["R-01"])


def test_report_structure_and_citations(document, fake, passport):
    report = pipeline.run(document, fake, passport, log=lambda m: None)
    md = render(report)
    pos = [md.index(h) for h in HEADINGS]
    assert pos == sorted(pos)
    assert "fake-model" in md and "test" in md and "docs/requirements.md" in md
    for r in report.requirements:
        assert r.rid in md
    assert "| R-01 | Ф-1 |" in md
    # Every violation in sections 3–4 has a quote with a location.
    body = md[md.index(HEADINGS[2]):md.index(HEADINGS[4])]
    quotes = re.findall(r"Цитата: «[^»]+» \((строк[аи] [\d–]+)\)", body)
    n = counts(report)["total"]
    assert n == 8  # 7 from the model + 1 deterministic traceability finding
    assert len(quotes) == n
    assert "место не найдено" not in md and "не найдено |" not in md
    assert "Отброшено замечаний без подтверждённой цитаты: 2" in md
    assert "Можно начинать разработку: Нет" in md
    assert counts(report)["by_severity"]["critical"] == 4


def test_traceability_rule(document, fake):
    reqs = pipeline.extract_requirements(document, fake, Locator(document), log=lambda m: None)
    stats = pipeline.traceability_stats(reqs)
    assert stats == {"requirements": 7, "with_document_id": 3, "without_document_id": 4,
                     "share_with_id": 0.43}
    v = pipeline.traceability_violation(reqs, stats)
    assert v.verified and v.location.line_start == 46 and "3 из 7" in v.explanation
    assert v.req_ids == ["R-04", "R-05", "R-06", "R-07"]
    for r in reqs:
        r.doc_id = r.doc_id or "X"
    assert pipeline.traceability_violation(reqs, pipeline.traceability_stats(reqs)) is None


def test_set_finding_repeating_requirement_finding_removed(document, passport):
    """GET vs save: found per requirement (R-06) and again in the set with another 2nd quote."""
    from tests.conftest import SET, _v
    set_check = {"characteristics": {}, "violations": [
        {**_v("other", "critical", "REST type | GET", None), "req_ids": ["R-06"]},
        {**_v("contradiction", "critical", "REST type | GET", "Заменить настройку и вернуть"),
         "req_ids": ["R-06"]},
        {**_v("contradiction", "critical", "Сохранение порога. Функциональные требования",
              "Повторное сохранение заменяет действующую настройку"), "req_ids": []},
    ]}
    report = pipeline.run(document, FakeClient(set_check=set_check), passport, log=lambda m: None)
    quotes = [(v.defect, v.quote) for v in report.set_check.violations]
    assert ("other", "REST type | GET") not in quotes  # same defect, same place, same rid
    assert ("contradiction", "REST type | GET") in quotes  # other defect type: kept
    assert any(q.startswith("Сохранение порога") for _, q in quotes)


def test_set_prompt_has_coverage_and_stats(document, fake, passport):
    pipeline.run(document, fake, passport, log=lambda m: None)
    set_prompt = next(p for s, p in fake.calls if "набор требований целиком" in s)
    assert "Матрица покрытия" in set_prompt
    assert "R-05 (строка 64)" in set_prompt
    assert '"share_with_id": 0.43' in set_prompt


def test_coverage_matrix(document, passport):
    from tests.conftest import COVERAGE
    client = FakeClient(coverage=COVERAGE)
    report = pipeline.run(document, client, passport, log=lambda m: None)
    gaps = [v for v in report.set_check.violations if "Нет критерия приёмки" in v.explanation]
    assert [(g.req_ids, g.location.line_start, g.severity) for g in gaps] == [
        (["R-01"], 37, "major"), (["R-03"], 39, "critical")]
    assert gaps[0].fix.startswith("Добавить критерий приёмки: Порог 999")
    assert "покрывают критерии: R-05." in gaps[0].explanation  # unknown R-99 removed
    assert report.set_check.characteristics["able_to_be_validated"] == "fail"
    assert report.dropped_violations == 3  # 2 from the default script + 1 invented coverage quote
    prompt = next(p for s, p in client.calls if "тест-аналитик" in s)
    assert '"rid": "R-05"' in prompt.split("Проверяемые требования:")[0]  # acceptance list
