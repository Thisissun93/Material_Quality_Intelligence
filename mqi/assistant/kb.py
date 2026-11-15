"""Plant documents for retrieval: kb/*.md split into sections, plus the product specification from specs.yaml.

Search is BM25 over words and Korean character bigrams (Korean attaches particles to words, so "재시험을",
"재시험은" and "재시험" should still meet). If an embedding model is configured, dense similarity is fused
with BM25 by reciprocal rank (hybrid search).
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from .. import config

KB_DIR = config.ROOT / "kb"
_HANGUL = re.compile(r"[가-힣]+")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_/%]*|[가-힣]+")


@dataclass(frozen=True)
class Chunk:
    id: str          # e.g. "SOP-QA-021 §3"
    doc: str         # e.g. "SOP-QA-021"
    title: str
    text: str


def tokenize(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        out.append(w)
        if _HANGUL.fullmatch(w) and len(w) > 2:
            out += [w[i:i + 2] for i in range(len(w) - 1)]
    return out


def _spec_chunks() -> list[Chunk]:
    from ..insight.rules import lab

    s = config.specs()["product"]
    lines = []
    for p, v in s["properties"].items():
        lines.append(f"{lab(p, 'ko')}({lab(p, 'en')}, {p}) 규격 {v['lsl']:,}~{v['usl']:,} {v['unit']}, "
                     f"목표 {v['target']:,} {v['unit']}")
    return [Chunk("SPEC-UF-300", "SPEC-UF-300", f"제품 규격서 {s['id']} ({s['name']})",
                  f"제품 {s['id']} {s['name']} 출하 규격. " + " / ".join(lines))]


def _split(path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"#\s*\[([^\]]+)\]\s*(.+)", text)
    if not m:
        return []
    doc, title = m.group(1).strip(), m.group(2).strip()
    parts = re.split(r"^##\s+", text, flags=re.M)[1:]
    out = []
    for part in parts:
        head, _, body = part.partition("\n")
        num = re.match(r"(\d+)\.", head.strip())
        sec = f"§{num.group(1)}" if num else head.strip()
        out.append(Chunk(f"{doc} {sec}", doc, f"{title} — {head.strip()}", " ".join(body.split())))
    return out


@lru_cache(maxsize=1)
def chunks() -> tuple[Chunk, ...]:
    out = []
    for f in sorted(KB_DIR.glob("*.md")):
        out += _split(f)
    return tuple(out + _spec_chunks())


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.tf = [Counter(d) for d in docs]
        self.avg = sum(map(len, docs)) / max(len(docs), 1)
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, q: list[str]) -> list[float]:
        out = []
        for tf, d in zip(self.tf, self.docs):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return out


@lru_cache(maxsize=1)
def _bm25() -> BM25:
    return BM25([tokenize(f"{c.doc} {c.title} {c.text}") for c in chunks()])


# ---------------------------------------------------------------- optional dense retrieval

def _embed_model() -> str | None:
    return os.environ.get("MQI_ASSISTANT_EMBED_MODEL") or None


def _embed(texts: list[str]) -> list[list[float]]:
    import httpx

    base = os.environ.get("MQI_ASSISTANT_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    key = os.environ.get("MQI_ASSISTANT_API_KEY", "none")
    r = httpx.post(f"{base}/embeddings", json={"model": _embed_model(), "input": texts},
                   headers={"Authorization": f"Bearer {key}"}, timeout=120)
    r.raise_for_status()
    return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]


@lru_cache(maxsize=1)
def _chunk_vectors() -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(v) for v in _embed([f"{c.title}\n{c.text}" for c in chunks()]))


def _cos(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb or 1)


def search(query: str, k: int = 4) -> list[tuple[Chunk, float]]:
    """Top-k sections. BM25 alone, or BM25 + embeddings fused by reciprocal rank when configured."""
    cs = chunks()
    bm = _bm25().scores(tokenize(query))
    rank_bm = sorted(range(len(cs)), key=lambda i: -bm[i])
    if _embed_model():
        try:
            qv = _embed([query])[0]
            dense = [_cos(qv, v) for v in _chunk_vectors()]
            rank_d = sorted(range(len(cs)), key=lambda i: -dense[i])
            rrf = {i: 0.0 for i in range(len(cs))}
            for r, i in enumerate(rank_bm):
                rrf[i] += 1 / (60 + r)
            for r, i in enumerate(rank_d):
                rrf[i] += 1 / (60 + r)
            order = sorted(rrf, key=lambda i: -rrf[i])
            return [(cs[i], round(rrf[i], 4)) for i in order[:k] if bm[i] > 0 or dense[i] > 0.3]
        except Exception:  # noqa: BLE001 - embedding service down: fall back to BM25
            pass
    return [(cs[i], round(bm[i], 3)) for i in rank_bm[:k] if bm[i] > 0]
