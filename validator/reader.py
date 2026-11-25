"""Чтение документа с требованиями (md/txt/pdf/docx) в модель Document."""
from __future__ import annotations

import os
import re
import sys

from .models import Document, Line

_TEXT_EXT = {".md", ".markdown", ".txt"}


def _title_of(lines: list[Line], path: str) -> str:
    for ln in lines:
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", ln.text)
        if m:
            return m.group(1).strip()
    for ln in lines:
        if ln.text.strip():
            return ln.text.strip()
    return os.path.basename(path)


def _read_text(path: str) -> list[Line]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    # splitlines() режет и по \x0c,   и т.п. — номера строк должны совпадать с файлом
    parts = re.split(r"\r\n|\r|\n", raw)
    if parts and parts[-1] == "":
        parts.pop()  # завершающий перевод строки не создаёт лишней строки
    return [Line(no=i, text=t) for i, t in enumerate(parts, 1)]


_OCR_MIN_CHARS = 20
_OCR_SCALE = 300 / 72


class _OcrUnavailable(Exception):
    """pytesseract / pypdfium2 / tesseract binary / language pack is missing."""


def _ocr_mode() -> str:
    mode = os.environ.get("VALIDATOR_OCR", "auto").strip().lower() or "auto"
    return mode if mode in ("auto", "off", "force") else "auto"


def _ocr_page(path: str, pno: int) -> str:
    """OCR one PDF page (1-based). Lazy imports; raises _OcrUnavailable if OCR cannot run."""
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError as e:
        raise _OcrUnavailable(str(e)) from e
    lang = os.environ.get("VALIDATOR_OCR_LANG") or "rus+eng"
    pdf = pdfium.PdfDocument(path)
    try:
        img = pdf[pno - 1].render(scale=_OCR_SCALE).to_pil()
        try:
            return pytesseract.image_to_string(img, lang=lang)
        except pytesseract.TesseractNotFoundError as e:
            raise _OcrUnavailable(str(e)) from e
        except pytesseract.TesseractError as e:
            # missing language pack: "Failed loading language 'rus'" / "Error opening data file"
            raise _OcrUnavailable(str(e)) from e
    finally:
        pdf.close()


def _bad(text: str) -> bool:
    """Line of glyphs without a Unicode mapping (decorative fonts, broken PDF title)."""
    t = text.strip()
    return not t or t.count("\ufffd") >= max(1, len(t) * 0.3)


def _is_bold(ch: dict) -> bool:
    return bool(re.search(r"bold|black|heavy|semibold", ch.get("fontname", ""), re.I))


