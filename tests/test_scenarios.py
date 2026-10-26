"""연습용 시나리오 3개(공고 부록)를 실제 PostgreSQL에서 단계별로 실행해 기대 결과와 비교함.

시험 코드이므로 시나리오의 학번을 쓴다(앱 코드에는 학번이나 이름을 보고 정하는 코드가 없음).
실행: python3 tests/test_scenarios.py  (환경 변수 TF_TEST_PSQL 에 psql 접속 인자)
"""
from __future__ import annotations

import hashlib
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from psqldb import PsqlDB  # noqa: E402
from tf import rules, security, service  # noqa: E402

# 이 환경에는 bcrypt가 없어 시험에서만 대신하는 해시를 씀(앱은 bcrypt)
security.hash_password = lambda p: "test$" + hashlib.sha256(p.encode()).hexdigest()
security.check_password = lambda p, h: h == "test$" + hashlib.sha256(p.encode()).hexdigest()

CSV_DIR = Path(os.environ.get("TF_CSV_DIR", ROOT.parent))
PSQL_ARGS = shlex.split(os.environ.get("TF_TEST_PSQL", "-h /tmp/pgtest -p 55432 -U postgres -d tftest"))


def fresh_db() -> PsqlDB:
    db = PsqlDB(*PSQL_ARGS)
    db.run_script("drop table if exists tf_votes, tf_applications, tf_members, tf_teams, "
                  "tf_student_sub_roles, tf_students, tf_settings, tf_ai_logs cascade")
    service.ensure_schema(db, (ROOT / "schema.sql").read_text(encoding="utf-8"))
    service.ensure_admin(db, "admin-pass-for-test")
    return db


class Runner:
    def __init__(self, db, csv_name: str, teams: int):
        self.db = db
        data = (CSV_DIR / csv_name).read_bytes()
        d = service.reset_with_roster(db, data, teams)
        assert d.word == "승인", d.message()
        self.ids = {}
        self.fails = 0
        self.log = []

    def uid(self, no: str) -> int:
        if no not in self.ids:
            u = service.login(self.db, no, "1234")
            assert u, f"로그인 실패 {no}"
            self.ids[no] = u["id"]
        return self.ids[no]

    def check(self, label: str, d, expect: str):
        ok = d.word == expect
        self.fails += not ok
        self.log.append(f"{'OK ' if ok else 'XX '} {label:<42} 기대 {expect:<3} 실제 {d.message()}")

    def apply(self, no, team, expect):
        self.check(f"{no} -> 팀{team} 지원", service.apply(self.db, self.uid(no), team), expect)

    def vote(self, voter, applicant, value, expect):
        self.check(f"{voter}이(가) {applicant}에 {value}",
                   service.vote(self.db, self.uid(voter), applicant, value), expect)

    def close(self):
        self.check("관리자 마감", service.close_recruiting(self.db), "승인")

    def unassigned_nos(self):
        return sorted(s.student_no for s in service.assign_context(self.db).unassigned)

    def assign_and_confirm(self):
        ctx = service.assign_context(self.db)
        plan = rules.solve(ctx.teams, ctx.unassigned)
        assert plan is not None, "규칙을 지키는 배정이 없다고 나옴"
        errs = rules.validate_plan(ctx.teams, ctx.team_names, ctx.unassigned, list(plan.items()))
        assert not errs, errs
        service.save_plan(self.db, plan, "시험: 앱이 스스로 배정")
        self.check("관리자 확정", service.confirm_plan(self.db), "승인")
        final = service.assign_context(self.db)
        problems = [p for t, ms in final.teams.items() for p in rules.team_problems(f"팀{t}", ms)]
        if final.unassigned or problems:
            self.fails += 1
            self.log.append(f"XX  확정 뒤 검사: 남은 학생 {len(final.unassigned)}명, 문제 {problems}")
        else:
            sizes = {t: len(ms) for t, ms in final.teams.items()}
            self.log.append(f"OK  확정 뒤 검사: 모든 학생 배정, 모든 팀 규칙 지킴 {sizes}")

    def report(self, title):
        print(f"\n== {title}")
        print("\n".join(self.log))
        return self.fails


