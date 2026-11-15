"""The assistant loop.

  ask(question) -> Answer(text, sources, tool_calls, grounding, backend, latency)

Backends
  llm        local LLM (any OpenAI-compatible /chat/completions with tool calling: Ollama, vLLM, a gateway), with
             guided retrieval: guard rails first, then the lot data and procedures the question needs are fetched
             and handed to the model, which writes the answer and may call more tools. The system then attaches
             the sources (attribution.py): small models write well but do not cite reliably.
  llm-tools  the same model left alone with the tools (pure tool calling). Kept for the evaluation.
  offline    rule-based planner over the same tools and documents. No LLM, deterministic. Public demo, CI, baseline.

  MQI_ASSISTANT_BACKEND (offline), MQI_ASSISTANT_BASE_URL (http://localhost:11434/v1),
  MQI_ASSISTANT_MODEL (qwen2.5:3b), MQI_ASSISTANT_API_KEY (if the gateway needs one)

The assistant is read-only: it has no tool that changes data, and it is told to send approvals and closures to
the people the procedures name.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field

from . import attribution, grounding, tools

LOT_RE = re.compile(r"UF-\d{4}-\d{3}", re.I)
_CITE = re.compile(r"\[([^\]]+)\]")

SYSTEM = """You are the quality assistant of an electronic-materials plant (capillary underfill UF-300).

Rules
1. Plant data (lots, test results, release status, equipment runs, specifications) only comes from the tools.
   Procedures, criteria, responsibilities and deadlines only come from search_documents (Korean documents:
   search in Korean). Never answer from memory.
2. Every number, status and lot in your answer must appear in a tool result. If the tools do not give it,
   say you cannot confirm it.
3. Cite the source at the end of each sentence in square brackets, copying one id from a "cite as" line of a
   tool result, e.g. [lot:<lot id>] or [<document id> §<section>]. Cite only ids that a tool gave you.
4. You cannot change anything: no release approval, no hold release, no closing or opening investigations,
   no edits. If asked, say so, and say who does it and where (from the documents).
