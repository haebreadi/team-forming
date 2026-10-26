"""DB에 기록하는 동작(명단 올리기, 로그인, 지원, 투표, 마감, 배정안 저장, 확정).

동작마다 트랜잭션 하나로 처리하고, 팀 행을 잠근 뒤(select ... for update)
규칙을 검사하므로 두 사람의 요청이 겹쳐도 규칙이 깨지지 않는다.
규칙 판단은 모두 rules.py 의 함수가 한다.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

from . import rules, security
from .db import is_unique_violation
from .rules import APPROVED, BLOCKED, PENDING, REJECTED, Decision, Student

ADMIN_NO = "admin"


# ---------------------------------------------------------------- 공통 읽기

def _students(cur, where: str = "true", params: tuple = ()) -> dict[int, Student]:
    cur.execute(
        "select s.id, s.student_no, s.name, s.main_role, "
        "coalesce(string_agg(r.role, ';' order by r.ord), '') as subs "
        "from tf_students s left join tf_student_sub_roles r on r.student_id = s.id "
        f"where not s.is_admin and ({where}) group by s.id order by s.student_no",
        params,
    )
    out = {}
    for r in cur.fetchall():
        subs = tuple(x for x in r["subs"].split(";") if x)
        out[r["id"]] = Student(r["id"], r["student_no"], r["name"], r["main_role"], subs)
    return out


def _team_members(cur, team_id: int) -> list[Student]:
    studs = _students(cur, "s.id in (select student_id from tf_members where team_id = %s)", (team_id,))
    cur.execute("select student_id from tf_members where team_id = %s order by slot_no", (team_id,))
    return [studs[r["student_id"]] for r in cur.fetchall()]


def _setting(cur, key: str, default: str = "") -> str:
    cur.execute("select value from tf_settings where key = %s", (key,))
    r = cur.fetchone()
    return r["value"] if r else default


def _set(cur, key: str, value: str) -> None:
    cur.execute(
        "insert into tf_settings (key, value) values (%s, %s) "
        "on conflict (key) do update set value = excluded.value",
        (key, value),
    )


def _is_closed(cur) -> bool:
    return _setting(cur, "closed", "false") == "true"


def _add_member(cur, team_id: int, student_id: int) -> None:
    """빈 자리 가운데 가장 앞 번호에 넣음. 팀 행을 잠근 뒤에만 부름."""
    cur.execute("select slot_no from tf_members where team_id = %s", (team_id,))
    used = {r["slot_no"] for r in cur.fetchall()}
    slot = next(n for n in range(1, rules.MAX_TEAM + 1) if n not in used)
    cur.execute("insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
                (team_id, slot, student_id))


# ---------------------------------------------------------------- 시작과 로그인

def ensure_schema(db, schema_sql: str) -> None:
    db.run_script(schema_sql)
    with db.tx() as cur:
        cur.execute("insert into tf_settings (key, value) values ('closed', 'false') on conflict (key) do nothing")


def ensure_admin(db, admin_password: str) -> None:
    """관리자 계정을 만들거나 비밀번호를 갱신함(공고 2.1절). 명단을 다시 올려도 남음."""
    with db.tx() as cur:
        cur.execute("select id, password_hash from tf_students where student_no = %s", (ADMIN_NO,))
        r = cur.fetchone()
        if r is None:
            cur.execute(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin) "
                "values (%s, %s, null, %s, true)",
                (ADMIN_NO, "관리자", security.hash_password(admin_password)),
            )
        elif not security.check_password(admin_password, r["password_hash"]):
            cur.execute("update tf_students set password_hash = %s, is_admin = true where id = %s",
                        (security.hash_password(admin_password), r["id"]))


def login(db, student_no: str, password: str) -> dict | None:
    student_no = (student_no or "").strip()
    if not student_no or not password:
        return None
    with db.tx() as cur:
        cur.execute("select id, student_no, name, password_hash, is_admin from tf_students where student_no = %s",
                    (student_no,))
        r = cur.fetchone()
    if r is None or not security.check_password(password, r["password_hash"]):
        return None
    return {"id": r["id"], "student_no": r["student_no"], "name": r["name"], "is_admin": bool(r["is_admin"])}


def user_exists(db, user_id: int) -> bool:
    with db.tx() as cur:
        cur.execute("select 1 from tf_students where id = %s", (user_id,))
        return cur.fetchone() is not None


# ---------------------------------------------------------------- 명단 올리기

class RosterError(ValueError):
    pass


def parse_roster(data: bytes) -> list[dict]:
    """명단 CSV(BOM이 붙은 UTF-8도 읽음, 공고 2.6절)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RosterError("CSV가 UTF-8이 아님")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise RosterError("CSV가 비어 있음")
    header = [h.strip().lstrip("﻿") for h in rows[0]]
    need = ["student_no", "name", "main_role", "sub_roles"]
    if header[:4] != need:
        raise RosterError("첫 줄이 student_no,name,main_role,sub_roles 가 아님")
    out, seen = [], set()
    for line_no, r in enumerate(rows[1:], start=2):
        if len(r) != 4:
            raise RosterError(f"{line_no}번째 줄의 열이 {len(r)}개임(4개여야 함)")
        no, name, main, subs_raw = (c.strip() for c in r)
        if not no or not name:
            raise RosterError(f"{line_no}번째 줄에 학번이나 이름이 없음")
        if no.lower() == ADMIN_NO:
            raise RosterError(f"{line_no}번째 줄: 학번 admin 은 쓸 수 없음")
        if no in seen:
            raise RosterError(f"학번 {no}이(가) 두 번 나옴")
        seen.add(no)
        if main not in rules.ROLES:
            raise RosterError(f"{line_no}번째 줄의 주 역할 '{main}'이(가) 6개 역할에 없음")
        subs = [s.strip() for s in subs_raw.split(";") if s.strip()]
        if len(subs) > 2:
            raise RosterError(f"{line_no}번째 줄의 보조 역할이 2개를 넘음")
        for s in subs:
            if s not in rules.ROLES:
                raise RosterError(f"{line_no}번째 줄의 보조 역할 '{s}'이(가) 6개 역할에 없음")
        if len(set(subs)) != len(subs) or main in subs:
            raise RosterError(f"{line_no}번째 줄의 역할이 겹침")
        out.append({"student_no": no, "name": name, "main_role": main, "sub_roles": subs})
    if not out:
        raise RosterError("CSV에 학생이 없음")
    return out


