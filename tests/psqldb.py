"""시험용 DB 연결: psycopg 대신 psql 프로그램으로 실제 PostgreSQL에 질의함.

이 작업 환경에는 psycopg를 설치할 수 없어서 만든 것이며, 앱은 쓰지 않는다.
service.py 가 쓰는 cur.execute / fetchone / fetchall 만 흉내 낸다.
SELECT 와 RETURNING 결과는 json_agg 로 받아 dict 목록으로 돌려준다.
"""
from __future__ import annotations

import json
import re
import subprocess
from contextlib import contextmanager

MARK = "__TF_END__"


class SqlError(Exception):
    def __init__(self, msg: str, sqlstate: str | None):
        super().__init__(msg)
        self.sqlstate = sqlstate


def _lit(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def _bind(sql: str, params) -> str:
    params = list(params or ())
    parts = sql.split("%s")
    if len(parts) - 1 != len(params):
        raise ValueError(f"자리 표시 {len(parts) - 1}개, 값 {len(params)}개: {sql}")
    out = parts[0]
    for p, rest in zip(params, parts[1:]):
        out += _lit(p) + rest
    return out


class _Session:
    def __init__(self, args):
        self.p = subprocess.Popen(
            ["psql", *args, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=0", "-v", "VERBOSITY=verbose"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        self.run("\\set VERBOSITY verbose")

    def run(self, sql: str) -> list[str]:
        self.p.stdin.write(sql.rstrip().rstrip(";") + ";\n\\echo " + MARK + "\n")
        self.p.stdin.flush()
        lines = []
        while True:
            line = self.p.stdout.readline()
            if line == "":
                raise RuntimeError("psql 종료됨")
            if line.strip() == MARK:
                break
            lines.append(line.rstrip("\n"))
        for ln in lines:
            m = re.search(r"ERROR:\s+([0-9A-Z]{5}):\s*(.*)", ln)
            if m:
                raise SqlError(m.group(2), m.group(1))
        return lines

    def close(self):
        self.p.stdin.close()
        self.p.wait()


class Cursor:
    def __init__(self, sess: _Session):
        self.s = sess
        self.rows: list[dict] = []

    def execute(self, sql: str, params=()):
        q = _bind(sql, params).strip()
        low = q.lower()
        if low.startswith("select"):
            wrapped = f"select coalesce(json_agg(q), '[]'::json) from ({q}) q"
        elif " returning " in low:
            wrapped = f"with q as ({q}) select coalesce(json_agg(q), '[]'::json) from q"
        else:
            self.s.run(q)
            self.rows = []
            return
        self.rows = json.loads("\n".join(self.s.run(wrapped)))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        r, self.rows = self.rows, []
        return r


class PsqlDB:
    def __init__(self, *args):
        self.args = args
        self.sess = _Session(args)

    @contextmanager
    def tx(self):
        self.sess.run("begin")
        try:
            yield Cursor(self.sess)
        except BaseException:
            self.sess.run("rollback")
            raise
        else:
            self.sess.run("commit")

    def run_script(self, sql: str):
        self.sess.run(sql)

    def close(self):
        self.sess.close()
