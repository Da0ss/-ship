"""Disk cache of LLM answers: a repeated run with the same input spends no free-tier quota."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_DIR = ".cache/llm"


class CachingClient:
    """Wrap an LLMClient. The key is a hash of model + system + prompt + schema."""

    def __init__(self, inner: Any, directory: str | None = None):
        self.inner = inner
        self.dir = Path(directory or os.environ.get("VALIDATOR_CACHE_DIR") or DEFAULT_DIR)
        self.hits = 0
        self.misses = 0
        self._cached_models: list[str] = []
        self._key_model = inner.model  # requested model; a fallback switch keeps the key stable

    # The pipeline and the CLI read these attributes from the client.
    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def used_models(self) -> list[str]:
        used = list(getattr(self.inner, "used_models", []) or [])
        return used + [m for m in self._cached_models if m not in used]

    def _key(self, system: str, prompt: str, schema: dict | None) -> str:
        blob = json.dumps([self._key_model, system, prompt, schema], ensure_ascii=False,
                          sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def generate_json(self, system: str, prompt: str, schema: dict | None = None) -> Any:
        path = self.dir / f"{self._key(system, prompt, schema)}.json"
        if path.is_file():
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                if entry.get("model") and entry["model"] not in self._cached_models:
                    self._cached_models.append(entry["model"])
                return entry["answer"]
            except (OSError, ValueError, KeyError):
                pass  # a broken entry is ignored and rewritten
        answer = self.inner.generate_json(system, prompt, schema)
        self.misses += 1
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"model": self.inner.model, "answer": answer},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            print(f"[cache] не удалось записать кэш: {exc}", file=sys.stderr)
        return answer