def reset_with_roster(db, data: bytes | None, team_count) -> Decision:
    """앱 상태를 초기화하고 명단의 학생과 빈 팀을 만듦(공고 2.6절). 관리자 계정은 남김."""
    if not data:
        return Decision(BLOCKED, "명단 CSV 파일을 올려야 함")
    try:
        team_count = int(team_count)
    except (TypeError, ValueError):
        return Decision(BLOCKED, "팀 수가 숫자가 아님")
    if not 1 <= team_count <= 50:
        return Decision(BLOCKED, "팀 수는 1~50이어야 함")
    try:
        rows = parse_roster(data)
    except RosterError as e:
        return Decision(BLOCKED, f"명단 CSV를 읽지 못함({e})")
    # 해시는 트랜잭션 밖에서 미리 만들어 잠그는 시간을 줄임. 학생 비밀번호는 모두 1234(공고 2.2절)
    hashes = [security.hash_password("1234") for _ in rows]
    with db.tx() as cur:
        cur.execute("delete from tf_votes")
        cur.execute("delete from tf_applications")
        cur.execute("delete from tf_members")
        cur.execute("delete from tf_teams")
        cur.execute("delete from tf_students where not is_admin")
        cur.execute("delete from tf_settings where key <> 'closed'")
        _set(cur, "closed", "false")
        for n in range(1, team_count + 1):
            cur.execute("insert into tf_teams (team_no, name) values (%s, %s)", (n, f"팀{n}"))
        for row, h in zip(rows, hashes):
            cur.execute(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin) "
                "values (%s, %s, %s, %s, false) returning id",
                (row["student_no"], row["name"], row["main_role"], h),
            )
            sid = cur.fetchone()["id"]
            for i, role in enumerate(row["sub_roles"], start=1):
                cur.execute("insert into tf_student_sub_roles (student_id, role, ord) values (%s, %s, %s)",
                            (sid, role, i))
    return Decision(APPROVED, f"명단 {len(rows)}명과 빈 팀 {team_count}개로 처음 상태를 만듦")


# ---------------------------------------------------------------- 지원과 투표