5. Only quality, production and equipment questions about this plant. Politely decline anything else.
6. Answer in the language of the question, in 2 to 6 short sentences."""

REFUSE_WRITE = {"ko": "저는 조회만 할 수 있고 승인·해제·종결 같은 처리는 할 수 없습니다.",
                "en": "I can only look things up; I cannot approve, release, close or change anything."}
REFUSE_SCOPE = {"ko": "이 공장의 품질·생산·설비에 관한 질문에만 답할 수 있습니다.",
                "en": "I can only answer questions about quality, production and equipment in this plant."}


@dataclass
class ToolCall:
    name: str
    args: dict
    source: str
    result: dict


@dataclass
class Answer:
    question: str
    text: str
    backend: str
    lang: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    grounding: dict = field(default_factory=dict)
    attribution: dict = field(default_factory=dict)
    latency_s: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def lang_of(text: str) -> str:
    return "ko" if re.search(r"[가-힣]", text) else "en"


def _evidence(calls: list[ToolCall]) -> str:
    return "\n".join(tools.dumps(c.result, 100000) for c in calls)


def _returned_sources(calls: list[ToolCall]) -> list[str]:
    out = []
    for c in calls:
        out += [x for x in c.source.split(",") if x]
        out += [s["source"] for s in c.result.get("sections", [])] if isinstance(c.result, dict) else []
    return list(dict.fromkeys(out))


def _finish(ans: Answer, t0: float, refusal: bool = False) -> Answer:
    cited = [c.strip() for c in _CITE.findall(ans.text)]
    seen = _returned_sources(ans.tool_calls)
    ans.sources = list(dict.fromkeys([c for c in cited if c in seen] or seen))
    found = [c for c in ans.tool_calls if not (isinstance(c.result, dict) and "error" in c.result)]
    ans.grounding = grounding.check(ans.text, _evidence(ans.tool_calls), seen,
                                    refusal or (bool(ans.tool_calls) and not found))
    ans.latency_s = round(time.perf_counter() - t0, 2)
    return ans


def backend_name() -> str:
    return os.environ.get("MQI_ASSISTANT_BACKEND", "offline")


def ask(question: str, backend: str | None = None, dbpath=None, history: list[dict] | None = None) -> Answer:
    backend = backend or backend_name()
    if backend == "offline":
        return ask_offline(question, dbpath)
    return ask_llm(question, dbpath, history=history, guided=(backend != "llm-tools"))


# ---------------------------------------------------------------- shared: guard rails and planner

_WRITE = re.compile(r"(승인|해제|종결|등록|수정|삭제|변경|처리|닫|풀|발행|바꿔)\S*\s*(해\s*)?(줘|주세요|주라|줄래|달라|하세요|해라)|"
                    r"^\s*(please\s+)?(approve|release|unhold|close|delete|modify|change|issue)\b|"
                    r"\b(can|could|would)\s+you\s+(please\s+)?(approve|release|unhold|close|delete|modify|change|issue)\b",
                    re.I)
_DOMAIN = re.compile(r"로트|lot|출하|보류|불가|규격|점도|tg|경화|필러|oos|재시험|재측정|측정|조사|설비|믹서|tc|온도|탈포|"
                     r"진공|fdc|알람|8d|scar|공급사|coa|성적서|msa|grr|gage|sop|절차|기준|spec|release|hold|block|"
                     r"retest|equipment|mixer|alarm|supplier|procedure|viscosity|thermocouple|센서|sensor|품질|quality",
                     re.I)
_HIST = re.compile(r"이력|측정값|재시험|재측정|최초|첫|oos|history|retest|first result", re.I)
_EQUIP = re.compile(r"설비|믹서|tc|온도|탈포|fdc|알람|센서|equipment|mixer|sensor|thermocouple|alarm", re.I)
_HOW = re.compile(r"어떻게|절차|대응|해야|해도|누가|기한|기준|방법|조치|되나|돼\?|how|who|what should|procedure|"
                  r"deadline|criteria|allowed", re.I)
_STATUS = {"block": re.compile(r"출하\s*불가|불가|blocked|block", re.I),
           "hold": re.compile(r"보류|hold", re.I), "release": re.compile(r"출하\s*가능|releasable|can ship", re.I)}
_PROP = {"viscosity": r"점도|viscosity", "tg": r"\btg\b", "peak_temp": r"피크|peak", "enthalpy": r"반응열|enthalpy",
         "filler_pct": r"필러|filler"}


def guard(question: str) -> str | None:
    """'write' for requests to change something, 'scope' for questions outside the plant. Decided by rules,
    before any model sees the question: these answers must not depend on a model's mood."""
    if _WRITE.search(question):
        return "write"
    if not LOT_RE.search(question) and not _DOMAIN.search(question):
        return "scope"
    return None


def _call(ans: Answer, name: str, dbpath, **args) -> dict:
    res, src = tools.call(name, args, ans.lang, dbpath)
    ans.tool_calls.append(ToolCall(name, args, src, res))
    return res


def gather(ans: Answer, question: str, dbpath=None) -> None:
    """Fetch what the question needs: the lots it names (with history / equipment when relevant), lot lists,
    specifications, and the procedures for how-to questions."""
    lots = [x.upper() for x in LOT_RE.findall(question)][:2]
    for lot in lots:
        r = _call(ans, "get_lot_release", dbpath, lot_id=lot)
        if "error" in r:
            continue
        if _HIST.search(question) or any("OOS" in x for x in r["reasons"]):
            _call(ans, "get_test_history", dbpath, lot_id=lot)
        if _EQUIP.search(question) or any(("설비" in x or "Equipment" in x) for x in r["reasons"]):
            _call(ans, "get_equipment_runs", dbpath, lot_id=lot)
    if not lots:
        st = next((k for k, rx in _STATUS.items() if rx.search(question)), None)
        if st and re.search(r"로트|lot|어떤|무엇|목록|list|which", question, re.I):
            _call(ans, "list_lots", dbpath, status=st)
        prop = next((p for p, rx in _PROP.items() if re.search(rx, question, re.I)), None)
        if re.search(r"규격|spec", question, re.I) and prop:
            _call(ans, "get_spec", dbpath, property=prop)
    if not ans.tool_calls or _HOW.search(question):
        _call(ans, "search_documents", dbpath, query=question if ans.lang == "ko" else question + " 절차 기준")


