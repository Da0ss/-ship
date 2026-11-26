"""Markdown report renderer (Russian)."""
from __future__ import annotations

from .criteria import normalize_criterion
from .selfcheck import self_check
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


TAG_RU = {"fintech": "🏦 банковская специфика", "security": "🔒 информационная безопасность"}
_LETTERS = "АБВГД"


def _fix_lines(v: Violation) -> list[str]:
    if v.fix_type != "question":
        return [f"   - Исправление: {_one_line(v.fix)}"]
    if not v.options:
        return [f"   - Предложение агента (значения нет в документе, решает владелец продукта): "
                f"{_one_line(v.fix)}"]
    out = [f"   - Вопрос владельцу продукта: {_one_line(v.fix)}"]
    out += [f"     - Вариант {_LETTERS[i]}: {_one_line(o)}" for i, o in enumerate(v.options[:5])]
    return out


def _violation_lines(n: int, v: Violation, with_req_ids: bool = False,
                     owner: str | None = None) -> list[str]:
    defect = DEFECT_TYPES.get(v.defect, v.defect)
    severity = SEVERITY_RU.get(v.severity, v.severity)
    criterion = _one_line(normalize_criterion(v.criterion, v.defect, "set" if with_req_ids else "req"))
    tag = f" · {TAG_RU[v.tag]}" if v.tag in TAG_RU else ""
    lines = [f"{n}. **{defect}** — {severity}{tag}. Критерий: {criterion}"]
    lines.append("   - " + _quote_line("Цитата", v.quote, v.location))
    if v.quote2:
        lines.append("   - " + _quote_line("Цитата 2", v.quote2, v.location2))
    if with_req_ids and v.req_ids:
        lines.append("   - Затронутые требования: " + ", ".join(v.req_ids))
    elif owner:
        others = [r for r in v.req_ids if r != owner]
        if others:
            lines.append("   - Также относится к: " + ", ".join(others))
    if v.also_at:
        places = sorted({l.line_start for l in v.also_at})
        shown = ", ".join(map(str, places[:12])) + (" …" if len(places) > 12 else "")
        lines.append(f"   - Ещё места: строки {shown}")
    lines.append(f"   - Проблема: {_one_line(v.explanation)}")
    lines += _fix_lines(v)
    return lines


def _place(v: Violation) -> str:
    locs = [l for l in (v.location, v.location2) if l]
    if not locs:
        return "место не найдено"
    nums = sorted({l.line_start for l in locs})
    return ("строка " if len(nums) == 1 else "строки ") + ", ".join(map(str, nums))


def blockers(report: Report) -> list[tuple[list[str], list[str], Violation]]:
    """Critical findings grouped by place: (req IDs, defect names, first violation)."""
    groups: dict[str, list] = {}
    for v, ids in _all_violations(report):
        if v.severity != "critical":
            continue
        g = groups.setdefault(_place(v), [[], [], v])
        g[0] += [i for i in ids if i not in g[0]]
        name = DEFECT_TYPES.get(v.defect, v.defect)
        if name not in g[1]:
            g[1].append(name)
    return list(groups.values())


def questions(report: Report) -> list[tuple[Violation, list[str]]]:
    return [(v, ids) for v, ids in _all_violations(report) if v.fix_type == "question"]


_STATUS_RU = {"yes": "✅ Да", "no": "❌ Нет", "n/a": "— Не требуется"}


def _summary_block(report: Report) -> list[str]:
    c = counts(report)
    vd = report.verdict
    sev = c["by_severity"]
    out = ["## Кратко", ""]
    out.append(f"**Вердикт: {'можно' if vd.ready_for_development else 'нельзя'} начинать разработку "
               f"и тестирование.** Требований: {c['requirements']}, замечаний: {c['total']} "
               f"(критичных {sev.get('critical', 0)}, существенных {sev.get('major', 0)}, "
               f"незначительных {sev.get('minor', 0)}).")
    out.append("")
    bl = blockers(report)
    if bl:
        n = len(bl)
        word = "место" if n % 10 == 1 and n % 100 != 11 else (
            "места" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "мест")
        out += [f"Блокеры ({n} {word} в документе):", ""]
        for i, (ids, names, v) in enumerate(bl[:7], 1):
            out.append(f"{i}. {', '.join(ids) or 'набор'} — {' / '.join(names)} ({_place(v)}): "
                       f"{_short(v.explanation, 170)}")
        if len(bl) > 7:
            out.append(f"   …и ещё {len(bl) - 7}, см. раздел 5.")
        out.append("")
    q = questions(report)
    tagged = sum(1 for v, _ in _all_violations(report) if v.tag in TAG_RU)
    extra = []
    if q:
        extra.append(f"вопросов к владельцу продукта — {len(q)} (раздел 8)")
    if tagged:
        extra.append(f"замечаний по банковской специфике и ИБ — {tagged}")
    if report.checklist:
        done = sum(1 for it in report.checklist if it.status == "yes")
        fail = sum(1 for it in report.checklist if it.status == "no")
        extra.append(f"чек-лист ревью: выполнено {done} из {len(report.checklist)}, "
                     f"не выполнено {fail} (раздел 7)")
    if extra:
        out += ["Также: " + "; ".join(extra) + ".", ""]
    return out


