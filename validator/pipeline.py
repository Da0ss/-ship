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
from .facts import TAG as FACT_TAG
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

_MARKUP = re.compile(r"^\s*(?:[*+\-•]|\d+[.)]|[a-zа-я]\))\s+", re.IGNORECASE)


def _clean_quote(text: str) -> str:
    """Candidate text without list markers, bold and code markup; table pipes become ' | '."""
    t = text.strip()
    if t.startswith("|"):
        cells = [c.strip() for c in t.strip("|").split("|")]
        t = " | ".join(c for c in cells if c)
    else:
        t = _MARKUP.sub("", t, count=1)
    t = t.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", t).strip()


def classify_candidates(document: Document, client: LLMClient,
                        log: Log = _log_default) -> list[Requirement]:
    """Stage 1, main mode: the code cuts the text, the LLM only marks requirements."""
    from .models import Location
    from .segmenter import format_for_llm, segment

    cands = segment(document)
    log(f"Шаг 1/5: код выделил фрагментов: {len(cands)}; модель отбирает требования…")
    if not cands:
        return []
    data = client.generate_json(
        P.CLASSIFY_SYSTEM,
        P.CLASSIFY_PROMPT.format(kinds=", ".join(P.KINDS), candidates=format_for_llm(cands)),
        P.CLASSIFY_SCHEMA,
    )
    items = data.get("items", []) if isinstance(data, dict) else []
    by_cid = {i.get("cid"): i for i in items if isinstance(i, dict)}
    missing = [c.cid for c in cands if c.cid not in by_cid]
    if missing:
        log(f"  модель не оценила фрагментов: {len(missing)}; они считаются требованиями")
    reqs: list[Requirement] = []
    for c in cands:
        item = by_cid.get(c.cid, {"is_requirement": True, "kind": "other", "summary": ""})
        # A fragment with a document ID is always a requirement.
        if not (item.get("is_requirement") or c.doc_id):
            continue
        quote = _clean_quote(c.text)
        if c.doc_id and quote.startswith(c.doc_id):
            quote = quote[len(c.doc_id):].lstrip(" .:—-")
        kind = item.get("kind") if item.get("kind") in P.KINDS else "other"
        reqs.append(Requirement(
            rid="", doc_id=c.doc_id, summary=str(item.get("summary") or "").strip() or quote[:80],
            quote=quote, section=c.section, kind=kind,
            location=Location(c.line_start, c.line_end,
                              next((l.page for l in document.lines if l.no == c.line_start), None)),
        ))
    width = max(2, len(str(len(reqs))))
    for n, req in enumerate(reqs, 1):
        req.rid = f"R-{n:0{width}d}"
    log(f"  найдено требований: {len(reqs)}")
    return reqs


def extract_requirements(document: Document, client: LLMClient, locator: Locator,
                         log: Log = _log_default) -> list[Requirement]:
    """Stage 1, fallback mode (VALIDATOR_SEGMENTER=off): the LLM cuts the text itself."""
    log("Шаг 1/5: нарезка документа на требования…")
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
    """Ambiguity indicators per requirement, matched by lemma (pymorphy3) with class names."""
    from .lexicon import CLASS_RU, find_weak_terms

    out: dict[str, list[str]] = {}
    for req in reqs:
        hits = find_weak_terms(req.quote)
        if hits:
            out[req.rid] = [f"{h['term']} [{CLASS_RU.get(h['class'], h['class'])}]: «{h['match']}»"
                            for h in hits]
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
        fix_type="question" if raw.get("fix_type") == "question" else "edit",
        options=[str(o).strip() for o in raw.get("options") or [] if str(o).strip()][:3],
        tag=raw.get("tag") if raw.get("tag") in ("fintech", "security") else None,
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
        log(f"Шаг 2/5: проверка требований {batch[0].rid}…{batch[-1].rid}")
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
    log("Шаг 3/5: проверка набора целиком (противоречия, пробелы)…")
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
    log("Шаг 3б/5: матрица покрытия требований критериями приёмки…")
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


# --- stage 3c: deterministic fact checks -----------------------------------------------------

def _group(defect: str) -> str:
    """Defect kinds that describe one problem from two angles count as one for de-duplication."""
    return "conflict" if defect in ("contradiction", "other") else defect


def _overlaps_any(v: Violation, o: Violation) -> bool:
    return any(_overlap(a, b) for a in (v.location, v.location2) if a
               for b in (o.location, o.location2) if b)


