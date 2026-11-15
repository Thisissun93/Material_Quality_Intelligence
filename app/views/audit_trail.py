import json

import pandas as pd
import streamlit as st

import ui
from mqi import audit

ACTION_KO = {"login.ok": "로그인", "login.fail": "로그인 실패", "esign.ok": "전자서명 확인", "esign.fail": "전자서명 실패",
             "user.add": "사용자 등록", "oos.open": "OOS 조사 등록", "oos.phase1": "OOS 1단계 종결",
             "oos.phase2": "OOS 2단계 확정", "test.retest": "재시험 등록", "equipment.run": "설비 런 수신",
             "release.review": "QA 검토 서명", "release.approve": "출하 승인 서명", "release.reject": "출하 반려"}


def render():
    ko = ui.lang() == "ko"
    L = (lambda k, e: k) if ko else (lambda k, e: e)
    st.title(ui.t("nav_audit"))
    if not ui.is_demo() and not ui.can("audit.read"):
        st.info(L("감사 추적은 QA·승인자·관리자만 볼 수 있습니다.", "QA, approvers and admins can see the audit trail."))
        return
    st.caption(L("누가 언제 무엇을 했는지 기록합니다. 기록은 추가만 되고(데이터베이스가 수정·삭제를 거부), 각 행은 직전 "
                 "행의 해시를 담고 있어 파일을 직접 고쳐도 어디서 끊겼는지 드러납니다.",
                 "Who did what and when. Entries are only added (the database refuses edits and deletes) and every "
                 "row carries the hash of the previous one, so a row changed behind the database's back shows up."))
    v = audit.verify()
    c = st.columns(3)
    ui.kpi(c[0], L("기록 수", "Entries"), f"{v['rows']:,}")
    ui.kpi(c[1], L("해시 체인", "Hash chain"), L("정상", "Intact") if v["ok"] else L(f"#{v['broken_at']}에서 끊김",
                                                                                  f"Broken at #{v['broken_at']}"),
           tone="good" if v["ok"] else "bad")
    f1, f2 = st.columns([1, 1])
    lot = f1.text_input(L("로트 (예: UF-2611-004)", "Lot (e.g. UF-2611-004)")).strip()
    kinds = ["", "release", "oos", "test", "equipment", "login", "esign", "user"]
    kind = f2.selectbox(L("종류", "Kind"), kinds, format_func=lambda k: k or L("전체", "All"))
    df = audit.entries(f"lot/{lot}" if lot else None, kind or None, 500)
    ui.kpi(c[2], L("표시", "Shown"), len(df))
    if df.empty:
        st.caption(L("기록 없음", "No entries"))
        return

    def detail(d):
        d = json.loads(d or "{}")
        return " · ".join(f"{k}={str(v)[:40]}" for k, v in d.items() if v not in (None, "", []))

    show = pd.DataFrame({
        "#": df["id"],
        L("일시", "When"): df["at"].str.replace("T", " "),
        L("사용자", "User"): df["actor"],
        L("역할", "Role"): df["role"].fillna(""),
        L("작업", "Action"): df["action"].map(lambda a: ACTION_KO.get(a, a) if ko else a),
        L("대상", "Target"): df["target"],
        L("내용", "Detail"): df["detail"].map(detail),
        L("해시", "Hash"): df["hash"].str[:12],
    })
    st.dataframe(show, hide_index=True, width="stretch", height=560)
