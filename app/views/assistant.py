"""AI assistant: questions about lots, equipment and procedures, answered with sources."""
from __future__ import annotations

import json
import re
import os

import streamlit as st

import ui
from mqi.assistant import agent

EXAMPLES = {
    "ko": ["UF-2611-003 출하해도 돼?", "UF-2610-014 점도 측정 이력 알려줘", "OOS 나오면 재시험 바로 해도 돼?",
           "TC1이랑 TC2 편차 알람이 뜨면 뭘 해야 해?", "보류 중인 로트 목록 보여줘", "UF-2611-003 출하 승인 처리해줘"],
    "en": ["Why is UF-2610-014 blocked?", "Show the lots on hold", "Who can release a lot that is on hold?",
           "What should I do when TC1 and TC2 disagree during degassing?"],
}


def _settings(ko: bool) -> str:
    L = (lambda k, e: k) if ko else (lambda k, e: e)
    default = agent.backend_name()
    with st.expander(L("AI 설정", "AI settings")):
        opts = ["offline", "llm"]
        b = st.radio(L("방식", "Mode"), opts, index=opts.index(default) if default in opts else 0, horizontal=True,
                     format_func=lambda x: {"offline": L("오프라인 (규칙 기반, LLM 없음)", "Offline (rules, no LLM)"),
                                            "llm": L("사내 LLM (OpenAI 호환 서버)", "On-site LLM (OpenAI-compatible)")}[x],
                     key="as_backend")
        if b == "llm":
            os.environ["MQI_ASSISTANT_BASE_URL"] = st.text_input(
                "Base URL", os.environ.get("MQI_ASSISTANT_BASE_URL", "http://localhost:11434/v1"))
            os.environ["MQI_ASSISTANT_MODEL"] = st.text_input(
                L("모델", "Model"), os.environ.get("MQI_ASSISTANT_MODEL", "qwen2.5:3b"))
            st.caption(L("Ollama·vLLM 같은 사내 서버를 씁니다. 공장 데이터가 외부로 나가지 않습니다. 공개 데모(Streamlit Cloud)에는 "
                         "LLM 서버가 없으므로 오프라인 방식을 쓰세요.",
                         "Uses an on-site server such as Ollama or vLLM; plant data stays on site. The public demo has no "
                         "LLM server: use offline mode there."))
    return b


_SRC = re.compile(r"\s*\[((?:lot|lots|history|equipment|oos|SPEC|SOP|WI|findings)[^\[\]]*)\]", re.I)
_KIND = {"lot": ("출하 판정", "Release check"), "history": ("검사 이력", "Test history"),
         "equipment": ("설비 이력", "Equipment runs"), "lots": ("로트 목록", "Lot list"),
         "oos": ("OOS 조사", "OOS investigations"), "findings": ("우선 조치", "Priority findings")}


def _label(sid: str, ko: bool) -> str:
    kind, _, rest = sid.partition(":")
    name = _KIND.get(kind.lower())
    return f"{name[0 if ko else 1]} {rest}".strip() if name and rest else sid


def _cite(text: str, ko: bool) -> tuple[str, list[str]]:
    """Source tags in the answer become small numbers; the sources are listed once under the answer.
    (The tags stay in the stored answer: the grounding check reads them.)"""
    ids: list[str] = []

    def rep(m):
        nums = []
        for sid in (x.strip() for x in re.split(r"[,;]", m.group(1)) if x.strip()):
            if sid.lower() not in [x.lower() for x in ids]:
                ids.append(sid)
            n = [x.lower() for x in ids].index(sid.lower()) + 1
            if n not in nums:
                nums.append(n)
        return "<sup>" + ",".join(map(str, nums)) + "</sup>"
    out = _SRC.sub(rep, text)
    out = re.sub(r"\n(\*\*)", r"\n\n\1", out)      # a bold heading after a list starts a new block
    return out, ids