def apply(db, student_id: int, team_no: int) -> Decision:
    try:
        with db.tx() as cur:
            # 같은 학생의 요청이 겹치지 않게 학생 행을 먼저 잠금
            cur.execute("select id from tf_students where id = %s for update", (student_id,))
            if cur.fetchone() is None:
                return Decision(BLOCKED, "로그인한 학생이 명단에 없음(명단이 다시 올라감)")
            me = _students(cur, "s.id = %s", (student_id,))[student_id]
            closed = _is_closed(cur)
            cur.execute("select 1 from tf_members where student_id = %s", (student_id,))
            has_team = cur.fetchone() is not None
            cur.execute("select 1 from tf_applications where student_id = %s and status = '대기'", (student_id,))
            has_pending = cur.fetchone() is not None
            cur.execute("select id, name from tf_teams where team_no = %s for update", (team_no,))
            team = cur.fetchone()
            if team is None:
                return Decision(BLOCKED, "없는 팀임")
            members = _team_members(cur, team["id"])
            d = rules.decide_apply(closed=closed, has_team=has_team, has_pending=has_pending,
                                   team_members=members, applicant=me, team_name=team["name"])
            if d.word == BLOCKED:
                return d  # 막힌 지원은 접수하지 않으므로 기록하지 않음
            cur.execute(
                "insert into tf_applications (team_id, student_id, status, reason, decided_at) "
                "values (%s, %s, %s, %s, case when %s = '승인' then now() end)",
                (team["id"], student_id, d.word, d.reason, d.word),
            )
            if d.word == APPROVED:
                _add_member(cur, team["id"], student_id)
            return d
    except Exception as e:  # 유일성 제약: 같은 순간 다른 요청이 먼저 기록됨
        if is_unique_violation(e):
            return Decision(BLOCKED, "같은 순간 다른 요청이 먼저 처리됨. 화면을 보고 다시 시도")
        raise


def vote(db, voter_id: int, applicant_no: str, value: str) -> Decision:
    if value not in ("찬성", "거부"):
        return Decision(BLOCKED, "투표는 찬성이나 거부만 할 수 있음")
    try:
        with db.tx() as cur:
            if _is_closed(cur):
                return Decision(BLOCKED, R_CLOSED_VOTE)
            cur.execute(
                "select a.id, a.team_id, a.student_id from tf_applications a "
                "join tf_students s on s.id = a.student_id "
                "where s.student_no = %s order by a.id desc limit 1",
                (applicant_no,),
            )
            app = cur.fetchone()
            if app is None:
                return Decision(BLOCKED, "투표할 지원이 없음")
            # 팀 행을 잠근 뒤 지원의 상태를 다시 읽음(다른 투표와 겹치지 않게)
            cur.execute("select id, name from tf_teams where id = %s for update", (app["team_id"],))
            team = cur.fetchone()
            cur.execute("select status, reason from tf_applications where id = %s for update", (app["id"],))
            cur_app = cur.fetchone()
            applicant = _students(cur, "s.id = %s", (app["student_id"],))[app["student_id"]]
            who = applicant.name
            if cur_app["status"] != PENDING:
                return Decision(cur_app["status"], f"{who}의 지원은 이미 결과가 나옴({cur_app['reason'] or ''})")
            members = _team_members(cur, team["id"])
            if voter_id not in {m.id for m in members}:
                return Decision(BLOCKED, f"{team['name']}의 팀원만 투표할 수 있음")
            cur.execute("select vote from tf_votes where application_id = %s and voter_id = %s",
                        (app["id"], voter_id))
            prev = cur.fetchone()
            if prev:
                return Decision(PENDING, f"이미 투표함(내 투표: {prev['vote']}). 투표는 바꿀 수 없음")
            cur.execute("insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
                        (app["id"], voter_id, value))
            cur.execute("select voter_id, vote from tf_votes where application_id = %s", (app["id"],))
            votes = {r["voter_id"]: r["vote"] for r in cur.fetchall()}
            d = rules.decide_after_vote(team_members=members, votes=votes, applicant=applicant,
                                        team_name=team["name"])
            if d.word in (APPROVED, REJECTED):
                cur.execute("update tf_applications set status = %s, reason = %s, decided_at = now() where id = %s",
                            (d.word, d.reason, app["id"]))
            if d.word == APPROVED:
                _add_member(cur, team["id"], applicant.id)
            return Decision(d.word, f"{who}의 지원: {d.reason}")
    except Exception as e:
        if is_unique_violation(e):
            return Decision(BLOCKED, "같은 순간 다른 요청이 먼저 처리됨. 화면을 보고 다시 시도")
        raise