# ---------------------------------------------------------------- offline (rule-based baseline)

def _doc_sentences(res: dict, n: int = 2) -> list[str]:
    out = []
    for s in res.get("sections", [])[:1]:
        for sent in re.split(r"(?<=[.다])\s+", s["text"])[:n]:
            out.append(f"{sent.strip()} [{s['source']}]")
    return out


def _refusal(ans: Answer, kind: str, dbpath) -> None:
    ans.text = (REFUSE_WRITE if kind == "write" else REFUSE_SCOPE)[ans.lang]
    if kind == "write":
        res = _call(ans, "search_documents", dbpath, query="보류 해제 승인 권한 QA 성적서 발행")
        ans.text += " " + " ".join(_doc_sentences(res, 1))


def ask_offline(question: str, dbpath=None) -> Answer:
    t0, lang = time.perf_counter(), lang_of(question)
    ans, ko = Answer(question, "", "offline", lang), lang == "ko"
    g = guard(question)
    if g:
        _refusal(ans, g, dbpath)
        return _finish(ans, t0, refusal=True)
    gather(ans, question, dbpath)
    parts: list[str] = []
    for c in ans.tool_calls:
        r = c.result
        if c.name == "get_lot_release":
            lot = c.args["lot_id"]
            if "error" in r:
                parts.append(f"{lot} 로트를 찾을 수 없습니다. [{c.source}]" if ko else f"Lot {lot} was not found. [{c.source}]")
                continue
            why = "; ".join(r["reasons"]) if r["reasons"] else ("보류·불가 사유 없음" if ko else "no reason to hold")
            parts.append(f"{lot}: {r['status']} — {why} [{c.source}]")
            if re.search(r"값|얼마|결과|value|result", question, re.I):
                parts.append(", ".join(f"{k} {v}" for k, v in r["reportable_values"].items()) + f" [{c.source}]")
        elif c.name == "get_test_history":
            for x in [x for x in r.get("results", []) if x["out_of_spec"] or x["test"] > 1][:4]:
                tag = (" 규격 이탈" if ko else " out of spec") if x["out_of_spec"] else ""
                inv = (" 무효" if ko else " invalidated") if x["invalidated"] else ""
                parts.append((f"{x['property']} {x['test']}차 {x['type']} {x['value']:g} {x['unit']}{tag}{inv}" if ko else
                              f"{x['property']} test {x['test']} ({x['type']}) {x['value']:g} {x['unit']}{tag}{inv}")
                             + f" [{c.source}]")
        elif c.name == "get_equipment_runs":
            for run in r.get("runs", [])[:2]:
                msg = "; ".join(run["alarms"]) or ("알람 없음" if ko else "no alarm")
                parts.append(f"{run['equipment']} run {run['run']}: {msg} [{c.source}]")
        elif c.name == "list_lots":
            ids = [x["lot_id"] for x in r["lots"]]
            parts.append((f"최근 40개 로트 중 {len(ids)}개: " if ko else f"{len(ids)} of the latest 40 lots: ")
                         + (", ".join(ids) or "-") + f" [{c.source}]")
        elif c.name == "get_spec":
            for k, v in r.items():
                parts.append(f"{k} {v['lsl']:,}~{v['usl']:,} {v['unit']}, " + ("목표" if ko else "target")
                             + f" {v['target']:,} [{c.source}]")
        elif c.name == "search_documents":
            parts += _doc_sentences(r, 2) or ["관련 문서를 찾지 못했습니다." if ko else "No matching procedure was found."]
    ans.text = " ".join(parts)
    return _finish(ans, t0)


# ---------------------------------------------------------------- local LLM (OpenAI-compatible)

