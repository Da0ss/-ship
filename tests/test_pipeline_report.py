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
    # Calls: extract + 1 check batch + set + verdict.
    assert len(fake.calls) == 4


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
    assert counts(report)["total"] == 0


def test_candidate_pairs_cover_known_conflicts(document, fake):
    reqs = pipeline.extract_requirements(document, fake, Locator(document), log=lambda m: None)
    pairs = {tuple(p["pair"]) for p in pipeline.candidate_pairs(reqs)}
    assert ("R-02", "R-05") in pairs  # month date vs acceptance criterion
    assert ("R-01", "R-07") in pairs  # threshold range vs integer rule


def test_weak_word_hints(document, fake):
    reqs = pipeline.extract_requirements(document, fake, Locator(document), log=lambda m: None)
    hints = pipeline.weak_word_hints(reqs)
    assert "в течение" in hints["R-04"]
    assert "максимум" in hints["R-03"]


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
    assert n == 7
    assert len(quotes) == n
    assert "место не найдено" not in md and "не найдено |" not in md
    assert "Отброшено замечаний без подтверждённой цитаты: 2" in md
    assert "Можно начинать разработку: Нет" in md
    assert counts(report)["by_severity"]["critical"] == 4
