"""Чтение документа с требованиями (md/txt/pdf/docx) в модель Document."""
from __future__ import annotations

import os
import re

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


def _read_pdf(path: str) -> list[Line]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(path)
        pages = list(reader.pages)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Не удалось прочитать PDF-файл «{path}»: {e}") from e
    lines: list[Line] = []
    no = 0
    for pno, page in enumerate(pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Не удалось извлечь текст со страницы {pno} PDF «{path}»: {e}") from e
        for t in text.splitlines():
            no += 1
            lines.append(Line(no=no, text=t.rstrip(), page=pno))
    return lines


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
    if ext in _TEXT_EXT:
        lines = _read_text(path)
    elif ext == ".pdf":
        lines = _read_pdf(path)
    elif ext == ".docx":
        lines = _read_docx(path)
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла «{ext or path}». "
            "Поддерживаются: .md, .markdown, .txt, .pdf, .docx."
        )
    return Document(path=path, title=_title_of(lines, path), lines=lines)
