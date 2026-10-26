"""팀 편성 규칙(공고 2.3~2.5절, 3장).

DB나 화면과 무관한 순수 함수만 둔다. 지원, 투표, 배정안 검사, 앱이 스스로
하는 배정(완전 탐색)이 모두 이 파일의 함수를 쓰므로, 규칙이 한 곳에만 있다.
학생 이름이나 학번을 보고 결과를 정하는 코드는 두지 않는다(감점 19번).
"""
from __future__ import annotations

from dataclasses import dataclass, field

PLANNER = "기획자"
WEB_PROGRAMMER = "웹 프로그래머"
ROLES = (PLANNER, WEB_PROGRAMMER, "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자")
REQUIRED_ROLES = (PLANNER, WEB_PROGRAMMER)
ROLE_ABBR = {
    PLANNER: "PL",
    WEB_PROGRAMMER: "WP",
    "프론트엔드 디자이너": "FD",
    "테스터": "TT",
    "보고서 작성자": "RW",
    "발표자": "PT",
}
MIN_TEAM = 2
MAX_TEAM = 4

# 결과 안내의 첫 단어(공고 2.7절)
APPROVED, PENDING, REJECTED, BLOCKED = "승인", "대기", "거절", "막힘"

# 막힘·거절 이유(공고 2.4절의 문구를 따름)
R_CLOSED = "모집이 마감됨"
R_HAS_TEAM = "이미 팀이 있음"
R_HAS_PENDING = "결과를 기다리는 지원이 있음"
R_FULL = "팀이 꽉 참"
R_SAME_MAIN = "같은 주 역할의 팀원이 있음"
R_REQUIRED_SLOT = "필수 포함 역할을 맡을 학생의 자리가 모자람"


@dataclass(frozen=True)
class Student:
    id: int
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple[str, ...] = field(default_factory=tuple)

    def can_take(self, role: str) -> bool:
        """주 역할이나 보조 역할로 그 역할을 맡을 수 있는가."""
        return self.main_role == role or role in self.sub_roles

    def roles_text(self) -> str:
        subs = ", ".join(self.sub_roles) if self.sub_roles else "없음"
        return f"주 {self.main_role} / 보조 {subs}"


# ---------------------------------------------------------------- 필수 포함 역할

def required_filled(members: list[Student]) -> int:
    """필수 포함 역할(기획자, 웹 프로그래머) 가운데 채운 수(0~2).

    두 역할은 서로 다른 학생이 맡아야 하므로, 둘 다 할 수 있는 학생 한 명만
    있으면 1개로 센다(공고 3장 "필수 포함 역할 자리 계산").
    """
    planners = [s.id for s in members if s.can_take(PLANNER)]
    programmers = [s.id for s in members if s.can_take(WEB_PROGRAMMER)]
    if planners and programmers and any(p != w for p in planners for w in programmers):
        return 2
    return 1 if (planners or programmers) else 0


def missing_required(members: list[Student]) -> list[str]:
    """채우지 못한 필수 포함 역할을 사람이 읽을 말로 돌려준다."""
    if required_filled(members) == 2:
        return []
    has_pl = any(s.can_take(PLANNER) for s in members)
    has_wp = any(s.can_take(WEB_PROGRAMMER) for s in members)
    if has_pl and has_wp:  # 한 학생이 둘 다 할 수 있지만 혼자뿐임
        return [f"{PLANNER}와 {WEB_PROGRAMMER}를 서로 다른 학생이 맡지 못함"]
    out = []
    if not has_pl:
        out.append(PLANNER)
    if not has_wp:
        out.append(WEB_PROGRAMMER)
    return out


def has_main_conflict(members: list[Student], student: Student) -> bool:
    return any(m.main_role == student.main_role for m in members)


def join_problem(members: list[Student], student: Student) -> str | None:
    """student가 이 팀에 들어갈 때 어기는 규칙. 없으면 None.

    지원할 때와, 모든 팀원이 찬성해 승인하기 직전(재검사)에 똑같이 쓴다.
    """
    if len(members) >= MAX_TEAM:
        return R_FULL
    if has_main_conflict(members, student):
        return R_SAME_MAIN
    after = members + [student]
    free_after = MAX_TEAM - len(after)
    unfilled_after = len(REQUIRED_ROLES) - required_filled(after)
    if free_after < unfilled_after:
        return R_REQUIRED_SLOT
    return None


# ---------------------------------------------------------------- 지원과 투표

@dataclass
class Decision:
    word: str          # 승인, 대기, 거절, 막힘
    reason: str = ""

    def message(self) -> str:
        return f"{self.word}: {self.reason}" if self.reason else self.word


