"""Deterministic fact checks: contradictions and gaps that code finds without an LLM.

Every rule is generic (any REST/requirements document), and every finding cites exact lines.
The critic stage still reviews these findings like the LLM ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Document, Location, Violation

TAG = "[проверка кодом]"

REASON_PHRASES = {
    200: "ok", 201: "created", 202: "accepted", 204: "no content", 301: "moved permanently",
    302: "found", 304: "not modified", 400: "bad request", 401: "unauthorized",
    403: "forbidden", 404: "not found", 405: "method not allowed", 409: "conflict",
    415: "unsupported media type", 422: "unprocessable", 429: "too many requests",
    500: "internal server error", 502: "bad gateway", 503: "service unavailable",
    504: "gateway timeout",
}
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*)$")
_STATUS_SECTION = re.compile(r"статус|status|коды ответ|response code", re.I)
_CODE_CONTEXT = re.compile(r"http|статус|код|ответ|возвращ|результат|успешн|status|response", re.I)
_CODE = re.compile(r"(?<![\d.,])\b([1-5]\d\d)\b(?![.,]?\d)(?!\s*(?:₸|тенге|тиын|руб|%|мс|ms|сек|мин|шт|символ|знак|bit|бит))",
                   re.I)
_BOLD_CODE = re.compile(r"^\s*\*\*([1-5]\d\d)\.\*\*")
_RETURNS = re.compile(r"возвращает(?:ся)?\s+(?:код\s+)?([1-5]\d\d)\b", re.I)
_METHOD = re.compile(r"(rest type|http[- ]?метод|method|метод)\W[^\n]*?\b(GET|POST|PUT|PATCH|DELETE)\b", re.I)
_WRITE_WORDS = re.compile(r"сохран|замен|созда|удал|измен|запис|обнов", re.I)
_NO_BODY = re.compile(r"тело\s+(?:запроса\s+)?не\s+использ|тело\s+отсутств|без\s+тела|body\s+is\s+not\s+used",
                      re.I)
_MD = re.compile(r"\*\*|`")


@dataclass
class _Ctx:
    doc: Document
    text: dict[int, str]
    section: dict[int, str]  # line → nearest heading above
    heading_lines: set[int]


def _context(doc: Document) -> _Ctx:
    section, heading_lines, current = {}, set(), ""
    for line in doc.lines:
        m = _HEADING.match(line.text)
        if m:
            current = m.group(2).strip()
            heading_lines.add(line.no)
        section[line.no] = current
    return _Ctx(doc, {l.no: l.text for l in doc.lines}, section, heading_lines)


def _clean(text: str) -> str:
    t = _MD.sub("", text).strip()
    t = re.sub(r"^#{1,6}\s+", "", t)
    if t.startswith("|"):
        t = " | ".join(c.strip() for c in t.strip("|").split("|") if c.strip())
    return re.sub(r"^[*\-+]\s+", "", t).strip()


def _loc(ctx: _Ctx, no: int) -> Location:
    page = next((l.page for l in ctx.doc.lines if l.no == no), None)
    return Location(no, no, page)


def _v(ctx: _Ctx, defect: str, severity: str, criterion: str, line: int, explanation: str,
       fix: str, line2: int | None = None) -> Violation:
    return Violation(
        defect=defect, criterion=criterion, severity=severity, quote=_clean(ctx.text[line]),
        quote2=_clean(ctx.text[line2]) if line2 else None, location=_loc(ctx, line),
        location2=_loc(ctx, line2) if line2 else None, explanation=f"{TAG} {explanation}",
        fix=fix, verified=True)


# --- rules ----------------------------------------------------------------------------------

def _status_sections(ctx: _Ctx) -> list[int]:
    """Lines that belong to a 'statuses' section (its heading and everything below it until a
    heading of the same or higher level)."""
    lines, inside_level = [], None
    for line in ctx.doc.lines:
        m = _HEADING.match(line.text)
        if m:
            level = len(m.group(1))
            if _STATUS_SECTION.search(m.group(2)):
                inside_level = level
            elif inside_level is not None and level <= inside_level:
                inside_level = None
        if inside_level is not None:
            lines.append(line.no)
    return lines


def _described_codes(ctx: _Ctx, status_lines: list[int]) -> dict[int, int]:
    described: dict[int, int] = {}
    for no in status_lines:
        t = ctx.text[no]
        m = _HEADING.match(t)
        code = None
        if m:
            c = re.match(r"\s*([1-5]\d\d)\b", m.group(2))
            code = int(c.group(1)) if c else None
        else:
            b = _BOLD_CODE.match(t)
            code = int(b.group(1)) if b else None
        if code and code not in described:
            described[code] = no
    return described


def rule_undescribed_codes(ctx: _Ctx) -> list[Violation]:
    """A response code is used in the text but the statuses section does not describe it."""
    status_lines = _status_sections(ctx)
    if not status_lines:
        return []
    described = _described_codes(ctx, status_lines)
    first_use: dict[int, int] = {}
    for line in ctx.doc.lines:
        if line.no in ctx.heading_lines or not _CODE_CONTEXT.search(line.text):
            continue
        if line.no in status_lines and (_BOLD_CODE.match(line.text) or _HEADING.match(line.text)):
            continue
        for m in _CODE.finditer(line.text):
            code = int(m.group(1))
            if code in REASON_PHRASES:
                first_use.setdefault(code, line.no)
    out = []
    for code, no in sorted(first_use.items(), key=lambda x: x[1]):
        if code in described or code not in REASON_PHRASES:
            continue
        out.append(_v(ctx, "gap", "major", "ISO/IEC/IEEE 29148 5.2.6 — Полнота", no,
                      f"Код ответа {code} используется в тексте, но в разделе статусов его "
                      f"описания нет (описаны: {', '.join(map(str, sorted(described)))}).",
                      f"Добавить в раздел статусов код {code}: условие возврата и пример тела ответа."))
    return out


def rule_reason_phrase(ctx: _Ctx) -> list[Violation]:
    """A status heading carries the reason phrase of another code (e.g. '200 … Created')."""
    out = []
    for no in _status_sections(ctx):
        m = _HEADING.match(ctx.text[no])
        if not m:
            continue
        c = re.match(r"\s*([1-5]\d\d)\b(.*)", m.group(2))
        if not c:
            continue
        code, tail = int(c.group(1)), c.group(2).lower()
        own = REASON_PHRASES.get(code)
        if own and own in tail:
            continue
        for other, phrase in REASON_PHRASES.items():
            if other != code and re.search(rf"\b{re.escape(phrase)}\b", tail):
                out.append(_v(ctx, "other", "major", "IEEE 830 4.3.1 — Корректность", no,
                              f"Заголовок статуса {code} содержит «{phrase.title()}» — это "
                              f"название кода {other}, а не {code}.",
                              f"Выбрать один код: «{code} {(own or '').title()}» или «{other} "
                              f"{phrase.title()}», и привести заголовок и пример к нему."))
                break
    return out


def rule_status_body_conflict(ctx: _Ctx) -> list[Violation]:
    """Inside the section of status NNN the text says that another code MMM is returned."""
    out = []
    status_lines = _status_sections(ctx)
    current, heading_no = None, None
    for no in status_lines:
        m = _HEADING.match(ctx.text[no])
        if m:
            c = re.match(r"\s*([1-5]\d\d)\b", m.group(2))
            current, heading_no = (int(c.group(1)), no) if c else (None, None)
            continue
        r = _RETURNS.search(ctx.text[no])
        if current and r and int(r.group(1)) != current:
            other = int(r.group(1))
            out.append(_v(ctx, "contradiction", "critical",
                          "ISO/IEC/IEEE 29148 5.2.5 — Непротиворечивость", heading_no,
                          f"Раздел описывает статус {current}, а текст внутри раздела говорит, "
                          f"что возвращается {other}. Клиент и тест не знают, какой код ждать.",
                          f"Оставить один код для этого случая ({current} или {other}) и "
                          f"описать его в отдельном разделе статуса.", line2=no))
    return out


def rule_method_semantics(ctx: _Ctx) -> list[Violation]:
    """GET (safe, read-only) is declared for an operation that writes data."""
    out = []
    for line in ctx.doc.lines:
        m = _METHOD.search(line.text)
        if not m or m.group(2).upper() != "GET":
            continue
        # The operation description: lines near the method line and the method logic section.
        window = [l for l in ctx.doc.lines if 0 < abs(l.no - line.no) <= 3 or
                  re.search(r"логик|logic|description|описан", ctx.section.get(l.no, ""), re.I)]
        writer = next((l for l in window if _WRITE_WORDS.search(l.text)
                       and l.no not in ctx.heading_lines), None)
        if writer:
            out.append(_v(ctx, "contradiction", "critical", "IEEE 830 4.3.1 — Корректность",
                          line.no,
                          "Метод GET безопасен и не меняет данные (RFC 9110, 9.2.1), а операция "
                          "сохраняет или заменяет данные. Кэш, прокси или повтор запроса могут "
                          "сломать запись.",
                          "Заменить GET на POST или PUT и передавать параметры в теле запроса.",
                          line2=writer.no))
        break
    return out


def rule_content_type_without_body(ctx: _Ctx) -> list[Violation]:
    out = []
    for line in ctx.doc.lines:
        if "content-type" not in line.text.lower():
            continue
        near = next((l for l in ctx.doc.lines if 0 < l.no - line.no <= 3
                     and _NO_BODY.search(l.text)), None)
        if near:
            out.append(_v(ctx, "contradiction", "major",
                          "ISO/IEC/IEEE 29148 5.2.5 — Непротиворечивость", line.no,
                          "Задан тип тела запроса, но сказано, что тела нет. Неясно, какой "
                          "заголовок отправляет клиент и проверяет сервер.",
                          "Убрать Content-Type для запроса без тела или описать тело запроса.",
                          line2=near.no))
    return out


def rule_money_units(ctx: _Ctx) -> list[Violation]:
    """Two money units for related amounts without a conversion rule."""
    for line in ctx.doc.lines:
        low = line.text.lower()
        if "тиын" in low and ("тенге" in low or "₸" in low):
            if re.search(r"1\s*тенге\s*=\s*100|100\s*тиын", ctx.doc.text, re.I):
                return []
            return [_v(ctx, "gap", "major", "ISO/IEC/IEEE 29148 5.2.5 — Однозначность", line.no,
                       "Суммы хранятся в двух единицах (тиыны и тенге). Правило пересчёта и "
                       "округления при сравнении с порогом не задано.",
                       "Хранить сумму и порог в одной единице (тиыны) или задать правило: "
                       "«порог в тенге × 100 сравнивается с суммой в тиынах».")]
    return []


def _tables(ctx: _Ctx) -> list[list[tuple[int, list[str]]]]:
    tables, cur = [], []
    for line in ctx.doc.lines:
        if line.text.strip().startswith("|"):
            cells = [c.strip() for c in line.text.strip().strip("|").split("|")]
            cur.append((line.no, cells))
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


def rule_field_type_mismatch(ctx: _Ctx) -> list[Violation]:
    """The same field has different formats in two tables (e.g. request vs response)."""
    seen: dict[str, tuple[str, int]] = {}
    out = []
    for table in _tables(ctx):
        header = [h.lower() for h in table[0][1]]
        if "format" not in header and "формат" not in header and "тип" not in header:
            continue
        name_i = next((i for i, h in enumerate(header) if h in ("parameter", "параметр", "поле", "field")), 0)
        fmt_i = next(i for i, h in enumerate(header) if h in ("format", "формат", "тип"))
        for no, cells in table[1:]:
            if len(cells) <= max(name_i, fmt_i) or set("".join(cells)) <= set(":- "):
                continue
            name = cells[name_i].strip("` ").lower()
            fmt = cells[fmt_i].strip("` ").lower()
            if not name or not fmt:
                continue
            if name in seen and seen[name][0] != fmt:
                out.append(_v(ctx, "contradiction", "major",
                              "ISO/IEC/IEEE 29148 5.2.5 — Непротиворечивость", seen[name][1],
                              f"Поле «{name}» имеет формат {seen[name][0]} в одной таблице и "
                              f"{fmt} в другой.",
                              f"Задать одному полю «{name}» один формат во всех таблицах.",
                              line2=no))
            seen.setdefault(name, (fmt, no))
    return out


RULES = [rule_undescribed_codes, rule_reason_phrase, rule_status_body_conflict,
         rule_method_semantics, rule_content_type_without_body, rule_money_units,
         rule_field_type_mismatch]


def find_facts(document: Document) -> list[Violation]:
    ctx = _context(document)
    out: list[Violation] = []
    for rule in RULES:
        out.extend(rule(ctx))
    return out
