"""Agent pipeline: extract → check each requirement → check the set → verdict."""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from itertools import combinations
from typing import Any

from . import prompts as P
from .citations import Locator, attach_requirement_locations, normalize, verify_violations
from .models import (
    DEFECT_TYPES, REQ_CHARACTERISTICS, SET_CHARACTERISTICS, SEVERITIES, Document, LLMClient,
    Passport, Report, Requirement, RequirementCheck, SetCheck, Verdict, Violation,
)

Log = Callable[[str], None]

# Defect type → characteristic that the defect breaks.
DEFECT_TO_CHAR = {
    "ambiguity": "unambiguous",
    "unverifiable": "verifiable",
    "contradiction": "consistent",
    "gap": "complete",
    "design": "implementation_free",
    "non_singular": "singular",
    "traceability": "traceable",
}

# Concepts for contradiction candidates: requirements that mention one concept are compared.
CONCEPTS: dict[str, str] = {
    "порог/сумма": r"порог|amount|limit|сумм|₸|тенге|тиын",
    "уведомление": r"push|уведомлен",
    "месяц/дата/время": r"месяц|дат[аеуы]|алматы|январ|феврал|календар|минут|\d{1,2}:\d{2}",
    "карта": r"карт|card",
    "HTTP-метод/URL": r"\bget\b|\bpost\b|\bput\b|rest type|savelimit|curl|чтени|сохранени|сохраня",
    "коды ответа": r"\b(200|201|400|401|403|404|500)\b|error|result|ok\b",
    "повтор/идемпотентность": r"повтор|идентификатор операции|замен",
    "возврат/исключения": r"возврат|перевод|сняти|проведен|провести",
    "включение": r"enabled|переключател|выключен|включен",
}


def _log_default(msg: str) -> None:
    print(msg, file=sys.stderr)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _status(value: Any) -> str:
    return value if value in ("pass", "fail", "n/a") else "n/a"


# --- stage 1: extraction ------------------------------------------------------------------------

def extract_requirements(document: Document, client: LLMClient, locator: Locator,
                         log: Log = _log_default) -> list[Requirement]:
    log("Шаг 1/4: нарезка документа на требования…")
    data = client.generate_json(
        P.EXTRACT_SYSTEM,
        P.EXTRACT_PROMPT.format(kinds=", ".join(P.KINDS), document=document.numbered_text()),
        P.EXTRACT_SCHEMA,
    )
    items = data.get("requirements", []) if isinstance(data, dict) else data or []
    reqs: list[Requirement] = []
    hints: dict[str, tuple[int, int]] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not str(item.get("quote", "")).strip():
            continue
        tmp = f"T{i}"
        doc_id = item.get("doc_id")
        doc_id = str(doc_id).strip() if doc_id not in (None, "", "null", "—", "-") else None
        reqs.append(Requirement(
            rid=tmp, doc_id=doc_id, summary=str(item.get("summary", "")).strip(),
            quote=str(item["quote"]).strip(), section=str(item.get("section", "")).strip(" #"),
            kind=str(item.get("kind", "other")),
        ))
        a = _int(item.get("line_start"))
        hints[tmp] = (a, max(a, _int(item.get("line_end"), a)))
    located = attach_requirement_locations(locator, reqs, hints)
    lost = len(reqs) - len(located)
    if lost:
        log(f"  отброшено требований без подтверждённой цитаты: {lost}")

    # Drop duplicates: the same document ID, or one quote inside another on the same lines.
    unique: list[Requirement] = []
    for req in sorted(located, key=lambda r: (r.location.line_start, r.location.line_end)):
        if any(_duplicate(req, u) for u in unique):
            continue
        unique.append(req)
    width = max(2, len(str(len(unique))))
    for n, req in enumerate(unique, 1):
        req.rid = f"R-{n:0{width}d}"
    log(f"  найдено требований: {len(unique)}")
    return unique


def _duplicate(a: Requirement, b: Requirement) -> bool:
    if a.doc_id and a.doc_id == b.doc_id:
        return True
    la, lb = a.location, b.location
    if (la.line_start, la.line_end) != (lb.line_start, lb.line_end):
        return False
    qa, qb = normalize(a.quote), normalize(b.quote)
    return qa in qb or qb in qa


