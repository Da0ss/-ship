"""Russian lexicon of ambiguity / unverifiability indicators for requirements.

Classes follow ISO/IEC/IEEE 29148:2018 (5.2.7, characteristics of good
requirements: unambiguous, verifiable) and the INCOSE Guide for Writing
Requirements (rules on vague terms, superlatives, subjective language,
open-ended clauses, escape clauses, universal quantifiers, "and/or").

Matching is by lemma with pymorphy3 (all normal forms of every token, so
ambiguous parses still match). Without pymorphy3 it falls back to a lowercase
substring match with a word-start boundary.
"""
from __future__ import annotations

import re
from functools import lru_cache

try:  # pragma: no cover - depends on environment
    import pymorphy3 as _pymorphy3
except Exception:  # ImportError or broken dictionaries
    _pymorphy3 = None

# class key -> terms in dictionary form (single words or phrases)
WEAK_TERMS: dict[str, list[str]] = {
    "superlative": [
        "лучший", "наилучший", "максимальный", "минимальный", "оптимальный",
        "наибольший", "наименьший", "наиболее", "самый", "высший", "идеальный",
        "предельный", "максимально", "минимально",
    ],
    "subjective": [
        "удобный", "удобно", "понятный", "понятно", "простой", "просто",
        "дружественный", "интуитивный", "интуитивно", "user-friendly",
        "гибкий", "адекватный", "эффективный", "эффективно", "надёжный",
        "надёжно", "комфортный", "современный", "хороший", "хорошо",
    ],
    "vague_adverb": [
        "быстро", "своевременно", "достаточно", "значительно", "примерно",
        "обычно", "как правило", "приблизительно", "относительно", "заметно",
        "существенно", "часто", "редко", "качественно",
    ],
    "vague_quantity": [
        "несколько", "много", "мало", "некоторый", "большинство", "максимум",
        "минимум", "множество", "ряд", "большой", "небольшой", "различный",
    ],
    "open_ended": [
        "и т.д.", "и т.п.", "и др.", "и пр.", "и далее", "и прочее",
        "в том числе", "включая", "например", "etc.",
    ],
    "loophole": [
        "по возможности", "при необходимости", "если возможно",
        "по усмотрению", "в случае необходимости", "при желании",
        "если необходимо", "по мере необходимости", "при наличии возможности",
        "в разумных пределах", "по согласованию",
    ],
    "comparative": [
        "лучше", "быстрее", "больше", "меньше", "выше", "ниже", "проще",
        "удобнее", "легче", "чаще", "реже", "точнее",
    ],
    "universal": [
        "все", "весь", "любой", "каждый", "всегда", "никогда", "всякий",
        "ни один", "везде", "полностью", "целиком",
    ],
    "and_or": [
        "и/или", "или", "либо", "и/либо",
    ],
    "vague_time": [
        "в течение", "в момент", "сразу", "немедленно", "своевременно",
        "оперативно", "в кратчайший срок", "в ближайшее время", "вскоре",
        "незамедлительно", "в реальном времени", "по истечении",
    ],
    "undefined_reference": [
        "соответствующий", "необходимый", "указанный", "такой", "данный",
        "вышеуказанный", "вышеупомянутый", "упомянутый", "известный",
        "определённый", "нужный", "подобный",
    ],
    "passive_unclear": [
        "при ошибке", "результат",
    ],
}

CLASS_RU: dict[str, str] = {
    "superlative": "Превосходная степень",
    "subjective": "Субъективная оценка",
    "vague_adverb": "Расплывчатое наречие",
    "vague_quantity": "Неопределённое количество",
    "open_ended": "Открытый перечень",
    "loophole": "Лазейка (необязательность)",
    "comparative": "Сравнение без базы",
    "universal": "Квантор всеобщности",
    "and_or": "И/или (неоднозначная связка)",
    "vague_time": "Неопределённое время",
    "undefined_reference": "Неопределённая ссылка",
    "passive_unclear": "Неясный результат/ошибка",
}

# On overlapping spans the earlier / longer / higher-priority class wins.
_PRIORITY = {k: i for i, k in enumerate([
    "open_ended", "loophole", "vague_time", "comparative", "and_or",
    "universal", "vague_quantity", "superlative", "subjective",
    "vague_adverb", "undefined_reference", "passive_unclear",
])}

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-/][0-9A-Za-zА-Яа-яЁё]+)*")
_GAP_STOP = re.compile(r"[.!?;:\n]")


def _norm(text: str) -> str:
    """Lowercase, ё->е, preserving length 1:1 (offsets stay valid)."""
    out = []
    for ch in text:
        low = ch.lower()
        if len(low) != 1:
            low = ch
        out.append("е" if low == "ё" else low)
    return "".join(out)