def render(report: Report) -> str:
    p = report.passport
    out: list[str] = []
    out.append(f"# Отчёт о проверке требований: {_one_line(p.document_title)}")
    out.append("")
    out.append(f"Отчёт сформирован автоматически агентом {AGENT_NAME}.")
    out.append("")
    out += _summary_block(report)

    # 1
    out += ["## 1. Паспорт запуска", "", "| Поле | Значение |", "|---|---|"]
    for k, val in (("Документ", p.document), ("Название", p.document_title), ("Дата", p.date),
                   ("Модель", p.model), ("Версия агента", p.agent_version)):
        out.append(f"| {k} | {_cell(val)} |")
    ocr_pages = getattr(p, "ocr_pages", None) or []
    ocr = (f"страницы {', '.join(str(n) for n in ocr_pages)} (распознаны Tesseract)"
           if ocr_pages else "не применялся")
    out.append(f"| OCR | {_cell(ocr)} |")
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
                out += _violation_lines(i, v, owner=check.rid)
        elif check.see_also:
            out.append("Замечания к этому требованию объединены с повторами: см. "
                       + ", ".join(check.see_also) + ".")
        else:
            out.append("Нарушений не найдено.")
        if check.violations and check.see_also:
            out += ["", "Ещё замечания к этому требованию: см. " + ", ".join(check.see_also) + "."]
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
    out += ["", "Критичные дефекты (сгруппированы по месту в документе):", ""]
    bl = blockers(report)
    if bl:
        for i, (ids, names, v) in enumerate(bl, 1):
            out.append(f"{i}. {', '.join(ids) or 'набор'} — {' / '.join(names)}: "
                       f"«{_short(v.quote)}» ({_place(v)})")
    else:
        out.append("Критичных дефектов не найдено.")
    if report.merged_duplicates:
        out += ["", f"Объединено повторяющихся замечаний: {report.merged_duplicates}"]
    if report.dropped_violations > 0:
        out += ["", f"Отброшено замечаний без подтверждённой цитаты: {report.dropped_violations}"]
    out.append("")
    if report.critic_ran:
        k = len(report.critic_rejected)
        out += [f"Критик проверил замечания: подтверждено {c['total']}, исправлено "
                f"{report.critic_adjusted} (критичность, тип или критерий), отклонено {k}.", ""]
        if k:
            out += ["Отклонённые критиком замечания:", ""]
            for i, (v, reason) in enumerate(report.critic_rejected, 1):
                ids = ", ".join(v.req_ids) or "набор"
                where = f" ({v.location.ru()})" if v.location else ""
                out.append(f"{i}. ({ids}) {DEFECT_TYPES.get(v.defect, v.defect)}: "
                           f"«{_short(v.quote, 100)}»{where} — причина: {_one_line(reason)}")
            out.append("")
    else:
        out += ["Критик не запускался.", ""]

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

    # 7
    if report.checklist:
        src = ("чек-лист «Ревью требований» из самого документа"
               if report.checklist_source == "document" else "шаблон чек-листа ревью банка "
               "(в документе чек-листа нет)")
        out += ["## 7. Чек-лист ревью требований", "",
                f"Агент заполнил {src}. Ответ «Да» ставится только при подтверждении в тексте.", "",
                "| № | Проверка | Ответ агента | Обоснование | Место |", "|---|---|---|---|---|"]
        for i, it in enumerate(report.checklist, 1):
            where = it.location.ru() if it.location else "—"
            ev = _one_line(it.evidence)
            if it.quote:
                ev += f" Цитата: «{_short(it.quote, 100)}»."
            if it.refs:
                ev += " (" + ", ".join(it.refs) + ")"
            out.append(f"| {i} | {_cell(it.item)} | {_STATUS_RU.get(it.status, it.status)} | "
                       f"{_cell(ev)} | {_cell(where)} |")
        out.append("")

    # 9 (rendered after 8): acceptance gaps table
    cov_block: list[str] = []
    if report.coverage_gaps:
        cov_block += ["## 9. Условия без критериев приёмки", "",
                      "Сводная таблица к замечанию «Нет критериев приёмки» (раздел 4). Каждая строка — "
                      "условие из требования, для которого в документе нет проверки, и готовый "
                      "критерий, который можно вставить в раздел «Критерии приемки».", "",
                      "| № | Требование | Условие | Место | Предлагаемый критерий приёмки |",
                      "|---|---|---|---|---|"]
        for i, v in enumerate(report.coverage_gaps, 1):
            cond = v.explanation.removeprefix("Нет критерия приёмки для условия: ").split(". Требование покрывают")[0]
            crit = v.fix.removeprefix("Добавить критерий приёмки: ")
            cov_block.append(f"| {i} | {', '.join(v.req_ids)} | {_cell(_short(cond, 140))} | "
                             f"{_cell(v.location.ru() if v.location else '—')} | {_cell(_short(crit, 200))} |")
        cov_block.append("")

    # 8
    q = questions(report)
    if q:
        out += ["## 8. Вопросы к владельцу продукта", "",
                "Эти исправления агент не может сделать сам: нужно бизнес-решение. Агент не "
                "подставляет значения, которых нет в документе.", ""]
        for i, (v, ids) in enumerate(q, 1):
            out.append(f"{i}. ({', '.join(ids) or 'набор'}, {_place(v)}) {_one_line(v.fix)}")
            for j, o in enumerate(v.options[:5]):
                out.append(f"   - {_LETTERS[j]}) {_one_line(o)}")
        out.append("")
    out += cov_block
    body = "\n".join(out)
    rows = ["### Самопроверка отчёта", "", "| Правило | Результат | Подробности |", "|---|---|---|"]
    for rule, ok, detail in self_check(report, body):
        rows.append(f"| {_cell(rule)} | {'✅' if ok else '❌'} | {_cell(detail)} |")
    rows.append("")
    return body + "\n" + "\n".join(rows)
