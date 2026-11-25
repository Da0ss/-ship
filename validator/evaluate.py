"""Compare a JSON report with a golden list of known defects.

python -m validator.evaluate reports/report.json tests/golden/requirements.golden.json

A golden defect is found when a report finding has one of its defect types and one of its
locations covers one of the golden lines (an empty "lines" list matches any location).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = [v for c in report.get("checks", []) for v in c.get("violations", [])]
    return out + list(report.get("set_check", {}).get("violations", []))


def _spans(v: dict[str, Any]) -> list[tuple[int, int]]:
    spans = []
    for key in ("location", "location2"):
        loc = v.get(key)
        if loc:
            spans.append((loc["line_start"], loc["line_end"]))
    return spans


def _matches(v: dict[str, Any], g: dict[str, Any]) -> bool:
    if v.get("defect") not in g["defects"]:
        return False
    if not g["lines"]:
        return True
    return any(a <= n <= b for a, b in _spans(v) for n in g["lines"])


def evaluate(report: dict[str, Any], golden: dict[str, Any]) -> dict[str, Any]:
    findings = _findings(report)
    rows = []
    for g in golden["defects"]:
        hits = [v for v in findings if _matches(v, g)]
        rows.append({"id": g["id"], "title": g["title"], "severity": g["severity"],
                     "found": bool(hits), "by": [v.get("id", "?") for v in hits]})
    matched = [v for v in findings if any(_matches(v, g) for g in golden["defects"])]
    crit = [r for r in rows if r["severity"] == "critical"]
    n = len(rows)
    return {
        "golden": n,
        "found": sum(r["found"] for r in rows),
        "recall": round(sum(r["found"] for r in rows) / n, 3) if n else 0.0,
        "critical_recall": round(sum(r["found"] for r in crit) / len(crit), 3) if crit else 0.0,
        "findings": len(findings),
        # Lower bound: the golden list is not complete, so an unmatched finding may be real.
        "precision_lower_bound": round(len(matched) / len(findings), 3) if findings else 0.0,
        "rows": rows,
    }


def render(result: dict[str, Any]) -> str:
    lines = [f"Эталон: {result['golden']} дефектов. Найдено: {result['found']} "
             f"(полнота {result['recall']:.0%}, по критичным {result['critical_recall']:.0%}).",
             f"Замечаний в отчёте: {result['findings']}; совпали с эталоном: "
             f"{result['precision_lower_bound']:.0%} (оценка точности снизу).", ""]
    for r in result["rows"]:
        mark = "✅" if r["found"] else "❌"
        lines.append(f"{mark} {r['id']} [{r['severity']}] {r['title']}"
                     + (f" — {', '.join(r['by'])}" if r["by"] else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m validator.evaluate",
                                description="Сравнение JSON-отчёта с эталоном дефектов.")
    p.add_argument("report", help="JSON-отчёт агента (--json)")
    p.add_argument("golden", help="эталон дефектов (JSON)")
    p.add_argument("--min-recall", type=float, default=0.0,
                   help="код возврата 1, если полнота ниже порога (для CI)")
    args = p.parse_args(argv)
    with open(args.report, encoding="utf-8") as f:
        report = json.load(f)
    with open(args.golden, encoding="utf-8") as f:
        golden = json.load(f)
    result = evaluate(report, golden)
    print(render(result))
    return 1 if result["recall"] < args.min_recall else 0


if __name__ == "__main__":
    sys.exit(main())
