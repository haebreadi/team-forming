"""규칙 함수와 배정 탐색기(앱이 스스로 하는 배정)의 시험. 실행: python3 tests/test_rules.py"""
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tf.rules import (PLANNER, ROLES, WEB_PROGRAMMER, Student, diagnose, join_problem,  # noqa: E402
                      required_filled, solve, team_problems, validate_plan)

n = 0
def S(main, *subs):
    global n
    n += 1
    return Student(n, str(1000 + n), f"s{n}", main, tuple(subs))

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("OK " if ok else "XX ") + f"{label}: {got!r}" + ("" if ok else f" (기대 {want!r})"))

# 공고 3장의 두 예
check("기획자;웹프 혼자 있는 팀은 필수 역할 1개", required_filled([S(PLANNER, WEB_PROGRAMMER)]), 1)
check("테스터+발표자 팀에 보고서 작성자", join_problem([S("테스터"), S("발표자")], S("보고서 작성자")),
      "필수 포함 역할을 맡을 학생의 자리가 모자람")
check("테스터+발표자 팀에 보조 웹프인 FD", join_problem([S("테스터"), S("발표자")], S("프론트엔드 디자이너", WEB_PROGRAMMER)), None)

# 배정 불가: 팀1(기획자, 테스터)에 웹프가 필요한데 남은 학생 중 웹프 가능자가 없음
teams = {1: [S(PLANNER), S("테스터")], 2: [S(PLANNER), S(WEB_PROGRAMMER)]}
un = [S("발표자"), S("보고서 작성자")]
check("배정 불가면 None", solve(teams, un), None)
check("이유", diagnose(teams, {1: "팀1", 2: "팀2"}, un), ["팀1에 웹 프로그래머를 맡을 학생이 없음"])

# 배정안 검사: 빠짐, 중복, 대상 아님
teams = {1: [S(PLANNER), S(WEB_PROGRAMMER)], 2: [S(PLANNER), S(WEB_PROGRAMMER)]}
a, b = S("테스터"), S("발표자")
errs = validate_plan(teams, {1: "팀1", 2: "팀2"}, [a, b], [(a.student_no, 1), (a.student_no, 2), ("9999", 1)])
check("검사 오류 수", len([e for e in errs if "배정되지" in e or "2번" in e or "대상이 아닌" in e]), 3)

# 무작위 비교: 탐색기가 찾은 답은 늘 검사를 통과하고, 못 찾으면 정말로 답이 없음(전수 비교)
random.seed(7)
for trial in range(300):
    n = 0
    k = random.choice([3, 4])
    teams = {}
    for t in range(1, k + 1):
        size = random.choice([0, 0, 2, 2, 3, 4])
        mains = random.sample(ROLES, size)
        teams[t] = [S(m, *random.sample([r for r in ROLES if r != m], random.choice([0, 0, 1, 2]))) for m in mains]
    un = [S(m, *random.sample([r for r in ROLES if r != m], random.choice([0, 1, 2])))
          for m in random.choices(ROLES, k=random.randint(0, 6))]
    names = {t: f"팀{t}" for t in teams}
    got = solve(teams, un)
    brute = any(not validate_plan(teams, names, un, [(s.student_no, t) for s, t in zip(un, combo)])
                for combo in itertools.product(list(teams), repeat=len(un)))
    if got is not None:
        if validate_plan(teams, names, un, list(got.items())):
            fails += 1; print("XX 탐색기 답이 검사를 통과하지 못함", trial)
    if (got is not None) != brute:
        fails += 1; print("XX 탐색기와 전수 비교가 다름", trial)
print("무작위 300회 비교 끝")
print(f"실패 {fails}건")
sys.exit(1 if fails else 0)
