"""Citation check: find a model quote in the document and compute its exact location.

The LLM can change Markdown markup, quote marks, dashes, spaces or letter case.
The search normalizes both texts, keeps a map from each normalized character to its
source line, and finds the quote. A quote that the search cannot find is not trusted.
"""
from __future__ import annotations

import difflib
import re

from .models import Document, Location, Requirement, Violation

_DROP = set("*`#>_\\")
_SPACE = set("|       \t")
_QUOTES = set("«»“”„\"'‘’‚")
_DASHES = set("‐‑‒–—―−")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\(\.\.\.\))\s*")
_LINE_PREFIX = re.compile(r"(?m)^\s*L\d+\|\s?")
FUZZY_THRESHOLD = 0.86


def _norm_char(ch: str) -> str:
    if ch in _DROP:
        return ""
    if ch in _SPACE or ch.isspace():
        return " "
    if ch in _QUOTES:
        return '"'
    if ch in _DASHES:
        return "-"
    ch = ch.lower()
    return "е" if ch == "ё" else ch


def normalize(text: str) -> str:
    out: list[str] = []
    for ch in text:
        n = _norm_char(ch)
        if n == " " and (not out or out[-1] == " "):
            continue
        out.append(n)
    return "".join(out).strip()


class Locator:
    """Normalized view of a document with a character-to-line map."""

    def __init__(self, document: Document):
        self.document = document
        self.page_of = {line.no: line.page for line in document.lines}
        chars: list[str] = []
        owner: list[int] = []
        for line in document.lines:
            if chars and chars[-1] != " ":
                chars.append(" ")
                owner.append(line.no)
            for ch in line.text:
                n = _norm_char(ch)
                if not n or (n == " " and (not chars or chars[-1] == " ")):
                    continue
                chars.append(n)
                owner.append(line.no)
        self.text = "".join(chars)
        self.owner = owner
        self.norm_lines = [(line.no, normalize(line.text)) for line in document.lines]

    # -- exact search -------------------------------------------------------
    def _occurrences(self, needle: str) -> list[tuple[int, int]]:
        spans, start = [], 0
        while needle:
            i = self.text.find(needle, start)
            if i < 0:
                break
            spans.append((i, i + len(needle) - 1))
            start = i + 1
        return spans

    def _loc(self, a: int, b: int) -> Location:
        first, last = self.owner[a], self.owner[b]
        return Location(first, last, self.page_of.get(first))

    @staticmethod
    def _pick(spans: list[tuple[int, int]], key) -> tuple[int, int]:
        return min(spans, key=key)

    def locate(self, quote: str, hint_line: int | None = None) -> Location | None:
        if not quote or not quote.strip():
            return None
        quote = _LINE_PREFIX.sub("", quote)
        parts = [normalize(p) for p in _ELLIPSIS.split(quote)]
        parts = [p.strip(' ".,;:') for p in parts]
        parts = [p for p in parts if p]
        if not parts:
            return None
        self.last_fuzzy = None
        found = self._locate_parts(parts, hint_line)
        if found:
            return found
        return self._locate_fuzzy(" ".join(parts), hint_line)

    def exact_quote(self, quote: str, loc: Location | None) -> str:
        """The document's own words for a quote found by fuzzy search (fixes model typos)."""
        window = getattr(self, "last_fuzzy", None)
        if not window or loc is None:
            return quote
        words = " ".join(l.text for l in self.document.lines
                         if loc.line_start <= l.no <= loc.line_end).split()
        for i in range(len(words)):
            for j in range(i + 1, len(words) + 1):
                got = normalize(" ".join(words[i:j]))
                if got == window:
                    return " ".join(words[i:j]).strip("*`")
                if len(got) > len(window):
                    break
        return quote

    def _locate_parts(self, parts: list[str], hint_line: int | None) -> Location | None:
        first_spans = self._occurrences(parts[0])
        if not first_spans:
            return None

        def dist(span: tuple[int, int]) -> int:
            return abs(self.owner[span[0]] - hint_line) if hint_line else 0

        for a, b in sorted(first_spans, key=dist):
            end, ok = b, True
            for part in parts[1:]:
                i = self.text.find(part, end + 1)
                # Fragments of one quote must stay close together (≤ 3 lines apart).
                if i < 0 or self.owner[i] - self.owner[end] > 3:
                    ok = False
                    break
                end = i + len(part) - 1
            if ok:
                return self._loc(a, end)
        return None

    def _locate_fuzzy(self, needle: str, hint_line: int | None) -> Location | None:
        """Window of document words with about the same length as the quote, ratio ≥ threshold."""
        words = needle.split()
        n = len(words)
        if n < 3 or len(needle) < 12:
            return None
        if not hasattr(self, "_tokens"):
            self._tokens = [(m.group(), self.owner[m.start()], self.owner[m.end() - 1])
                            for m in re.finditer(r"\S+", self.text)]
        toks = self._tokens
        best: tuple[float, int, int, int] | None = None  # score, -distance, first, last
        best_window = ""
        for size in range(max(1, n - 2), n + 3):
            for i in range(0, len(toks) - size + 1):
                window = " ".join(t[0] for t in toks[i:i + size])
                m = difflib.SequenceMatcher(None, needle, window, autojunk=False)
                if m.real_quick_ratio() < FUZZY_THRESHOLD or m.quick_ratio() < FUZZY_THRESHOLD:
                    continue
                score = m.ratio()
                if score < FUZZY_THRESHOLD:
                    continue
                first, last = toks[i][1], toks[i + size - 1][2]
                dist = -abs(first - hint_line) if hint_line else 0
                cand = (round(score, 4), dist, first, last)
                if best is None or cand[:2] > best[:2]:
                    best = cand
                    best_window = window
        if not best:
            return None
        self.last_fuzzy = best_window
        return Location(best[2], best[3], self.page_of.get(best[2]))

    def lines_text(self, start: int, end: int) -> str:
        return " ".join(l.text.strip() for l in self.document.lines if start <= l.no <= end).strip()


