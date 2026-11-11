"""Optional: rewrite a draft with an LLM. Off unless ANTHROPIC_API_KEY is set.

Everything works without it; the rule engine already produces the findings and drafts.
"""
from __future__ import annotations

import json
import os
import urllib.request

MODEL = os.environ.get("MQI_LLM_MODEL", "claude-sonnet-5-5")


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def polish(text: str, lang: str = "ko", timeout: int = 60) -> str:
    if not available():
        return text
    instr = ("다음 품질 문서 초안을 사실과 숫자는 그대로 두고, 품질 엔지니어가 바로 보낼 수 있게 간결하게 다듬어 주세요."
             if lang == "ko" else
             "Polish this quality document draft for a quality engineer. Keep every fact and number unchanged. Be concise.")
    body = json.dumps({"model": MODEL, "max_tokens": 2000,
                       "messages": [{"role": "user", "content": f"{instr}\n\n{text}"}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return "".join(c.get("text", "") for c in data.get("content", [])) or text
