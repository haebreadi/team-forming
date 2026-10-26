"""오픈라우터 AI 호출(공고 2.5절, 4.3절).

- 모델 하나를 부른 때부터 응답을 다 받을 때까지 전체 120초를 재서 끊는다(3장의 어려운 해결).
  요청은 별도 스레드에서 보내고, 부른 쪽은 120초만 기다린 뒤 다음 모델로 넘어간다.
  요청 스레드도 응답을 조각으로 받으며 같은 마감 시각을 넘으면 스스로 멈춘다.
- 키 값은 어디에도 출력하지 않는다(오류 안내에도 넣지 않음).
"""
from __future__ import annotations

import json
import re
import threading
import time

from .rules import MAX_TEAM, MIN_TEAM, REQUIRED_ROLES, Student

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_TIMEOUT_SEC = 120
# 1차, 2차, 3차 모델(무료). 비밀 값 OPENROUTER_MODELS 에 쉼표로 적으면 그것을 씀
DEFAULT_MODELS = [
    "google/gemma-4-31b-it:free",
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]


class AIFailure(Exception):
    """모델 하나의 실패. outcome 은 화면과 기록에 쓰는 짧은 말."""

    def __init__(self, outcome: str, detail: str = ""):
        super().__init__(outcome)
        self.outcome = outcome
        self.detail = detail


def _desc(s: Student) -> dict:
    return {"student_no": s.student_no, "name": s.name, "main_role": s.main_role, "sub_roles": list(s.sub_roles)}


def build_messages(teams: dict[int, list[Student]], team_names: dict[int, str],
                   unassigned: list[Student]) -> list[dict]:
    data = {
        "teams": [{"team": t, "name": team_names.get(t, f"팀{t}"), "members": [_desc(m) for m in teams[t]]}
                  for t in sorted(teams)],
        "unassigned_students": [_desc(s) for s in unassigned],
    }
    rules_text = (
        "규칙:\n"
        f"1. unassigned_students 의 학생만 팀에 넣는다. 이미 팀에 있는 학생은 옮기지 않는다.\n"
        "2. 팀이 없는 학생은 한 명도 빠짐없이, 각자 정확히 한 팀에 넣는다.\n"
        f"3. 배정 뒤 모든 팀의 인원은 {MIN_TEAM}~{MAX_TEAM}명이다(빈 팀도 {MIN_TEAM}명 이상으로 채운다).\n"
        "4. 한 팀 안에서 주 역할(main_role)이 같은 학생이 2명 이상이면 안 된다. 보조 역할은 겹쳐도 된다.\n"
        f"5. 모든 팀에 {REQUIRED_ROLES[0]}를 맡을 학생과 {REQUIRED_ROLES[1]}를 맡을 학생이 서로 다른 사람으로 "
        "1명씩 있어야 한다. 주 역할이나 보조 역할(sub_roles)로 맡을 수 있다. "
        "한 학생이 둘 다 할 수 있어도 한 역할만 맡는다.\n"
    )
    fmt = ('출력은 다른 말 없이 JSON 하나만 쓴다. 형식: '
           '{"assignments": [{"student_no": "학번", "team": 팀번호}, ...]}. '
           "team 에는 teams 의 team 숫자를 쓴다. 답하기 전에 규칙 3, 4, 5를 팀마다 다시 확인한다.")
    return [
        {"role": "system", "content": "너는 대학 수업의 팀 배정 도우미다. 규칙을 모두 지키는 배정안을 JSON으로만 답한다."},
        {"role": "user", "content": rules_text + "\n지금 상태:\n" + json.dumps(data, ensure_ascii=False, indent=1)
         + "\n\n" + fmt},
    ]


def parse_assignments(text: str) -> list[tuple[str, int]]:
    """AI 응답에서 (학번, 팀 번호) 목록을 꺼냄. 형식이 틀리면 AIFailure."""
    if not text or not text.strip():
        raise AIFailure("빈 응답")
    t = re.sub(r"```(?:json)?", "", text)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise AIFailure("응답에 JSON이 없음", text[:300])
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        raise AIFailure("응답의 JSON을 읽지 못함", text[:300])
    items = obj.get("assignments") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise AIFailure("응답에 assignments 목록이 없음", text[:300])
    out = []
    for it in items:
        if not isinstance(it, dict) or "student_no" not in it or "team" not in it:
            raise AIFailure("assignments 항목의 형식이 틀림", text[:300])
        no = str(it["student_no"]).strip()
        m = re.search(r"\d+", str(it["team"]))
        if not m:
            raise AIFailure("팀 번호를 읽지 못함", text[:300])
        out.append((no, int(m.group())))
    return out


def _post(model: str, messages: list[dict], api_key: str, deadline: float, url: str,
          timeout_sec: float = MODEL_TIMEOUT_SEC) -> str:
    import requests

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 8000,                 # 추론 모델이 생각하는 데 다 쓰지 않게 넉넉히
        "reasoning": {"effort": "low"},     # 빈 응답을 줄임(공고 4.3절)
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
               "X-Title": "team-forming-app"}
    left = max(1.0, deadline - time.monotonic())
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=(min(15.0, left), left)) as r:
            chunks = []
            for chunk in r.iter_content(chunk_size=4096):
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise AIFailure(f"{int(timeout_sec)}초 안에 응답을 다 받지 못함")
            body = b"".join(chunks).decode("utf-8", "replace")
            status = r.status_code
    except AIFailure:
        raise
    except requests.Timeout:
        raise AIFailure(f"{int(timeout_sec)}초 안에 응답하지 않음")
    except requests.RequestException as e:
        raise AIFailure("연결 오류", type(e).__name__)
    if status != 200:
        msg = ""
        try:
            msg = json.loads(body).get("error", {}).get("message", "")
        except Exception:
            pass
        label = "사용량 초과(HTTP 429)" if status == 429 else f"HTTP {status} 오류"
        raise AIFailure(label, msg[:300])
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        raise AIFailure("응답 형식 오류", body[:300])
    if "error" in obj and not obj.get("choices"):
        raise AIFailure("오류 응답", str(obj["error"].get("message", ""))[:300] if isinstance(obj["error"], dict) else "")
    try:
        content = obj["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIFailure("응답 형식 오류", body[:300])
    if not content.strip():
        raise AIFailure("빈 응답", "추론에 max_tokens를 다 썼을 수 있음")
    return content


def call_model(model: str, messages: list[dict], api_key: str,
               timeout_sec: float = MODEL_TIMEOUT_SEC, url: str = OPENROUTER_URL) -> str:
    """모델 하나를 부르고, 부른 때부터 timeout_sec 초가 지나면 기다리지 않고 AIFailure."""
    deadline = time.monotonic() + timeout_sec
    box: dict = {}

    def worker():
        try:
            box["text"] = _post(model, messages, api_key, deadline, url, timeout_sec)
        except BaseException as e:  # 스레드 밖으로 넘겨 받음
            box["error"] = e

    th = threading.Thread(target=worker, daemon=True, name=f"ai-{model}")
    th.start()
    th.join(timeout_sec)
    if th.is_alive():
        raise AIFailure(f"{int(timeout_sec)}초 안에 응답하지 않음")
    if "error" in box:
        e = box["error"]
        if isinstance(e, AIFailure):
            raise e
        raise AIFailure("알 수 없는 오류", type(e).__name__)
    return box["text"]