R_CLOSED_VOTE = "모집이 마감되어 투표할 수 없음"


# ---------------------------------------------------------------- 마감

def close_recruiting(db) -> Decision:
    with db.tx() as cur:
        cur.execute("select value from tf_settings where key = 'closed' for update")
        r = cur.fetchone()
        if r and r["value"] == "true":
            return Decision(BLOCKED, "이미 마감됨")
        cur.execute("select count(*) as n from tf_teams")
        if cur.fetchone()["n"] == 0:
            return Decision(BLOCKED, "명단을 먼저 올려야 함")
        _set(cur, "closed", "true")
        cur.execute(
            "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now() "
            "where status = '대기' returning id"
        )
        rejected = len(cur.fetchall())
        cur.execute(
            "delete from tf_members where team_id in "
            "(select team_id from tf_members group by team_id having count(*) = 1) returning student_id"
        )
        removed = len(cur.fetchall())
        cur.execute("delete from tf_settings where key in ('plan', 'plan_error')")
    return Decision(APPROVED, f"모집을 마감함(대기 지원 {rejected}건 거절, 1명뿐인 팀에서 {removed}명 빠짐)")


# ---------------------------------------------------------------- 배정

@dataclass
class AssignContext:
    closed: bool
    confirmed: bool
    teams: dict[int, list[Student]]        # 팀 번호 -> 팀원
    team_names: dict[int, str]
    unassigned: list[Student]


def _assign_context(cur) -> AssignContext:
    closed = _is_closed(cur)
    confirmed = _setting(cur, "confirmed", "false") == "true"
    studs = _students(cur)
    cur.execute("select id, team_no, name from tf_teams order by team_no")
    teams_rows = cur.fetchall()
    cur.execute("select team_id, student_id from tf_members order by team_id, slot_no")
    mem = cur.fetchall()
    by_id = {t["id"]: t["team_no"] for t in teams_rows}
    teams = {t["team_no"]: [] for t in teams_rows}
    names = {t["team_no"]: t["name"] for t in teams_rows}
    in_team = set()
    for m in mem:
        teams[by_id[m["team_id"]]].append(studs[m["student_id"]])
        in_team.add(m["student_id"])
    unassigned = [s for sid, s in studs.items() if sid not in in_team]
    return AssignContext(closed, confirmed, teams, names, unassigned)


def assign_context(db) -> AssignContext:
    with db.tx() as cur:
        return _assign_context(cur)


def save_plan(db, assignments: dict[str, int], source: str) -> None:
    payload = json.dumps({"assignments": assignments, "source": source}, ensure_ascii=False)
    with db.tx() as cur:
        _set(cur, "plan", payload)
        cur.execute("delete from tf_settings where key = 'plan_error'")


def save_plan_error(db, reasons: list[str]) -> None:
    with db.tx() as cur:
        _set(cur, "plan_error", json.dumps(reasons, ensure_ascii=False))
        cur.execute("delete from tf_settings where key = 'plan'")


def clear_plan(db) -> None:
    with db.tx() as cur:
        cur.execute("delete from tf_settings where key in ('plan', 'plan_error')")


def confirm_plan(db) -> Decision:
    """검사를 통과한 배정안을 DB에 저장함. 저장 직전에 지금의 DB로 다시 검사함."""
    try:
        with db.tx() as cur:
            cur.execute("select value from tf_settings where key = 'closed' for update")
            r = cur.fetchone()
            if not r or r["value"] != "true":
                return Decision(BLOCKED, "모집을 먼저 마감해야 함")
            cur.execute("select id, team_no from tf_teams order by team_no for update")
            team_id = {t["team_no"]: t["id"] for t in cur.fetchall()}
            ctx = _assign_context(cur)
            if ctx.confirmed:
                return Decision(BLOCKED, "이미 확정함")
            raw = _setting(cur, "plan", "")
            if not raw:
                return Decision(BLOCKED, "확정할 배정안이 없음(AI 배정을 먼저 해야 함)")
            plan = json.loads(raw)["assignments"]
            pairs = [(no, int(t)) for no, t in plan.items()]
            errors = rules.validate_plan(ctx.teams, ctx.team_names, ctx.unassigned, pairs)
            if errors:
                return Decision(BLOCKED, "배정안이 지금 상태와 맞지 않음: " + "; ".join(errors[:3]))
            by_no = {s.student_no: s for s in ctx.unassigned}
            for no, t in plan.items():
                _add_member(cur, team_id[int(t)], by_no[no].id)
            _set(cur, "confirmed", "true")
            cur.execute("delete from tf_settings where key = 'plan'")
    except Exception as e:
        if is_unique_violation(e):
            return Decision(BLOCKED, "다른 요청과 겹쳐 확정하지 못함. 다시 배정해야 함")
        raise
    return Decision(APPROVED, f"배정안을 확정함({len(plan)}명 배정)")


