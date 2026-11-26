"""Bank-domain review on top of the ISO/IEEE checks.

1. FinTech review: an LLM pass over a fixed checklist of banking risks (card operation types,
   currency, refunds, time, duplicates and races, failures, card states, data protection,
   access, rollback, audit, client communication). Findings are verified like any other.
2. Bank review checklist: the agent fills the document's own review checklist
   (rows with "Да / Нет") or, when the document has none, the default bank template.
3. Post-processing: merge duplicate findings; turn fixes with invented values into
   questions for the product owner.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

from . import prompts as P
from .citations import Locator, verify_violations
from .models import (ChecklistItem, DEFECT_TYPES, Document, LLMClient, Requirement,
                     RequirementCheck, SEVERITIES, SetCheck, Violation)

Log = Callable[[str], None]

# --- 1. FinTech review --------------------------------------------------------------------------

FINTECH_TOPICS = [
    ("operation_type", "Тип карточной операции: авторизация (холд), списание после клиринга, "
                       "отмена авторизации, реверсал. Какие события учитываются и в какой момент."),
    ("currency", "Валюта и суммы: операции в иностранной валюте по карте в тенге, курс, "
                 "комиссия, округление, единицы (тиын/тенге)."),
    ("refunds", "Возвраты: частичный возврат, возврат в другом периоде, возврат после "
                "уведомления, опротестование (chargeback)."),
    ("time", "Время: часовой пояс, граница периода, дата операции против даты проведения, "
             "запоздавшие события."),
    ("duplicates_races", "Повторы и порядок: ключ идемпотентности, дубли событий, события не по "
                         "порядку, две операции одновременно (гонка, двойное уведомление)."),
    ("failures", "Сбои: недоступность зависимых сервисов, повторная отправка, тайм-ауты, что "
                 "видит клиент."),
    ("card_states", "Состояния карты и клиента: блокировка, перевыпуск, закрытие, дополнительные "
                    "карты, смена владельца, карта стала неактивной."),
    ("data_protection", "Защита данных: номер или идентификатор карты, суммы, ПДн в URL, логах, "
                        "тексте уведомления; маскирование; PCI DSS."),
    ("access", "Доступ: проверка владельца, сессия, токены, доступ к чужим данным (IDOR), "
               "раскрытие существования чужой карты."),
    ("rollback", "Откат: выключатель функции (feature flag), план отката, судьба данных при "
                 "откате."),
    ("audit", "Аудит и мониторинг: журнал изменения настроек, метрики доставки, алерты."),
    ("communication", "Коммуникация с клиентом: текст уведомления, язык (русский/казахский), "
                      "согласие и отключение уведомлений, состояние при включении."),
]

DOMAIN_SYSTEM = (
    "Ты — старший банковский аналитик и специалист по информационной безопасности карточных "
    "продуктов. Ты ищешь в требованиях пропуски и риски, которые знает банк, но не видят "
    "стандарты ISO/IEEE: карточные операции, деньги, время, повторы, сбои, данные клиента, "
    "доступ, откат.\n" + P.COMMON_RULES + P.SEVERITY_RULES
)

DOMAIN_PROMPT = """Проверь документ по темам банковского чек-листа:
{topics}

Правила:
- Пиши замечание, только если тема касается функции из документа и документ её не решает
  или решает противоречиво. Не пиши замечание "на всякий случай".
- Не повторяй уже найденные замечания (список ниже).
- quote — дословная цитата места документа, к которому относится пропуск (требование,
  поле, шаг, таблица), 3–25 слов.
- explanation называет конкретный сценарий, который ломается: какие события, какие суммы,
  что получит клиент или банк.
- tag: "security" для тем data_protection и access, иначе "fintech".
- defect: обычно gap, ambiguity или contradiction; criterion по ISO/IEC/IEEE 29148
  (5.2.5 для одного требования, 5.2.6 для набора).
- Почти всегда это бизнес-решение: fix_type "question", options — 2–3 варианта.
- Не больше {limit} замечаний, самые важные первыми.

Требования (ID отчёта и цитаты):
{requirements}

Уже найденные замечания (не повторять):
{existing}

Документ:
<document>
{document}
</document>