def attach_requirement_locations(locator: Locator, reqs: list[Requirement],
                                 hints: dict[str, tuple[int, int]]) -> list[Requirement]:
    """Locate every requirement quote. On failure, replace the quote with the hinted lines."""
    kept: list[Requirement] = []
    max_line = len(locator.document.lines)
    for req in reqs:
        hint = hints.get(req.rid)
        loc = locator.locate(req.quote, hint[0] if hint else None)
        if loc is not None:
            req.quote = locator.exact_quote(req.quote, loc)
        if loc is None and hint:
            a, b = max(1, hint[0]), min(max_line, max(hint[0], hint[1]))
            text = locator.lines_text(a, b)
            if text:
                req.quote = text
                loc = Location(a, b, locator.page_of.get(a))
        if loc is None:
            continue  # nothing in the document confirms this requirement
        req.location = loc
        kept.append(req)
    return kept


def verify_violations(locator: Locator, violations: list[Violation],
                      hint_of: dict[str, int] | None = None) -> tuple[list[Violation], int]:
    """Keep only violations whose quotes exist in the document. Return (kept, dropped)."""
    kept: list[Violation] = []
    dropped = 0
    hint_of = hint_of or {}
    for v in violations:
        hint = next((hint_of[r] for r in v.req_ids if r in hint_of), None)
        v.location = locator.locate(v.quote, hint)
        v.quote = locator.exact_quote(v.quote, v.location)
        ok = v.location is not None
        if v.quote2:
            hint2 = next((hint_of[r] for r in reversed(v.req_ids) if r in hint_of), None)
            v.location2 = locator.locate(v.quote2, hint2)
            v.quote2 = locator.exact_quote(v.quote2, v.location2)
            ok = ok and v.location2 is not None
        elif v.defect == "contradiction":
            ok = False  # a contradiction needs two places in the text
        v.verified = ok
        if ok:
            kept.append(v)
        else:
            dropped += 1
    return kept, dropped
