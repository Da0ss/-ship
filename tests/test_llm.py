from types import SimpleNamespace

import pytest

from validator import llm


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code} {msg}")
        self.code = code


def make_client(monkeypatch, script):
    """GeminiClient with a fake SDK: script maps model -> list of results/exceptions."""
    monkeypatch.setenv("GEMINI_MIN_INTERVAL", "0")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c = llm.GeminiClient(model="m1", api_key="dummy")
    calls = []

    def generate_content(model, contents, config):
        calls.append(model)
        item = script[model].pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item)

    c._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return c, calls


def test_switch_to_fallback_on_503(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m2")
    c, calls = make_client(monkeypatch, {"m1": [ApiError(503, "UNAVAILABLE")] * 2,
                                         "m2": ['{"a": 1}']})
    assert c.generate_json("s", "p") == {"a": 1}
    assert calls == ["m1", "m1", "m2"] and c.used_models == ["m2"] and c.model == "m2"


def test_switch_immediately_on_404(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m2")
    c, calls = make_client(monkeypatch, {"m1": [ApiError(404, "NOT_FOUND")], "m2": ["[1]"]})
    assert c.generate_json("s", "p") == [1]
    assert calls == ["m1", "m2"]


def test_retry_429_then_ok_and_fenced_json(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    c, calls = make_client(monkeypatch, {"m1": [ApiError(429, "RESOURCE_EXHAUSTED retry in 3s"),
                                                '```json\n{"ok": true}\n```']})
    assert c.generate_json("s", "p") == {"ok": True}
    assert calls == ["m1", "m1"]


def test_non_retryable_error_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    c, _ = make_client(monkeypatch, {"m1": [ApiError(400, "INVALID_ARGUMENT")]})
    with pytest.raises(ApiError):
        c.generate_json("s", "p")


def test_bad_json_reasked_once(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    c, calls = make_client(monkeypatch, {"m1": ["not json", '{"x": 2}']})
    assert c.generate_json("s", "p") == {"x": 2}
    assert len(calls) == 2


def test_missing_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm.GeminiClient()


def test_timeout_is_passed_to_sdk(monkeypatch):
    monkeypatch.setenv("GEMINI_TIMEOUT", "42")
    c = llm.GeminiClient(model="m1", api_key="dummy")
    assert c.timeout_s == 42.0
    assert c._client._api_client._http_options.timeout == 42000


def test_timeout_error_is_retried(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    c, calls = make_client(monkeypatch, {"m1": [TimeoutError("read timeout"), '{"ok": 1}']})
    assert c.generate_json("s", "p") == {"ok": 1}
    assert calls == ["m1", "m1"]


def test_daily_quota_switches_without_waiting(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m2")
    err = ApiError(429, "RESOURCE_EXHAUSTED quota_id: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    c, calls = make_client(monkeypatch, {"m1": [err], "m2": ['{"ok": 1}']})
    assert c.generate_json("s", "p") == {"ok": 1}
    assert calls == ["m1", "m2"]


def test_per_minute_429_is_not_daily():
    assert not llm._daily_quota(ApiError(429, "RESOURCE_EXHAUSTED PerMinute retry in 3s"))
    assert llm._daily_quota(ApiError(429, "RESOURCE_EXHAUSTED requests per day"))


def test_overload_rotates_back_to_first_model(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m2")
    busy = ApiError(503, "UNAVAILABLE high demand")
    c, calls = make_client(monkeypatch, {"m1": [busy, busy, '{"ok": 1}'], "m2": [busy, busy]})
    assert c.generate_json("s", "p") == {"ok": 1}
    assert calls == ["m1", "m1", "m2", "m2", "m1"]
