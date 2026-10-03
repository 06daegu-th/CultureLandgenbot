"""테스트는 외부 네트워크를 쓰지 않는다: 종목 상세·실시간 시세·커뮤니티 소스는 기본으로 '연결 안 됨'.

PostgreSQL 통합 테스트: QUANT_TEST_DATABASE_URL 이 없고 이 컴퓨터에 PostgreSQL 프로그램(initdb·pg_ctl)이 있으면
임시 DB 를 직접 띄워서 돌린다 (끝나면 지움) → '건너뜀(skip)' 없이 전체 검증.
QUANT_NO_SKIP=1 이면 건너뛴 테스트가 하나라도 있을 때 실패로 끝난다 (CI 에서 '0 skip' 을 강제).
"""

import glob
import os
import shutil
import socket
import subprocess
import tempfile

import pytest

_PG: dict = {}


def _pg_bin() -> str | None:
    cands = [os.environ["QUANT_PG_BIN"]] if os.environ.get("QUANT_PG_BIN") else []
    for d in cands + sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if os.path.exists(os.path.join(d, "initdb")) and os.path.exists(os.path.join(d, "pg_ctl")):
            return d
    w = shutil.which("initdb")
    return os.path.dirname(w) if w and shutil.which("pg_ctl") else None


def _start_temp_postgres() -> str | None:
    b = _pg_bin()
    if not b:
        return None
    try:
        import psycopg  # noqa: F401 - 드라이버가 없으면 띄워도 못 씀
    except ImportError:
        return None
    root = tempfile.mkdtemp(prefix="qa-pg-")
    data, sock = os.path.join(root, "data"), os.path.join(root, "sock")
    os.makedirs(sock)
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    as_user: list[str] = []
    if os.geteuid() == 0:  # initdb 는 root 로 실행할 수 없다 → postgres 사용자로
        try:
            import pwd
            pw = pwd.getpwnam("postgres")
        except KeyError:
            return None
        os.chown(root, pw.pw_uid, pw.pw_gid)
        os.chown(sock, pw.pw_uid, pw.pw_gid)
        as_user = ["runuser", "-u", "postgres", "--"] if shutil.which("runuser") else []
        if not as_user:
            return None
    try:
        subprocess.run([*as_user, f"{b}/initdb", "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-locale"],
                       check=True, capture_output=True, timeout=120)
        subprocess.run([*as_user, f"{b}/pg_ctl", "-D", data, "-l", os.path.join(root, "log"), "-w", "-t", "60",
                        "-o", f"-p {port} -k {sock} -c listen_addresses=127.0.0.1 -c fsync=off", "start"],
                       check=True, capture_output=True, timeout=90)
    except (subprocess.SubprocessError, OSError):
        shutil.rmtree(root, ignore_errors=True)
        return None
    _PG.update(root=root, data=data, bin=b, as_user=as_user)
    return f"postgresql://postgres@127.0.0.1:{port}/postgres"


CORE_DEPS = ("numpy", "pandas", "sqlalchemy", "sklearn", "defusedxml", "pyarrow", "holidays", "certifi")


def _missing_core() -> list[str]:
    import importlib.util
    return [m for m in CORE_DEPS if importlib.util.find_spec(m) is None]


def pytest_configure(config):
    # v23: 의존성이 빠진 환경(예: pip install -e . 없이 pytest 만 설치)에서는 수백 개가 알 수 없는 오류로 깨지는 대신
    # 처음에 한 줄로 이유를 말하고 멈춘다
    miss = _missing_core()
    if miss:
        raise pytest.UsageError(f"필수 패키지 없음: {', '.join(miss)} — 먼저 `pip install -e \".[dev]\"` 를 실행하세요 "
                                "(pyproject.toml 의 dependencies 를 설치합니다)")
    if not os.environ.get("QUANT_TEST_DATABASE_URL") and os.environ.get("QUANT_TEST_NO_TEMP_PG") != "1":
        url = _start_temp_postgres()
        if url:
            os.environ["QUANT_TEST_DATABASE_URL"] = url


def pytest_unconfigure(config):
    if _PG:
        subprocess.run([*_PG["as_user"], f"{_PG['bin']}/pg_ctl", "-D", _PG["data"], "-m", "immediate", "stop"],
                       capture_output=True, timeout=60, check=False)
        shutil.rmtree(_PG["root"], ignore_errors=True)
        os.environ.pop("QUANT_TEST_DATABASE_URL", None)
        _PG.clear()


COLLECTED: dict = {}


def pytest_collection_finish(session):
    """문서의 '테스트 N개'가 실제와 같은지 검사하려고 전체 실행일 때 수집 개수를 기록 (test_docs_counts)."""
    args = [a for a in session.config.args if not a.startswith("-")]
    full = not args or all(os.path.abspath(a) == os.path.abspath(os.path.join(str(session.config.rootpath), "tests")) for a in args)
    COLLECTED.update(n=len(session.items), full=full and not session.config.getoption("keyword") and not session.config.getoption("markexpr"))


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("QUANT_NO_SKIP") == "1":
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        skipped = len(tr.stats.get("skipped", [])) if tr else 0
        if skipped:
            session.exitstatus = 1
            if tr:
                tr.write_line(f"QUANT_NO_SKIP=1: 건너뛴 테스트 {skipped}개 → 실패 처리", red=True)


def _offline(*_a, **_k):
    raise RuntimeError("offline (tests)")


@pytest.fixture(autouse=True)
def _no_profile_network(monkeypatch):
    from quant_ai.data import fundamentals
    monkeypatch.setattr(fundamentals, "DEFAULT_FETCHERS", {k: _offline for k in fundamentals.DEFAULT_FETCHERS})
    from quant_ai.data import live_quotes
    from quant_ai.data.collectors import community
    monkeypatch.setattr(live_quotes, "fetch_kr", lambda codes: {})
    monkeypatch.setattr(live_quotes, "fetch_us", lambda syms: {})
    monkeypatch.setattr(community, "fetch_stocktwits", _offline)
    monkeypatch.setattr(community, "fetch_naver_board", _offline)
    from quant_ai.data.collectors import dart_docs, investor_flow
    from quant_ai.engines import sector
    monkeypatch.setattr(investor_flow, "fetch_naver", _offline)
    monkeypatch.setattr(dart_docs, "fetch_document", _offline)
    monkeypatch.setattr(sector, "fetch_sector", _offline)
    # v13 외부 소스
    from quant_ai.data.collectors import altdata, options, wics
    from quant_ai.engines import earnings
    from quant_ai.review import notary
    monkeypatch.setattr(options, "fetch_yahoo", _offline)
    monkeypatch.setattr(earnings, "fetch_yahoo", _offline)
    monkeypatch.setattr(altdata, "_get", _offline)
    monkeypatch.setattr(wics, "_get", _offline)
    monkeypatch.setattr(notary, "_post", _offline)
    # 로드맵 (v14) 외부 소스
    from quant_ai.data.collectors import bok, kr_consensus, vkospi
    monkeypatch.setattr(kr_consensus, "fetch_naver", _offline)
    monkeypatch.setattr(bok, "_get", _offline)
    monkeypatch.setattr(vkospi, "_post", _offline)