def add_fact_findings(document: Document, reqs: list[Requirement], checks: list[RequirementCheck],
                      set_check: SetCheck, log: Log = _log_default,
                      held: list[Violation] | None = None) -> int:
    """Add code-found contradictions and gaps (validator/facts.py) that the LLM missed.
    A fact already reported by the LLM goes to `held`: if the critic later rejects the LLM
    finding, restore_held_facts() puts the fact back (code facts are not dropped by the critic)."""
    from .facts import find_facts

    existing = [v for c in checks for v in c.violations] + set_check.violations
    added = 0
    for f in find_facts(document):
        # Same kind of problem at the same place is already reported by the LLM.
        if any(_group(o.defect) == _group(f.defect) and _overlaps_any(f, o) for o in existing):
            if held is not None:
                f.req_ids = _rids_at(reqs, f)
                held.append(f)
            continue
        f.req_ids = _rids_at(reqs, f)
        set_check.violations.append(f)
        existing.append(f)
        added += 1
        if f.defect == "contradiction":
            set_check.characteristics["consistent"] = "fail"
        elif f.defect == "gap":
            set_check.characteristics["complete"] = "fail"
    log(f"Проверки кодом: добавлено замечаний {added}")
    return added


def restore_held_facts(held: list[Violation], checks: list[RequirementCheck],
                       set_check: SetCheck) -> int:
    """Put back code facts whose LLM twin did not survive the critic."""
    remaining = [v for c in checks for v in c.violations] + set_check.violations
    restored = 0
    for f in held:
        if not any(_group(o.defect) == _group(f.defect) and _overlaps_any(f, o) for o in remaining):
            set_check.violations.append(f)
            remaining.append(f)
            restored += 1
            if f.defect == "contradiction":
                set_check.characteristics["consistent"] = "fail"
            elif f.defect == "gap":
                set_check.characteristics["complete"] = "fail"
    return restored


# --- stage 3c: critic ------------------------------------------------------------------------

def _violation_view(vid: str, v: Violation) -> dict[str, Any]:
    return {"vid": vid, "req_ids": v.req_ids, "defect": v.defect, "severity": v.severity,
            "criterion": v.criterion, "quote": v.quote,
            "lines": [v.location.line_start, v.location.line_end] if v.location else None,
            "quote2": v.quote2,
            "lines2": [v.location2.line_start, v.location2.line_end] if v.location2 else None,
            "explanation": v.explanation, "fix": v.fix}


def critic_review(document: Document, client: LLMClient, checks: list[RequirementCheck],
                  set_check: SetCheck, log: Log = _log_default, batch_size: int = 60
                  ) -> tuple[list[tuple[Violation, str]], int]:
    """Second opinion on every finding. Returns (rejected with reasons, number adjusted).
    Changes checks and set_check in place."""
    items: list[tuple[str, Violation, list[Violation]]] = []
    for c in checks:
        for v in c.violations:
            items.append((f"V-{len(items) + 1:03d}", v, c.violations))
    for v in set_check.violations:
        items.append((f"V-{len(items) + 1:03d}", v, set_check.violations))
    if not items:
        return [], 0
    log(f"Шаг 4/5: критик проверяет замечания ({len(items)})…")
    decisions: dict[str, dict[str, Any]] = {}
    for i in range(0, len(items), batch_size):
        part = items[i:i + batch_size]
        prompt = P.CRITIC_PROMPT.format(
            defects=", ".join(f"{k} ({v})" for k, v in DEFECT_TYPES.items()),
            violations=P.dumps([_violation_view(vid, v) for vid, v, _ in part]),
            document=document.numbered_text(),
        )
        data = client.generate_json(P.CRITIC_SYSTEM, prompt, P.CRITIC_SCHEMA)
        for r in (data.get("reviews") or []) if isinstance(data, dict) else []:
            if isinstance(r, dict) and r.get("vid"):
                decisions[r["vid"]] = r

    vids = {vid for vid, _, _ in items}
    rejects = []
    for vid, v, owner in items:
        d = decisions.get(vid, {})
        dup = d.get("duplicate_of")
        if v.explanation.startswith(FACT_TAG):
            continue  # deterministic code facts: the critic may adjust them, not drop them
        if d.get("decision") == "reject" or (dup in vids and dup != vid):
            reason = str(d.get("reason") or "").strip()
            if dup in vids and dup != vid:
                reason = f"повтор замечания {dup}. {reason}".strip()
            rejects.append((vid, v, owner, reason or "ложное замечание"))
    # Guard: a critic that rejects most findings is likely broken; keep only its duplicates.
    max_share = float(os.environ.get("VALIDATOR_CRITIC_MAX_REJECT", "0.6"))
    if len(rejects) > max_share * len(items):
        log(f"  критик отклонил {len(rejects)} из {len(items)} — больше {max_share:.0%}; "
            "применяю только отклонение повторов")
        rejects = [r for r in rejects if r[3].startswith("повтор замечания")]

    rejected: list[tuple[Violation, str]] = []
    for vid, v, owner, reason in rejects:
        if v in owner:
            owner.remove(v)
            rejected.append((v, reason))
    adjusted = 0
    rejected_ids = {id(v) for v, _ in rejected}
    for vid, v, _ in items:
        d = decisions.get(vid, {})
        if id(v) in rejected_ids or d.get("decision") != "adjust":
            continue
        changed = False
        if d.get("severity") in SEVERITIES and d["severity"] != v.severity:
            v.severity, changed = d["severity"], True
        if d.get("defect") in DEFECT_TYPES and d["defect"] != v.defect:
            # A contradiction needs two quotes; do not turn a one-quote finding into one.
            if d["defect"] != "contradiction" or v.quote2:
                v.defect, changed = d["defect"], True
        if str(d.get("criterion") or "").strip() and d["criterion"].strip() != v.criterion:
            v.criterion, changed = d["criterion"].strip(), True
        adjusted += changed

    # Characteristics follow the remaining evidence.
    for c in checks:
        c.characteristics = _apply_evidence(c.characteristics, c.violations)
    remaining = [v for c in checks for v in c.violations] + set_check.violations
    chars = set_check.characteristics
    if chars.get("consistent") == "fail" and not any(v.defect == "contradiction" for v in remaining):
        chars["consistent"] = "pass"
    if chars.get("complete") == "fail" and not any(v.defect == "gap" for v in set_check.violations):
        chars["complete"] = "pass"
    if chars.get("traceable") == "fail" and not any(
            v.defect == "traceability" for v in set_check.violations):
        chars["traceable"] = "pass"
    log(f"  подтверждено: {len(remaining)}, исправлено: {adjusted}, отклонено: {len(rejected)}")
    return rejected, adjusted