def _post(messages: list[dict], timeout: float, with_tools: bool = True) -> dict:
    import httpx

    base = os.environ.get("MQI_ASSISTANT_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    # a cap on the answer length: small models sometimes repeat themselves until the request times out
    body = {"model": os.environ.get("MQI_ASSISTANT_MODEL", "qwen2.5:3b"), "messages": messages, "temperature": 0,
            "max_tokens": int(os.environ.get("MQI_ASSISTANT_MAX_TOKENS", "600"))}
    if with_tools:
        body["tools"] = tools.SCHEMAS
    r = httpx.post(f"{base}/chat/completions", json=body, timeout=timeout,
                   headers={"Authorization": f"Bearer {os.environ.get('MQI_ASSISTANT_API_KEY', 'none')}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def _inline_calls(content: str) -> list[dict]:
    """Some small models write the call as JSON text instead of using tool_calls."""
    calls = []
    for m in re.finditer(r"\{[^{}]*\"name\"\s*:\s*\"(\w+)\"[^{}]*\"arguments\"\s*:\s*(\{[^{}]*\})[^{}]*\}",
                         content or ""):
        if m.group(1) in tools.TOOLS:
            calls.append({"id": f"inline{len(calls)}", "type": "function",
                          "function": {"name": m.group(1), "arguments": m.group(2)}})
    return calls


def _tool_msgs(c: ToolCall, cid: str) -> list[dict]:
    if c.name == "search_documents" and isinstance(c.result, dict) and c.result.get("sections"):
        body = "\n\n".join(f"cite as: [{s['source']}]\n{s['title']}\n{s['text']}" for s in c.result["sections"])
    else:
        body = f"cite as: [{c.source}]\n" + tools.dumps(c.result)
    return [{"role": "assistant", "content": "",
             "tool_calls": [{"id": cid, "type": "function",
                             "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}]},
            {"role": "tool", "tool_call_id": cid, "name": c.name, "content": body}]


def ask_llm(question: str, dbpath=None, guided: bool = True, max_rounds: int = 3, timeout: float = 300,
            history: list[dict] | None = None, post=None) -> Answer:
    post = post or _post
    t0, lang = time.perf_counter(), lang_of(question)
    model = os.environ.get("MQI_ASSISTANT_MODEL", "qwen2.5:3b")
    ans = Answer(question, "", ("llm:" if guided else "llm-tools:") + model, lang)
    if guided:
        g = guard(question)
        if g:
            _refusal(ans, g, dbpath)
            return _finish(ans, t0, refusal=True)
    system = SYSTEM + ("\n7. 질문이 한국어이므로 반드시 한국어로 답하세요." if lang == "ko" else "")
    msgs = [{"role": "system", "content": system}, *(history or []), {"role": "user", "content": question}]
    try:
        if guided:
            gather(ans, question, dbpath)
            for k, c in enumerate(ans.tool_calls):
                msgs += _tool_msgs(c, f"pre{k}")
        for _ in range(max_rounds):
            m = post(msgs, timeout)
            calls = m.get("tool_calls") or _inline_calls(m.get("content") or "")
            if not calls:
                ans.text = (m.get("content") or "").strip()
                break
            for k, c in enumerate(calls):
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                res, src = tools.call(name, args, lang, dbpath)
                tc = ToolCall(name, args, src, res)
                ans.tool_calls.append(tc)
                msgs += _tool_msgs(tc, c.get("id") or f"call{k}")
        if not ans.text:
            msgs.append({"role": "user", "content": "위 도구 결과만으로 출처를 붙여 답하세요." if lang == "ko"
                         else "Answer now from the tool results above, with citations."})
            ans.text = (post(msgs, timeout, with_tools=False).get("content") or "").strip()
        if guided and ans.text:
            ans.text, ans.attribution = attribution.attribute(ans.text, ans.tool_calls)
    except Exception as e:  # noqa: BLE001 - the UI shows the error; nothing is guessed
        ans.error = f"{type(e).__name__}: {e}"
        ans.text = ("AI 서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요." if lang == "ko"
                    else "The AI server is not reachable. Please try again later.")
    return _finish(ans, t0)
