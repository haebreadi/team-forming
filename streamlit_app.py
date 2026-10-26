"""팀 프로젝트 팀 편성 앱(Streamlit).

화면 요소의 key 는 공고 2.7절의 표를 그대로 따른다. 다른 사람의 변경은
st.fragment(run_every=3초)로 바뀐 부분만 자동으로 다시 그린다(3장의 어려운 해결).
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

from tf import ai, jobs, rules, service
from tf.db import Database
from tf.rules import APPROVED, BLOCKED

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")
KST = ZoneInfo("Asia/Seoul")
REFRESH_SEC = 3  # 5초 이하(공고 2.7절)

st.set_page_config(page_title="팀 편성", page_icon="👥", layout="wide")


# ---------------------------------------------------------------- 설정과 DB

def secret(name: str) -> str | None:
    """.env(로컬) 또는 Streamlit Secrets(배포)의 값. 값은 화면에 출력하지 않음."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        v = st.secrets.get(name)
    except Exception:
        v = None
    return str(v) if v else None


def model_list() -> list[str]:
    raw = secret("OPENROUTER_MODELS")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else list(ai.DEFAULT_MODELS)
    return models[:3]


@st.cache_resource(show_spinner="DB에 연결하는 중…")
def get_db() -> Database:
    url = secret("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 없음")
    db = Database(url)  # 연결 풀은 앱 전체에서 한 번만 만듦
    service.ensure_schema(db, (APP_DIR / "schema.sql").read_text(encoding="utf-8"))
    return db


@st.cache_resource(show_spinner=False)
def ensure_admin(_db: Database, password_fingerprint: str) -> bool:
    # 비밀번호가 바뀌면 fingerprint 가 달라져 다시 실행됨(관리자 비밀번호 갱신)
    service.ensure_admin(_db, secret("ADMIN_PASSWORD") or "")
    return True


def kst(ts) -> str:
    if ts is None:
        return ""
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ts
    return ts.astimezone(KST).strftime("%H:%M:%S")


def label(s: rules.Student) -> str:
    return f"{s.name} — {s.roles_text()}"


def set_result(d: rules.Decision) -> None:
    st.session_state["last_result"] = d.message()
    st.session_state["last_result_at"] = time.time()


def show_result() -> None:
    with st.container(key="result", border=True):
        msg = st.session_state.get("last_result")
        if not msg:
            st.caption("단추를 누르면 결과가 여기에 나옴")
        elif msg.startswith(APPROVED):
            st.success(msg)
        elif msg.startswith(BLOCKED) or msg.startswith(rules.REJECTED):
            st.error(msg)
        else:
            st.info(msg)


def logout() -> None:
    for k in ("user", "last_result", "last_result_at"):
        st.session_state.pop(k, None)


# ---------------------------------------------------------------- 단추 동작(콜백)

def do_login(db: Database) -> None:
    u = service.login(db, st.session_state.get("login_student_no", ""), st.session_state.get("login_password", ""))
    if u is None:
        st.session_state["login_error"] = "학번이나 비밀번호가 틀림"
        return
    st.session_state.pop("login_error", None)
    st.session_state["user"] = u
    st.session_state.pop("last_result", None)


def do_apply(db: Database, team_no: int) -> None:
    u = st.session_state.get("user")
    if not u or u["is_admin"]:
        return set_result(rules.Decision(BLOCKED, "학생으로 로그인해야 함"))
    set_result(service.apply(db, u["id"], team_no))


def do_vote(db: Database, applicant_no: str, value: str) -> None:
    u = st.session_state.get("user")
    if not u or u["is_admin"]:
        return set_result(rules.Decision(BLOCKED, "학생으로 로그인해야 함"))
    set_result(service.vote(db, u["id"], applicant_no, value))


def _admin_only() -> bool:
    u = st.session_state.get("user")
    if not u or not u["is_admin"]:
        set_result(rules.Decision(BLOCKED, "관리자만 할 수 있음"))
        return False
    return True


def do_start(db: Database) -> None:
    if not _admin_only():
        return
    f = st.session_state.get("admin_roster_file")
    d = service.reset_with_roster(db, f.getvalue() if f else None, st.session_state.get("admin_team_count"))
    if d.word == APPROVED:
        jobs.cancel()
    set_result(d)