def decide_apply(*, closed: bool, has_team: bool, has_pending: bool,
                 team_members: list[Student], applicant: Student, team_name: str) -> Decision:
    """지원 단추를 누른 순간의 판단(공고 2.4절)."""
    if closed:
        return Decision(BLOCKED, R_CLOSED)
    if has_team:
        return Decision(BLOCKED, R_HAS_TEAM)
    if has_pending:
        return Decision(BLOCKED, R_HAS_PENDING)
    problem = join_problem(team_members, applicant)
    if problem:
        return Decision(BLOCKED, problem)
    if not team_members:
        return Decision(APPROVED, f"{team_name}의 팀원이 됨(빈 팀이라 바로 승인)")
    return Decision(PENDING, f"{team_name} 팀원 {len(team_members)}명의 투표를 기다림")


def decide_after_vote(*, team_members: list[Student], votes: dict[int, str],
                      applicant: Student, team_name: str) -> Decision:
    """투표가 하나 더해진 뒤 지원의 상태(공고 2.4절).

    votes: 투표자 id -> '찬성' 또는 '거부'. 투표할 사람은 지금의 팀원이다.
    """
    member_ids = {m.id for m in team_members}
    for voter_id, v in votes.items():
        if voter_id in member_ids and v == "거부":
            voter = next(m for m in team_members if m.id == voter_id)
            return Decision(REJECTED, f"팀원 {voter.name}이(가) 거부함")
    yes = sum(1 for m in team_members if votes.get(m.id) == "찬성")
    if yes < len(team_members):
        return Decision(PENDING, f"찬성 {yes}/{len(team_members)}, 남은 팀원의 투표를 기다림")
    # 모든 팀원이 찬성: 승인 직전에 규칙을 다시 검사(3장의 어려운 해결)
    problem = join_problem(team_members, applicant)
    if problem:
        return Decision(REJECTED, f"승인 직전 검사에서 막힘({problem})")
    return Decision(APPROVED, f"{team_name}의 팀원이 됨")


# ---------------------------------------------------------------- 팀 구성 검사

def team_problems(team_name: str, members: list[Student]) -> list[str]:
    """확정할 팀 하나가 어기는 규칙(인원, 주 역할 중복, 필수 포함 역할)."""
    out = []
    if not (MIN_TEAM <= len(members) <= MAX_TEAM):
        out.append(f"{team_name}의 인원이 {len(members)}명임(2~4명이어야 함)")
    seen: dict[str, str] = {}
    for m in members:
        if m.main_role in seen:
            out.append(f"{team_name}에 주 역할 {m.main_role}이(가) 겹침({seen[m.main_role]}, {m.name})")
        else:
            seen[m.main_role] = m.name
    for role in missing_required(members):
        out.append(f"{team_name}에 {role}를 맡을 학생이 없음" if role in REQUIRED_ROLES
                   else f"{team_name}에서 {role}")
    return out


def validate_plan(teams: dict[int, list[Student]], team_names: dict[int, str],
                  unassigned: list[Student], pairs: list[tuple[str, int]]) -> list[str]:
    """배정안 검사 다섯 가지(공고 2.5절 4항). 문제가 없으면 빈 목록.

    pairs: (학번, 팀 번호) 목록. AI 응답을 그대로 넣어 중복도 잡아낸다.
    """
    errors: list[str] = []
    by_no = {s.student_no: s for s in unassigned}
    count: dict[str, int] = {}
    final = {t: list(ms) for t, ms in teams.items()}
    for no, team_no in pairs:
        count[no] = count.get(no, 0) + 1
        if no not in by_no:
            errors.append(f"배정 대상이 아닌 학번 {no}이(가) 들어 있음(이미 팀이 있거나 없는 학생)")
            continue
        if team_no not in final:
            errors.append(f"없는 팀 번호 {team_no}에 {by_no[no].name}을(를) 넣음")
            continue
        if count[no] == 1:
            final[team_no].append(by_no[no])
    # 1. 팀이 없는 학생이 빠짐없이 배정됨
    for s in unassigned:
        if s.student_no not in count:
            errors.append(f"{s.name}이(가) 배정되지 않음")
    # 2. 한 학생은 한 팀에만
    for no, c in count.items():
        if c > 1 and no in by_no:
            errors.append(f"{by_no[no].name}이(가) {c}번 배정됨")
    # 3~5. 인원, 주 역할 중복, 필수 포함 역할
    for t in sorted(final):
        errors.extend(team_problems(team_names.get(t, f"팀{t}"), final[t]))
    return errors