Верни объект {{"findings": [...]}}."""

DOMAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **P.SET_VIOLATION_SCHEMA["properties"],
                    "topic": P._str(enum=[k for k, _ in FINTECH_TOPICS]),
                    "tag": P._str(enum=["fintech", "security"]),
                },
                "required": P.SET_VIOLATION_SCHEMA["required"] + ["topic", "tag"],
            },
        }
    },
    "required": ["findings"],
}


def _short_view(v: Violation) -> str:
    where = f"строка {v.location.line_start}" if v.location else "?"
    return f"- {DEFECT_TYPES.get(v.defect, v.defect)} ({where}): {v.explanation[:160]}"


def _group(defect: str) -> str:
    return "contradiction" if defect == "contradiction" else "other"


def _locs(v: Violation) -> list[tuple[int, int]]:
    return [(l.line_start, l.line_end) for l in (v.location, v.location2) if l]


def _overlap(a: Violation, b: Violation) -> bool:
    return any(x1 <= y2 and y1 <= x2 for x1, x2 in _locs(a) for y1, y2 in _locs(b))


def domain_review(document: Document, client: LLMClient, locator: Locator,
                  reqs: list[Requirement], checks: list[RequirementCheck], set_check: SetCheck,
                  log: Log, limit: int = 10) -> int:
    existing = [v for c in checks for v in c.violations] + set_check.violations
    log("Банковская проверка: карточные операции, деньги, время, повторы, ИБ, откат…")
    prompt = DOMAIN_PROMPT.format(
        topics="\n".join(f"- {k}: {d}" for k, d in FINTECH_TOPICS),
        limit=limit,
        requirements=P.dumps([{"rid": r.rid, "doc_id": r.doc_id, "quote": r.quote[:200]}
                              for r in reqs]),
        existing="\n".join(_short_view(v) for v in existing) or "нет",
        document=document.numbered_text(),
    )
    try:
        data = client.generate_json(DOMAIN_SYSTEM, prompt, DOMAIN_SCHEMA)
    except Exception as exc:  # the domain pass is an addition; the report stays valid without it
        log(f"  банковская проверка пропущена: {exc}")
        return 0
    from .pipeline import _parse_violation, _rids_at

    raw = (data.get("findings") or []) if isinstance(data, dict) else []
    parsed = []
    for item in raw[:limit]:
        v = _parse_violation(item, [])
        if v is None:
            continue
        v.tag = "security" if item.get("tag") == "security" or item.get("topic") in (
            "data_protection", "access") else "fintech"
        parsed.append(v)
    kept, dropped = verify_violations(locator, parsed, {})
    known = {r.rid for r in reqs}
    added = 0
    for v in kept:
        # Only an exact repeat is skipped here: a bank finding often cites the same line as an
        # ISO finding but names another risk. Real repeats are left to the critic.
        if any(o.defect == v.defect and o.quote == v.quote and o.quote2 == v.quote2
               for o in existing):
            continue
        ids = [r for r in v.req_ids if r in known] or _rids_at(reqs, v)
        v.req_ids = ids
        set_check.violations.append(v)
        existing.append(v)
        added += 1
        if v.defect == "gap":
            set_check.characteristics["complete"] = "fail"
        elif v.defect == "contradiction":
            set_check.characteristics["consistent"] = "fail"
    log(f"  модель вернула: {len(raw)}; добавлено: {added}; без цитаты отброшено: {dropped}")
    return added


# --- 2. Bank review checklist -------------------------------------------------------------------

# Default checklist of a bank requirement review (used when the document has no checklist).
TEMPLATE_CHECKLIST = [
    "Техническое требование зафиксировано и покрывает бизнес-требование",
    "Функциональная спецификация имеет структурированное описание по шаблону",
    "Описаны взаимодействия систем и компонентов (sequence-диаграммы)",
    "Требование покрывает спецификацию UX/UI",
    "Предусмотрена схема отката или скрытия функционала",
    "Требование может быть протестировано",
    "Описаны модель данных или поток данных",
    "Нет противоречий и явных операционных рисков",
    "Архитектура описана и согласована",
    "Изменения в других системах согласованы с ответственными",
    "Нет рисков информационной безопасности",
    "Есть схема полного бизнес-процесса",
    "Интеграционное взаимодействие поставлено чётко",
    "Версия документа зафиксирована",
]

_YESNO = re.compile(r"да\s*/\s*нет", re.I)

CHECKLIST_SYSTEM = (
    "Ты — аудитор процесса ревью требований в банке. Ты заполняешь чек-лист ревью по тексту "
    "документа и по уже подтверждённым замечаниям. Ты не придумываешь факты.\n" + P.COMMON_RULES
)

CHECKLIST_PROMPT = """Заполни чек-лист ревью для документа.