def do_close(db: Database) -> None:
    if _admin_only():
        set_result(service.close_recruiting(db))


def do_assign(db: Database) -> None:
    if not _admin_only():
        return
    ctx = service.assign_context(db)
    if not ctx.closed:
        return set_result(rules.Decision(BLOCKED, "모집을 먼저 마감해야 함"))
    if ctx.confirmed:
        return set_result(rules.Decision(BLOCKED, "이미 확정함"))
    if jobs.is_running():
        return set_result(rules.Decision(BLOCKED, "AI 배정이 이미 진행 중임"))
    fallback = rules.solve(ctx.teams, ctx.unassigned)
    if fallback is None:  # 공고 2.5절 8항: AI를 부르지 않음
        service.save_plan_error(db, rules.diagnose(ctx.teams, ctx.team_names, ctx.unassigned))
        return set_result(rules.Decision(BLOCKED, "규칙을 지키는 배정이 없음(이유는 AI 배정 진행 안내에 있음)"))
    if not ctx.unassigned:
        service.save_plan(db, {}, "팀이 없는 학생이 없어 AI를 부르지 않음")
        return set_result(rules.Decision(APPROVED, "팀이 없는 학생이 없음. 지금 팀 구성을 확정할 수 있음"))
    service.clear_plan(db)
    jobs.start(db, ctx, fallback, model_list(), secret("OPENROUTER_API_KEY") or "")
    set_result(rules.Decision(APPROVED, "AI 배정을 시작함(1차 모델의 응답을 기다리는 중)"))


def do_confirm(db: Database) -> None:
    if _admin_only():
        set_result(service.confirm_plan(db))


# ---------------------------------------------------------------- 공통 화면 조각

def team_box(t: dict, mine: bool, pending_count: int) -> None:
    title = f"**{t['name']}**" + ("  🟢 내 팀" if mine else "") + f"  ·  {len(t['members'])}/{rules.MAX_TEAM}명"
    st.markdown(title)
    if t["members"]:
        for m in t["members"]:
            st.markdown(f"- {label(m)}")
        missing = rules.missing_required(t["members"])
        if missing:
            st.caption("아직 없는 필수 포함 역할: " + ", ".join(missing))
    else:
        st.caption("빈 팀(지원하면 바로 승인)")
    if pending_count:
        st.caption(f"대기 중인 지원 {pending_count}건")


# ---------------------------------------------------------------- 학생 화면

@st.fragment(run_every=REFRESH_SEC)
def student_live(db: Database) -> None:
    u = st.session_state["user"]
    try:
        v = service.load_view(db, u["id"])
    except Exception as e:
        st.warning(f"DB를 읽지 못함({type(e).__name__}). 잠시 뒤 자동으로 다시 시도함")
        return
    if u["id"] not in v.students:
        logout()
        st.warning("명단이 다시 올라가 로그아웃됨. 다시 로그인하세요")
        st.rerun()

    show_result()

    my_team = v.team_of.get(u["id"])
    my_pending = v.my_last if v.my_last and v.my_last["status"] == rules.PENDING else None
    if v.confirmed:
        st.info(f"팀 구성이 확정됨. 내 팀: 팀{my_team}" if my_team else "팀 구성이 확정됨")
    elif v.closed:
        st.warning("모집이 마감됨. 지원과 투표를 할 수 없음")
    if my_team:
        st.markdown(f"#### 내 팀: 팀{my_team}")
    else:
        st.markdown("#### 아직 팀이 없음")
    if v.my_last:
        a = v.my_last
        st.caption(f"내 마지막 지원: {a['team_name']} · {a['status']}" + (f" ({a['reason']})" if a["reason"] else ""))

    can_apply = not my_team and not my_pending and not v.closed
    pend_by_team: dict[int, int] = {}
    for p in v.pending:
        pend_by_team[p["team_no"]] = pend_by_team.get(p["team_no"], 0) + 1

    st.markdown("### 팀 목록")
    cols = st.columns(2)
    for i, t in enumerate(v.teams):
        with cols[i % 2]:
            with st.container(key=f"team_{t['team_no']}", border=True):
                team_box(t, t["team_no"] == my_team, pend_by_team.get(t["team_no"], 0))
                if not my_team:
                    st.button(f"{t['name']}에 지원", key=f"apply_{t['team_no']}", disabled=not can_apply,
                              on_click=do_apply, args=(db, t["team_no"]))

    if my_team:
        st.markdown("### 내 팀에 들어온 지원")
        mine = [p for p in v.pending if p["team_no"] == my_team]
        if not mine:
            st.caption("대기 중인 지원이 없음")
        members = next(t["members"] for t in v.teams if t["team_no"] == my_team)
        for p in mine:
            s = p["student"]
            yes = sum(1 for m in members if p["votes"].get(m.id) == "찬성")
            with st.container(border=True):
                st.markdown(f"**{label(s)}**  ·  지원 시각 {kst(p['created_at'])}  ·  찬성 {yes}/{len(members)}")
                my_vote = p["votes"].get(u["id"])
                if my_vote:
                    st.markdown(f"내 투표: **{my_vote}**")
                elif not v.closed:
                    c1, c2 = st.columns(2)
                    c1.button("찬성", key=f"vote_yes_{s.student_no}", on_click=do_vote,
                              args=(db, s.student_no, "찬성"), type="primary")
                    c2.button("거부", key=f"vote_no_{s.student_no}", on_click=do_vote,
                              args=(db, s.student_no, "거부"))
    st.caption(f"{REFRESH_SEC}초마다 자동으로 새로 보여 줌 · 마지막 갱신 {datetime.now(KST).strftime('%H:%M:%S')}")


