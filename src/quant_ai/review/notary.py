"""예측 장부 외부 공증 — 봉인 해시를 제3자가 확인할 수 있는 곳에 남긴다.

1. OpenTimestamps (기본 · 무료 · 키 없음): 장부 봉인 문서(anchor-<id>.txt)의 SHA-256 을 공개 캘린더 서버에 제출
   → 받은 증명을 표준 .ots 파일로 저장. 몇 시간 뒤 비트코인 블록에 묶이면 `ots upgrade` · `ots verify` 로 누구나
   "이 예측 장부는 이 시각 이전에 이미 존재했다"를 검증할 수 있다 (이 시스템도, 나도 날짜를 속일 수 없다).
2. GitHub Gist (선택 · QUANT_GIST_TOKEN): 비밀 gist 에 봉인 문서를 올린다 → gist 수정 이력이 공개 타임스탬프.
받은 영수증은 artifacts/notary/ 와 ops 'notary' 에 남긴다. 실패해도 장부 봉인 자체에는 영향이 없다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .. import ops

log = logging.getLogger(__name__)
CALENDARS = ("https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org")
OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_SHA256 = b"\x08"


def anchor_document(anchor: dict) -> bytes:
    """공증할 문서: 사람이 읽을 수 있고, 장부 검증(verify)이 다시 계산하는 digest 를 담는다."""
    lines = ["quant-ai prediction ledger anchor", f"upto_id: {anchor['upto_id']}", f"count: {anchor.get('n')}",
             f"digest: {anchor['digest']}", f"prev_digest: {anchor.get('prev_digest') or ''}", f"sealed_at: {anchor['ts']}",
             "verify: GET /api/verify recomputes this digest from the ledger rows."]
    return ("\n".join(lines) + "\n").encode()


def ots_file(digest: bytes, calendar_response: bytes) -> bytes:
    """DetachedTimestampFile = 매직 · 버전 1 · sha256 태그 · 파일 해시 · 캘린더가 준 타임스탬프."""
    return OTS_MAGIC + b"\x01" + OTS_SHA256 + digest + calendar_response


def _post(url: str, data: bytes, headers: dict) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - 고정 https
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
        return r.read()


def stamp_ots(doc: bytes, post=None) -> dict:
    digest = hashlib.sha256(doc).digest()
    last = None
    for cal in CALENDARS:
        try:
            resp = (post or _post)(f"{cal}/digest", digest, {"Accept": "application/vnd.opentimestamps.v1",
                                                             "User-Agent": "quant-ai"})
            return {"service": "opentimestamps", "calendar": cal, "sha256": digest.hex(),
                    "ots_b64": base64.b64encode(ots_file(digest, resp)).decode(), "status": "pending (비트코인 확정 대기)"}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {str(e)[:80]}"
    raise RuntimeError(f"OpenTimestamps 캘린더 모두 실패: {last}")


def stamp_gist(doc: bytes, token: str, name: str, post=None) -> dict:
    body = json.dumps({"description": "quant-ai prediction ledger anchor", "public": False,
                       "files": {name: {"content": doc.decode()}}}).encode()
    resp = (post or _post)("https://api.github.com/gists", body, {"Authorization": f"Bearer {token}",
                                                                   "Accept": "application/vnd.github+json",
                                                                   "Content-Type": "application/json", "User-Agent": "quant-ai"})
    j = json.loads(resp)
    return {"service": "gist", "url": j.get("html_url"), "id": j.get("id"), "created_at": j.get("created_at")}


def notarize(app, anchor: dict, post=None, now: datetime | None = None) -> dict:
    """최신 봉인(anchor)을 외부에 남긴다. 같은 upto_id 는 한 번만."""
    mode = getattr(app.settings, "notary", "ots")
    st = ops.get_state(app.engine, "notary")
    hist = st.get("receipts") or []
    if mode == "off":
        return {"skipped": "QUANT_NOTARY=off"}
    if any(r.get("upto_id") == anchor["upto_id"] for r in hist):
        return {"skipped": "이미 공증됨", "upto_id": anchor["upto_id"]}
    now = now or datetime.now(UTC)
    doc = anchor_document(anchor)
    d = Path(app.settings.artifacts_dir) / "notary"
    d.mkdir(parents=True, exist_ok=True)
    name = f"anchor-{anchor['upto_id']}.txt"
    (d / name).write_bytes(doc)
    rec = {"upto_id": anchor["upto_id"], "digest": anchor["digest"], "at": now.isoformat(), "file": name, "results": []}
    if mode in ("ots", "both"):
        try:
            r = stamp_ots(doc, post)
            (d / f"{name}.ots").write_bytes(base64.b64decode(r["ots_b64"]))
            rec["results"].append({k: v for k, v in r.items() if k != "ots_b64"} | {"file": f"{name}.ots"})
        except Exception as e:  # noqa: BLE001
            rec["results"].append({"service": "opentimestamps", "error": str(e)[:160]})
    if mode in ("gist", "both"):
        tok = getattr(app.settings, "gist_token", None)
        if tok:
            try:
                rec["results"].append(stamp_gist(doc, tok, name, post))
            except Exception as e:  # noqa: BLE001
                rec["results"].append({"service": "gist", "error": f"{type(e).__name__}"})
        else:
            rec["results"].append({"service": "gist", "error": "QUANT_GIST_TOKEN 없음"})
    rec["ok"] = any("error" not in r for r in rec["results"])
    ops.set_state(app.engine, "notary", {"receipts": (hist + [rec])[-200:], "last": rec, "mode": mode})
    return rec


__all__ = ["notarize", "anchor_document", "ots_file", "stamp_ots", "stamp_gist", "OTS_MAGIC"]
