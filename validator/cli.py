"""Command line interface of the requirements validator."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .models import Passport
from .report import render


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _agent_version() -> str:
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(["git", "describe", "--tags", "--long", "--always", "--dirty"], cwd=here,
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from . import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m validator",
                                description="Проверка документа требований по ISO/IEC/IEEE 29148 и IEEE 830.")
    p.add_argument("input", nargs="?", help="путь к файлу с требованиями")
    p.add_argument("output", nargs="?", help="путь к итоговому отчёту .md")
    p.add_argument("--list-models", action="store_true",
                   help="показать модели Gemini, доступные этому ключу, и выйти")
    p.add_argument("--model", default=None, help="модель (переопределяет GEMINI_MODEL / OPENAI_MODEL)")
    p.add_argument("--provider", choices=["gemini", "openai"], default=None,
                   help="провайдер LLM (переопределяет LLM_PROVIDER; по умолчанию gemini)")
    p.add_argument("--env-file", default=".env", help="файл с переменными окружения (по умолчанию .env)")
    p.add_argument("--no-cache", action="store_true",
                   help="не использовать кэш ответов модели (.cache/llm)")
    p.add_argument("--json", dest="json_path", default=None, metavar="PATH",
                   help="дополнительно записать отчёт в машиночитаемом JSON")
    return p


def main(argv: list[str] | None = None, client=None, fast_client=None) -> int:
    args = _parser().parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    except ImportError:
        pass

    if args.list_models:
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            _log("Ошибка: не задан GEMINI_API_KEY (см. README).")
            return 2
        from .llm import list_gemini_models
        for mid, name in list_gemini_models():
            print(f"{mid:40} {name}")
        return 0
    if not args.input or not args.output:
        _parser().print_usage(sys.stderr)
        _log("Ошибка: укажите путь к файлу требований и путь к отчёту.")
        return 2
    if not os.path.isfile(args.input):
        _log(f"Ошибка: файл не найден: {args.input}")
        return 2

    if client is None:
        if args.provider:
            os.environ["LLM_PROVIDER"] = args.provider
        provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        if provider == "openai":
            if not os.environ.get("OPENAI_API_KEY"):
                _log("Ошибка: не задан OPENAI_API_KEY. Впишите ключ в .env или задайте переменную "
                     "окружения (см. README, раздел про OpenAI-совместимый провайдер).")
                return 2
        elif not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            _log("Ошибка: не задан GEMINI_API_KEY. Скопируйте .env.example в .env и впишите ключ "
                 "(https://aistudio.google.com/apikey) или задайте переменную окружения.")
            return 2
        from .llm import make_client
        try:
            client = make_client(model=args.model)
        except RuntimeError as exc:
            _log(f"Ошибка: {exc}")
            return 2
        from .llm import make_fast_client
        try:
            fast_client = make_fast_client()
        except RuntimeError as exc:
            _log(f"Ошибка: {exc}")
            return 2
        if not args.no_cache:
            from .cache import CachingClient
            client = CachingClient(client)
            if fast_client is not None:
                fast_client = CachingClient(fast_client)

    from .reader import read_document
    from .pipeline import run

    try:
        _log(f"Чтение документа: {args.input}")
        document = read_document(args.input)
        _log(f"Прочитано строк: {len(document.lines)}. Модель: {client.model}. Проверка требований…")
        passport = Passport(
            document=args.input,
            document_title=document.title,
            date=datetime.now().astimezone().isoformat(timespec="seconds"),
            model=client.model,
            agent_version=_agent_version(),
            ocr_pages=list(getattr(document, "ocr_pages", []) or []),
        )
        report = run(document, client, passport, fast_client=fast_client)
        used = getattr(client, "used_models", None)
        if used:  # the model that really answered (fallbacks included)
            report.passport.model = ", ".join(used)
        fast_used = getattr(fast_client, "used_models", None) if fast_client else None
        if fast_used:
            report.passport.model += f"; лёгкие этапы: {', '.join(fast_used)}"
    except (FileNotFoundError, ValueError) as exc:
        _log(f"Ошибка: {exc}")
        return 2
    except Exception as exc:  # LLM/API failure: short message instead of a traceback
        _log(f"Ошибка при обращении к модели: {type(exc).__name__}: {exc}")
        return 1

    _log("Формирование отчёта…")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(report), encoding="utf-8")
    if args.json_path:
        from .export import write_json
        write_json(report, args.json_path)
        _log(f"JSON: {args.json_path}")
    _log(f"Готово. Отчёт: {out_path}")
    return 0
