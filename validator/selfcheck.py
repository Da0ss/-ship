"""Deterministic self-check of the rendered report against the task rules."""
from __future__ import annotations

import re

from .models import Report, Violation

_HEADINGS = (
    "## 1. Паспорт запуска",
    "## 2. Таблица требований",
    "## 3. Проверка каждого требования",
    "## 4. Проверка набора целиком",
    "## 5. Итог",
    "## 6. Вердикт",
)
_GENERIC = {"требование неполное", "требование неоднозначно", "есть неточности",
            "требование неясное", "требование непонятно", "требование нечёткое"}
_ITEM = re.compile(r"^\d+\. \*\*")


def _empty(value: object) -> bool:
    return value is None or not str(value).strip()


def _all_violations(report: Report) -> list[tuple[Violation, str]]:
    items = [(v, c.rid) for c in report.checks for v in c.violations]
    items += [(v, "набор") for v in report.set_check.violations]
    return items


def _label(v: Violation, owner: str) -> str:
    return ", ".join(v.req_ids) if v.req_ids else owner


def _fmt(ids: list[str]) -> str:
    return ", ".join(dict.fromkeys(ids))


def _section(lines: list[str], start: str, end: str | None) -> list[str]:
    s = next((i for i, ln in enumerate(lines) if ln.startswith(start)), None)
    if s is None:
        return []
    e = len(lines)
    if end:
        e = next((i for i in range(s + 1, len(lines)) if lines[i].startswith(end)), len(lines))
    return lines[s + 1:e]


def self_check(report: Report, markdown: str) -> list[tuple[str, bool, str]]:
    from .report import counts  # local import: report imports this module

    results: list[tuple[str, bool, str]] = []
    lines = markdown.split("\n")

    # 1
    positions, missing, pos = [], [], -1
    for h in _HEADINGS:
        idx = next((i for i, ln in enumerate(lines) if ln.startswith(h)), None)
        if idx is None:
            missing.append(h)
        else:
            positions.append(idx)
    ok = not missing and positions == sorted(positions)
    detail = "" if ok else ("нет разделов: " + "; ".join(missing) if missing else "разделы идут не по порядку")
    results.append(("Все 6 разделов на месте", ok, detail))

    # 2
    p = report.passport
    bad = [n for n, val in (("document", p.document), ("document_title", p.document_title),
                            ("date", p.date), ("model", p.model), ("agent_version", p.agent_version))
           if _empty(val) or str(val).strip().lower() == "unknown"]
    results.append(("Паспорт заполнен", not bad, "не заполнено: " + ", ".join(bad) if bad else ""))

    # 3
    bad = [r.rid for r in report.requirements if _empty(r.quote) or r.location is None]
    results.append(("У каждого требования есть цитата и место", not bad,
                    "без цитаты или места: " + _fmt(bad) if bad else ""))

    # 4
    items = _all_violations(report)
    bad = []
    for v, owner in items:
        miss = _empty(v.quote) or v.location is None
        if v.defect == "contradiction":
            miss = miss or _empty(v.quote2) or v.location2 is None
        if miss:
            bad.append(_label(v, owner))
    results.append(("У каждого нарушения есть цитата и место", not bad,
                    "нарушения без цитаты или места: " + _fmt(bad) if bad else ""))

    # 5
    bad = []
    for v, owner in items:
        expl = str(v.explanation or "").strip().lower().rstrip(".").strip()
        if not expl or _empty(v.fix) or expl in _GENERIC:
            bad.append(_label(v, owner))
    results.append(("Нет общих утверждений", not bad,
                    "пустое или общее объяснение/исправление: " + _fmt(bad) if bad else ""))

    # 6
    sec34 = _section(lines, "## 3.", "## 5.")
    rendered = sum(1 for ln in sec34 if _ITEM.match(ln))
    total = counts(report)["total"]
    rows = [ln for ln in _section(lines, "## 2.", "## 3.") if ln.startswith("|")]
    table_rows = max(len(rows) - 2, 0)
    problems = []
    if rendered != total:
        problems.append(f"замечаний в итоге {total}, в разделах 3–4 {rendered}")
    if table_rows != len(report.requirements):
        problems.append(f"требований {len(report.requirements)}, строк в таблице {table_rows}")
    results.append(("Числа итога сходятся", not problems, "; ".join(problems)))

    # 7
    crit = counts(report)["by_severity"].get("critical", 0)
    ready = report.verdict.ready_for_development
    ok = ready == (crit == 0)
    results.append(("Вердикт согласован с критичными", ok,
                    "" if ok else f"критичных {crit}, но «можно начинать разработку» = {'Да' if ready else 'Нет'}"))

    # 8
    letters = [ch for ch in markdown if ch.isalpha()]
    cyr = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    share = cyr / len(letters) if letters else 0.0
    results.append(("Отчёт на русском", share >= 0.6, "" if share >= 0.6 else f"кириллица {share:.0%} букв"))
    return results
