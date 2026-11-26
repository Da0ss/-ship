"""Клиенты LLM (Gemini через google-genai и OpenAI-совместимый API), протокол LLMClient."""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from typing import Any

DEFAULT_MODEL = "gemini-4-hydrogen"
# Models that most free-tier keys have go last: a key without the newer ones still works.
DEFAULT_FALLBACKS = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-2.5-flash"
DEFAULT_FAST_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FAST_FALLBACKS = "gemini-3.1-flash-lite,gemini-2.5-flash-lite"
SWITCH_AFTER = 2  # 503/404 attempts on one model before the switch to a fallback model
MAX_RETRIES = 20  # Gemini free tier is often overloaded (503): be patient rather than fail


def _max_retries() -> int:
    try:
        return max(1, int(os.environ.get("LLM_MAX_RETRIES", MAX_RETRIES)))
    except ValueError:
        return MAX_RETRIES
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


def _daily_quota(exc: Exception) -> bool:
    """429 because the per-day quota of this model is used up: waiting will not help today."""
    text = str(exc)
    return ("429" in text or "RESOURCE_EXHAUSTED" in text) and bool(
        re.search(r"per[_ ]?day|PerDay|requests_per_day|RPD|daily", text, re.I))


class GeminiClient:
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 fallbacks: str | None = None, min_interval: float | None = None):
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "Не найден ключ Gemini API. Задайте переменную окружения GEMINI_API_KEY "
                "(или GOOGLE_API_KEY), например через файл .env. Подробности см. в README."
            )
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        if fallbacks is None:
            fallbacks = os.environ.get("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACKS)
        self.fallbacks = [m.strip() for m in fallbacks.split(",") if m.strip() and m.strip() != self.model]
        self.used_models: list[str] = []
        try:
            self.min_interval = (min_interval if min_interval is not None
                                 else float(os.environ.get("GEMINI_MIN_INTERVAL", "13")))
        except ValueError:
            self.min_interval = 13.0
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
        for attempt in range(_max_retries() + 1):
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
                if _daily_quota(e) and self.fallbacks:
                    old, self.model = self.model, self.fallbacks.pop(0)
                    failures_on_model = 0
                    _log(f"[gemini] дневная квота {old} исчерпана; переключение на {self.model}")
                    continue
                overloaded = code in (404, 503) or "UNAVAILABLE" in str(e) or "NOT_FOUND" in str(e)
                if overloaded:
                    failures_on_model += 1
                    if (failures_on_model >= SWITCH_AFTER or code == 404) and self.fallbacks:
                        old, self.model = self.model, self.fallbacks.pop(0)
                        if code != 404 and "NOT_FOUND" not in str(e):
                            # Overload is temporary: keep the model in the ring and come back later.
                            self.fallbacks.append(old)
                        failures_on_model = 0
                        _log(f"[gemini] модель {old} перегружена или недоступна ({code}); "
                             f"переключение на {self.model}")
                        time.sleep(min(delay, BACKOFF_CAP) / 2)
                        delay = min(delay * 1.5, BACKOFF_CAP)
                        continue
                if not (self._is_retryable(e) or overloaded) or attempt >= _max_retries():
                    raise
                wait = self._retry_delay(e)
                wait = min(max(wait + 1, 1), 300) if wait is not None else delay
                wait += random.uniform(0, 0.5)
                _log(f"[gemini] ошибка запроса ({type(e).__name__}: {str(e)[:120]}); "
                     f"повтор {attempt + 1}/{_max_retries()} через {wait:.0f} с")
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


# --- OpenAI-совместимый провайдер ---
def _extract_json_block(text: str) -> Any:
    """Parse text as JSON; fall back to the first {...} or [...] block inside it."""
    try:
        return _parse_json(text)
    except ValueError:
        pass
    t = _strip_fences(text or "")
    decoder = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                return decoder.raw_decode(t[i:])[0]
            except json.JSONDecodeError:
                continue
    raise ValueError("в ответе нет JSON")


def _env_float(names: list[str], default: float) -> float:
    for n in names:
        v = os.environ.get(n)
        if v:
            try:
                return float(v)
            except ValueError:
                return default
    return default


class OpenAICompatClient:
    """Any OpenAI-compatible Chat Completions API (OpenAI, OpenRouter, vLLM...)."""

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None):
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Не найден ключ OpenAI-совместимого API. Задайте переменную окружения "
                "OPENAI_API_KEY, например через файл .env. Подробности см. в README."
            )
        self.model = model or os.environ.get("OPENAI_MODEL") or ""
        if not self.model:
            raise RuntimeError(
                "Не задана модель. Задайте переменную окружения OPENAI_MODEL или параметр --model."
            )
        fallbacks = os.environ.get("OPENAI_FALLBACK_MODELS", "")
        self.fallbacks = [m.strip() for m in fallbacks.split(",") if m.strip() and m.strip() != self.model]
        self.used_models: list[str] = []
        self.min_interval = _env_float(["LLM_MIN_INTERVAL", "GEMINI_MIN_INTERVAL"], 4.0)
        self.timeout_s = _env_float(["LLM_TIMEOUT", "GEMINI_TIMEOUT"], 180.0)
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        import openai

        self._client = openai.OpenAI(api_key=key, base_url=self.base_url,
                                     timeout=self.timeout_s, max_retries=0)
        self._json_mode = True
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _status(exc: Exception) -> int | None:
        code = getattr(exc, "status_code", None)
        return code if isinstance(code, int) else None

    @classmethod
    def _is_retryable(cls, exc: Exception) -> bool:
        code = cls._status(exc)
        if code is not None:
            return code == 429 or code >= 500
        names = {c.__name__ for c in type(exc).__mro__}
        return bool(names & {"APITimeoutError", "APIConnectionError", "TimeoutException",
                             "TransportError", "ConnectionError", "TimeoutError", "OSError"})

    def _call(self, system: str, prompt: str, schema: dict[str, Any] | None) -> str | None:
        if schema is not None:
            system = (system + "\n\nВерни только JSON без пояснений и без markdown. "
                      "JSON должен соответствовать схеме:\n" + json.dumps(schema, ensure_ascii=False))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        delay = BACKOFF_START
        unavailable = 0
        for attempt in range(_max_retries() + 1):
            self._throttle()
            kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.1}
            if self._json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                started = time.monotonic()
                resp = self._client.chat.completions.create(**kwargs)
                self._last_call = time.monotonic()
                if self.model not in self.used_models:
                    self.used_models.append(self.model)
                text = resp.choices[0].message.content if resp.choices else None
                _log(f"[llm] {self.model}: ответ за {time.monotonic() - started:.0f} с, "
                     f"{len(text or '')} символов")
                return text
            except Exception as e:  # noqa: BLE001
                self._last_call = time.monotonic()
                code = self._status(e)
                msg = str(e)
                low = msg.lower()
                if code == 400 and self._json_mode and any(
                        k in low for k in ("response_format", "json_object", "not supported")):
                    self._json_mode = False
                    _log("[llm] провайдер не поддерживает response_format; повтор без него")
                    continue
                model_problem = code == 404 or (code == 403 and any(
                    k in low for k in ("model", "verified", "not found")))
                if code == 429 and _daily_quota(e) and self.fallbacks:
                    old, self.model = self.model, self.fallbacks.pop(0)
                    _log(f"[llm] дневная квота {old} исчерпана; переключение на {self.model}")
                    continue
                if code == 503:
                    unavailable += 1
                if (model_problem or unavailable >= SWITCH_AFTER) and self.fallbacks:
                    old, self.model = self.model, self.fallbacks.pop(0)
                    unavailable = 0
                    _log(f"[llm] модель {old} недоступна ({code}); переключение на {self.model}")
                    continue
                if not self._is_retryable(e) or attempt >= _max_retries():
                    raise
                wait = delay + random.uniform(0, 0.5)
                _log(f"[llm] ошибка запроса ({type(e).__name__}: {msg[:120]}); "
                     f"повтор {attempt + 1}/{_max_retries()} через {wait:.0f} с")
                time.sleep(wait)
                delay = min(delay * 2, BACKOFF_CAP)
        return None  # недостижимо

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any] | None = None) -> Any:
        text = self._call(system, prompt, schema)
        try:
            return _extract_json_block(text)
        except ValueError:
            pass
        _log("[llm] ответ не является корректным JSON; повторный запрос к модели")
        reprompt = (prompt + "\n\nПредыдущий ответ не удалось разобрать как JSON. "
                    "Верни ТОЛЬКО корректный JSON без пояснений и без markdown-ограждений.")
        text = self._call(system, reprompt, schema)
        try:
            return _extract_json_block(text)
        except ValueError as e:
            raise RuntimeError(f"Модель дважды вернула некорректный JSON: {e}") from e


