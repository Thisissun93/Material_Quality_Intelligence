"""Read an unknown export file and describe its structure, without assuming the maker."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "latin-1")
_NUM = re.compile(r"^[\s+-]?\d+([.,]\d+)?([eE][+-]?\d+)?\s*$")


@dataclass
class Fingerprint:
    path: Path
    text: str
    lines: list[str]
    encoding: str
    layout: str                      # table | keyvalue
    delimiter: str | None = None
    decimal: str = "."
    header_index: int | None = None
    headers: list[str] = field(default_factory=list)
    keyvalues: dict[str, str] = field(default_factory=dict)

    @property
    def head(self) -> str:
        return "\n".join(self.lines[:60])


def read_text(path: Path) -> tuple[str, str]:
    raw = Path(path).read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def _sniff_delimiter(lines: list[str]) -> str | None:
    sample = "\n".join(l for l in lines[:40] if l.strip())
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sum(l.count(d) for l in lines[:40]) for d in ",;\t|"}
        d, n = max(counts.items(), key=lambda kv: kv[1])
        return d if n else None


def _is_num(tok: str) -> bool:
    return bool(_NUM.match(tok.strip())) if tok.strip() else False


def _header_row(lines: list[str], delim: str) -> int | None:
    """Header = first row with >=3 mostly non-numeric cells that is followed by a numeric-ish row."""
    best, best_score = None, 0.0
    for i, line in enumerate(lines[:60]):
        cells = [c.strip() for c in line.split(delim)]
        if len(cells) < 3:
            continue
        text_share = sum(1 for c in cells if c and not _is_num(c)) / len(cells)
        nxt = lines[i + 1].split(delim) if i + 1 < len(lines) else []
        num_share = sum(1 for c in nxt if _is_num(c.replace(",", "."))) / max(len(nxt), 1)
        score = text_share * 0.6 + num_share * 0.4 + len(cells) * 0.01
        if text_share >= 0.6 and len(nxt) == len(cells) and score > best_score:
            best, best_score = i, score
    return best


def _keyvalues(lines: list[str]) -> dict[str, str]:
    kv = {}
    for line in lines:
        m = re.match(r"^\s*([^:=]{2,60}?)\s*[:=]\s*(.+?)\s*$", line)
        if m:
            kv[m.group(1).strip()] = m.group(2).strip()
    return kv


def fingerprint(path: Path | str) -> Fingerprint:
    path = Path(path)
    text, enc = read_text(path)
    lines = text.splitlines()
    delim = _sniff_delimiter(lines)
    hdr = _header_row(lines, delim) if delim else None
    if hdr is not None:
        headers = [h.strip() for h in lines[hdr].split(delim)]
        body = " ".join(lines[hdr + 1: hdr + 6])
        decimal = "," if delim != "," and re.search(r"\d,\d", body) else "."
        return Fingerprint(path, text, lines, enc, "table", delim, decimal, hdr, headers)
    return Fingerprint(path, text, lines, enc, "keyvalue", keyvalues=_keyvalues(lines))