# ---------------------------------------------------------------- morphology
_MORPH = None


def _analyzer():
    global _MORPH
    if _MORPH is None and _pymorphy3 is not None:
        try:
            _MORPH = _pymorphy3.MorphAnalyzer()
        except Exception:
            _MORPH = False
    return _MORPH or None


def morph_available() -> bool:
    return _analyzer() is not None


@lru_cache(maxsize=65536)
def _parse(word: str) -> tuple[frozenset, bool, tuple]:
    """(all normal forms incl. the word itself, has a COMP parse,
    per-parse info [(lemma, grammemes)] in pymorphy rank order)."""
    w = _norm(word)
    m = _analyzer()
    if m is None:
        return frozenset({w}), False, ()
    forms = {w}
    comp = False
    infos = []
    for p in m.parse(w):
        lemma = _norm(p.normal_form)
        forms.add(lemma)
        infos.append((lemma, frozenset(p.tag.grammemes)))
        if "COMP" in p.tag:
            comp = True
    return frozenset(forms), comp, tuple(infos)


def _term_lemmas(word: str) -> frozenset:
    """Lemma set of a lexicon word: the word itself + its top-ranked normal form
    (using all parses here would over-match, e.g. 'данный' -> 'дать')."""
    w = _norm(word)
    m = _analyzer()
    if m is None:
        return frozenset({w})
    return frozenset({w, _norm(m.parse(w)[0].normal_form)})


# ------------------------------------------------------------ compiled terms
def _is_raw(term: str) -> bool:
    return "." in term


def _build_dotted(term: str) -> str:
    parts = _norm(term).split()
    rx = ""
    for i, part in enumerate(parts):
        if i:
            rx += r"\s*" if parts[i - 1].endswith(".") else r"\s+"
        letters = [s for s in part.split(".")]
        pieces = []
        for j, s in enumerate(letters):
            if j < len(letters) - 1:
                pieces.append(re.escape(s) + r"\.")
            elif s:
                pieces.append(re.escape(s))
        rx += r"\s*".join(pieces)
    return rx


@lru_cache(maxsize=1)
def _compiled():
    """Compile terms once: (morph_terms, raw_terms)."""
    morph_terms = []  # (term, cls, [lemma sets])
    raw_terms = []    # (term, cls, regex)
    for cls, terms in WEAK_TERMS.items():
        for term in terms:
            if _is_raw(term):
                raw_terms.append((term, cls, re.compile(
                    r"(?<!\w)" + _build_dotted(term) + r"(?!\w)")))
                continue
            words = _TOKEN_RE.findall(term)
            if not words:
                continue
            lemmas = [_term_lemmas(w) for w in words]
            morph_terms.append((term, cls, lemmas))
    return morph_terms, raw_terms


@lru_cache(maxsize=1)
def _fallback_patterns():
    pats = []
    for cls, terms in WEAK_TERMS.items():
        for term in terms:
            t = _norm(term)
            if _is_raw(term):
                rx = _build_dotted(term)
            else:
                stem = t
                if cls != "comparative" and len(stem) >= 6 and " " not in stem and stem[-1] in "ийыоаяеью":
                    stem = stem.rstrip("ийыоаяеью")[: max(4, len(stem) - 2)]
                rx = re.escape(stem).replace(r"\ ", r"\s+")
            pats.append((term, cls, re.compile(r"(?<!\w)" + rx)))
    return pats


# ------------------------------------------------------------------- matching
# --- context / tag filters (false-positive suppression) ---------------------
_ADJ = {"ADJF", "PRTF"}
# term -> allowed POS of the matching parse; the token is also rejected when
# its top-ranked parse is a NOUN ("данные" = data, not "данный").
_ADJ_ONLY_NOT_NOUN = {"данный", "указанный", "соответствующий"}
_STOP_AFTER_RESULT = {"сохранения", "ok", "error", "200", "201"}
_NUM_RE = re.compile(r"^\d+$")


def _parse_ok(term, cls, infos, lemmas_j):
    """Which parses of a token may match this lexicon term."""
    ps = [(l, g) for l, g in infos if l in lemmas_j]
    t = _norm(term)
    if cls != "comparative" and ps:
        # comparative forms ("больше" -> "большой") belong to the comparative class
        ps = [(l, g) for l, g in ps if "COMP" not in g]
        if not ps:
            return False
    if t in _ADJ_ONLY_NOT_NOUN:
        for l, g in infos:
            if g & _ADJ and l in lemmas_j:
                return True
            if "NOUN" in g:  # a noun reading outranks the adjective one
                return False
        return False
    if t == "простой":
        return any(g & {"ADJF", "ADJS"} for _, g in ps)
    if cls == "superlative":
        # "лучший"/"наилучший" share the lemma "хороший": only true
        # superlative forms (Supr tag) count, a plain "хороший" does not
        return any(l != "хороший" or "Supr" in g for l, g in ps)
    if cls == "subjective" and t in ("хороший", "хорошо"):
        # "лучше"/"лучший" are comparative/superlative, not plain "хороший"
        return any(not (g & {"Supr", "COMP"}) for _, g in ps)
    return True  # incl. words matched only by surface form