def scenario_easy(db):
    r = Runner(db, "쉬움.csv", 3)
    r.apply("2026101", 1, "승인"); r.apply("2026102", 2, "승인"); r.apply("2026103", 3, "승인")
    r.apply("2026104", 1, "대기"); r.vote("2026101", "2026104", "찬성", "승인")
    r.apply("2026105", 2, "대기"); r.vote("2026102", "2026105", "찬성", "승인")
    r.apply("2026107", 1, "대기"); r.vote("2026101", "2026107", "찬성", "대기")
    r.vote("2026104", "2026107", "거부", "거절")
    r.apply("2026106", 3, "대기"); r.vote("2026103", "2026106", "찬성", "승인")
    r.close()
    r.assign_and_confirm()
    return r.report("쉬움")


def scenario_middle(db):
    r = Runner(db, "중간.csv", 4)
    r.apply("2026201", 1, "승인")
    r.apply("2026205", 1, "대기"); r.vote("2026201", "2026205", "찬성", "승인")
    r.apply("2026202", 1, "막힘")  # 주 역할 중복 → 뒤의 투표는 건너뜀
    r.apply("2026206", 2, "승인")
    r.apply("2026209", 2, "대기"); r.vote("2026206", "2026209", "찬성", "승인")
    r.apply("2026210", 2, "대기"); r.vote("2026206", "2026210", "찬성", "대기")
    r.vote("2026209", "2026210", "거부", "거절")
    r.apply("2026203", 3, "승인"); r.apply("2026211", 4, "승인")
    r.apply("2026212", 4, "대기"); r.apply("2026212", 3, "막힘")
    r.close()
    v = service.load_view(db, r.uid("2026212"))
    r.check("마감 뒤 2026212의 지원", rules.Decision(v.my_last["status"]), "거절")
    un = r.unassigned_nos()
    ok = "2026203" in un and "2026211" in un
    r.fails += not ok
    r.log.append(f"{'OK ' if ok else 'XX '} 마감 뒤 2026203, 2026211이 팀에서 빠짐: 팀 없는 학생 {un}")
    r.apply("2026202", 3, "막힘")
    r.assign_and_confirm()
    return r.report("중간")


def scenario_hard(db):
    r = Runner(db, "어려움.csv", 4)
    r.apply("2026301", 1, "승인")
    r.apply("2026304", 1, "대기"); r.vote("2026301", "2026304", "찬성", "승인")
    r.apply("2026307", 1, "대기"); r.vote("2026301", "2026307", "찬성", "대기")
    r.vote("2026304", "2026307", "찬성", "승인")
    r.apply("2026302", 2, "승인")
    r.apply("2026308", 2, "대기"); r.vote("2026302", "2026308", "찬성", "승인")
    r.apply("2026305", 2, "대기"); r.apply("2026306", 2, "대기")
    r.vote("2026302", "2026305", "찬성", "대기"); r.vote("2026308", "2026305", "찬성", "승인")
    r.vote("2026302", "2026306", "찬성", "대기"); r.vote("2026308", "2026306", "찬성", "대기")
    r.vote("2026305", "2026306", "찬성", "거절")  # 승인 직전 검사
    r.apply("2026311", 3, "승인")
    r.apply("2026315", 3, "대기"); r.vote("2026311", "2026315", "찬성", "승인")
    r.apply("2026314", 3, "막힘")  # 필수 포함 역할 자리
    r.apply("2026310", 4, "승인")
    r.apply("2026313", 1, "대기"); r.vote("2026301", "2026313", "찬성", "대기")
    r.close()
    v = service.load_view(db, r.uid("2026313"))
    r.check("마감 뒤 2026313의 지원", rules.Decision(v.my_last["status"]), "거절")
    un = r.unassigned_nos()
    ok = "2026310" in un
    r.fails += not ok
    r.log.append(f"{'OK ' if ok else 'XX '} 마감 뒤 2026310이 팀에서 빠짐: 팀 없는 학생 {un}")
    r.assign_and_confirm()
    return r.report("어려움")


