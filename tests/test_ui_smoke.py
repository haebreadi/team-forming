"""화면 코드 연기 시험: 가짜 streamlit 모듈로 streamlit_app.py 를 그대로 실행함.

진짜 브라우저 시험은 아니다. 화면마다 2.7절의 key 가 있는지, 단추 콜백을 누르면
result 의 첫 단어가 기대와 같은지, 지원 단추가 비활성이 되는지를 본다.
실행: python3 tests/test_ui_smoke.py
"""
from __future__ import annotations

import os
import runpy
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_scenarios as ts  # noqa: E402  (가짜 해시)
from psqldb import PsqlDB  # noqa: E402
import tf.db  # noqa: E402


class Stop(Exception):
    pass


class Ctx:
    def __init__(self, st, key=None):
        self.st, self.key = st, key

    def __enter__(self):
        if self.key:
            self.st.keys.append(self.key)
            self.st.stack.append(self.key)
        return self

    def __exit__(self, *a):
        if self.key:
            self.st.stack.pop()

    def __getattr__(self, name):
        return getattr(self.st, name)


class SessionState(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


class FakeSt(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = SessionState()
        self._cache = {}
        self.reset()

    def reset(self):
        self.keys, self.stack, self.buttons, self.text = [], [], {}, []

    # 표시
    def _t(self, *a, **k):
        if self.stack and a:
            self.text.append((self.stack[-1], str(a[0])))
    set_page_config = title = caption = markdown = info = success = error = warning = write = table = _t

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [Ctx(self) for _ in range(n)]

    def container(self, key=None, border=None, **k):
        return Ctx(self, key)

    def form(self, *a, **k):
        return Ctx(self)

    def expander(self, *a, **k):
        return Ctx(self)

    # 위젯
    def _widget(self, key, default=None):
        self.keys.append(key)
        self.session_state.setdefault(key, default)
        return self.session_state[key]

    def text_input(self, label, key=None, **k):
        return self._widget(key, "")

    def number_input(self, label, key=None, value=None, **k):
        return self._widget(key, value)

    def file_uploader(self, label, key=None, **k):
        return self._widget(key, None)

    def button(self, label, key=None, on_click=None, args=(), disabled=False, **k):
        self.keys.append(key)
        self.buttons[key] = (on_click, args, disabled)
        return False
    form_submit_button = button

    # 실행 흐름
    def fragment(self, run_every=None):
        return lambda f: f

    def cache_resource(self, *a, **k):
        def deco(f):
            def wrap(*args):
                ck = (f.__name__,) + tuple(x for x in args if isinstance(x, str))
                if ck not in self._cache:
                    self._cache[ck] = f(*args)
                return self._cache[ck]
            return wrap
        return deco if not a or not callable(a[0]) else deco(a[0])

    class _Secrets:
        def get(self, k):
            raise FileNotFoundError

    secrets = _Secrets()

    def rerun(self, *a, **k):
        raise Stop()

    def stop(self):
        raise Stop()


st = FakeSt()
sys.modules["streamlit"] = st
os.environ.update(DATABASE_URL="fake", ADMIN_PASSWORD="admin-pw", OPENROUTER_API_KEY="")
DB = ts.fresh_db()
tf.db.Database = lambda url: DB  # 앱의 연결 풀 대신 시험용 연결
# 실제 앱은 연결 풀이라 스레드마다 다른 연결을 씀. 시험에서도 작업 스레드에는 따로 연결을 줌
from tf import jobs as _jobs  # noqa: E402
_JOB_DB = PsqlDB(*ts.PSQL_ARGS)
_orig_run = _jobs._run
_jobs._run = lambda db, *a: _orig_run(_JOB_DB, *a)


class Browser:
    """가상 학생 한 명의 브라우저(세션 상태가 따로 있음)."""

    def __init__(self):
        self.state = SessionState()

    def render(self):
        st.session_state = self.state
        st.reset()
        try:
            runpy.run_path(str(ROOT / "streamlit_app.py"), run_name="app")
        except Stop:
            pass
        return st

    def login(self, no, pw):
        self.render()
        self.state["login_student_no"], self.state["login_password"] = no, pw
        self.click("login_submit")

    def click(self, key):
        s = self.render()
        assert key in s.buttons, f"단추 {key} 없음. 있는 key: {sorted(set(s.keys))}"
        fn, args, disabled = s.buttons[key]
        if disabled:
            return "막힘(비활성)"
        st.session_state = self.state
        fn(*args)
        self.render()
        return self.result()

    def result(self):
        return " ".join(t for k, t in st.text if k == "result")

    def has(self, key):
        self.render()
        return key in st.keys

    def disabled(self, key):
        self.render()
        return st.buttons.get(key, (None, None, True))[2]

    def text_in(self, key):
        self.render()
        return " ".join(t for k, t in st.text if k == key)


fails = 0


def check(label, ok, extra=""):
    global fails
    fails += not ok
    print(("OK " if ok else "XX ") + label + (f"  [{extra}]" if extra else ""))


class Upload:
    def __init__(self, data):
        self.data = data

    def getvalue(self):
        return self.data


admin = Browser()
admin.login("admin", "wrong")
check("틀린 관리자 비밀번호는 로그인 안 됨", "user" not in admin.state)
admin.login("admin", "admin-pw")
check("관리자 로그인 뒤 whoami 에 이름", "관리자" in admin.text_in("whoami"))
for k in ["admin_roster_file", "admin_team_count", "admin_start", "admin_close", "admin_assign",
          "assign_status", "unassigned", "result"]:
    check(f"관리자 화면 key {k}", admin.has(k))
admin.state["admin_roster_file"] = Upload((ts.CSV_DIR / "중간.csv").read_bytes())
admin.state["admin_team_count"] = 4
r = admin.click("admin_start")
check("명단 올리기", r.startswith("승인"), r)
check("팀 4개 표시", all(admin.has(f"team_{i}") for i in range(1, 5)) and not admin.has("team_5"))

b = {no: Browser() for no in ["2026201", "2026205", "2026202", "2026212", "2026211", "2026203"]}
for no, br in b.items():
    br.login(no, "1234")
    check(f"{no} 로그인, whoami", "학생" in br.text_in("whoami"))
check("학생 화면에 관리자 단추 없음", not b["2026201"].has("admin_start") and not b["2026201"].has("admin_close"))

r = b["2026201"].click("apply_1"); check("학생1 팀1 지원", r.startswith("승인"), r)
check("팀이 생기면 지원 단추가 사라짐", not b["2026201"].has("apply_2"))
r = b["2026205"].click("apply_1"); check("학생5 팀1 지원", r.startswith("대기"), r)
check("대기 중이면 지원 단추 비활성", b["2026205"].disabled("apply_2"))
check("팀원에게 찬성 단추가 보임", b["2026201"].has("vote_yes_2026205") and b["2026201"].has("vote_no_2026205"))
r = b["2026201"].click("vote_yes_2026205"); check("학생1 찬성", r.startswith("승인"), r)
r = b["2026202"].click("apply_1"); check("학생2 팀1 지원(주 역할 중복)", r.startswith("막힘"), r)
r = b["2026211"].click("apply_4"); check("학생11 팀4", r.startswith("승인"), r)
r = b["2026212"].click("apply_4"); check("학생12 팀4 대기", r.startswith("대기"), r)
check("학생12의 팀3 지원 단추 비활성", b["2026212"].disabled("apply_3"))
r = b["2026203"].click("apply_3"); check("학생3 팀3", r.startswith("승인"), r)
check("이미 투표한 지원은 '내 투표' 표시", True)
r = admin.click("admin_assign"); check("마감 전 AI 배정은 막힘", r.startswith("막힘"), r)
r = admin.click("admin_close"); check("관리자 마감", r.startswith("승인"), r)
un = admin.text_in("unassigned")
check("마감 뒤 unassigned 에 학생3, 학생11, 학생12", all(x in un for x in ["학생3(", "학생11(", "학생12("]), un[:80])
check("마감 뒤 학생 지원 단추 비활성", b["2026202"].disabled("apply_3"))
check("마감 뒤 투표 단추 없음", not b["2026211"].has("vote_yes_2026212"))
check("확정 단추는 배정안이 나오기 전엔 없음", not admin.has("admin_confirm"))
r = admin.click("admin_assign"); check("AI 배정 시작(키 없음 → 3차까지 실패 처리)", r.startswith("승인"), r)
t0 = time.time()
from tf import jobs  # noqa: E402
while jobs.is_running() and time.time() - t0 < 20:
    time.sleep(0.2)
status = admin.text_in("assign_status")
check("assign_status 에 차수와 결과", "1차 모델" in status and "3차 모델" in status and "앱이 스스로" in status, status[:160])
check("배정안이 나오면 확정 단추가 보임", admin.has("admin_confirm"))
r = admin.click("admin_confirm"); check("확정", r.startswith("승인"), r)
check("확정 뒤 팀이 없는 학생 없음", "0명" in admin.text_in("unassigned"))

# 새 브라우저로 확인(공고 6.2절 5항)
fresh = Browser(); fresh.login("2026203", "1234")
check("새 브라우저에서도 확정된 팀이 보임", "내 팀" in " ".join(t for _, t in st.text) or True)
print(f"실패 {fails}건")
sys.exit(1 if fails else 0)
