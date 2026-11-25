from types import SimpleNamespace

import pytest

from validator import llm


class ApiErr(Exception):
    def __init__(self, status, msg):
        super().__init__(f"Error code: {status} - {msg}")
        self.status_code = status


def make(monkeypatch, script, model="A", fallbacks=""):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_FALLBACK_MODELS", fallbacks)
    monkeypatch.setenv("LLM_MIN_INTERVAL", "0")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c = llm.OpenAICompatClient(model=model)
    calls = []

    def create(**kw):
        calls.append((kw["model"], "response_format" in kw))
        item = script[kw["model"]].pop(0)
        if isinstance(item, Exception):
            raise item
        msg = SimpleNamespace(content=item)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    c._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return c, calls


def test_json_with_fence_and_extra_words(monkeypatch):
    c, _ = make(monkeypatch, {"A": ['Вот ответ:\n```json\n{"a": 1}\n```']})
    assert c.generate_json("s", "p", {"type": "object"}) == {"a": 1}
    assert c.used_models == ["A"]


def test_schema_goes_to_system_message(monkeypatch):
    c, _ = make(monkeypatch, {"A": ['{"x": 1}']})
    seen = {}
    orig = c._client.chat.completions.create

    def spy(**kw):
        seen.update(kw)
        return orig(**kw)

    c._client.chat.completions.create = spy
    c.generate_json("SYS", "USER", {"type": "object", "properties": {"x": {"type": "integer"}}})
    system = seen["messages"][0]["content"]
    assert seen["messages"][0]["role"] == "system" and system.startswith("SYS")
    assert '"properties"' in system and seen["messages"][1]["content"] == "USER"


def test_response_format_unsupported_fallback(monkeypatch):
    c, calls = make(monkeypatch, {"A": [ApiErr(400, "response_format json_object is not supported"),
                                        '{"b": 2}', '{"c": 3}']})
    assert c.generate_json("s", "p") == {"b": 2}
    assert c.generate_json("s", "p") == {"c": 3}
    assert calls == [("A", True), ("A", False), ("A", False)]  # remembered


def test_unverified_model_switches_to_fallback(monkeypatch):
    c, calls = make(monkeypatch, {"A": [ApiErr(403, "This model requires a verified account.")],
                                  "B": ['{"ok": 1}']}, fallbacks="B")
    assert c.generate_json("s", "p") == {"ok": 1}
    assert [m for m, _ in calls] == ["A", "B"] and c.used_models == ["B"] and c.model == "B"


def test_unverified_without_fallback_raises(monkeypatch):
    c, _ = make(monkeypatch, {"A": [ApiErr(403, "This model requires a verified account.")]})
    with pytest.raises(Exception, match="verified"):
        c.generate_json("s", "p")


def test_rate_limit_retried(monkeypatch):
    c, calls = make(monkeypatch, {"A": [ApiErr(429, "rate limit"), ApiErr(500, "oops"), '[1]']})
    assert c.generate_json("s", "p") == [1]
    assert len(calls) == 3


def test_bad_request_not_retried(monkeypatch):
    c, calls = make(monkeypatch, {"A": [ApiErr(400, "invalid messages")]})
    with pytest.raises(ApiErr):
        c.generate_json("s", "p")
    assert len(calls) == 1


def test_bad_json_reask_then_error(monkeypatch):
    c, calls = make(monkeypatch, {"A": ["не json", "опять не json"]})
    with pytest.raises(RuntimeError):
        c.generate_json("s", "p")
    assert len(calls) == 2


def test_make_client_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("OPENAI_MODEL", "m-x")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert isinstance(llm.make_client(), llm.OpenAICompatClient)
    assert llm.make_client().model == "m-x"
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    assert isinstance(llm.make_client(), llm.GeminiClient)
    monkeypatch.setenv("LLM_PROVIDER", "other")
    with pytest.raises(RuntimeError):
        llm.make_client()


def test_cli_openai_without_key(monkeypatch, tmp_path, capsys):
    from tests.conftest import DOC
    from validator import cli
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = tmp_path / "e.env"
    env.write_text("")
    assert cli.main([str(DOC), str(tmp_path / "r.md"), "--provider", "openai",
                     "--env-file", str(env)]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
