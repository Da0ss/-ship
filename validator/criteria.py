"""Normalization of violation criteria to one Russian form.

Output: "<standard> <clause> — <Russian characteristic>".
"""
from __future__ import annotations

import re

# (stems, Russian name); first match wins, so more specific stems go first.
_CHARACTERISTICS: list[tuple[tuple[str, ...], str]] = [
    (("implementation free", "implementation-free", "implementation_free", "independent",
      "реализац"), "Независимость от реализации"),
    (("unambig", "ambig", "однозначн"), "Однозначность"),
    (("necessary", "необходим"), "Необходимость"),
    (("consistent", "непротивореч"), "Непротиворечивость"),
    (("complete", "полнот"), "Полнота"),
    (("singular", "единичн", "atomic"), "Единичность"),
    (("feasible", "осуществим"), "Осуществимость"),
    (("traceab", "прослежива"), "Прослеживаемость"),
    (("verifiable", "testable", "проверяем"), "Проверяемость"),
    (("correct", "корректн"), "Корректность"),
    (("ranked", "ранжир", "priorit"), "Ранжирование"),
    (("modifiable", "модифицир"), "Модифицируемость"),
    (("comprehensible", "understandable", "понятн"), "Понятность"),
    (("validat", "валидац"), "Возможность валидации"),
]

_DEFECT_TO_CHARACTERISTIC = {
    "ambiguity": "Однозначность",
    "unverifiable": "Проверяемость",
    "contradiction": "Непротиворечивость",
    "gap": "Полнота",
    "design": "Независимость от реализации",
    "non_singular": "Единичность",
    "traceability": "Прослеживаемость",
    "other": "Корректность",
}

_ISO = "ISO/IEC/IEEE 29148"
_IEEE = "IEEE 830"
_ISO_CLAUSE_RE = re.compile(r"(?<![\d.])5\.2\.[56](?![\d])")
_IEEE_CLAUSE_RE = re.compile(r"(?<![\d.])4\.3(?:\.\d+)?(?![\d])")


def _characteristic(text: str, defect: str) -> str:
    low = text.lower().replace("_", " ")
    for stems, name in _CHARACTERISTICS:
        if any(s.replace("_", " ") in low for s in stems):
            return name
    return _DEFECT_TO_CHARACTERISTIC.get(defect, "Корректность")


def normalize_criterion(raw: str, defect: str, scope: str = "req") -> str:
    raw = raw or ""
    if re.search(r"29148", raw):
        standard = _ISO
    elif re.search(r"(?<!\d)830(?!\d)", raw):
        standard = _IEEE
    else:
        standard = _ISO

    name = _characteristic(raw, defect)
    if name in ("Ранжирование", "Модифицируемость", "Корректность") and standard == _ISO:
        standard = _IEEE  # these characteristics exist only in IEEE 830 4.3
    if standard == _ISO:
        m = _ISO_CLAUSE_RE.search(raw)
        clause = m.group(0) if m else ("5.2.6" if scope == "set" else "5.2.5")
    else:
        m = _IEEE_CLAUSE_RE.search(raw)
        clause = m.group(0) if m else _IEEE_SUBCLAUSE.get(name, "4.3")

    return f"{standard} {clause} — {name}"


# IEEE 830-1998 4.3.1–4.3.8: subclause of each characteristic of a good SRS.
_IEEE_SUBCLAUSE = {
    "Корректность": "4.3.1", "Однозначность": "4.3.2", "Полнота": "4.3.3",
    "Непротиворечивость": "4.3.4", "Ранжирование": "4.3.5", "Проверяемость": "4.3.6",
    "Модифицируемость": "4.3.7", "Прослеживаемость": "4.3.8",
}