def make_fast_client():
    """Optional second client for light stages (classification, verdict, checklist).
    Set VALIDATOR_FAST_MODEL (and VALIDATOR_FAST_FALLBACKS) to save the quota of the strong model.
    Returns None when not configured."""
    provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    raw = os.environ.get("VALIDATOR_FAST_MODEL")
    if raw is None and provider == "gemini":
        raw = DEFAULT_FAST_MODEL  # not set at all: use the light Gemini model by default
    fast = (raw or "").strip()
    if not fast:
        return None  # set but empty: all stages on the strong model
    fb = os.environ.get("VALIDATOR_FAST_FALLBACKS",
                        DEFAULT_FAST_FALLBACKS if provider == "gemini" else "")
    try:
        interval = float(os.environ.get("VALIDATOR_FAST_MIN_INTERVAL", "4.5"))
    except ValueError:
        interval = 4.5
    if provider == "openai":
        c = OpenAICompatClient(model=fast)
        c.fallbacks = [m.strip() for m in fb.split(",") if m.strip() and m.strip() != fast]
        return c
    return GeminiClient(model=fast, fallbacks=fb, min_interval=interval)


def list_gemini_models() -> list[tuple[str, str]]:
    """(model id, display name) of Gemini models that can generate text with this key."""
    from google import genai
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=key)
    out = []
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = (m.name or "").removeprefix("models/")
        out.append((name, getattr(m, "display_name", "") or ""))
    return sorted(out)


def make_client(model: str | None = None):
    provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    if provider == "openai":
        return OpenAICompatClient(model=model)
    if provider == "gemini":
        return GeminiClient(model=model)
    raise RuntimeError(f"Неизвестный провайдер LLM: {provider}. Допустимо: gemini, openai.")
