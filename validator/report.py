"""Markdown report renderer (Russian)."""
from __future__ import annotations

from .models import (
    AGENT_NAME,
    DEFECT_TYPES,
    REQ_CHARACTERISTICS,
    SET_CHARACTERISTICS,
    SEVERITIES,
    SEVERITY_RU,
    Report,
    Violation,
)

_MARK = {"pass": "✅", "fail": "❌", "n/a": "—"}
_RESULT_WORD = {"pass": "✅ соответствует", "fail": "❌ не соответствует", "n/a": "— неприменимо"}


def _cell(value: object) -> str:
    text = " ".join(str(value).split())
    return text.replace("|", "\\|")


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def _short(text: str, limit: int = 120) -> str:
    text = _one_line(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _all_violations(report: Report) -> list[tuple[Violation, list[str]]]:
    """Every violation with the report IDs it belongs to."""
    result: list[tuple[Violation, list[str]]] = []
    for check in report.checks:
        for v in check.violations:
            result.append((v, v.req_ids or [check.rid]))
    for v in report.set_check.violations:
        result.append((v, list(v.req_ids)))
    return result


def counts(report: Report) -> dict:
    """Totals for section 5: total, by_severity, by_defect, critical (list of dicts)."""
    items = _all_violations(report)
    by_severity = {s: 0 for s in SEVERITIES}
    by_defect = {d: 0 for d in DEFECT_TYPES}
    critical: list[dict] = []
    for v, ids in items:
        by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
        by_defect[v.defect] = by_defect.get(v.defect, 0) + 1
        if v.severity == "critical":
            critical.append({
                "req_ids": ids,
                "defect": v.defect,
                "quote": _short(v.quote),
                "location": v.location.ru() if v.location else "не найдено",
            })
    return {
        "requirements": len(report.requirements),
        "total": len(items),
        "by_severity": by_severity,
        "by_defect": by_defect,
        "critical": critical,
    }


def _quote_line(label: str, quote: str, loc) -> str:
    where = f" ({loc.ru()})" if loc else " (место не найдено)"
    return f"{label}: «{_one_line(quote)}»{where}"


def _violation_lines(n: int, v: Violation, with_req_ids: bool = False) -> list[str]:
    defect = DEFECT_TYPES.get(v.defect, v.defect)
    severity = SEVERITY_RU.get(v.severity, v.severity)
    lines = [f"{n}. **{defect}** — {severity}. Критерий: {_one_line(v.criterion)}"]
    lines.append("   - " + _quote_line("Цитата", v.quote, v.location))
    if v.quote2:
        lines.append("   - " + _quote_line("Цитата 2", v.quote2, v.location2))
    if with_req_ids and v.req_ids:
        lines.append("   - Затронутые требования: " + ", ".join(v.req_ids))
    lines.append(f"   - Проблема: {_one_line(v.explanation)}")
    lines.append(f"   - Исправление: {_one_line(v.fix)}")
    return lines


def render(report: Report) -> str:
    p = report.passport
    out: list[str] = []
    out.append(f"# Отчёт о проверке требований: {_one_line(p.document_title)}")
    out.append("")
    out.append(f"Отчёт сформирован автоматически агентом {AGENT_NAME}.")
    out.append("")

    # 1
    out += ["## 1. Паспорт запуска", "", "| Поле | Значение |", "|---|---|"]
    for k, val in (("Документ", p.document), ("Название", p.document_title), ("Дата", p.date),
                   ("Модель", p.model), ("Версия агента", p.agent_version)):
        out.append(f"| {k} | {_cell(val)} |")
    out.append("")

    # 2
    out += ["## 2. Таблица требований", "",
            "| ID отчёта | ID документа | Раздел | Место | Краткая формулировка | Цитата |",
            "|---|---|---|---|---|---|"]
    for r in report.requirements:
        place = r.location.ru() if r.location else "не найдено"
        out.append("| " + " | ".join([
            _cell(r.rid), _cell(r.doc_id) if r.doc_id else "—", _cell(r.section), _cell(place),
            _cell(r.summary), "«" + _cell(r.quote) + "»"]) + " |")
    out.append("")

    # 3
    out += ["## 3. Проверка каждого требования", ""]
    reqs = {r.rid: r for r in report.requirements}
    for check in report.checks:
        r = reqs.get(check.rid)
        head = check.rid
        if r is not None:
            if r.doc_id:
                head += f" ({_one_line(r.doc_id)})"
            head += f": {_one_line(r.summary)}"
        out += [f"### {head}", ""]
        parts = [f"{REQ_CHARACTERISTICS[k]} {_MARK.get(check.characteristics.get(k, 'n/a'), '—')}"
                 for k in REQ_CHARACTERISTICS if k in check.characteristics]
        if parts:
            out += ["; ".join(parts), ""]
        if check.violations:
            for i, v in enumerate(check.violations, 1):
                out += _violation_lines(i, v)
        else:
            out.append("Нарушений не найдено.")
        out.append("")

    # 4
    out += ["## 4. Проверка набора целиком", "", "| Характеристика | Результат |", "|---|---|"]
    for k, label in SET_CHARACTERISTICS.items():
        res = report.set_check.characteristics.get(k, "n/a")
        out.append(f"| {_cell(label)} | {_RESULT_WORD.get(res, res)} |")
    out.append("")
    if report.set_check.violations:
        for i, v in enumerate(report.set_check.violations, 1):
            out += _violation_lines(i, v, with_req_ids=True)
    else:
        out.append("Нарушений не найдено.")
    out.append("")

    # 5
    c = counts(report)
    out += ["## 5. Итог", "",
            f"- Требований проверено: {c['requirements']}",
            f"- Всего замечаний: {c['total']}"]
    for s in SEVERITIES:
        out.append(f"- {SEVERITY_RU[s]}: {c['by_severity'].get(s, 0)}")
    out += ["", "| Тип дефекта | Количество |", "|---|---|"]
    for d, n in c["by_defect"].items():
        out.append(f"| {_cell(DEFECT_TYPES.get(d, d))} | {n} |")
    out += ["", "Критичные дефекты:", ""]
    if c["critical"]:
        for i, item in enumerate(c["critical"], 1):
            ids = ", ".join(item["req_ids"]) or "набор"
            out.append(f"{i}. {ids} — {DEFECT_TYPES.get(item['defect'], item['defect'])}: "
                       f"«{item['quote']}» ({item['location']})")
    else:
        out.append("Критичных дефектов не найдено.")
    if report.dropped_violations > 0:
        out += ["", f"Отброшено замечаний без подтверждённой цитаты: {report.dropped_violations}"]
    out.append("")

    # 6
    vd = report.verdict
    yn = lambda b: "Да" if b else "Нет"  # noqa: E731
    out += ["## 6. Вердикт", "",
            f"- Можно начинать разработку: {yn(vd.ready_for_development)}",
            f"- Можно начинать тестирование: {yn(vd.ready_for_testing)}", "",
            _one_line(vd.summary), "", "Что исправить первым:", ""]
    if vd.fix_first:
        for i, item in enumerate(vd.fix_first, 1):
            out.append(f"{i}. {_one_line(item)}")
    else:
        out.append("Нет обязательных исправлений.")
    out.append("")
    return "\n".join(out)
