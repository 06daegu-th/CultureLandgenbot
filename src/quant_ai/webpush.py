"""웹 푸시 — 사이트를 닫아도 휴대폰·PC 로 알림 (브라우저 표준 Web Push, 별도 서비스 가입 없음).

  · VAPID 키: 처음 한 번 만들어 artifacts/vapid.json 에 둔다 (개인키는 이 컴퓨터에만).
  · 구독: 브라우저가 알림을 허용하면 구독 정보(엔드포인트·공개키)를 서버에 저장.
  · 보내기: RFC 8291 (aes128gcm) 로 암호화해 브라우저 푸시 서비스(FCM·Mozilla·Apple)로 POST.
    푸시 서비스는 내용을 볼 수 없다. 사라진 구독(404/410)은 자동 삭제.

주의: 휴대폰에서 받으려면 휴대폰이 이 대시보드를 HTTPS 주소로 열어 한 번 구독해야 한다
(같은 컴퓨터의 http://127.0.0.1 은 PC 브라우저에서만 동작). docs/MOBILE.md 참고.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import ops

log = logging.getLogger(__name__)
SUBS_KEY = "push_subs"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def ub64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, hmac, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - ./run.sh 가 설치
        raise RuntimeError("cryptography 미설치 — ./run.sh 를 다시 실행하면 설치됩니다") from e
    return hashes, hmac, serialization, ec, decode_dss_signature, AESGCM


def available() -> bool:
    try:
        _crypto()
        return True
    except RuntimeError:
        return False


def _hmac(key: bytes, data: bytes) -> bytes:
    hashes, hmac, *_ = _crypto()
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def vapid_keys(artifacts_dir) -> dict:
    """{'private_pem', 'public'(b64url, 65바이트 비압축 점)} — 없으면 만든다."""
    _, _, serialization, ec, _, _ = _crypto()
    path = Path(artifacts_dir) / "vapid.json"
    if path.exists():
        return json.loads(path.read_text())
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    data = {"private_pem": pem, "public": b64u(pub)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows
        pass
    return data


def vapid_header(endpoint: str, keys: dict, sub: str = "mailto:quant-ai@localhost", ttl_s: int = 12 * 3600) -> str:
    hashes, _, serialization, ec, decode_dss_signature, _ = _crypto()
    u = urlparse(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + ttl_s, "sub": sub},
                             separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(keys["private_pem"].encode(), None)
    der = key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={head}.{claims}.{b64u(sig)}, k={keys['public']}"


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None, rs: int = 4096) -> bytes:
    """RFC 8291 aes128gcm 본문 (헤더 + 암호문)."""
    _, _, serialization, ec, _, AESGCM = _crypto()
    ua_pub = ub64(p256dh)
    auth_secret = ub64(auth)
    salt = salt or os.urandom(16)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_pub = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")[:32]
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!I", rs) + bytes([len(as_pub)]) + as_pub + body


def decrypt(body: bytes, ua_private, auth: str) -> bytes:
    """테스트·점검용: 받는 쪽(브라우저) 입장에서 복호화."""
    _, _, serialization, ec, _, AESGCM = _crypto()
    salt, idlen = body[:16], body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    ua_pub = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    prk_key = _hmac(ub64(auth), shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")[:32]
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    pt = AESGCM(cek).decrypt(nonce, ct, None)
    return pt.rstrip(b"\x00")[:-1]  # 마지막 구분자 0x02 제거


def subscribe(engine, sub: dict, label: str = "") -> dict:
    ep, keys = sub.get("endpoint", ""), sub.get("keys") or {}
    if not ep.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("잘못된 구독 정보")
    subs = [x for x in ops.get_state(engine, SUBS_KEY).get("subs", []) if x["endpoint"] != ep]
    subs.append({"endpoint": ep, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}, "label": label[:60],
                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    ops.set_state(engine, SUBS_KEY, {"subs": subs[-20:]})
    return {"subscribed": len(subs)}


def unsubscribe(engine, endpoint: str) -> dict:
    subs = [x for x in ops.get_state(engine, SUBS_KEY).get("subs", []) if x["endpoint"] != endpoint]
    ops.set_state(engine, SUBS_KEY, {"subs": subs})
    return {"subscribed": len(subs)}


def _post(url: str, body: bytes, headers: dict, timeout: float = 10.0) -> int:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)  # noqa: S310 - https 구독만
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def send(engine, artifacts_dir, title: str, body: str = "", link: str = "#control", post=None) -> dict:
    """모든 구독에 보내기. {sent, removed, errors}."""
    subs = ops.get_state(engine, SUBS_KEY).get("subs", [])
    if not subs or not available():
        return {"sent": 0, "removed": 0, "errors": [] if subs else ["구독 없음"]}
    keys = vapid_keys(artifacts_dir)
    payload = json.dumps({"title": title[:120], "body": body[:300], "link": link}, ensure_ascii=False).encode()
    sent, gone, errors = 0, [], []
    for sub in subs:
        try:
            data = encrypt(payload, sub["keys"]["p256dh"], sub["keys"]["auth"])
            code = (post or _post)(sub["endpoint"], data, {
                "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": "86400",
                "Urgency": "high", "Authorization": vapid_header(sub["endpoint"], keys)})
            if code in (404, 410):
                gone.append(sub["endpoint"])
            elif 200 <= code < 300:
                sent += 1
            else:
                errors.append(f"HTTP {code}")
        except Exception as e:  # noqa: BLE001 - 한 구독 실패가 나머지를 막지 않게
            errors.append(f"{type(e).__name__}: {str(e)[:100]}")
    if gone:
        ops.set_state(engine, SUBS_KEY, {"subs": [x for x in subs if x["endpoint"] not in gone]})
    return {"sent": sent, "removed": len(gone), "errors": errors[:5]}


def sender(engine, artifacts_dir):
    """alerts.configure 에 넘길 함수 (알림 저장과 같은 스레드를 붙잡지 않도록 백그라운드로)."""
    import threading

    def _send(title, body, link):
        if ops.get_state(engine, SUBS_KEY).get("subs"):
            threading.Thread(target=send, args=(engine, artifacts_dir, title, body, link), daemon=True).start()
    return _send


__all__ = ["vapid_keys", "vapid_header", "encrypt", "decrypt", "subscribe", "unsubscribe", "send", "sender", "available"]