# ---------------------------------------------------------------- 관리자 화면

def admin_static(db: Database) -> None:
    st.markdown("### 1. 명단 올리기")
    st.caption("명단을 올리면 지원, 투표, 팀원, 마감 표시를 모두 지우고 명단의 학생과 빈 팀을 새로 만듦(관리자 계정은 남김)")
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        st.file_uploader("명단 CSV(student_no,name,main_role,sub_roles)", type=["csv"], key="admin_roster_file")
    with c2:
        st.number_input("팀 수", min_value=1, max_value=50, value=4, step=1, key="admin_team_count")
    with c3:
        st.write("")
        st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=do_start, args=(db,),
                  type="primary")


def assign_status_box(v: service.View) -> None:
    js = jobs.status()
    with st.container(key="assign_status", border=True):
        st.markdown("**AI 배정 진행 안내**")
        if js.get("running"):
            elapsed = time.time() - (js.get("attempt_started") or time.time())
            st.info(f"⏳ {js['attempt']}차 모델({js['model']})의 응답을 기다리는 중 · {elapsed:.0f}초 지남 "
                    f"(모델마다 최대 {int(js.get('timeout_sec', 120))}초, 실패하면 다음 모델로 넘어감)")
        for h in js.get("history", []):
            icon = "✅" if h["outcome"] == "성공" else "❌"
            st.markdown(f"{icon} {h['attempt']}차 모델 `{h['model']}`: {h['outcome']} ({h['elapsed']}초)"
                        + (f" — {h['detail']}" if h["detail"] else ""))
        if js.get("result") and not js.get("running"):
            st.markdown(f"**{js['result']}**")
        if v.plan_error:
            st.error("규칙을 지키는 배정이 없음")
            for r in v.plan_error:
                st.markdown(f"- {r}")
        elif v.plan and not v.confirmed:
            st.success(f"검사를 통과한 배정안이 있음(출처: {v.plan['source']}). 아래 표를 보고 확정하세요")
        elif v.confirmed:
            st.success("팀 구성을 확정함")
        elif not js:
            st.caption("모집을 마감한 뒤 AI 배정을 누르세요")


