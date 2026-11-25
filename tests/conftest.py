"""Shared fixtures: the task document and a scripted fake LLM client."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from validator.models import Passport
from validator.reader import read_document

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "requirements.md"


class FakeClient:
    """Return scripted JSON by stage. Record every call for assertions."""

    model = "fake-model"

    def __init__(self, extract=None, checks=None, set_check=None, verdict=None, coverage=None,
                 critic=None):
        self.critic = critic  # callable(list of violation views) -> dict, None = confirm all
        self.coverage = coverage if coverage is not None else {"matrix": []}
        self.extract = extract if extract is not None else EXTRACT
        self.checks = checks  # callable(rids) -> dict, or None for default
        self.set_check = set_check if set_check is not None else SET
        self.verdict = verdict if verdict is not None else VERDICT
        self.calls: list[tuple[str, str]] = []

    def generate_json(self, system, prompt, schema=None):
        self.calls.append((system, prompt))
        if "Код уже нарезал документ" in system:
            return classify_answer(prompt)
        if "нарезать документ" in system:
            return copy.deepcopy(self.extract)
        if "строгий ревьюер" in system:
            block = prompt.split("Требования для проверки:")[1].split("Верни объект")[0]
            rids = [r["rid"] for r in json.loads(block)]
            return self.checks(rids) if self.checks else default_checks(rids)
        if "набор требований целиком" in system:
            return copy.deepcopy(self.set_check)
        if "независимый критик" in system:
            block = prompt.split("Замечания:")[1].split("Документ:")[0]
            views = json.loads(block)
            if self.critic:
                return self.critic(views)
            return {"reviews": [{"vid": v["vid"], "decision": "confirm", "reason": "ок"}
                                for v in views]}
        if "тест-аналитик" in system:
            return copy.deepcopy(self.coverage)
        if "вердикт" in system:
            if isinstance(self.verdict, Exception):
                raise self.verdict
            return copy.deepcopy(self.verdict)
        raise AssertionError("unexpected prompt")


def _req(doc_id, section, kind, quote, a, b, summary):
    return {"doc_id": doc_id, "section": section, "kind": kind, "quote": quote,
            "line_start": a, "line_end": b, "summary": summary}


# Extraction answer for docs/requirements.md (order shuffled on purpose; one fake quote).
EXTRACT = {"requirements": [
    _req("Ф-2", "Функциональные требования", "functional",
         "Ф-2. Месяц определяется по дате совершения покупки в Алматы.", 38, 38,
         "Месяц расходов по дате совершения покупки"),
    _req("Ф-1", "Функциональные требования", "functional",
         "Ф-1. Держатель активной карты KZT задаёт целый порог от 1 000 до 1 000 000 ₸ включительно.",
         37, 37, "Порог 1 000–1 000 000 ₸"),
    _req("Ф-3", "Функциональные требования", "functional",
         "На карту приходится максимум один такой push за календарный месяц.", 39, 39,
         "Один push в месяц"),
    _req(None, "Нефункциональные требования", "nonfunctional",
         "Сервис передаёт push на отправку в течение минуты после получения события.", 46, 46,
         "Push за минуту"),
    _req(None, "Критерии приемки", "acceptance",
         "| Покупка 31 января в 23:58 на 4 000 ₸ | Провести 1 февраля в 00:03 по Алматы |", 64, 64,
         "Покупка на стыке месяцев"),
    _req(None, "Общие параметры", "api", "REST type | GET", 104, 104, "Метод GET"),
    # Model paraphrase that is not in the text, but the line hint is correct → quote replaced.
    _req(None, "Особые правила", "api", "Порог хранится как целое число тенге", 124, 124,
         "Целые тенге"),
    # Hallucinated requirement with a wrong hint → dropped.
    _req(None, "Выдумка", "other", "Система должна поддерживать биткоин", 0, 0, "Выдумка"),
    # Duplicate of Ф-1 → dropped.
    _req("Ф-1", "Функциональные требования", "functional",
         "Держатель активной карты KZT задаёт целый порог", 37, 37, "Порог"),
]}


def _v(defect, severity, quote, quote2=None, criterion="ISO/IEC/IEEE 29148 5.2.5"):
    return {"defect": defect, "criterion": criterion, "severity": severity, "quote": quote,
            "quote2": quote2, "explanation": "Конкретная причина.", "fix": "Конкретная правка."}


ALL_PASS = {k: "pass" for k in ["necessary", "implementation_free", "unambiguous", "consistent",
                                "complete", "singular", "feasible", "traceable", "verifiable",
                                "correct"]}


def default_checks(rids):
    out = []
    for rid in rids:
        chars = dict(ALL_PASS)
        viol = []
        if rid == "R-02":  # Ф-2
            chars["consistent"] = "fail"
            viol.append(_v("contradiction", "critical",
                           "Месяц определяется по дате совершения покупки в Алматы",
                           "Февральский счётчик вырос на 4 000 ₸"))
        if rid == "R-04":  # NFR
            chars["verifiable"] = "fail"
            viol.append(_v("unverifiable", "major", "в течение минуты после получения события"))
            # Fake quote → must be dropped.
            viol.append(_v("ambiguity", "minor", "сервис работает очень быстро и удобно"))
        if rid == "R-06":  # GET
            chars["correct"] = "fail"
            viol.append(_v("other", "critical", "**REST type** | GET"))
        if rid == "R-07":
            chars["complete"] = "fail"  # fail without evidence → becomes pass
        out.append({"rid": rid, "characteristics": chars, "violations": viol})
    return {"checks": out}


SET = {
    "characteristics": {k: "pass" for k in ["complete", "consistent", "feasible", "comprehensible",
                                             "able_to_be_validated", "ranked", "modifiable",
                                             "traceable"]},
    "violations": [
        {**_v("contradiction", "critical",
              "максимум один такой push за календарный месяц",
              "следующее достижение вызывает push независимо от истории предыдущей настройки",
              "ISO/IEC/IEEE 29148 5.2.6"), "req_ids": ["R-03"]},
        {**_v("contradiction", "critical", "403. Unsuccessful — Forbidden",
              "Для чужой карты возвращается 404, как и для отсутствующей карты.",
              "ISO/IEC/IEEE 29148 5.2.6"), "req_ids": []},
        {**_v("gap", "major", "заполняются тестировщиками", None, "IEEE 830 4.3.3"),
         "req_ids": []},
        # Contradiction without a second quote → dropped.
        {**_v("contradiction", "major", "Сохранение порога"), "req_ids": ["R-06"]},
        # Repeat of a per-requirement finding → removed as duplicate.
        {**_v("unverifiable", "major", "в течение минуты после получения события"),
         "req_ids": ["R-04"]},
        # Unknown rid → filtered.
        {**_v("gap", "minor", "Документ (версия/дата) в источнике не заполнен"),
         "req_ids": ["R-99"]},
    ],
}

VERDICT = {"summary": "Документ не готов: есть противоречия.",
           "fix_first": ["(R-02) Выбрать одну дату для месяца.", "(R-06) Заменить GET на POST."]}


def classify_answer(prompt):
    """Mark every candidate as a requirement except checklist rows ("Да / Нет") and line 57."""
    items = []
    for line in prompt.split("Кандидаты:")[1].strip().splitlines():
        cid, lines, block, doc_id, text = [x.strip() for x in line.split(" | ", 4)]
        start = int(lines[1:].split("-")[0])
        is_req = "Да / Нет" not in text and start != 57
        kind = "acceptance" if 63 <= start <= 65 else ("functional" if doc_id != "-" else "api")
        items.append({"cid": cid, "is_requirement": is_req, "kind": kind,
                      "summary": f"Кратко {cid}" if is_req else ""})
    return {"items": items}


@pytest.fixture(autouse=True)
def llm_segmenter_mode(monkeypatch, request):
    """Old tests use the LLM extraction mode; tests marked 'segmenter' use the code segmenter."""
    if "facts" not in request.keywords:
        monkeypatch.setenv("VALIDATOR_FACTS", "off")
    else:
        monkeypatch.delenv("VALIDATOR_FACTS", raising=False)
    if "segmenter" not in request.keywords:
        monkeypatch.setenv("VALIDATOR_SEGMENTER", "off")
    else:
        monkeypatch.delenv("VALIDATOR_SEGMENTER", raising=False)


@pytest.fixture
def document():
    return read_document(str(DOC))


@pytest.fixture
def passport():
    return Passport(document="docs/requirements.md", document_title="Уведомление о расходах по карте",
                    date="2026-11-25T12:00:00+05:00", model="fake-model", agent_version="test")


@pytest.fixture
def fake():
    return FakeClient()


COVERAGE = {"matrix": [
    {"rid": "R-01", "covered_by": ["R-05", "R-99"], "uncovered": [
        {"condition": "нижняя граница 1 000 ₸", "quote": "от 1 000 до 1 000 000 ₸ включительно",
         "fix": "Порог 999 ₸ | Сохранить | Ошибка «Проверьте сумму»", "severity": "major"},
        {"condition": "выдумка", "quote": "лимит 5 000 000 ₸ для VIP", "fix": "x",
         "severity": "critical"},
    ]},
    {"rid": "R-03", "covered_by": [], "uncovered": [
        {"condition": "второй push в месяце", "quote": "максимум один такой push",
         "fix": "Push уже был | Достичь порога снова | Push нет", "severity": "critical"},
    ]},
    {"rid": "R-77", "covered_by": [], "uncovered": [
        {"condition": "чужой rid", "quote": "Тело не используется", "fix": "x", "severity": "major"},
    ]},
]}
