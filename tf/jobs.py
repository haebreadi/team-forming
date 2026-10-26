"""AI 배정 작업(백그라운드 스레드).

관리자가 "AI 배정"을 누르면 작업 하나를 띄우고, 화면(st.fragment)은 2초마다
이 작업의 진행(몇 차 모델을 기다리는 중인지, 몇 초 지났는지)을 읽어 보여 준다.
화면을 새로 고치거나 다시 그려도 작업은 끊기지 않는다.

순서(공고 2.5절): 1차 → 2차 → 3차 모델. 모델마다 전체 120초 제한.
응답이 없거나, 빈 응답·오류이거나, 배정안이 검사 다섯 가지를 통과하지 못하면 다음 모델.
3차까지 실패하면 앱이 모든 경우를 따져 찾은 배정(rules.solve)을 쓴다.
"""
from __future__ import annotations

import threading
import time

from . import ai, rules, service

_lock = threading.Lock()
_state: dict = {"running": False}
_cancel = threading.Event()


def status() -> dict:
    with _lock:
        s = dict(_state)
        s["history"] = list(_state.get("history", []))
        return s


def _update(**kw) -> None:
    with _lock:
        _state.update(kw)


def _add_history(item: dict) -> None:
    with _lock:
        _state.setdefault("history", []).append(item)


def cancel() -> None:
    """명단을 다시 올릴 때 진행 중인 작업의 결과를 버리게 함."""
    _cancel.set()
    with _lock:
        _state.clear()
        _state["running"] = False


def is_running() -> bool:
    with _lock:
        return bool(_state.get("running"))


def start(db, ctx: service.AssignContext, fallback: dict[str, int], models: list[str], api_key: str,
          timeout_sec: float = ai.MODEL_TIMEOUT_SEC, url: str = ai.OPENROUTER_URL) -> bool:
    with _lock:
        if _state.get("running"):
            return False
        _cancel.clear()
        _state.clear()
        _state.update(running=True, started=time.time(), attempt=0, model="", attempt_started=None,
                      history=[], result="", total_models=len(models), timeout_sec=timeout_sec)
    th = threading.Thread(target=_run, args=(db, ctx, fallback, models, api_key, timeout_sec, url),
                          daemon=True, name="ai-assign")
    th.start()
    return True


def _run(db, ctx, fallback, models, api_key, timeout_sec, url) -> None:
    try:
        messages = ai.build_messages(ctx.teams, ctx.team_names, ctx.unassigned)
        for i, model in enumerate(models, start=1):
            if _cancel.is_set():
                return
            _update(attempt=i, model=model, attempt_started=time.time())
            t0 = time.monotonic()
            outcome, detail, ok_plan = "", "", None
            if not api_key:
                outcome, detail = "키 없음", "OPENROUTER_API_KEY가 설정되지 않음"
            else:
                try:
                    text = ai.call_model(model, messages, api_key, timeout_sec, url)
                    pairs = ai.parse_assignments(text)
                    errors = rules.validate_plan(ctx.teams, ctx.team_names, ctx.unassigned, pairs)
                    if errors:
                        outcome, detail = "배정안 검사 실패", "; ".join(errors[:4])
                    else:
                        ok_plan, outcome = dict(pairs), "성공"
                except ai.AIFailure as e:
                    outcome, detail = e.outcome, e.detail
                except Exception as e:  # 예상하지 못한 오류도 실패로 처리하고 다음 모델로
                    outcome, detail = "처리 오류", type(e).__name__
            elapsed = time.monotonic() - t0
            _add_history({"attempt": i, "model": model, "outcome": outcome, "detail": detail,
                          "elapsed": round(elapsed, 1)})
            service.log_ai(db, i, model, outcome, detail, elapsed)
            if ok_plan is not None:
                if not _cancel.is_set():
                    service.save_plan(db, ok_plan, f"{i}차 모델({model})")
                    _update(result=f"{i}차 모델의 배정안이 검사를 통과함")
                return
        # 3차까지 실패: 앱이 스스로 배정(모든 경우를 따져 찾은 배정)
        if not _cancel.is_set():
            service.log_ai(db, 0, "앱 자체 배정", "성공", "3차 모델까지 실패해 앱이 모든 경우를 따져 배정함", 0)
            service.save_plan(db, fallback, "앱이 스스로 배정(3차 모델까지 실패)")
            _update(result="3차 모델까지 실패해 앱이 스스로 배정함")
    except Exception as e:
        _update(result=f"배정 작업 오류({type(e).__name__}). AI 배정을 다시 누르세요")
    finally:
        _update(running=False, finished=time.time())