# ---------------------------------------------------------------- 앱이 스스로 하는 배정

def solve(teams: dict[int, list[Student]], unassigned: list[Student]) -> dict[str, int] | None:
    """검사 다섯 가지를 모두 통과하는 배정을 모든 경우를 따져 찾는다(3장의 어려운 해결).

    되돌아가기 탐색이며 가지치기를 해도 빠뜨리는 경우가 없다. 찾으면
    {학번: 팀 번호}, 어떤 배정도 규칙을 지킬 수 없으면 None.
    """
    team_nos = sorted(teams)
    state = {t: list(teams[t]) for t in team_nos}

    # 필수 포함 역할을 맡을 수 있는 학생을 먼저 놓으면 빨리 찾음(순서는 결과의 존재에 영향 없음)
    def key(s: Student):
        cap = sum(s.can_take(r) for r in REQUIRED_ROLES)
        return (-cap, s.student_no)

    order = sorted(unassigned, key=key)
    assign: dict[str, int] = {}

    def hopeless(idx: int) -> bool:
        remaining = order[idx:]
        need_min = sum(max(0, MIN_TEAM - len(state[t])) for t in team_nos)
        if need_min > len(remaining):
            return True
        free_total = sum(MAX_TEAM - len(state[t]) for t in team_nos)
        if free_total < len(remaining):
            return True
        for t in team_nos:
            ms = state[t]
            unfilled = len(REQUIRED_ROLES) - required_filled(ms)
            if unfilled > MAX_TEAM - len(ms):
                return True
            for role in REQUIRED_ROLES:
                if not any(m.can_take(role) for m in ms):
                    if not any(s.can_take(role) and not has_main_conflict(ms, s) for s in remaining):
                        return True
        return False

    def rec(idx: int) -> bool:
        if hopeless(idx):
            return False
        if idx == len(order):
            return all(not team_problems("", state[t]) for t in team_nos)
        s = order[idx]
        # 사람이 적은 팀부터 시도해 인원이 고르게 나오게 함
        for t in sorted(team_nos, key=lambda t: (len(state[t]), t)):
            ms = state[t]
            if len(ms) >= MAX_TEAM or has_main_conflict(ms, s):
                continue
            ms.append(s)
            assign[s.student_no] = t
            if rec(idx + 1):
                return True
            ms.pop()
            del assign[s.student_no]
        return False

    return dict(assign) if rec(0) else None


def diagnose(teams: dict[int, list[Student]], team_names: dict[int, str],
             unassigned: list[Student]) -> list[str]:
    """규칙을 지키는 배정이 없을 때 그 이유(공고 2.5절 8항)."""
    reasons: list[str] = []
    free_total = sum(MAX_TEAM - len(ms) for ms in teams.values())
    if len(unassigned) > free_total:
        reasons.append(f"팀이 없는 학생 {len(unassigned)}명이 남은 자리 {free_total}개보다 많음")
    need_min = sum(max(0, MIN_TEAM - len(ms)) for ms in teams.values())
    if need_min > len(unassigned):
        reasons.append(f"모든 팀을 2명 이상으로 채우려면 {need_min}명이 더 필요하지만 팀이 없는 학생은 {len(unassigned)}명임")
    for t in sorted(teams):
        ms, name = teams[t], team_names.get(t, f"팀{t}")
        for m in ms:
            if sum(1 for x in ms if x.main_role == m.main_role) > 1:
                reasons.append(f"{name}에 주 역할이 겹치는 팀원이 있음")
                break
        free = MAX_TEAM - len(ms)
        unfilled = len(REQUIRED_ROLES) - required_filled(ms)
        for role in REQUIRED_ROLES:
            if any(m.can_take(role) for m in ms):
                continue
            if free == 0:
                reasons.append(f"{name}이(가) 4명으로 찼는데 {role}를 맡을 학생이 없음")
            elif not any(s.can_take(role) and not has_main_conflict(ms, s) for s in unassigned):
                reasons.append(f"{name}에 {role}를 맡을 학생이 없음")
        if 0 < free < unfilled:
            reasons.append(f"{name}에 필수 포함 역할 {unfilled}개를 채울 자리가 {free}개뿐임")
    if not reasons:
        reasons.append("한 팀씩 보면 가능해 보이지만, 모든 경우를 따져 보아도 모든 팀이 규칙을 동시에 지키는 배정이 없음"
                       "(필수 포함 역할을 맡을 학생이 여러 팀에 동시에 필요함)")
    return reasons
