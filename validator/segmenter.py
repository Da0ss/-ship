"""Детерминированная нарезка документа с требованиями на кандидаты (без LLM).

LLM позже только классифицирует кандидатов, поэтому сегментатор не теряет текст:
каждая непустая строка, не являющаяся заголовком, разделителем таблицы, ограждением
кода или горизонтальной линией, попадает ровно в один кандидат.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Document, Line


@dataclass
class Candidate:
    cid: str  # "C-001", "C-002", ... в порядке документа
    doc_id: str | None  # ID требования в начале текста, например "Ф-1"
    section: str  # текст ближайшего заголовка выше (без "#" и "**")
    block: str  # "bullet" | "numbered" | "table_row" | "paragraph" | "code"
    text: str  # исходный текст строк, markdown сохранён, строки склеены через " "
    line_start: int
    line_end: int


_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BULLET_RE = re.compile(r"^\s*[*\-+•]\s+")
_NUMBERED_RE = re.compile(r"^\s*(?:\d+[.)]|[A-Za-zА-Яа-яЁё]\))\s+")
_BOLD_ONLY_RE = re.compile(r"^\s*\*\*([^*]+)\*\*\s*$")
_SEP_CELL_RE = re.compile(r"^:?-+:?$")
_ID_RE = re.compile(r"^([A-Za-zА-Яа-яЁё]{1,6}[-.]?\d+(?:\.\d+)*)(?![\w])")
_MARKER_RE = re.compile(r"^\s*(?:[*\-+•]\s+|\d+[.)]\s+|[A-Za-zА-Яа-яЁё]\)\s+)")

_SHORT_HEADING_MAX = 60


def _is_table_row(s: str) -> bool:
    return s.lstrip().startswith("|")


def _cells(s: str) -> list[str]:
    s = s.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(s: str) -> bool:
    if not _is_table_row(s):
        return False
    cells = _cells(s)
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def _is_list_item(s: str) -> bool:
    return bool(_BULLET_RE.match(s) or _NUMBERED_RE.match(s))


def _extract_doc_id(text: str, block: str) -> str | None:
    s = text.strip()
    if block == "table_row":
        s = s.lstrip("|").strip()
    s = _MARKER_RE.sub("", s, count=1) if block in ("bullet", "numbered") else s
    s = s.lstrip("*_ \t")
    m = _ID_RE.match(s)
    if not m:
        return None
    return m.group(1).rstrip(".*")


def _clean_heading(s: str) -> str:
    return re.sub(r"\*\*|__", "", s).strip()


def segment(document: Document) -> list[Candidate]:
    lines: list[Line] = document.lines
    n = len(lines)
    raw = [ln.text for ln in lines]
    has_md_headings = any(_HEADING_RE.match(t) for t in raw)

    def blank(i: int) -> bool:
        return not raw[i].strip()

    def next_nonblank(i: int) -> int | None:
        j = i + 1
        while j < n and blank(j):
            j += 1
        return j if j < n else None

    def is_heading(i: int) -> str | None:
        """Текст заголовка, если строка i — заголовок, иначе None."""
        t = raw[i]
        m = _HEADING_RE.match(t)
        if m:
            return _clean_heading(m.group(1))
        m = _BOLD_ONLY_RE.match(t)
        if m:
            j = next_nonblank(i)
            if j is not None and (_is_table_row(raw[j]) or _is_list_item(raw[j])):
                return _clean_heading(m.group(1))
            return None
        if not has_md_headings:
            s = t.strip()
            if (
                s
                and len(s) <= _SHORT_HEADING_MAX
                and not s.endswith(".")
                and not _is_list_item(t)
                and not _is_table_row(t)
                and not _FENCE_RE.match(t)
                and not _HR_RE.match(t)
                and (i + 1 >= n or blank(i + 1))
                and (i == 0 or blank(i - 1))
            ):
                return _clean_heading(s)
        return None

    out: list[Candidate] = []
    section = ""

    def emit(block: str, text: str, a: int, b: int) -> None:
        out.append(
            Candidate(
                cid=f"C-{len(out) + 1:03d}",
                doc_id=_extract_doc_id(text, block),
                section=section,
                block=block,
                text=text,
                line_start=lines[a].no,
                line_end=lines[b].no,
            )
        )

    i = 0
    while i < n:
        t = raw[i]
        if blank(i):  # 2
            i += 1
            continue
        h = is_heading(i)  # 1
        if h is not None:
            section = h
            i += 1
            continue
        if _HR_RE.match(t):  # 2
            i += 1
            continue
        if _FENCE_RE.match(t):  # 3
            j = i + 1
            inner: list[int] = []
            while j < n and not _FENCE_RE.match(raw[j]):
                if not blank(j):
                    inner.append(j)
                j += 1
            if inner:
                emit("code", " ".join(raw[k].strip() for k in inner), inner[0], inner[-1])
            i = j + 1  # пропускаем закрывающее ограждение (или конец файла)
            continue
        if _is_table_row(t):  # 4
            prev_is_row = i > 0 and _is_table_row(raw[i - 1])
            if _is_separator(t):
                i += 1
                continue
            if not prev_is_row and i + 1 < n and _is_separator(raw[i + 1]):
                i += 1  # строка заголовка таблицы
                continue
            if any(_cells(t)):
                emit("table_row", t.strip(), i, i)
            i += 1
            continue
        if _is_list_item(t):  # 5
            block = "bullet" if _BULLET_RE.match(t) else "numbered"
            j = i + 1
            while (
                j < n
                and not blank(j)
                and raw[j][:1] in (" ", "\t")
                and not _is_list_item(raw[j])
                and not _FENCE_RE.match(raw[j])
            ):
                j += 1
            emit(block, " ".join(raw[k].strip() for k in range(i, j)), i, j - 1)
            i = j
            continue
        # 6: абзац
        j = i
        while True:
            hard_break = raw[j].endswith("  ")
            j += 1
            if hard_break or j >= n:
                break
            nxt = raw[j]
            if (
                blank(j)
                or nxt.lstrip().startswith("**")
                or _HR_RE.match(nxt)
                or _FENCE_RE.match(nxt)
                or _is_table_row(nxt)
                or _is_list_item(nxt)
                or is_heading(j) is not None
            ):
                break
        emit("paragraph", " ".join(raw[k].strip() for k in range(i, j)), i, j - 1)
        i = j
    return out


def format_for_llm(cands: list[Candidate]) -> str:
    return "\n".join(
        f"{c.cid} | L{c.line_start}-{c.line_end} | {c.block} | {c.doc_id or '-'} | {c.text}"
        for c in cands
    )
