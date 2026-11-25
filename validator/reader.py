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
    for pno, page in enumerate(pages, 1):
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


def _read_docx(path: str) -> list[Line]:
    try:
        import docx  # python-docx
    except ImportError as e:
        raise ValueError(
            "Для чтения .docx нужен пакет python-docx (pip install python-docx). "
            "Либо сохраните документ как .md, .txt или .pdf."
        ) from e
    doc = docx.Document(path)
    lines: list[Line] = []
    for p in doc.paragraphs:
        style = (p.style.name or "") if p.style is not None else ""
        text = p.text
        if style == "Title" or style == "Heading 1":
            text = f"# {text}"
        elif style.startswith("Heading ") and style[8:].isdigit():
            text = "#" * int(style[8:]) + " " + text
        for t in text.split("\n"):
            lines.append(Line(no=len(lines) + 1, text=t.rstrip()))
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
