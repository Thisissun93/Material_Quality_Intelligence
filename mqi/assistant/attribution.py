"""Post-hoc attribution: the system, not the model, decides which source supports each sentence.

Small on-site models write correct sentences but do not cite reliably (they skip brackets, merge ids, or copy
an id they were never given). So after the model answers:

  1. citations to sources that were not looked up are removed (and recorded),
  2. every sentence without a valid citation is matched against what was actually looked up (each tool result,
     each document section) by shared words and numbers, and the best-supported source is attached,
  3. sentences that match nothing stay uncited, so the grounding check and the UI show them as unsupported.
"""
from __future__ import annotations

import re

from . import kb

_SENT = re.compile(r"(?<=[.!?。])\s+(?=[^\s\[])|(?<=[다요])\s+(?=[^\s\[.])|\n+")
_CIT = re.compile(r"\s*\[([^\]]+)\]")
_NUMTOK = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _units(calls) -> list[tuple[str, str]]:
    """(source id, text) for every piece of evidence."""
    import json

    out = []
    for c in calls:
        r = c.result if isinstance(c.result, dict) else {}
        if "error" in r:
            continue
        if c.name == "search_documents":
            out += [(s["source"], f"{s['title']} {s['text']}") for s in r.get("sections", [])]
        else:
            out.append((c.source, json.dumps(r, ensure_ascii=False)))
    return out


_STRONG = re.compile(r"UF-\d{4}-\d{3}|[A-Za-z][A-Za-z0-9\-]{1,}|\d+(?:[.,]\d+)*")
_STOP = {"the", "and", "for", "with", "this", "that", "lot", "qa", "is", "are", "was", "of", "to", "in", "on", "it"}


def _strong(text: str) -> set[str]:
    """Names, ids and numbers: one exact match of these is better evidence than many common words."""
    out = set()
    for m in _STRONG.findall(text):
        t = m.lower().replace(",", "")
        if t not in _STOP and (len(t) >= 2 or t.isdigit()):
            out.add(t)
    return out


def _toks(text: str) -> set[str]:
    t = set(kb.tokenize(text))
    t |= {n.replace(",", "") for n in _NUMTOK.findall(text)}
    return {x for x in t if len(x) > 1 or x.isdigit()}


def attribute(answer: str, calls, min_overlap: float = 0.25) -> tuple[str, dict]:
    units = _units(calls)
    valid = {s.upper(): s for s, _ in units}
    utoks = [(s, _toks(t), _strong(t)) for s, t in units]
    removed, added, unsupported = [], [], 0
    out = []
    for sent in [x for x in _SENT.split(answer.strip()) if x.strip()]:
        cites = _CIT.findall(sent)
        good = [c for c in cites if c.strip().upper() in valid]
        removed += [c for c in cites if c.strip().upper() not in valid]
        bare = _CIT.sub("", sent).rstrip()
        end = ""
        if bare.endswith((".", "。")):
            bare, end = bare[:-1].rstrip(), bare[-1]
        if good:
            out.append(bare + "".join(f" [{valid[c.strip().upper()]}]" for c in dict.fromkeys(good)) + end)
            continue
        st, ss = _toks(bare), _strong(bare)
        if len(st) < 3 or not utoks:              # greetings, one-word lines: nothing to attribute
            out.append(bare + end)
            continue
        best, score = None, 0.0
        for s, ut, us in utoks:
            ov = len(st & ut) / len(st)
            if ss and ss <= us:                   # every name, id and number of the sentence is in this source
                ov = max(ov, 0.5 + 0.1 * len(ss))
            if ov > score:
                best, score = s, ov
        if best and score >= min_overlap:
            out.append(f"{bare} [{best}]{end}")
            added.append(best)
        else:
            out.append(bare + end)
            unsupported += 1
    return " ".join(out), {"removed_citations": removed, "attributed": added, "unsupported_sentences": unsupported}
