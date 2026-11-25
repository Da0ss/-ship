from pathlib import Path

import pytest

from validator.citations import Locator, normalize, verify_violations
from validator.models import Document, Line, Violation
from validator.reader import read_document


def test_markdown_lines_match_file(document):
    raw = Path(document.path).read_text(encoding="utf-8").splitlines()
    assert len(document.lines) == len(raw) == 198
    assert document.title == "Уведомление о расходах по карте"
    for line, text in zip(document.lines, raw):
        assert line.text == text
    assert document.lines[36].no == 37 and "Ф-1" in document.lines[36].text
    assert document.numbered_text().splitlines()[36].startswith("L37| ")


def test_reader_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_document(str(tmp_path / "nope.md"))
    bad = tmp_path / "x.xyz"
    bad.write_text("a")
    with pytest.raises(ValueError):
        read_document(str(bad))


def _make_pdf(path, pages):
    """Write a minimal valid PDF with one text line per page (no extra dependencies)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def test_reader_pdf_pages(tmp_path):
    path = tmp_path / "r.pdf"
    _make_pdf(path, ["Requirement one shall work", "Requirement two shall fail"])
    doc = read_document(str(path))
    pages = {l.page for l in doc.lines if l.text.strip()}
    assert pages == {1, 2}
    loc = Locator(doc).locate("requirement two shall fail")
    assert loc is not None and loc.page == 2 and "стр. PDF 2" in loc.ru()


def test_normalize():
    assert normalize("**Ф-1.**  Держатель «карты» — ё") == 'ф-1. держатель "карты" - е'


@pytest.mark.parametrize("quote,line", [
    ("Ф-1. Держатель активной карты KZT", 37),  # markdown ** removed
    ("сумма за месяц и кнопка «Сохранить»", 29),
    ('кнопка "Сохранить"', 29),  # other quote marks
    ("REST type | GET", 104),  # table cell
    ("Dev: /alerts/saveLimit", 108),  # backticks removed
    ("СЕРВИС ПЕРЕДАЁТ PUSH на отправку", 46),  # case
    ("От 1 000 до 1 000 000 ₸", 37),
])
def test_locate_exact(document, quote, line):
    loc = Locator(document).locate(quote)
    assert loc is not None, quote
    assert loc.line_start == line


def test_locate_multiline_and_ellipsis(document):
    loc = Locator(document).locate("Проверить пользовательскую сессию. 2. Проверить принадлежность")
    assert (loc.line_start, loc.line_end) == (117, 118)
    loc = Locator(document).locate("Месяц определяется … в Алматы")
    assert (loc.line_start, loc.line_end) == (38, 38)


def test_locate_fragments_far_apart_rejected(document):
    # Fragments from lines 1 and 198 must not form one quote.
    assert Locator(document).locate("Уведомление о расходах ... Ошибка сервиса") is None


def test_locate_hint_picks_nearest(document):
    loc_a = Locator(document).locate("Уведомление о расходах по карте", hint_line=1)
    loc_b = Locator(document).locate("Уведомление о расходах по карте", hint_line=97)
    assert loc_a.line_start == 1
    assert loc_b.line_start == 97


def test_locate_fuzzy_small_typo(document):
    loc = Locator(document).locate("Сервис передает push на отправку в течении минуты после получения события")
    assert loc is not None and loc.line_start == 46


def test_locate_rejects_invented(document):
    loc = Locator(document)
    assert loc.locate("Система должна поддерживать оплату биткоином") is None
    assert loc.locate("") is None
    assert loc.locate("   ") is None


def test_verify_violations_rules(document):
    locator = Locator(document)
    good = Violation("ambiguity", "c", "major", "в течение минуты", "e", "f")
    fake = Violation("ambiguity", "c", "major", "работает очень быстро", "e", "f")
    contra_no_q2 = Violation("contradiction", "c", "critical", "в течение минуты", "e", "f")
    contra = Violation("contradiction", "c", "critical", "Тело не используется", "e", "f",
                       quote2="Content-Type")
    kept, dropped = verify_violations(locator, [good, fake, contra_no_q2, contra])
    assert kept == [good, contra] and dropped == 2
    assert good.verified and good.location.line_start == 46
    assert contra.location.line_start == 148 and contra.location2.line_start == 147


def test_locator_on_synthetic_doc():
    doc = Document("x.md", "x", [Line(1, "A **bold** text"), Line(2, ""), Line(3, "next line")])
    loc = Locator(doc).locate("bold text next line")
    assert (loc.line_start, loc.line_end) == (1, 3)
