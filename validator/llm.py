"""Клиент Gemini (google-genai), реализующий протокол LLMClient."""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from typing import Any

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_FALLBACKS = "gemini-3.5-flash,gemini-3.8-flash"
SWITCH_AFTER = 2  # 503/404 attempts on one model before the switch to a fallback model
MAX_RETRIES = 6
BACKOFF_START = 5.0
BACKOFF_CAP = 60.0


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _strip_fences(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```[a-zA-Z0-9]*\s*\n?(.*?)\n?```\s*$", t, re.DOTALL)
    if m:
        return m.group(1).strip()
    return t


def _parse_json(text: str | None) -> Any:
    if text is None:
        raise ValueError("пустой ответ")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_strip_fences(text))


class GeminiClient:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "Не найден ключ Gemini API. Задайте переменную окружения GEMINI_API_KEY "
                "(или GOOGLE_API_KEY), например через файл .env. Подробности см. в README."
            )
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        fallbacks = os.environ.get("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACKS)
        self.fallbacks = [m.strip() for m in fallbacks.split(",") if m.strip() and m.strip() != self.model]
        self.used_models: list[str] = []
        try:
            self.min_interval = float(os.environ.get("GEMINI_MIN_INTERVAL", "4"))
        except ValueError:
            self.min_interval = 4.0
        from google import genai
        from google.genai import types

        self._types = types
        try:
            self.timeout_s = float(os.environ.get("GEMINI_TIMEOUT", "180"))
        except ValueError:
            self.timeout_s = 180.0
        # Without a timeout one hung request blocks the whole run.
        self._client = genai.Client(
            api_key=key, http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)))
        self._last_call = 0.0

    # --- внутренние помощники ---
    def _throttle(self) -> None:
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            return code == 429 or code >= 500
        status = str(getattr(exc, "status", "") or "")
        text = f"{status} {exc}"
        if "RESOURCE_EXHAUSTED" in text or "UNAVAILABLE" in text:
            return True
        # сетевые ошибки (httpx / сокеты / таймауты)
        names = {c.__name__ for c in type(exc).__mro__}
        if names & {"TransportError", "TimeoutException", "ConnectionError", "TimeoutError", "OSError"}:
            return True
        return False

    @staticmethod
    def _retry_delay(exc: Exception) -> float | None:
        text = str(exc)
        m = re.search(r"retry in ([0-9.]+)\s*s", text, re.I) or \
            re.search(r"retryDelay['\"]?\s*:\s*['\"]?([0-9.]+)s", text) or \
            re.search(r"retry[_ ]delay[^0-9]{0,10}([0-9.]+)", text, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        return None

    def _call(self, system: str, prompt: str, schema: dict[str, Any] | None) -> str | None:
        t = self._types
        cfg: dict[str, Any] = {
            "system_instruction": system,
            "temperature": 0.1,
            "response_mime_type": "application/json",
        }
        if schema is not None:
            cfg["response_schema"] = schema
        config = t.GenerateContentConfig(**cfg)
        delay = BACKOFF_START
        failures_on_model = 0
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            try:
                started = time.monotonic()
                resp = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=config)
                self._last_call = time.monotonic()
                if self.model not in self.used_models:
                    self.used_models.append(self.model)
                text = resp.text
                _log(f"[gemini] {self.model}: ответ за {time.monotonic() - started:.0f} с, "
                     f"{len(text or '')} символов")
                return text
            except Exception as e:  # noqa: BLE001
                self._last_call = time.monotonic()
                code = getattr(e, "code", None)
                overloaded = code in (404, 503) or "UNAVAILABLE" in str(e) or "NOT_FOUND" in str(e)
                if overloaded:
                    failures_on_model += 1
                    if (failures_on_model >= SWITCH_AFTER or code == 404) and self.fallbacks:
                        old, self.model = self.model, self.fallbacks.pop(0)
                        failures_on_model = 0
                        _log(f"[gemini] модель {old} недоступна ({code}); переключение на {self.model}")
                        continue
                if not (self._is_retryable(e) or overloaded) or attempt >= MAX_RETRIES:
                    raise
                wait = self._retry_delay(e)
                wait = min(max(wait + 1, 1), 300) if wait is not None else delay
                wait += random.uniform(0, 0.5)
                _log(f"[gemini] ошибка запроса ({type(e).__name__}: {str(e)[:120]}); "
                     f"повтор {attempt + 1}/{MAX_RETRIES} через {wait:.0f} с")
                time.sleep(wait)
                delay = min(delay * 2, BACKOFF_CAP)
        return None  # недостижимо

    # --- публичный API ---
    def generate_json(self, system: str, prompt: str, schema: dict[str, Any] | None = None) -> Any:
        text = self._call(system, prompt, schema)
        try:
            return _parse_json(text)
        except ValueError:
            pass
        _log("[gemini] ответ не является корректным JSON; повторный запрос к модели")
        reprompt = (prompt + "\n\nПредыдущий ответ не удалось разобрать как JSON. "
                    "Верни ТОЛЬКО корректный JSON без пояснений и без markdown-ограждений.")
        text = self._call(system, reprompt, schema)
        try:
            return _parse_json(text)
        except ValueError as e:
            raise RuntimeError(f"Модель дважды вернула некорректный JSON: {e}") from e
