import pytest

from tests.conftest import DOC, FakeClient
from validator import pipeline
from validator.reader import read_document
from validator.segmenter import segment

pytestmark = pytest.mark.segmenter


def _skipped(text):
    s = text.strip()
    return (not s or s.startswith("#") or s in ("---", "```") or s.startswith("```")
            or set(s) <= set("|:- ") or set(s.replace("|", "").strip()) == set())


def test_segmenter_covers_every_line(document):
    cands = segment(document)
    covered = [n for c in cands for n in range(c.line_start, c.line_end + 1)]
    assert len(covered) == len(set(covered))  # no line in two candidates
    covered = set(covered)
    for line in document.lines:
        if line.no in covered:
            continue
        # Uncovered lines: markup, empty, table header rows (followed by a separator) only.
        nxt = document.lines[line.no] if line.no < len(document.lines) else None
        header = nxt is not None and line.text.startswith("|") and set(nxt.text) <= set("|:- ")
        assert _skipped(line.text) or header, (line.no, line.text)


def test_segmenter_ids_and_blocks(document):
    cands = segment(document)
    by_line = {c.line_start: c for c in cands}
    assert [by_line[n].doc_id for n in (37, 38, 39, 40)] == ["Ф-1", "Ф-2", "Ф-3", "Ф-4"]
    assert {c.doc_id for c in cands if c.doc_id} == {"Ф-1", "Ф-2", "Ф-3", "Ф-4"}
    assert [by_line[n].block for n in (63, 117, 130, 162, 163)] == [
        "table_row", "numbered", "code", "paragraph", "paragraph"]
    assert by_line[64].section == "Критерии приемки"
    assert by_line[143].section == "Query-параметры"


def test_classify_mode_exact_locations(document, passport):
    client = FakeClient()
    report = pipeline.run(document, client, passport, log=lambda m: None)
    reqs = report.requirements
    # Checklist rows (72–85) and line 57 are not requirements; the rest are.
    starts = [r.location.line_start for r in reqs]
    assert not any(72 <= s <= 85 for s in starts) and 57 not in starts
    f1 = next(r for r in reqs if r.doc_id == "Ф-1")
    assert f1.quote.startswith("Держатель активной карты KZT") and f1.location.line_start == 37
    row = next(r for r in reqs if r.location.line_start == 64)
    assert row.quote == ("Покупка 31 января в 23:58 на 4 000 ₸ | Провести 1 февраля в 00:03 по "
                         "Алматы | Февральский счётчик вырос на 4 000 ₸")
    assert row.kind == "acceptance"
    assert [r.rid for r in reqs][:3] == ["R-01", "R-02", "R-03"]
    # The LLM extraction prompt is not used in this mode.
    assert not any("нарезать документ" in s for s, _ in client.calls)
    # Every requirement quote is found in the document at its own line.
    from validator.citations import Locator
    loc = Locator(document)
    for r in reqs:
        found = loc.locate(r.quote, r.location.line_start)
        assert found and found.line_start == r.location.line_start, r.quote


def test_classify_missing_items_kept(document, passport):
    class Lazy(FakeClient):
        def generate_json(self, system, prompt, schema=None):
            if "Код уже нарезал" in system:
                self.calls.append((system, prompt))
                return {"items": []}
            return super().generate_json(system, prompt, schema)

    report = pipeline.run(document, Lazy(), passport, log=lambda m: None)
    assert len(report.requirements) == len(segment(document))


def _scan_pdf(path):
    from PIL import Image, ImageDraw, ImageFont
    import glob
    fonts = glob.glob("/usr/share/fonts/**/DejaVuSans.ttf", recursive=True)
    if not fonts:
        pytest.skip("нет шрифта с кириллицей")
    img = Image.new("RGB", (2480, 600), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(fonts[0], 48)
    draw.text((100, 150), "Ф-1. Держатель карты задаёт порог.", fill="black", font=font)
    draw.text((100, 300), "Сервис передаёт push в течение минуты.", fill="black", font=font)
    img.save(str(path), "PDF", resolution=300)


def test_ocr_scanned_pdf(tmp_path):
    pytest.importorskip("pytesseract")
    import shutil
    if not shutil.which("tesseract"):
        pytest.skip("нет tesseract")
    path = tmp_path / "scan.pdf"
    _scan_pdf(path)
    doc = read_document(str(path))
    assert doc.ocr_pages == [1]
    text = " ".join(l.text for l in doc.lines)
    assert "Держатель карты" in text and "push" in text
    assert all(l.page == 1 for l in doc.lines)


def test_ocr_off_raises_on_scan(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _scan_pdf(path)
    monkeypatch.setenv("VALIDATOR_OCR", "off")
    with pytest.raises(ValueError, match="tesseract-ocr-rus"):
        read_document(str(path))


def test_markdown_has_no_ocr_pages():
    assert read_document(str(DOC)).ocr_pages == []


@pytest.mark.parametrize("text,cls,match", [
    ("Сервис передаёт push в течение минуты", "vague_time", "в течение"),
    ("На карту приходится максимум один push", "vague_quantity", "максимум"),
    ("Система работает быстрее", "comparative", "быстрее"),
    ("все пользователи и т.д.", "open_ended", "и т.д."),
    ("при необходимости повторить", "loophole", "при необходимости"),
    ("Интерфейс должен быть удобным", "subjective", "удобным"),  # inflected form
])
def test_lexicon_hits(text, cls, match):
    from validator.lexicon import find_weak_terms
    hits = find_weak_terms(text)
    assert any(h["class"] == cls and h["match"].lower() == match for h in hits), hits
    for h in hits:
        assert text[h["start"]:h["end"]].lower() == h["match"].lower()


@pytest.mark.parametrize("text", ["В расходы входят проведённые покупки", "Держатель карты"])
def test_lexicon_no_false_hits(text):
    from validator.lexicon import find_weak_terms
    assert find_weak_terms(text) == []


def test_passport_ocr_row(document, fake, passport):
    from validator.report import render
    report = pipeline.run(document, fake, passport, log=lambda m: None)
    assert "| OCR | не применялся |" in render(report)
    report.passport.ocr_pages = [1, 3]
    assert "| OCR | страницы 1, 3 (распознаны Tesseract) |" in render(report)


def test_cli_passes_ocr_pages(tmp_path, monkeypatch):
    from validator import cli
    path = tmp_path / "scan.pdf"
    _scan_pdf(path)
    pytest.importorskip("pytesseract")
    import shutil
    if not shutil.which("tesseract"):
        pytest.skip("нет tesseract")
    out = tmp_path / "r.md"
    assert cli.main([str(path), str(out), "--env-file", str(tmp_path / "n")],
                    client=FakeClient()) == 0
    assert "| OCR | страницы 1 (распознаны Tesseract) |" in out.read_text(encoding="utf-8")
