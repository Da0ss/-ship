from validator.cache import CachingClient


class Counter:
    model = "m1"

    def __init__(self):
        self.n = 0
        self.used_models = ["m1"]

    def generate_json(self, system, prompt, schema=None):
        self.n += 1
        return {"n": self.n, "prompt": prompt}


def test_cache_hit_and_miss(tmp_path):
    inner = Counter()
    c = CachingClient(inner, str(tmp_path))
    a = c.generate_json("s", "p1", {"type": "object"})
    b = c.generate_json("s", "p1", {"type": "object"})
    d = c.generate_json("s", "p2", {"type": "object"})
    assert a == b == {"n": 1, "prompt": "p1"} and d["n"] == 2
    assert (c.hits, c.misses, inner.n) == (1, 2, 2)
    # New process, same directory: answers come from disk, the model is not called.
    inner2 = Counter()
    c2 = CachingClient(inner2, str(tmp_path))
    assert c2.generate_json("s", "p1", {"type": "object"})["n"] == 1
    assert inner2.n == 0 and c2.model == "m1"


def test_cache_key_depends_on_model_and_schema(tmp_path):
    inner = Counter()
    c = CachingClient(inner, str(tmp_path))
    c.generate_json("s", "p", None)
    c.generate_json("s", "p", {"type": "array"})
    inner.model = "m2"  # fallback switch inside the client: same key
    c.generate_json("s", "p", None)
    assert inner.n == 2
    other = Counter()
    other.model = "m2"  # another requested model: another key
    CachingClient(other, str(tmp_path)).generate_json("s", "p", None)
    assert other.n == 1


def test_broken_entry_is_rewritten(tmp_path):
    inner = Counter()
    c = CachingClient(inner, str(tmp_path))
    c.generate_json("s", "p")
    for f in tmp_path.glob("*.json"):
        f.write_text("{broken")
    assert c.generate_json("s", "p")["n"] == 2


def test_used_models_from_cache(tmp_path):
    inner = Counter()
    CachingClient(inner, str(tmp_path)).generate_json("s", "p")
    inner2 = Counter()
    inner2.used_models = []
    c2 = CachingClient(inner2, str(tmp_path))
    c2.generate_json("s", "p")
    assert c2.used_models == ["m1"]