def log_ai(db, attempt: int, model: str, outcome: str, detail: str, elapsed: float) -> None:
    try:
        with db.tx() as cur:
            cur.execute(
                "insert into tf_ai_logs (attempt, model, outcome, detail, elapsed_sec) values (%s, %s, %s, %s, %s)",
                (attempt, model, outcome, (detail or "")[:2000], round(elapsed, 1)),
            )
    except Exception:
        pass  # 기록 실패가 배정을 막지 않게 함


# ---------------------------------------------------------------- 화면용 읽기

@dataclass
class View:
    closed: bool
    confirmed: bool
    students: dict[int, Student]
    teams: list[dict]                       # {id, team_no, name, members: [Student]}
    team_of: dict[int, int]                 # 학생 id -> 팀 번호
    pending: list[dict]                     # {id, team_no, student, votes: {voter_id: vote}, created_at}
    my_last: dict | None                    # 내 마지막 지원
    plan: dict | None
    plan_error: list[str] | None
    ai_logs: list[dict] = field(default_factory=list)

    @property
    def unassigned(self) -> list[Student]:
        return [s for sid, s in self.students.items() if sid not in self.team_of]


def load_view(db, me_id: int | None, with_logs: bool = False) -> View:
    with db.tx() as cur:
        cur.execute("select key, value from tf_settings")
        st = {r["key"]: r["value"] for r in cur.fetchall()}
        studs = _students(cur)
        cur.execute("select id, team_no, name from tf_teams order by team_no")
        teams = [dict(r, members=[]) for r in cur.fetchall()]
        cur.execute("select team_id, student_id from tf_members order by team_id, slot_no")
        mem = cur.fetchall()
        cur.execute(
            "select id, team_id, student_id, status, reason, created_at from tf_applications "
            "where status = '대기' or id = (select max(id) from tf_applications where student_id = %s) "
            "order by id",
            (me_id or -1,),
        )
        apps = cur.fetchall()
        cur.execute(
            "select v.application_id, v.voter_id, v.vote from tf_votes v "
            "join tf_applications a on a.id = v.application_id where a.status = '대기'"
        )
        votes = cur.fetchall()
        logs = []
        if with_logs:
            cur.execute("select created_at, attempt, model, outcome, detail, elapsed_sec "
                        "from tf_ai_logs order by id desc limit 12")
            logs = cur.fetchall()
    by_id = {t["id"]: t for t in teams}
    team_of = {}
    for m in mem:
        t = by_id[m["team_id"]]
        t["members"].append(studs[m["student_id"]])
        team_of[m["student_id"]] = t["team_no"]
    vmap: dict[int, dict[int, str]] = {}
    for v in votes:
        vmap.setdefault(v["application_id"], {})[v["voter_id"]] = v["vote"]
    pending, my_last = [], None
    for a in apps:
        item = {"id": a["id"], "team_no": by_id[a["team_id"]]["team_no"], "team_name": by_id[a["team_id"]]["name"],
                "student": studs.get(a["student_id"]), "status": a["status"], "reason": a["reason"],
                "votes": vmap.get(a["id"], {}), "created_at": a["created_at"]}
        if a["status"] == PENDING:
            pending.append(item)
        if a["student_id"] == me_id:
            my_last = item
    plan = json.loads(st["plan"]) if st.get("plan") else None
    plan_error = json.loads(st["plan_error"]) if st.get("plan_error") else None
    return View(st.get("closed") == "true", st.get("confirmed") == "true", studs, teams, team_of,
                pending, my_last, plan, plan_error, logs)