Для каждого пункта (cid) верни:
- status: "yes" — документ выполняет пункт; "no" — не выполняет; "n/a" — пункт не относится
  к этой доработке (объясни почему);
- evidence: одно предложение по-русски, почему так, с конкретными словами или разделом
  документа или номерами замечаний;
- quote: дословная цитата места документа, которое подтверждает ответ (3–25 слов), или null,
  если ответ "нет, этого в документе нет";
- refs: ID требований отчёта (R-..), к которым относится ответ.
Если по пункту есть подтверждённое критичное или существенное замечание — ответ "no".
Сам чек-лист с пустыми отметками не доказывает выполнение пункта.

Пункты чек-листа:
{items}

Подтверждённые замечания (ID требований — тип — критичность — строка — суть):
{findings}

Документ:
<document>
{document}
</document>

Верни объект {{"items": [...]}}."""

CHECKLIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cid": P._str(),
                    "status": P._str(enum=["yes", "no", "n/a"]),
                    "evidence": P._str(),
                    "quote": P._str(nullable=True),
                    "refs": {"type": "array", "items": P._str()},
                },
                "required": ["cid", "status", "evidence"],
            },
        }
    },
    "required": ["items"],
}


def find_document_checklist(document: Document) -> list[tuple[int, str]]:
    """Rows of a review checklist table in the document: cells with 'Да / Нет' marks."""
    rows = []
    for line in document.lines:
        t = line.text.strip()
        if not t.startswith("|") or not _YESNO.search(t):
            continue
        cells = [c.strip() for c in t.strip("|").split("|")]
        first = re.sub(r"[*`]", "", cells[0]).strip() if cells else ""
        if first and not _YESNO.search(first):
            rows.append((line.no, first))
    return rows


def fill_checklist(document: Document, client: LLMClient, locator: Locator,
                   reqs: list[Requirement], checks: list[RequirementCheck], set_check: SetCheck,
                   log: Log) -> tuple[list[ChecklistItem], str]:
    rows = find_document_checklist(document)
    source = "document" if len(rows) >= 3 else "template"
    items = [t for _, t in rows] if source == "document" else list(TEMPLATE_CHECKLIST)
    log(f"Чек-лист ревью: {len(items)} пунктов ({'из документа' if source == 'document' else 'шаблон банка'})")
    findings = []
    for v in [v for c in checks for v in c.violations] + set_check.violations:
        where = v.location.line_start if v.location else "?"
        findings.append(f"- {', '.join(v.req_ids) or 'набор'} — {DEFECT_TYPES.get(v.defect)} — "
                        f"{v.severity} — строка {where} — {v.explanation[:140]}")
    prompt = CHECKLIST_PROMPT.format(
        items=P.dumps([{"cid": f"C-{i:02d}", "item": t} for i, t in enumerate(items, 1)]),
        findings="\n".join(findings) or "нет",
        document=document.numbered_text(),
    )
    try:
        data = client.generate_json(CHECKLIST_SYSTEM, prompt, CHECKLIST_SCHEMA)
    except Exception as exc:
        log(f"  чек-лист не заполнен: {exc}")
        return [], ""
    answers = {}
    for a in (data.get("items") or []) if isinstance(data, dict) else []:
        if isinstance(a, dict) and a.get("cid"):
            answers[a["cid"]] = a
    known = {r.rid for r in reqs}
    out = []
    for i, text in enumerate(items, 1):
        a = answers.get(f"C-{i:02d}", {})
        status = a.get("status") if a.get("status") in ("yes", "no", "n/a") else "no"
        evidence = str(a.get("evidence") or "").strip() or "Модель не дала ответа; пункт не подтверждён."
        quote = str(a.get("quote") or "").strip() or None
        loc = locator.locate(quote) if quote else None
        if quote and loc is None:
            quote = None  # an unconfirmed quote is not shown
        elif quote:
            quote = locator.exact_quote(quote, loc)
        refs = [r for r in a.get("refs") or [] if r in known]
        out.append(ChecklistItem(text, status, evidence, quote, loc, refs))
    return out, source


# --- 3. Post-processing -------------------------------------------------------------------------

_NUM = re.compile(r"\d[\d\s  ]*\d|\d")


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m.group()) for m in _NUM.finditer(text)}


def guard_invented_values(document: Document, checks: list[RequirementCheck],
                          set_check: SetCheck) -> int:
    """A fix that adds a number the document never states is a business decision, not an edit."""
    doc_numbers = _numbers(document.text)
    changed = 0
    for v in [v for c in checks for v in c.violations] + set_check.violations:
        if v.fix_type != "edit" or v.fix.startswith("Добавить критерий приёмки"):
            continue  # acceptance-criteria rows carry test data by design
        new = {n for n in _numbers(v.fix) if n not in doc_numbers and not re.fullmatch(r"\d", n)}
        new -= {n for n in _numbers(" ".join(v.req_ids))}
        if new:
            v.fix_type = "question"
            changed += 1
    return changed


def _pair(v: Violation) -> tuple | None:
    if not v.location:
        return None
    a = v.location.line_start
    b = v.location2.line_start if v.location2 else None
    # A bank/ИБ risk and an ISO finding on the same line are different findings: keep both.
    return (v.defect, v.tag or "", tuple(sorted(x for x in (a, b) if x is not None)))


def dedupe(checks: list[RequirementCheck], set_check: SetCheck) -> int:
    """Merge findings of the same type at the same places (e.g. one contradiction reported
    from both of its requirements). The first one stays and collects all requirement IDs."""
    seen: dict[tuple, Violation] = {}
    merged = 0
    owners: list[tuple[list[Violation], str | None, Any]] = [(c.violations, c.rid, c) for c in checks]
    owners.append((set_check.violations, None, None))
    owner_of: dict[int, str] = {}
    for lst, rid, check in owners:
        for v in list(lst):
            key = _pair(v)
            if key is None:
                continue
            first = seen.get(key)
            if first is None:
                if rid and rid not in v.req_ids:
                    v.req_ids = [rid] + list(v.req_ids)
                seen[key] = v
                owner_of[id(v)] = rid or "раздел 4"
                continue
            for r in ([rid] if rid else []) + list(v.req_ids):
                if r not in first.req_ids:
                    first.req_ids.append(r)
            if SEVERITIES.index(v.severity) < SEVERITIES.index(first.severity):
                first.severity = v.severity
            if v.options and not first.options:
                first.options, first.fix_type = v.options, v.fix_type
            lst.remove(v)
            if check is not None:
                ref = owner_of[id(first)]
                if ref != check.rid and ref not in check.see_also:
                    check.see_also.append(ref)
            merged += 1
    return merged


COVERAGE_PREFIX = "Добавить критерий приёмки"


def group_coverage(set_check: SetCheck, keep_individual: tuple[str, ...] = ("critical",)
                   ) -> list[Violation]:
    """One finding instead of dozens of "no acceptance criterion for condition X".
    Critical coverage gaps stay separate; the rest become one grouped finding whose places are
    listed in also_at. Returns the grouped originals (for the report's coverage table)."""
    cov = [v for v in set_check.violations
           if v.fix.startswith(COVERAGE_PREFIX) and v.severity not in keep_individual
           and v.location is not None]
    if len(cov) < 3:
        return []
    for v in cov:
        set_check.violations.remove(v)
    cov.sort(key=lambda v: v.location.line_start)
    rids: list[str] = []
    for v in cov:
        rids += [r for r in v.req_ids if r not in rids]
    first = cov[0]
    grouped = Violation(
        defect="gap", criterion="ISO/IEC/IEEE 29148 5.2.6 — Полнота", severity="major",
        quote=first.quote, location=first.location,
        explanation=(f"Нет критериев приёмки для {len(cov)} условий в {len(rids)} требованиях: "
                     "граничные значения, ошибки, отключённое состояние и ответы API нельзя "
                     "принять по документу. Полный список с предлагаемыми критериями — в разделе "
                     "«Условия без критериев приёмки»."),
        fix="Добавить в раздел «Критерии приемки» строки из таблицы «Условия без критериев "
            "приёмки» (по одной на условие).",
        req_ids=rids, verified=True,
        also_at=[v.location for v in cov[1:]],
    )
    set_check.violations.append(grouped)
    return cov


def enabled(name: str) -> bool:
    return os.environ.get(name, "on").lower() != "off"
