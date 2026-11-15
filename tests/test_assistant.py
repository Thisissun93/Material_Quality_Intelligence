"""Quality assistant: retrieval, read-only tools, guard rails, grounding, the LLM loop (with a scripted model)
and the evaluation harness. The real-model evaluation is in docs/assistant_eval.md."""
import json

import pytest

from mqi.assistant import agent, eval as ev, grounding, kb, tools


# ---------------------------------------------------------------- retrieval

@pytest.mark.parametrize("q,doc", [
    ("OOS 나오면 재시험 바로 해도 돼?", "SOP-QA-021"),
    ("보류 로트는 누가 해제할 수 있어?", "SOP-QA-005"),
    ("Gage R&R %GRR 판정 기준", "SOP-QC-020"),
    ("8D 봉쇄 조치 기한", "SOP-QA-040"),
    ("공급사 변경점 미통보 SCAR", "SOP-QA-030"),
    ("탈포 진공도 기준", "SOP-PR-030"),
])
def test_search_finds_the_right_procedure(q, doc):
    assert doc in [c.doc for c, _ in kb.search(q, 3)]


def test_spec_document_follows_specs_yaml():
    spec = next(c for c in kb.chunks() if c.id == "SPEC-UF-300")
    assert "2,500~4,500" in spec.text


# ---------------------------------------------------------------- tools are read-only

def test_no_tool_can_write():
    names = set(tools.TOOLS)
    assert names and all(n.startswith(("get_", "list_", "search_")) for n in names)


def test_tools_on_the_demo(bundle):
    r, src = tools.get_lot_release("UF-2611-003", dbpath=bundle.dbpath)
    assert src == "lot:UF-2611-003" and r["status"].startswith("보류")
    r, _ = tools.get_lot_release("UF-9999-999", dbpath=bundle.dbpath)
    assert "error" in r
    r, _ = tools.call("get_spec", {"property": "viscosity", "bogus": 1})
    assert list(r.values())[0]["usl"] == 4500


# ---------------------------------------------------------------- grounding

def test_grounding_flags_invented_numbers_lots_and_sources():
    ev_text = '{"lot_id": "UF-2610-014", "value": 4640.0}'
    ok = grounding.check("UF-2610-014 첫 결과 4,640 [lot:UF-2610-014]", ev_text, ["lot:UF-2610-014"])
    assert ok["grounded"]
    bad = grounding.check("UF-2610-015 첫 결과 4,700 [SOP-QA-099 §1]", ev_text, ["lot:UF-2610-014"])
    assert bad["unsupported_numbers"] == ["4,700"] and bad["unsupported_lots"] == ["UF-2610-015"]
    assert bad["invalid_citations"] and not bad["grounded"]
    assert grounding.check("재시험 전에 조사를 먼저 등록합니다.", ev_text, ["SOP-QA-021 §2"])["uncited"]


def test_rounding_is_not_a_hallucination():
    assert grounding.check("최고 64.9°C [x]", '{"max": 64.87}', ["x"])["grounded"]
    assert not grounding.check("최고 65.9°C [x]", '{"max": 64.87}', ["x"])["grounded"]


# ---------------------------------------------------------------- guard rails and offline backend

@pytest.mark.parametrize("q,kind", [("UF-2611-003 출하 승인 처리해줘", "write"), ("OOS 조사 종결해줘", "write"),
                                    ("오늘 서울 날씨 어때?", "scope"), ("UF-2611-003 출하해도 돼?", None)])
def test_guard(q, kind):
    assert agent.guard(q) == kind


def test_offline_answers_with_sources(bundle):
    a = agent.ask("UF-2611-003 출하해도 돼?", "offline", bundle.dbpath)
    assert "보류" in a.text and "[lot:UF-2611-003]" in a.text and a.grounding["grounded"]
    a = agent.ask("UF-2611-003 출하 승인 처리해줘", "offline", bundle.dbpath)
    assert "할 수 없" in a.text and "SOP-QA-005" in a.text


# ---------------------------------------------------------------- LLM loop with a scripted model

def _script(*replies):
    """A fake OpenAI-compatible model that returns the given messages in order and records what it saw."""
    seen = []

    def post(msgs, timeout, with_tools=True):
        seen.append(msgs)
        return replies[min(len(seen) - 1, len(replies) - 1)]
    return post, seen