# --- stage 4: verdict ---------------------------------------------------------------------------

def make_verdict(document: Document, client: LLMClient, reqs: list[Requirement],
                 checks: list[RequirementCheck], set_check: SetCheck,
                 log: Log = _log_default, n_rejected: int = 0) -> Verdict:
    log("Шаг 5/5: вывод и вердикт…")
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
        dev=yes_no[ready], test=yes_no[ready], n_rejected=n_rejected,
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
        log: Log = _log_default, batch_size: int | None = None,
        fast_client: LLMClient | None = None) -> Report:
    """fast_client (optional) runs the light stages: classification of fragments, verdict text,
    review checklist. The strong client keeps the checks, set check, coverage, bank review,
    critic. This saves the small daily quota of strong free-tier models."""
    fast = fast_client or client
    batch_size = batch_size or _int(os.environ.get("VALIDATOR_BATCH_SIZE"), 15) or 15
    locator = Locator(document)
    if os.environ.get("VALIDATOR_SEGMENTER", "on").lower() == "off":
        reqs = extract_requirements(document, client, locator, log)
    else:
        reqs = classify_candidates(document, fast, log)
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
    held: list[Violation] = []
    if os.environ.get("VALIDATOR_FACTS", "on").lower() != "off":
        add_fact_findings(document, reqs, checks, set_check, log, held)
    from . import review
    if review.enabled("VALIDATOR_DOMAIN"):
        review.domain_review(document, client, locator, reqs, checks, set_check, log)
    rejected: list[tuple[Violation, str]] = []
    adjusted = 0
    critic_on = os.environ.get("VALIDATOR_CRITIC", "on").lower() != "off"
    if critic_on:
        rejected, adjusted = critic_review(document, client, checks, set_check, log)
    back = restore_held_facts(held, checks, set_check)
    if back:
        log(f"Проверки кодом: возвращено замечаний после критика {back}")
    questions = review.guard_invented_values(document, checks, set_check)
    merged = review.dedupe(checks, set_check)
    coverage_gaps = review.group_coverage(set_check)
    if coverage_gaps:
        log(f"Условия без критериев приёмки сведены в одну таблицу: {len(coverage_gaps)}")
    log(f"Повторы объединены: {merged}; правок с выдуманными значениями переведено в вопросы: {questions}")
    verdict = make_verdict(document, fast, reqs, checks, set_check, log, len(rejected))
    checklist, source = [], ""
    if review.enabled("VALIDATOR_CHECKLIST"):
        # The checklist is one call and its answers carry weight with a bank reviewer:
        # the strong model fills it (the light one said "Да" to items with open defects).
        checklist, source = review.fill_checklist(document, client, locator, reqs, checks,
                                                  set_check, log)
    return Report(passport=passport, requirements=reqs, checks=checks, set_check=set_check,
                  verdict=verdict, dropped_violations=dropped1 + dropped2,
                  critic_rejected=rejected, critic_adjusted=adjusted, critic_ran=critic_on,
                  checklist=checklist, checklist_source=source, merged_duplicates=merged,
                  coverage_gaps=coverage_gaps)