def _layout_pages(path: str) -> list[list[str]] | None:
    """PDF → Markdown-like lines: headings by font size (#, ##, ...), bordered tables as
    '| a | b |' rows, a bold line start as **...**, a blank line between paragraphs.
    The checks (segmenter, code facts) then work on a PDF like on Markdown.
    Returns None when pdfplumber is missing or the PDF cannot be parsed."""
    if os.environ.get("VALIDATOR_PDF_LAYOUT", "on").lower() == "off":
        return None
    try:
        import pdfplumber
    except ImportError:
        return None
    from collections import Counter
    try:
        pdf = pdfplumber.open(path)
    except Exception:  # noqa: BLE001
        return None
    try:
        sizes: Counter = Counter()
        for page in pdf.pages:
            sizes.update(round(c["size"], 1) for c in page.chars if c["text"].strip())
        if not sizes:
            return [[] for _ in pdf.pages]
        body = sizes.most_common(1)[0][0]
        big = sorted({s for s in sizes if s >= body * 1.12}, reverse=True)
        level = {s: min(i + 1, 4) for i, s in enumerate(big)}
        pages: list[list[str]] = []
        for page in pdf.pages:
            items: list[tuple[float, list[str]]] = []
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]
            for t in tables:
                rows = [[" ".join(str(c or "").split()).replace("|", "\\|") for c in r]
                        for r in t.extract()]
                filled = [r for r in rows if any(r)]
                if not filled:
                    continue
                # Keep one empty row of a table that has only a header (an unfilled sign-off form).
                rows = filled + [[""] * len(filled[0])] if len(filled) == 1 and len(rows) > 1 else filled
                md = ["| " + " | ".join(rows[0]) + " |"]
                if len(rows) > 1:
                    md.append("|" + "---|" * len(rows[0]))
                md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                items.append((t.bbox[1], md))

            def outside(obj, boxes=boxes):
                x = (obj["x0"] + obj["x1"]) / 2
                y = (obj["top"] + obj["bottom"]) / 2
                return not any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)

            rest = page.filter(outside) if boxes else page
            prev_bottom, prev_h, prev_x1 = None, None, 0.0
            text_lines = rest.extract_text_lines(keep_blank_chars=False)
            left = min((l["x0"] for l in text_lines), default=0.0)
            right = max((l["x1"] for l in text_lines), default=0.0)
            for ln in text_lines:
                text = " ".join(ln["text"].split())
                chars = [c for c in ln["chars"] if c["text"].strip()]
                if _bad(text) or not chars:
                    continue
                size = Counter(round(c["size"], 1) for c in chars).most_common(1)[0][0]
                out: list[str] = []
                h = ln["bottom"] - ln["top"]
                if prev_bottom is not None and ln["top"] - prev_bottom > 0.8 * (prev_h or h):
                    out.append("")
                all_bold = all(_is_bold(c) for c in chars)
                minor_head = (all_bold and size >= body and len(text.split()) <= 8
                              and not text.rstrip().endswith((".", ",", ";", ":"))
                              and not re.match(r"^[*•\-]|^\d+$", text))
                if (size in level or minor_head) and len(text.split()) <= 16:
                    lvl = level.get(size, min(len(big) + 1, 5))
                    if out == [] and items and items[-1][1] and items[-1][1][-1] != "":
                        out.append("")
                    out += ["#" * lvl + " " + text, ""]
                else:
                    n_bold = 0
                    for c in ln["chars"]:
                        if c["text"].strip() and not _is_bold(c):
                            break
                        n_bold += 1
                    lead = "".join(c["text"] for c in ln["chars"][:n_bold]).strip()
                    if lead and lead != text and len(lead.split()) <= 6:
                        text = f"**{lead}** " + text[len(lead):].strip() if text.startswith(lead) else text
                    starts_item = re.match(r"^(\*\*|[•\-*–]\s|\d+[.)]\s|[A-Za-zА-Яа-я]{1,3}-\d+)", text)
                    last = items[-1][1] if items else None
                    wrapped = prev_x1 >= left + 0.85 * (right - left)  # previous line ran to the margin
                    if (wrapped and not out and not starts_item and last and last[-1]
                            and not last[-1].startswith(("#", "|"))):
                        last[-1] = last[-1] + " " + text  # wrapped line of the same paragraph
                        prev_bottom, prev_h, prev_x1 = ln["bottom"], h, ln["x1"]
                        continue
                    out.append(text)
                items.append((ln["top"], out))
                prev_bottom, prev_h, prev_x1 = ln["bottom"], h, ln["x1"]
            items.sort(key=lambda it: it[0])
            lines: list[str] = []
            for _, block in items:
                if block and block[0].startswith("|") and lines and lines[-1] != "":
                    lines.append("")
                lines += block
                if block and block[-1].startswith("|"):
                    lines.append("")
            while lines and not lines[-1]:
                lines.pop()
            cleaned: list[str] = []
            for t in lines:  # no double blank lines
                if t == "" and cleaned and cleaned[-1] == "":
                    continue
                cleaned.append(t)
            pages.append(cleaned)
        return pages
    except Exception:  # noqa: BLE001 — fall back to the plain text layer
        return None
    finally:
        pdf.close()


