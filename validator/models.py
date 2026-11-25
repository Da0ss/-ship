"""Data contract shared by all modules of the validator agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

AGENT_NAME = "ai-requirements-validator"

# Quality characteristics of one requirement: ISO/IEC/IEEE 29148:2018 5.2.5 + IEEE 830-1998 4.3.
REQ_CHARACTERISTICS: dict[str, str] = {
    "necessary": "Необходимость (29148 5.2.5)",
    "implementation_free": "Независимость от реализации (29148 5.2.5)",
    "unambiguous": "Однозначность (29148 5.2.5; 830 4.3.2)",
    "consistent": "Непротиворечивость (29148 5.2.5; 830 4.3.4)",
    "complete": "Полнота (29148 5.2.5; 830 4.3.3)",
    "singular": "Единичность (29148 5.2.5)",
    "feasible": "Осуществимость (29148 5.2.5)",
    "traceable": "Прослеживаемость (29148 5.2.5; 830 4.3.8)",
    "verifiable": "Проверяемость (29148 5.2.5; 830 4.3.6)",
    "correct": "Корректность (830 4.3.1)",
}

# Characteristics of the whole set: ISO/IEC/IEEE 29148:2018 5.2.6 + IEEE 830-1998 4.3.
SET_CHARACTERISTICS: dict[str, str] = {
    "complete": "Полнота набора (29148 5.2.6; 830 4.3.3)",
    "consistent": "Непротиворечивость набора (29148 5.2.6; 830 4.3.4)",
    "feasible": "Осуществимость набора (29148 5.2.6)",
    "comprehensible": "Понятность набора (29148 5.2.6)",
    "able_to_be_validated": "Возможность валидации (29148 5.2.6)",
    "ranked": "Ранжирование по важности/стабильности (830 4.3.5)",
    "modifiable": "Модифицируемость (830 4.3.7)",
    "traceable": "Прослеживаемость набора (830 4.3.8)",
}

DEFECT_TYPES: dict[str, str] = {
    "ambiguity": "Неоднозначность",
    "unverifiable": "Непроверяемость",
    "contradiction": "Противоречие",
    "gap": "Пробел / неполнота",
    "design": "Лишнее проектное решение",
    "non_singular": "Составное требование",
    "traceability": "Прослеживаемость",
    "other": "Прочее",
}

SEVERITIES = ("critical", "major", "minor")
SEVERITY_RU = {"critical": "Критичный", "major": "Существенный", "minor": "Незначительный"}


@dataclass
class Line:
    no: int  # 1-based line number in the extracted text
    text: str
    page: int | None = None  # PDF page, 1-based; None for text formats


@dataclass
class Document:
    path: str
    title: str
    lines: list[Line]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def numbered_text(self) -> str:
        """Text with a line-number prefix 'L<no>| ' on each line. The LLM sees this view."""
        return "\n".join(f"L{line.no}| {line.text}" for line in self.lines)


@dataclass
class Location:
    line_start: int
    line_end: int
    page: int | None = None

    def ru(self) -> str:
        span = (f"строка {self.line_start}" if self.line_start == self.line_end
                else f"строки {self.line_start}–{self.line_end}")
        return f"стр. PDF {self.page}, {span}" if self.page else span


@dataclass
class Requirement:
    rid: str  # report ID: R-01, R-02, ...
    doc_id: str | None  # ID in the document (e.g. "Ф-1"); None when absent
    summary: str  # short Russian wording
    quote: str  # verbatim quote from the document
    section: str  # section heading in the document
    kind: str  # functional / nonfunctional / data / integration / acceptance / api / ui / architecture / other
    location: Location | None = None  # set by citations.locate()


@dataclass
class Violation:
    defect: str  # key of DEFECT_TYPES
    criterion: str  # e.g. "29148 5.2.5 Однозначность"
    severity: str  # one of SEVERITIES
    quote: str  # verbatim quote from the document
    explanation: str
    fix: str
    req_ids: list[str] = field(default_factory=list)  # report IDs involved
    quote2: str | None = None  # second quote for contradictions
    location: Location | None = None
    location2: Location | None = None
    verified: bool = False  # True when every quote is found in the document


@dataclass
class RequirementCheck:
    rid: str
    # characteristic key -> "pass" | "fail" | "n/a"
    characteristics: dict[str, str]
    violations: list[Violation]


@dataclass
class SetCheck:
    characteristics: dict[str, str]
    violations: list[Violation]


@dataclass
class Passport:
    document: str
    document_title: str
    date: str  # ISO date-time
    model: str
    agent_version: str  # git commit or tag


@dataclass
class Verdict:
    ready_for_development: bool
    ready_for_testing: bool
    summary: str
    fix_first: list[str]


@dataclass
class Report:
    passport: Passport
    requirements: list[Requirement]
    checks: list[RequirementCheck]
    set_check: SetCheck
    verdict: Verdict
    dropped_violations: int = 0  # violations removed because their quotes were not found


class LLMClient(Protocol):
    model: str

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any] | None = None) -> Any:
        """Return parsed JSON produced by the model."""
        ...