def _show(a: dict, ko: bool):
    L = (lambda k, e: k) if ko else (lambda k, e: e)
    body, ids = _cite(a["text"] or "—", ko)
    st.markdown(body.replace("\n", "  \n") if not re.search(r"^\s*- ", body, re.M) else body,
                unsafe_allow_html=True)
    if ids:
        st.caption(L("출처 ", "Sources ") + " · ".join(f"{i}) {_label(x, ko)}" for i, x in enumerate(ids, 1)))
    g = a["grounding"]
    if a.get("error"):
        st.warning(a["error"])
    elif g.get("grounded"):
        st.caption("✅ " + L("근거 확인: 답의 숫자·로트·출처가 모두 조회 결과와 일치", "Grounded: every number, lot and source "
                                                                             "matches what was looked up")
                   + f" · {a['backend']} · {a['latency_s']}s")
    else:
        bits = []
        if g.get("unsupported_numbers"):
            bits.append(L("근거 없는 숫자 ", "unsupported numbers ") + ", ".join(g["unsupported_numbers"]))
        if g.get("unsupported_lots"):
            bits.append(L("근거 없는 로트 ", "unsupported lots ") + ", ".join(g["unsupported_lots"]))
        if g.get("invalid_citations"):
            bits.append(L("조회하지 않은 출처 ", "sources not looked up ") + ", ".join(g["invalid_citations"]))
        if g.get("uncited"):
            bits.append(L("출처 없음", "no source"))
        st.warning("⚠️ " + L("근거 부족 — 그대로 쓰지 말고 원본을 확인하세요: ", "Not grounded — check the source before "
                                                                     "using it: ") + "; ".join(bits))
    at = a.get("attribution") or {}
    if at.get("removed_citations") or at.get("unsupported_sentences"):
        st.caption("ℹ️ " + L(f"모델이 단 출처 중 조회하지 않은 {len(at.get('removed_citations', []))}개를 지웠고, "
                             f"근거를 찾지 못한 문장 {at.get('unsupported_sentences', 0)}개가 있습니다.",
                             f"Removed {len(at.get('removed_citations', []))} source(s) the model invented; "
                             f"{at.get('unsupported_sentences', 0)} sentence(s) without matching evidence."))
    if a["tool_calls"]:
        with st.expander(L("조회한 데이터와 문서", "What was looked up") + f" ({len(a['tool_calls'])})"):
            for c in a["tool_calls"]:
                st.markdown(f"**{c['name']}** `{json.dumps(c['args'], ensure_ascii=False)}` → `[{c['source']}]`")
                secs = c["result"].get("sections") if isinstance(c["result"], dict) else None
                if secs:
                    for s in secs[:3]:
                        st.markdown(f"> **[{s['source']}]** {s['title']}  \n> {s['text'][:400]}")
                else:
                    st.json(c["result"], expanded=False)


def render():
    ko = ui.lang() == "ko"
    L = (lambda k, e: k) if ko else (lambda k, e: e)
    st.title(L("AI 어시스턴트", "AI assistant"))
    st.caption(L("로트·검사 이력·설비 데이터는 시스템에서 조회하고, 절차·기준은 사내 문서에서 찾아 출처와 함께 답합니다. "
                 "조회만 할 수 있고 승인·해제·종결은 하지 않습니다.",
                 "Looks up lots, test history and equipment data, finds procedures in plant documents, and answers "
                 "with sources. Read-only: it never approves, releases or closes anything."))
    backend = _settings(ko)
    hist = st.session_state.setdefault("as_hist", [])
    pick = st.pills(L("예시 질문", "Try"), EXAMPLES["ko" if ko else "en"], key="as_pick")
    q = st.chat_input(L("질문을 입력하세요 (예: UF-2611-003 출하해도 돼?)", "Ask (e.g. Why is UF-2610-014 blocked?)"))
    q = q or (pick if pick and st.session_state.get("as_last_pick") != pick else None)
    if pick:
        st.session_state["as_last_pick"] = pick
    for turn in hist:
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            _show(turn, ko)
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner(L("조회하고 답을 쓰는 중…", "Looking up and writing…")):
                a = agent.ask(q, backend).to_dict()
            _show(a, ko)
        hist.append(a)
    if hist and st.button(L("대화 지우기", "Clear")):
        st.session_state["as_hist"] = []
        st.rerun()
