"""v34 메일 보내기 — 이메일 확인 · 비밀번호 재설정 · 초대.

설정 (.env):
  QUANT_SMTP_HOST · QUANT_SMTP_PORT(기본 587) · QUANT_SMTP_USER · QUANT_SMTP_PASSWORD · QUANT_SMTP_FROM
  QUANT_SMTP_TLS = starttls(기본) / ssl / none
SMTP 가 없으면 메일 대신 '보낼 편지함'(운영자 콘솔)에 쌓는다 — 운영자가 직접 전달 (작은 베타용).
SMTP 가 있으면 편지함에는 받는 사람·제목만 남기고 본문(링크)은 남기지 않는다 (재설정 링크가 DB 에 남지 않게).
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage

from . import ops

log = logging.getLogger("quant_ai.mailer")
OUTBOX = "mail_outbox"


def configured() -> bool:
    return bool(os.environ.get("QUANT_SMTP_HOST") and os.environ.get("QUANT_SMTP_FROM"))


def _smtp_send(to: str, subject: str, body: str) -> None:
    host = os.environ["QUANT_SMTP_HOST"]
    port = int(os.environ.get("QUANT_SMTP_PORT") or 587)
    mode = (os.environ.get("QUANT_SMTP_TLS") or "starttls").lower()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["QUANT_SMTP_FROM"], to, subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    cls = smtplib.SMTP_SSL if mode == "ssl" else smtplib.SMTP
    kw = {"context": ctx} if mode == "ssl" else {}
    with cls(host, port, timeout=15, **kw) as s:
        if mode == "starttls":
            s.starttls(context=ctx)
        if os.environ.get("QUANT_SMTP_USER"):
            s.login(os.environ["QUANT_SMTP_USER"], os.environ.get("QUANT_SMTP_PASSWORD") or "")
        s.send_message(msg)


def send(app, to: str, subject: str, body: str, kind: str = "mail") -> bool:
    """보냈으면 True. 실패·미설정이면 편지함에 남기고 False (호출한 쪽은 그대로 진행)."""
    ok, err = False, None
    if configured():
        try:
            _smtp_send(to, f"[Quant AI] {subject}", body)
            ok = True
        except Exception as e:  # noqa: BLE001 - 메일 실패가 가입·재설정 흐름을 깨면 안 된다
            err = f"{type(e).__name__}: {str(e)[:200]}"
            log.warning("메일 실패 (%s): %s", kind, err)
    try:
        box = ops.get_state(app.engine, OUTBOX).get("items", [])
        item = {"at": datetime.now(UTC).isoformat(), "to": to, "subject": subject, "kind": kind, "sent": ok, "error": err}
        if not configured():
            item["body"] = body  # SMTP 가 없을 때만 — 운영자가 직접 전달해야 하므로
        ops.set_state(app.engine, OUTBOX, {"items": [*box, item][-100:]})
    except Exception as e:  # noqa: BLE001
        log.warning("편지함 기록 실패: %s", e)
    return ok


def outbox(app) -> dict:
    return {"smtp": configured(), "items": ops.get_state(app.engine, OUTBOX).get("items", [])[::-1]}


__all__ = ["send", "outbox", "configured"]