# --- stage 2: per-requirement check -------------------------------------------------------------

def weak_word_hints(reqs: list[Requirement]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for req in reqs:
        text = normalize(req.quote)
        found = [w for w in P.WEAK_WORDS if re.search(r"(?<!\w)" + re.escape(normalize(w)), text)]
        if found:
            out[req.rid] = found
    return out


def _parse_violation(raw: Any, req_ids: list[str]) -> Violation | None:
    if not isinstance(raw, dict) or not str(raw.get("quote", "")).strip():
        return None
    defect = raw.get("defect") if raw.get("defect") in DEFECT_TYPES else "other"
    severity = raw.get("severity") if raw.get("severity") in SEVERITIES else "major"
    quote2 = raw.get("quote2")
    quote2 = str(quote2).strip() if quote2 not in (None, "", "null") else None
    return Violation(
        defect=defect, criterion=str(raw.get("criterion", "")).strip(), severity=severity,
        quote=str(raw["quote"]).strip(), quote2=quote2,
        explanation=str(raw.get("explanation", "")).strip(), fix=str(raw.get("fix", "")).strip(),
        req_ids=req_ids,
    )


def _req_view(req: Requirement) -> dict[str, Any]:
    return {"rid": req.rid, "doc_id": req.doc_id, "section": req.section, "kind": req.kind,
            "lines": [req.location.line_start, req.location.line_end], "quote": req.quote}


def _apply_evidence(chars: dict[str, str], violations: list[Violation]) -> dict[str, str]:
    """A characteristic fails only with a verified violation (no unfounded claims)."""
    broken = {DEFECT_TO_CHAR[v.defect] for v in violations if v.defect in DEFECT_TO_CHAR}
    out = {}
    for key in chars:
        value = chars[key]
        if key in DEFECT_TO_CHAR.values():
            value = "fail" if key in broken else ("pass" if value == "fail" else value)
        elif value == "fail" and not violations:
            value = "pass"
        out[key] = value
    return out


def check_requirements(document: Document, client: LLMClient, locator: Locator,
                       reqs: list[Requirement], batch_size: int = 8,
                       log: Log = _log_default) -> tuple[list[RequirementCheck], int]:
    hints = weak_word_hints(reqs)
    line_of = {r.rid: r.location.line_start for r in reqs}
    by_rid: dict[str, dict[str, Any]] = {}
    batches = [reqs[i:i + batch_size] for i in range(0, len(reqs), batch_size)]
    queue = list(batches)
    attempt = 0
    while queue:
        batch = queue.pop(0)
        attempt += 1
        log(f"Шаг 2/4: проверка требований {batch[0].rid}…{batch[-1].rid}")
        prompt = P.CHECK_PROMPT.format(
            characteristics=P.describe(REQ_CHARACTERISTICS),
            defects=", ".join(f"{k} ({v})" for k, v in DEFECT_TYPES.items()),
            hints=P.dumps({r.rid: hints[r.rid] for r in batch if r.rid in hints}) or "нет",
            requirements=P.dumps([_req_view(r) for r in batch]),
            document=document.numbered_text(),
        )
        data = client.generate_json(P.CHECK_SYSTEM, prompt, P.CHECK_SCHEMA)
        items = data.get("checks", []) if isinstance(data, dict) else data or []
        wanted = {r.rid for r in batch}
        for item in items:
            if isinstance(item, dict) and item.get("rid") in wanted:
                by_rid[item["rid"]] = item
        missing = [r for r in batch if r.rid not in by_rid]
        # One more call for requirements the model skipped (bounded by the attempt count).
        if missing and attempt <= len(batches) * 2:
            log(f"  модель пропустила {len(missing)} треб., повторный запрос")
            queue.append(missing)

    checks: list[RequirementCheck] = []
    dropped = 0
    for req in reqs:
        item = by_rid.get(req.rid, {})
        raw_chars = item.get("characteristics") or {}
        chars = {k: _status(raw_chars.get(k)) for k in REQ_CHARACTERISTICS}
        parsed = [v for v in (_parse_violation(x, [req.rid]) for x in item.get("violations") or [])
                  if v]
        kept, lost = verify_violations(locator, parsed, line_of)
        dropped += lost
        checks.append(RequirementCheck(req.rid, _apply_evidence(chars, kept), kept))
    return checks, dropped


# --- stage 3: set check -------------------------------------------------------------------------

def candidate_pairs(reqs: list[Requirement], limit: int = 80) -> list[dict[str, Any]]:
    """Pairs of requirements that share a concept: the main input for the contradiction search."""
    tagged = {r.rid: {c for c, rx in CONCEPTS.items()
                      if re.search(rx, f"{r.quote} {r.summary}", re.IGNORECASE)} for r in reqs}
    pairs = []
    for a, b in combinations(reqs, 2):
        common = tagged[a.rid] & tagged[b.rid]
        if common:
            pairs.append({"pair": [a.rid, b.rid], "common": sorted(common)})
    # Pairs with more shared concepts first.
    pairs.sort(key=lambda p: -len(p["common"]))
    return pairs[:limit]


def _same(v: Violation, other: Violation) -> bool:
    return (v.defect == other.defect and normalize(v.quote) == normalize(other.quote)
            and normalize(v.quote2 or "") == normalize(other.quote2 or ""))


def check_set(document: Document, client: LLMClient, locator: Locator, reqs: list[Requirement],
              checks: list[RequirementCheck], log: Log = _log_default) -> tuple[SetCheck, int]:
    log("Шаг 3/4: проверка набора целиком (противоречия, пробелы)…")
    known = [f"{c.rid}: {DEFECT_TYPES[v.defect]} — «{v.quote[:80]}»"
             for c in checks for v in c.violations]
    acceptance = [f"{r.rid} (строка {r.location.line_start}): {r.summary}" for r in reqs
                  if r.kind == "acceptance"]
    stats = traceability_stats(reqs)
    prompt = P.SET_PROMPT.format(
        characteristics=P.describe(SET_CHARACTERISTICS),
        known="\n".join(known) or "нет",
        acceptance="\n".join(acceptance) or "нет ни одного критерия приёмки",
        stats=P.dumps(stats),
        pairs=P.dumps(candidate_pairs(reqs)),
        requirements=P.dumps([_req_view(r) for r in reqs]),
        document=document.numbered_text(),
    )
    data = client.generate_json(P.SET_SYSTEM, prompt, P.SET_SCHEMA)
    data = data if isinstance(data, dict) else {}
    valid = {r.rid for r in reqs}
    line_of = {r.rid: r.location.line_start for r in reqs}
    parsed = []
    for raw in data.get("violations") or []:
        ids = [i for i in (raw.get("req_ids") or []) if i in valid] if isinstance(raw, dict) else []
        v = _parse_violation(raw, ids)
        if v:
            parsed.append(v)
    kept, dropped = verify_violations(locator, parsed, line_of)
    # Remove duplicates inside the set check and repeats of per-requirement findings.
    per_req = [v for c in checks for v in c.violations]
    unique: list[Violation] = []
    per_req_by_rid = {c.rid: c.violations for c in checks}
    for v in kept:
        if not v.req_ids:  # attach requirement IDs from quote locations
            v.req_ids = _rids_at(reqs, v)
        if any(_same(v, o) for o in unique) or any(_same(v, o) for o in per_req):
            continue
        if _repeats_requirement_finding(v, per_req_by_rid):
            continue
        unique.append(v)
    trace = traceability_violation(reqs, stats)
    if trace and not any(v.defect == "traceability" and "идентификатор" in v.explanation
                         for v in unique):
        unique.insert(0, trace)
    raw_chars = data.get("characteristics") or {}
    chars = {k: _status(raw_chars.get(k)) for k in SET_CHARACTERISTICS}
    if any(v.defect == "contradiction" for v in unique) or any(
            v.defect == "contradiction" for v in per_req):
        chars["consistent"] = "fail"
    if any(v.defect == "gap" for v in unique):
        chars["complete"] = "fail"
    if not unique:
        chars = {k: ("pass" if s == "fail" else s) for k, s in chars.items()}
        if any(v.defect == "contradiction" for v in per_req):
            chars["consistent"] = "fail"
    if trace:
        chars["traceable"] = "fail"
    return SetCheck(chars, unique), dropped


def _overlap(a, b) -> bool:
    return bool(a and b and a.line_start <= b.line_end and b.line_start <= a.line_end)


def _repeats_requirement_finding(v: Violation, per_req_by_rid: dict[str, list[Violation]]) -> bool:
    """A set finding repeats a per-requirement one: same defect at the same place of the same
    requirement (e.g. GET vs "save" found per requirement and again in the set check)."""
    for rid in v.req_ids:
        for o in per_req_by_rid.get(rid, []):
            if o.defect != v.defect:
                continue
            if any(_overlap(x, y) for x in (v.location, v.location2) if x
                   for y in (o.location,) if y):
                return True
    return False


def traceability_stats(reqs: list[Requirement]) -> dict[str, Any]:
    with_id = [r.rid for r in reqs if r.doc_id]
    return {"requirements": len(reqs), "with_document_id": len(with_id),
            "without_document_id": len(reqs) - len(with_id),
            "share_with_id": round(len(with_id) / len(reqs), 2) if reqs else 0.0}


def traceability_violation(reqs: list[Requirement], stats: dict[str, Any]) -> Violation | None:
    """Deterministic rule (830 4.3.8): fewer than 80% of requirements have a document ID."""
    if not reqs or stats["share_with_id"] >= 0.8:
        return None
    no_id = [r for r in reqs if not r.doc_id]
    first = no_id[0]
    sample = ", ".join(r.rid for r in no_id[:10]) + ("…" if len(no_id) > 10 else "")
    return Violation(
        defect="traceability", criterion="IEEE 830 4.3.8 — Прослеживаемость",
        severity="major", quote=first.quote, location=first.location, verified=True,
        explanation=(f"Идентификатор в документе есть только у {stats['with_document_id']} из "
                     f"{stats['requirements']} требований. Без идентификатора: {sample}. На такие "
                     "требования нельзя сослаться из тест-кейсов и задач."),
        fix="Присвоить уникальный идентификатор каждому требованию всех разделов "
            "(например, НФ-1, Д-1, И-1, КП-1, API-1) и указать связь с критериями приёмки.",
        req_ids=[r.rid for r in no_id],
    )


def _rids_at(reqs: list[Requirement], v: Violation) -> list[str]:
    out = []
    for loc in (v.location, v.location2):
        if not loc:
            continue
        for r in reqs:
            if r.location.line_start <= loc.line_end and loc.line_start <= r.location.line_end:
                if r.rid not in out:
                    out.append(r.rid)
    return out


# --- stage 3b: coverage matrix -----------------------------------------------------------------

COVERAGE_KINDS = ("functional", "api", "nonfunctional", "integration", "data")


def check_coverage(document: Document, client: LLMClient, locator: Locator,
                   reqs: list[Requirement], log: Log = _log_default
                   ) -> tuple[list[Violation], dict[str, list[str]], int]:
    """Gaps between requirements and acceptance criteria. Returns (gaps, matrix, dropped)."""
    acceptance = [r for r in reqs if r.kind == "acceptance"]
    targets = [r for r in reqs if r.kind == "functional" or r.doc_id]
    targets += [r for r in reqs if r.kind in COVERAGE_KINDS and r not in targets]
    if not targets:
        return [], {}, 0
    log("Шаг 3б/4: матрица покрытия требований критериями приёмки…")
    prompt = P.COVERAGE_PROMPT.format(
        acceptance=P.dumps([_req_view(r) for r in acceptance]) if acceptance else "нет",
        requirements=P.dumps([_req_view(r) for r in targets]),
        document=document.numbered_text(),
    )
    data = client.generate_json(P.COVERAGE_SYSTEM, prompt, P.COVERAGE_SCHEMA)
    rows = data.get("matrix", []) if isinstance(data, dict) else []
    valid = {r.rid for r in targets}
    acc_ids = {r.rid for r in acceptance}
    matrix: dict[str, list[str]] = {}
    gaps: list[Violation] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("rid") not in valid:
            continue
        rid = row["rid"]
        matrix[rid] = [a for a in row.get("covered_by") or [] if a in acc_ids]
        for item in row.get("uncovered") or []:
            if not isinstance(item, dict) or not str(item.get("quote", "")).strip():
                continue
            sev = item.get("severity") if item.get("severity") in ("critical", "major") else "major"
            covered = ", ".join(matrix[rid]) or "нет"
            gaps.append(Violation(
                defect="gap", criterion="ISO/IEC/IEEE 29148 5.2.6 — Полнота", severity=sev,
                quote=str(item["quote"]).strip(),
                explanation=(f"Нет критерия приёмки для условия: {str(item.get('condition', '')).strip()}. "
                             f"Требование покрывают критерии: {covered}."),
                fix=f"Добавить критерий приёмки: {str(item.get('fix', '')).strip()}",
                req_ids=[rid],
            ))
    line_of = {r.rid: r.location.line_start for r in reqs}
    kept, dropped = verify_violations(locator, gaps, line_of)
    log(f"  непокрытых условий: {len(kept)}")
    return kept, matrix, dropped


# --- stage 4: verdict ---------------------------------------------------------------------------

def make_verdict(document: Document, client: LLMClient, reqs: list[Requirement],
                 checks: list[RequirementCheck], set_check: SetCheck,
                 log: Log = _log_default) -> Verdict:
    log("Шаг 4/4: вердикт…")
    allv = [(c.rid, v) for c in checks for v in c.violations] + [
        (",".join(v.req_ids) or "набор", v) for v in set_check.violations]
    sev = {s: sum(1 for _, v in allv if v.severity == s) for s in SEVERITIES}
    ready = sev["critical"] == 0
    lines = [f"- [{v.severity}] ({rid}) {DEFECT_TYPES[v.defect]}: «{v.quote[:100]}» — "
             f"{v.explanation[:200]}" for rid, v in allv if v.severity in ("critical", "major")]
    yes_no = {True: "Да", False: "Нет"}
    prompt = P.VERDICT_PROMPT.format(
        title=document.title, n_req=len(reqs), n_viol=len(allv), n_crit=sev["critical"],
        n_major=sev["major"], n_minor=sev["minor"], violations="\n".join(lines[:80]) or "нет",
        dev=yes_no[ready], test=yes_no[ready],
    )
    try:
        data = client.generate_json(P.VERDICT_SYSTEM, prompt, P.VERDICT_SCHEMA)
        summary = str(data.get("summary", "")).strip()
        fix_first = [str(x).strip() for x in data.get("fix_first") or [] if str(x).strip()]
    except Exception as exc:  # the verdict text is optional; the decision is computed above
        log(f"  вердикт от модели не получен ({exc}); формирую по правилам")
        summary, fix_first = "", []
    if not summary:
        summary = (f"Найдено критичных нарушений: {sev['critical']}." if not ready
                   else "Критичных нарушений нет.")
    if not fix_first:
        fix_first = [f"({rid}) {v.fix or v.explanation}" for rid, v in allv
                     if v.severity == "critical"][:7]
    return Verdict(ready_for_development=ready, ready_for_testing=ready, summary=summary,
                   fix_first=fix_first)


# --- entry point --------------------------------------------------------------------------------

def run(document: Document, client: LLMClient, passport: Passport,
        log: Log = _log_default, batch_size: int | None = None) -> Report:
    batch_size = batch_size or _int(os.environ.get("VALIDATOR_BATCH_SIZE"), 8) or 8
    locator = Locator(document)
    reqs = extract_requirements(document, client, locator, log)
    if not reqs:
        raise ValueError("Модель не выделила ни одного требования с подтверждённой цитатой.")
    checks, dropped1 = check_requirements(document, client, locator, reqs, batch_size, log)
    set_check, dropped2 = check_set(document, client, locator, reqs, checks, log)
    gaps, _matrix, dropped3 = check_coverage(document, client, locator, reqs, log)
    for g in gaps:
        if not any(_same(g, o) for o in set_check.violations):
            set_check.violations.append(g)
    if gaps:
        set_check.characteristics["complete"] = "fail"
        set_check.characteristics["able_to_be_validated"] = "fail"
    dropped2 += dropped3
    verdict = make_verdict(document, client, reqs, checks, set_check, log)
    return Report(passport=passport, requirements=reqs, checks=checks, set_check=set_check,
                  verdict=verdict, dropped_violations=dropped1 + dropped2)
