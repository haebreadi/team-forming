"""비밀번호 해시(bcrypt). 원래 비밀번호는 DB에 저장하지 않는다."""
from __future__ import annotations

ROUNDS = 10  # 명단 16명을 올릴 때 1초 안팎


def hash_password(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=ROUNDS)).decode("ascii")


def check_password(password: str, password_hash: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False