def _has(tok, gram, pos=None):
    return any(gram in g and (pos is None or pos in g) for _, g in tok[2][2])


def _ctx_ok(term, cls, toks, i, n, low, text):
    """Context rules; i..i+n-1 is the matched token range."""
    t = _norm(term)
    if t == "простой":
        if i > 0 and "время" in toks[i - 1][2][0]:
            return False
        if i + n < len(toks) and toks[i + n][2][0] & {"система", "сервис"}:
            return False
    if t == "результат":
        if text.lstrip().startswith("|"):
            return False
        for k in range(i + n, min(i + n + 2, len(toks))):
            if low[toks[k][0]:toks[k][1]] in _STOP_AFTER_RESULT:
                return False
    return True


def _comp_has_baseline(toks, i, low):
    """Comparative with an explicit baseline is not vague."""
    if i + 1 < len(toks):
        nxt = toks[i + 1]
        w = low[nxt[0]:nxt[1]]
        if _NUM_RE.match(w) or w == "чем":
            return True
        if _has(nxt, "gent", "NOUN"):
            return True
        # "ниже нового порога": genitive adjective + genitive noun
        if _has(nxt, "gent", "ADJF") and i + 2 < len(toks) and _has(toks[i + 2], "gent", "NOUN"):
            return True
    # "равной порогу или выше": baseline stands before "или"/"либо"
    if i >= 2 and low[toks[i - 1][0]:toks[i - 1][1]] in ("или", "либо") \
            and (_has(toks[i - 2], "datv", "NOUN") or _has(toks[i - 2], "gent", "NOUN")):
        return True
    return False


def _hit(term, cls, text, s, e):
    return {"term": term, "class": cls, "match": text[s:e], "start": s, "end": e}


def _candidates_morph(text: str, low: str):
    morph_terms, raw_terms = _compiled()
    toks = [(m.start(), m.end(), _parse(m.group())) for m in _TOKEN_RE.finditer(low)]
    cands = []

    # comparatives by COMP tag
    listed = {}
    for t in WEAK_TERMS["comparative"]:
        listed[_norm(t)] = t
    for ti, (s, e, (forms, comp, _inf)) in enumerate(toks):
        if comp:
            if _comp_has_baseline(toks, ti, low):
                continue
            word = low[s:e]
            term = listed.get(word)
            if term is None:
                term = next((listed[f] for f in forms if f in listed), None)
            if term is None:
                term = sorted(forms - {word})[0] if len(forms) > 1 else word
            cands.append((s, e, term, "comparative"))

    for term, cls, lemmas in morph_terms:
        if cls == "comparative":
            continue
        n = len(lemmas)
        for i in range(len(toks) - n + 1):
            ok = True
            for j in range(n):
                if not (toks[i + j][2][0] & lemmas[j]) or not _parse_ok(
                        term, cls, toks[i + j][2][2], lemmas[j]):
                    ok = False
                    break
                if j and _GAP_STOP.search(low[toks[i + j - 1][1]:toks[i + j][0]]):
                    ok = False
                    break
            if ok and _ctx_ok(term, cls, toks, i, n, low, text):
                cands.append((toks[i][0], toks[i + n - 1][1], term, cls))

    for term, cls, rx in raw_terms:
        for m in rx.finditer(low):
            cands.append((m.start(), m.end(), term, cls))
    return cands


def _candidates_fallback(text: str, low: str):
    cands = []
    for term, cls, rx in _fallback_patterns():
        for m in rx.finditer(low):
            # extend to the end of the word the prefix landed in
            e = m.end()
            while e < len(low) and (low[e].isalnum()) and not _is_raw(term):
                e += 1
            cands.append((m.start(), e, term, cls))
    return cands


def find_weak_terms(text: str) -> list[dict]:
    """Find weak terms; document order, no overlapping/duplicate spans."""
    if not text:
        return []
    low = _norm(text)
    cands = _candidates_morph(text, low) if morph_available() else _candidates_fallback(text, low)
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0]), _PRIORITY.get(c[3], 99)))
    out, last_end = [], -1
    for s, e, term, cls in cands:
        if s < last_end:
            continue
        out.append(_hit(term, cls, text, s, e))
        last_end = e
    return out
