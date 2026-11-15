"""Grounding check: every number and lot number in an answer must appear in the evidence it was given.

Evidence = the tool results and document sections the assistant actually saw. An answer that mentions a value
nobody returned is flagged, whatever backend produced it.
"""
from __future__ import annotations

import re

_LOT = re.compile(r"UF-\d{4}-\d{3}")
_SRC = re.compile(r"\[[^\]]*\]")                     # citations like [SOP-QA-021 §3] or [lot:UF-2611-003]
_DOCID = re.compile(r"\b[A-Z]{2,5}-[A-Z]{2,3}-\d{3}\b|\bUF-\d{3}\b|\bOOS-\d{4}-\d{3}\b|§\d+|\b[A-Z]\d\b")
_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[str]:
    return [m.group(0) for m in _NUM.finditer(text)]


def _value(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _norm(c: str) -> str:
    return re.sub(r"\s+", " ", c.strip().strip("[]")).upper()


def check(answer: str, evidence: str, sources: list[str] | None = None, refusal: bool = False) -> dict:
    """{'grounded', 'unsupported_numbers', 'unsupported_lots', 'invalid_citations', 'uncited'}

    sources: ids the tools actually returned. A citation of anything else is invalid (a model inventing
    "[SOP-X §9]" or citing a tool it never called). An answer with facts but no valid citation is 'uncited'.
    """
    valid = {_norm(s) for s in (sources or [])}
    ev_lots_all = set(_LOT.findall(evidence))
    cites = [_norm(p) for c in _SRC.findall(answer) for p in c.strip("[]").split(",") if p.strip()]

    def ok(c: str) -> bool:
        if c in valid:
            return True
        m = re.fullmatch(r"(LOT|HISTORY|EQUIPMENT):(UF-\d{4}-\d{3})", c)
        return bool(m) and m.group(2) in ev_lots_all     # the lot is in a returned result (e.g. a lot list)

    invalid = sorted({c for c in cites if not ok(c)}) if sources is not None else []
    upper = answer.upper()
    mentioned = any(v in upper for v in valid if len(v) >= 6)   # "SOP-QA-021 §2에 따르면" counts as a citation
    uncited = sources is not None and not refusal and not (any(ok(c) for c in cites) or mentioned)
    body = _SRC.sub(" ", answer)
    ev_lots = set(_LOT.findall(evidence))
    bad_lots = sorted({x for x in _LOT.findall(body) if x not in ev_lots})
    body = _DOCID.sub(" ", _LOT.sub(" ", body))
    ev = [v for v in (_value(x) for x in _numbers(_LOT.sub(" ", evidence))) if v is not None]
    bad = []
    for tok in _numbers(body):
        v = _value(tok)
        if v is None:
            continue
        dec = len(tok.split(".")[1]) if "." in tok else 0
        tol = 0.5 * 10 ** (-dec) + 1e-9
        if not any(abs(v - e) <= tol for e in ev):
            bad.append(tok)
    cited = sorted({c for c in cites if ok(c)} | {v for v in valid if len(v) >= 6 and v in upper})
    return {"grounded": not (bad or bad_lots or invalid or uncited), "unsupported_numbers": bad,
            "unsupported_lots": bad_lots, "invalid_citations": invalid, "uncited": uncited, "cited": cited}