def extra_rules(db):
    """시나리오에 없는 규칙: 투표 바꾸기, 팀원이 아닌 사람의 투표, 꽉 찬 팀, 마감 뒤 투표, 학생 관리자 기능 없음."""
    r = Runner(db, "쉬움.csv", 3)
    r.apply("2026101", 1, "승인")
    r.apply("2026104", 1, "대기")
    r.vote("2026102", "2026104", "찬성", "막힘")          # 팀원이 아님
    r.apply("2026104", 2, "막힘")                         # 결과를 기다리는 지원이 있음
    r.vote("2026101", "2026104", "찬성", "승인")
    r.vote("2026101", "2026104", "거부", "승인")          # 이미 결과가 나온 지원(바뀌지 않음)
    r.apply("2026104", 2, "막힘")                         # 이미 팀이 있음
    r.apply("2026107", 1, "대기"); r.vote("2026101", "2026107", "찬성", "대기")
    r.vote("2026101", "2026107", "거부", "대기")          # 투표를 바꿀 수 없음
    r.vote("2026104", "2026107", "찬성", "승인")
    r.apply("2026108", 1, "대기"); r.vote("2026101", "2026108", "찬성", "대기")
    r.vote("2026104", "2026108", "찬성", "대기"); r.vote("2026107", "2026108", "찬성", "승인")
    r.apply("2026102", 1, "막힘")                         # 꽉 찬 팀(4명)
    r.apply("2026102", 2, "승인")
    r.apply("2026103", 2, "막힘")                         # 같은 주 역할(기획자)
    r.apply("2026106", 2, "대기")
    r.close()
    r.check("두 번째 마감", service.close_recruiting(db), "막힘")
    r.vote("2026102", "2026106", "찬성", "막힘")          # 마감 뒤 투표
    r.apply("2026105", 3, "막힘")                         # 마감 뒤 지원
    r.check("마감 전 확정 시도는 막힘(배정안 없음)", service.confirm_plan(db), "막힘")
    bad = service.reset_with_roster(db, "student_no,name\n1,a\n".encode(), 3)
    r.check("잘못된 CSV", bad, "막힘")
    bom = service.reset_with_roster(db, "﻿student_no,name,main_role,sub_roles\n9001,가,기획자,\n9002,나,테스터,기획자;발표자\n".encode("utf-8"), 2)
    r.check("BOM이 붙은 CSV", bom, "승인")
    adm = service.login(db, "admin", "admin-pass-for-test")
    ok = bool(adm and adm["is_admin"]) and service.login(db, "admin", "1234") is None
    r.fails += not ok
    r.log.append(f"{'OK ' if ok else 'XX '} 명단을 다시 올려도 관리자 계정이 남고, 틀린 비밀번호는 거부")
    ok = service.login(db, "9001", "9999") is None and service.login(db, "9001", "1234") is not None
    r.fails += not ok
    r.log.append(f"{'OK ' if ok else 'XX '} 학생 비밀번호 1234만 로그인됨")
    return r.report("시나리오 밖 규칙")


def db_constraints(db):
    """schema.sql 의 유일성 제약이 실제로 막는지(마지막 자리에 두 명, 한 학생 두 팀, 다섯 번째 자리)."""
    service.reset_with_roster(db, (CSV_DIR / "쉬움.csv").read_bytes(), 3)
    r = Runner.__new__(Runner); r.db = db; r.ids = {}; r.fails = 0; r.log = []
    a, b, c = r.uid("2026101"), r.uid("2026102"), r.uid("2026103")
    with db.tx() as cur:
        cur.execute("select id from tf_teams where team_no = 1")
        t1 = cur.fetchone()["id"]
        cur.execute("select id from tf_teams where team_no = 2")
        t2 = cur.fetchone()["id"]
        cur.execute("insert into tf_members (team_id, slot_no, student_id) values (%s, 4, %s)", (t1, a))
    cases = [
        ("같은 자리(4번)에 두 번째 학생", "insert into tf_members (team_id, slot_no, student_id) values (%s, 4, %s)", (t1, b), "23505"),
        ("한 학생을 두 팀에", "insert into tf_members (team_id, slot_no, student_id) values (%s, 1, %s)", (t2, a), "23505"),
        ("다섯 번째 자리", "insert into tf_members (team_id, slot_no, student_id) values (%s, 5, %s)", (t1, c), "23514"),
        ("6개 밖의 역할", "update tf_students set main_role = '디자이너' where id = %s", (c,), "23514"),
    ]
    for label, sql, params, code in cases:
        try:
            with db.tx() as cur:
                cur.execute(sql, params)
            got = "통과됨"
        except Exception as e:
            got = getattr(e, "sqlstate", "?")
        ok = got == code
        r.fails += not ok
        r.log.append(f"{'OK ' if ok else 'XX '} {label}: 기대 {code} 실제 {got}")
    return r.report("DB 제약")


if __name__ == "__main__":
    db = fresh_db()
    total = 0
    for fn in (scenario_easy, scenario_middle, scenario_hard, extra_rules, db_constraints):
        total += fn(db)
    print(f"\n실패 {total}건")
    sys.exit(1 if total else 0)
