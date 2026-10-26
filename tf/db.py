"""DB 연결. 연결 풀을 한 번만 만들고 모든 질의가 다시 씀(공고 4.2절).

Supabase의 연결 풀러(포트 6543, transaction 모드)는 prepared statement를
지원하지 않으므로 prepare_threshold=None 으로 끈다.
"""
from __future__ import annotations

from contextlib import contextmanager


class Database:
    def __init__(self, url: str):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=6,
            kwargs={"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": 15},
            check=ConnectionPool.check_connection,   # 끊긴 연결은 버리고 새로 만듦
            open=True,
            timeout=30,
        )

    @contextmanager
    def tx(self):
        """한 트랜잭션. 예외 없이 끝나면 커밋, 예외가 나면 되돌림."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                yield cur

    def run_script(self, sql: str) -> None:
        """schema.sql 처럼 여러 문장이 든 SQL을 실행함."""
        with self.pool.connection() as conn:
            conn.execute(sql)


def is_unique_violation(exc: BaseException) -> bool:
    return getattr(exc, "sqlstate", None) == "23505"