def _read_pdf(path: str) -> tuple[list[Line], list[int]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(path)
        pages = list(reader.pages)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Не удалось прочитать PDF-файл «{path}»: {e}") from e
    mode = _ocr_mode()
    lines: list[Line] = []
    ocr_pages: list[int] = []
    missing: list[int] = []  # pages that needed OCR but could not get it
    ocr_ok = True
    no = 0
    layout = _layout_pages(path) if mode != "force" else None
    if layout is not None and len(layout) != len(pages):
        layout = None
    for pno, page in enumerate(pages, 1):
        if layout is not None and len("".join(layout[pno - 1]).strip()) >= _OCR_MIN_CHARS:
            for t in layout[pno - 1]:
                no += 1
                lines.append(Line(no=no, text=t, page=pno))
            continue
        try:
            text = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Не удалось извлечь текст со страницы {pno} PDF «{path}»: {e}") from e
        if mode == "force" or (mode == "auto" and len(text.strip()) < _OCR_MIN_CHARS):
            if ocr_ok:
                try:
                    text = _ocr_page(path, pno)
                    print(f"OCR: страница {pno}", file=sys.stderr)
                    ocr_pages.append(pno)
                except _OcrUnavailable:
                    ocr_ok = False
                    missing.append(pno)
            else:
                missing.append(pno)
        elif mode == "off" and len(text.strip()) < _OCR_MIN_CHARS:
            missing.append(pno)
        page_lines = [t.rstrip() for t in text.splitlines()]
        if pno in ocr_pages:
            while page_lines and not page_lines[-1]:
                page_lines.pop()
        for t in page_lines:
            no += 1
            lines.append(Line(no=no, text=t, page=pno))
    if missing:
        msg = (
            f"Страницы {', '.join(map(str, missing))} без текстового слоя: OCR недоступен. "
            "Установите tesseract-ocr и tesseract-ocr-rus (см. README)."
        )
        if not any(ln.text.strip() for ln in lines):
            raise ValueError(msg)
        if mode != "off":
            print(msg, file=sys.stderr)
    return lines, ocr_pages


def _docx_paragraph(p) -> str:
    style = (p.style.name or "") if p.style is not None else ""
    text = p.text
    if style == "Title" or style == "Heading 1":
        return f"# {text}"
    if style.startswith("Heading ") and style[8:].isdigit():
        return "#" * int(style[8:]) + " " + text
    # Bold start of a paragraph ("Ф-1.", "200.") is kept as **...** like in Markdown.
    lead = ""
    for run in p.runs:
        if not run.text.strip() and not lead:
            continue
        if run.bold:
            lead += run.text
        else:
            break
    lead = lead.strip()
    if lead and lead != text.strip() and len(lead.split()) <= 6 and text.strip().startswith(lead):
        text = f"**{lead}** " + text.strip()[len(lead):].strip()
    if "List" in style:
        text = "* " + text
    return text


def _read_docx(path: str) -> list[Line]:
    try:
        import docx  # python-docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as e:
        raise ValueError(
            "Для чтения .docx нужен пакет python-docx (pip install python-docx). "
            "Либо сохраните документ как .md, .txt или .pdf."
        ) from e
    doc = docx.Document(path)
    lines: list[Line] = []

    def add(t: str) -> None:
        lines.append(Line(no=len(lines) + 1, text=t.rstrip()))

    # Paragraphs and tables in document order (tables are often where API contracts live).
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            for t in _docx_paragraph(Paragraph(child, doc)).split("\n"):
                add(t)
        elif tag == "tbl":
            table = Table(child, doc)
            rows = []
            for row in table.rows:
                cells, seen = [], set()
                for cell in row.cells:
                    if id(cell._tc) in seen:  # merged cell repeats
                        continue
                    seen.add(id(cell._tc))
                    cells.append(" ".join(cell.text.split()).replace("|", "\\|"))
                rows.append(cells)
            if lines and lines[-1].text:
                add("")
            for i, r in enumerate(rows):
                add("| " + " | ".join(r) + " |")
                if i == 0 and len(rows) > 1:
                    add("|" + "---|" * len(r))
            add("")
    return lines


def read_document(path: str) -> Document:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Файл не найден: {path}")
    ext = os.path.splitext(path)[1].lower()
    ocr_pages: list[int] = []
    if ext in _TEXT_EXT:
        lines = _read_text(path)
    elif ext == ".pdf":
        lines, ocr_pages = _read_pdf(path)
    elif ext == ".docx":
        lines = _read_docx(path)
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла «{ext or path}». "
            "Поддерживаются: .md, .markdown, .txt, .pdf, .docx."
        )
    document = Document(path=path, title=_title_of(lines, path), lines=lines)
    document.ocr_pages = ocr_pages
    return document