def _call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_llm_tool_loop_and_grounding(bundle):
    post, seen = _script(_call("get_lot_release", lot_id="UF-2610-014"),
                         {"role": "assistant", "content": "UF-2610-014는 출하 불가입니다. 첫 점도 4,640이 규격 이탈입니다. "
                                                          "[lot:UF-2610-014]"})
    a = agent.ask_llm("UF-2610-014 상태?", bundle.dbpath, guided=False, post=post)
    assert [c.name for c in a.tool_calls] == ["get_lot_release"] and a.grounding["grounded"]
    assert any(m["role"] == "tool" for m in seen[1])                 # the model saw the tool result


def test_llm_hallucination_is_flagged(bundle):
    post, _ = _script({"role": "assistant", "content": "UF-2610-014 점도는 3,900으로 합격입니다. [SOP-QA-005 §9]"})
    a = agent.ask_llm("UF-2610-014 상태?", bundle.dbpath, guided=False, post=post)
    g = a.grounding
    assert not g["grounded"] and "3,900" in g["unsupported_numbers"] and g["invalid_citations"]


def test_guided_mode_prefetches_and_guards(bundle):
    post, seen = _script({"role": "assistant", "content": "재시험 전에 OOS 조사를 먼저 등록해야 합니다. [SOP-QA-021 §2]"})
    a = agent.ask_llm("OOS 나오면 재시험 바로 해도 돼?", bundle.dbpath, guided=True, post=post)
    assert a.tool_calls[0].name == "search_documents" and a.grounding["grounded"]
    assert "SOP-QA-021" in json.dumps(seen[0], ensure_ascii=False)   # documents were in the prompt
    post, seen = _script({"role": "assistant", "content": "승인했습니다."})
    a = agent.ask_llm("UF-2611-003 출하 승인 처리해줘", bundle.dbpath, guided=True, post=post)
    assert seen == [] and "할 수 없" in a.text                        # refused before the model was asked


def test_attribution_fixes_citations_but_not_facts(bundle):
    """The model forgot brackets and invented a source: the system attaches the real one and removes the fake;
    an invented number still fails the grounding check."""
    post, _ = _script({"role": "assistant", "content": "UF-2611-003은 M1에서 TC1과 TC2 편차 5.5°C 알람이 있었습니다 "
                                                       "[SOP-QA-999 §1]. 그래서 보류 상태입니다."})
    a = agent.ask_llm("UF-2611-003 설비 문제 있었어?", bundle.dbpath, guided=True, post=post)
    assert "SOP-QA-999 §1" in a.attribution["removed_citations"]
    assert ("[equipment:UF-2611-003]" in a.text or "[lot:UF-2611-003]" in a.text) and a.grounding["grounded"]
    assert "]." in a.text and " ." not in a.text
    post, _ = _script({"role": "assistant", "content": "UF-2611-003은 TC 편차가 7.5°C였습니다."})
    a = agent.ask_llm("UF-2611-003 설비 문제 있었어?", bundle.dbpath, guided=True, post=post)
    assert "7.5" in a.grounding["unsupported_numbers"] and not a.grounding["grounded"]


def test_llm_server_down_is_reported_not_guessed(bundle):
    def post(*_a, **_k):
        raise ConnectionError("refused")
    a = agent.ask_llm("UF-2610-014 상태?", bundle.dbpath, guided=True, post=post)
    assert a.error and "연결할 수 없습니다" in a.text


# ---------------------------------------------------------------- evaluation harness

def test_offline_baseline_on_the_eval_set(bundle):
    """The rule-based baseline must stay where it is; a drop means retrieval, tools or the set broke."""
    res = ev.run("offline", ev.load(), bundle.dbpath, verbose=False)
    s = res["summary"]
    assert s["n"] >= 30 and s["pass"] >= 0.80 and s["refusal"] == 1.0 and s["grounded"] >= 0.95


def test_api_ask(client):
    r = client.post("/api/v1/assistant/ask", json={"question": "UF-2611-003 출하해도 돼?", "backend": "offline"})
    assert r.status_code == 200 and "보류" in r.json()["text"] and r.json()["grounding"]["grounded"]
    assert client.post("/api/v1/assistant/ask", json={"question": ""}).status_code == 422