@st.fragment(run_every=REFRESH_SEC)
def admin_live(db: Database) -> None:
    try:
        v = service.load_view(db, None, with_logs=True)
    except Exception as e:
        st.warning(f"DB를 읽지 못함({type(e).__name__}). 잠시 뒤 자동으로 다시 시도함")
        return
    show_result()

    st.markdown("### 2. 모집 마감과 배정")
    state = "확정됨" if v.confirmed else ("마감됨" if v.closed else "모집 중")
    st.markdown(f"지금 상태: **{state}** · 학생 {len(v.students)}명 · 팀 {len(v.teams)}개")
    c1, c2, c3 = st.columns(3)
    c1.button("모집 마감", key="admin_close", on_click=do_close, args=(db,))
    c2.button("AI 배정", key="admin_assign", on_click=do_assign, args=(db,),
              disabled=jobs.is_running())
    if v.plan and not v.confirmed:
        c3.button("배정안 확정", key="admin_confirm", on_click=do_confirm, args=(db,), type="primary")
    assign_status_box(v)

    if v.plan and not v.confirmed:
        by_no = {s.student_no: s for s in v.students.values()}
        rows = []
        for no, t in sorted(v.plan["assignments"].items(), key=lambda x: (x[1], x[0])):
            s = by_no.get(no)
            if s:
                rows.append({"이름": s.name, "주 역할": s.main_role,
                             "보조 역할": ", ".join(s.sub_roles) or "없음", "배정된 팀": f"팀{t}"})
        st.markdown("**배정안(팀이 없는 학생만 넣음, 기존 팀원은 옮기지 않음)**")
        if rows:
            st.table(rows)

    with st.container(key="unassigned", border=True):
        un = v.unassigned
        st.markdown(f"**팀이 없는 학생 {len(un)}명**")
        if un:
            for s in un:
                st.markdown(f"- {label(s)}")
        else:
            st.caption("없음")

    st.markdown("### 팀 구성")
    pend_by_team: dict[int, list] = {}
    for p in v.pending:
        pend_by_team.setdefault(p["team_no"], []).append(p)
    cols = st.columns(2)
    for i, t in enumerate(v.teams):
        with cols[i % 2]:
            with st.container(key=f"team_{t['team_no']}", border=True):
                team_box(t, False, 0)
                for p in pend_by_team.get(t["team_no"], []):
                    yes = sum(1 for x in p["votes"].values() if x == "찬성")
                    st.caption(f"대기: {p['student'].name} (찬성 {yes}/{len(t['members'])}, {kst(p['created_at'])})")

    with st.expander("AI 호출 기록과 설정"):
        st.markdown("모델: " + " → ".join(f"{i}차 `{m}`" for i, m in enumerate(model_list(), 1)))
        st.markdown("설정 값: " + ", ".join(f"{k} {'있음' if secret(k) else '없음'}"
                                            for k in ("OPENROUTER_API_KEY", "DATABASE_URL", "ADMIN_PASSWORD")))
        if v.ai_logs:
            st.table([{"시각": kst(l["created_at"]), "차수": l["attempt"] or "앱", "모델": l["model"],
                       "결과": l["outcome"], "걸린 초": float(l["elapsed_sec"] or 0),
                       "내용": (l["detail"] or "")[:120]} for l in v.ai_logs])
        else:
            st.caption("기록 없음")
    st.caption(f"{REFRESH_SEC}초마다 자동으로 새로 보여 줌 · 마지막 갱신 {datetime.now(KST).strftime('%H:%M:%S')}")


# ---------------------------------------------------------------- 시작

def login_page(db: Database) -> None:
    st.title("👥 팀 프로젝트 팀 편성")
    st.caption("학생은 학번과 비밀번호(1234)로, 관리자는 학번 admin 으로 로그인")
    with st.form("login_form", border=True):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        st.form_submit_button("로그인", key="login_submit", type="primary", on_click=do_login, args=(db,))
    if st.session_state.get("login_error"):
        st.error(st.session_state["login_error"])


def main() -> None:
    try:
        db = get_db()
    except Exception as e:
        st.error(f"DB에 연결하지 못함({type(e).__name__}). DATABASE_URL 설정과 Supabase 프로젝트가 "
                 "일시정지되지 않았는지 확인하세요")
        st.stop()
    admin_pw = secret("ADMIN_PASSWORD")
    if not admin_pw:
        st.error("ADMIN_PASSWORD 없음. .env 또는 Streamlit Secrets에 넣으세요")
        st.stop()
    ensure_admin(db, hashlib.sha256(admin_pw.encode()).hexdigest())

    user = st.session_state.get("user")
    if not user:
        login_page(db)
        return
    c1, c2 = st.columns([5, 1])
    with c1:
        with st.container(key="whoami"):
            if user["is_admin"]:
                st.markdown(f"### 🛠️ {user['name']} (admin)")
            else:
                st.markdown(f"### 🙋 {user['name']} ({user['student_no']})")
    with c2:
        st.button("로그아웃", key="logout", on_click=logout)

    if user["is_admin"]:
        admin_static(db)
        admin_live(db)
    else:
        student_live(db)


main()
