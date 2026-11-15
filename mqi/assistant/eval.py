"""Evaluate the assistant on tests/eval/assistant_eval.yaml.

    python -m mqi.assistant.eval --backend offline
    MQI_ASSISTANT_MODEL=qwen2.5:3b python -m mqi.assistant.eval --backend llm --backend llm-tools

Scores per question: tools (expected tools called), facts (required facts present), forbidden (no false claims),
citation (cites the expected document, and only sources the tools returned), grounded (no number, lot or
citation without evidence), refusal (declines to change data / off-topic questions). A question passes when
every check that applies to it passes. Writes docs/assistant_eval.md and docs/assistant_eval.json.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path

import yaml

from .. import config
from . import agent

EVAL_FILE = config.ROOT / "tests" / "eval" / "assistant_eval.yaml"
REFUSE = re.compile(r"할 수 없|답할 수 있습니다|답변할 수 없|cannot|can only|not able|불가능합니다|권한", re.I)


def load(path: Path = EVAL_FILE) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _has(text: str, alt: str) -> bool:
    t = text.replace(",", "").lower()
    return any(a.strip().replace(",", "").lower() in t for a in alt.split("|"))


def score(item: dict, a: agent.Answer) -> dict:
    called = {c.name for c in a.tool_calls}
    text = a.text or ""
    r: dict[str, bool | None] = {}
    r["tools"] = all(t in called for t in item.get("tools", [])) if item.get("tools") else None
    r["facts"] = all(_has(text, m) for m in item.get("must", [])) if item.get("must") else None
    r["forbidden"] = not any(_has(text, m) for m in item.get("must_not", [])) if item.get("must_not") else None
    if item.get("cite"):
        want = [w.strip().upper() for w in item["cite"].split("|")]
        r["citation"] = any(c.startswith(w) for c in a.grounding.get("cited", []) for w in want)
    else:
        r["citation"] = None
    r["grounded"] = bool(a.grounding.get("grounded")) and not a.error
    if item.get("refusal"):
        r["refusal"] = bool(REFUSE.search(text))
        if item["refusal"] == "scope":
            r["refusal"] = r["refusal"] and not a.tool_calls or (r["refusal"] and
                                                                  all(c.name == "search_documents" for c in a.tool_calls))
    else:
        r["refusal"] = None
    r["pass"] = all(v for v in r.values() if v is not None)
    return r


def label(backend: str) -> str:
    import os

    if backend == "offline":
        return "offline"
    model = os.environ.get("MQI_ASSISTANT_MODEL", "qwen2.5:3b")
    emb = os.environ.get("MQI_ASSISTANT_EMBED_MODEL")
    return f"{backend}:{model}" + (f" + {emb}" if emb else "")


def run(backend: str, items: list[dict], dbpath=None, verbose: bool = True) -> dict:
    rows = []
    for it in items:
        t = time.perf_counter()
        a = agent.ask(it["q"], backend, dbpath)
        s = score(it, a)
        rows.append({"id": it["id"], "cat": it["cat"], "q": it["q"], "answer": a.text, "error": a.error,
                     "tools": [c.name for c in a.tool_calls], "grounding": a.grounding, "scores": s,
                     "latency_s": round(time.perf_counter() - t, 2)})
        if verbose:
            print(f"[{backend}] {it['id']:8s} {'PASS' if s['pass'] else 'FAIL'} {rows[-1]['latency_s']:6.1f}s "
                  f"{ {k: v for k, v in s.items() if v is False} }", flush=True)
    return {"backend": label(backend), "rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    def rate(key, sub=None):
        vals = [r["scores"][key] for r in (sub or rows) if r["scores"][key] is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    cats = sorted({r["cat"] for r in rows})
    return {
        "n": len(rows),
        "pass": rate("pass"),
        **{k: rate(k) for k in ("tools", "facts", "forbidden", "citation", "grounded", "refusal")},
        "latency_median_s": round(statistics.median(r["latency_s"] for r in rows), 2),
        "by_category": {c: rate("pass", [r for r in rows if r["cat"] == c]) for c in cats},
    }


def _pct(v):
    return "–" if v is None else f"{v * 100:.0f}%"


def report(results: list[dict], out_md: Path, out_json: Path, note: str = "") -> None:
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    keys = [("pass", "통과"), ("tools", "도구 선택"), ("facts", "사실 포함"), ("forbidden", "틀린 주장 없음"),
            ("citation", "출처 표기"), ("grounded", "근거 검사"), ("refusal", "거절 판단")]
    lines = ["# 품질 어시스턴트 평가 결과", "", note, "",
             f"문항 {results[0]['summary']['n']}개 (`tests/eval/assistant_eval.yaml`). 문항마다 해당하는 검사를 모두 통과해야 '통과'입니다.", "",
             "| 방식 | " + " | ".join(k[1] for k in keys) + " | 응답 시간(중앙값) |",
             "|---|" + "---|" * (len(keys) + 1)]
    for r in results:
        s = r["summary"]
        lines.append(f"| `{r['backend']}` | " + " | ".join(_pct(s[k]) for k, _ in keys) + f" | {s['latency_median_s']}초 |")
    cats = list(results[0]["summary"]["by_category"])
    lines += ["", "**유형별 통과율**", "", "| 방식 | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
    for r in results:
        lines.append(f"| `{r['backend']}` | " + " | ".join(_pct(r["summary"]["by_category"][c]) for c in cats) + " |")
    for r in results:
        fails = [x for x in r["rows"] if not x["scores"]["pass"]]
        if not fails:
            continue
        lines += ["", f"### `{r['backend']}` 실패 문항", ""]
        for x in fails:
            bad = ", ".join(k for k, v in x["scores"].items() if v is False and k != "pass")
            ans = (x["answer"] or "").replace("\n", " ")
            lines.append(f"- **{x['id']}** {x['q']} — 실패: {bad}  \n  답: {ans[:220]}{'…' if len(ans) > 220 else ''}")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", action="append", default=None)
    ap.add_argument("--db", default=None, help="store to evaluate on (default: a fresh demo plant)")
    ap.add_argument("--only", default=None, help="comma-separated question ids")
    ap.add_argument("--out", default=str(config.ROOT / "docs" / "assistant_eval"))
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    items = load()
    if a.only:
        keep = set(a.only.split(","))
        items = [i for i in items if i["id"] in keep]
    dbpath = a.db
    if dbpath is None:
        import tempfile

        from .. import demo

        tmp = Path(tempfile.mkdtemp())
        dbpath = tmp / "eval.sqlite"
        demo.build(tmp / "inbox", dbpath)
    results = [run(b, items, dbpath) for b in (a.backend or ["offline"])]
    report(results, Path(a.out + ".md"), Path(a.out + ".json"), a.note)
    for r in results:
        print(r["backend"], json.dumps(r["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
