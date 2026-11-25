"""Machine-readable JSON export of the report."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .criteria import normalize_criterion
from .models import Location, Report, Violation
from .report import counts

SCHEMA_VERSION = 1


def _loc(loc: Location | None) -> dict | None:
    if loc is None:
        return None
    return {"line_start": loc.line_start, "line_end": loc.line_end, "page": loc.page}


def _violation(v: Violation, scope: str, rid: str | None = None) -> dict:
    req_ids = list(v.req_ids)
    if scope == "requirement" and not req_ids and rid:
        req_ids = [rid]
    return {
        "scope": scope,
        "defect": v.defect,
        "severity": v.severity,
        "criterion": normalize_criterion(v.criterion, v.defect, "set" if scope == "set" else "req"),
        "criterion_raw": v.criterion,
        "quote": v.quote,
        "location": _loc(v.location),
        "quote2": v.quote2,
        "location2": _loc(v.location2),
        "explanation": v.explanation,
        "fix": v.fix,
        "fix_type": v.fix_type,
        "options": list(v.options),
        "tag": v.tag,
        "req_ids": req_ids,
    }


def report_to_dict(report: Report) -> dict:
    counter = 0

    def numbered(v: Violation, scope: str, rid: str | None = None) -> dict:
        nonlocal counter
        counter += 1
        return {"id": f"V-{counter:03d}", **_violation(v, scope, rid)}

    checks = [{
        "rid": c.rid,
        "characteristics": dict(c.characteristics),
        "violations": [numbered(v, "requirement", c.rid) for v in c.violations],
    } for c in report.checks]
    set_check = {
        "characteristics": dict(report.set_check.characteristics),
        "violations": [numbered(v, "set") for v in report.set_check.violations],
    }
    rejected = []
    for v, reason in report.critic_rejected:
        item = _violation(v, "set" if not v.req_ids else "requirement")
        rejected.append({"violation": item, "reason": reason})
    vd = report.verdict
    return {
        "schema_version": SCHEMA_VERSION,
        "passport": asdict(report.passport),
        "requirements": [{
            "rid": r.rid, "doc_id": r.doc_id, "section": r.section, "kind": r.kind,
            "summary": r.summary, "quote": r.quote, "location": _loc(r.location),
        } for r in report.requirements],
        "checks": checks,
        "set_check": set_check,
        "counts": json.loads(json.dumps(counts(report), ensure_ascii=False)),
        "critic": {"ran": report.critic_ran, "adjusted": report.critic_adjusted,
                   "rejected": rejected},
        "dropped_violations": report.dropped_violations,
        "merged_duplicates": report.merged_duplicates,
        "checklist": {"source": report.checklist_source, "items": [{
            "item": c.item, "status": c.status, "evidence": c.evidence, "quote": c.quote,
            "location": _loc(c.location), "refs": list(c.refs)} for c in report.checklist]},
        "verdict": {
            "ready_for_development": vd.ready_for_development,
            "ready_for_testing": vd.ready_for_testing,
            "summary": vd.summary,
            "fix_first": list(vd.fix_first),
        },
    }


def write_json(report: Report, path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(report_to_dict(report), f, ensure_ascii=False, indent=2)
        f.write("\n")
