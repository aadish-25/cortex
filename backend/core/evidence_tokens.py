"""Opaque, user-bound references for opening cited evidence without exposing Qdrant IDs."""
import base64
import hashlib
import hmac
import json
import time
import uuid

from backend.core.config import settings


def create_evidence_token(point_id: str, user_id: uuid.UUID, ttl_seconds: int = 30 * 24 * 3600) -> str:
    payload = {
        "p": str(point_id),
        "u": str(user_id),
        "e": int(time.time()) + ttl_seconds,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(settings.JWT_SECRET_KEY.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{encoded}.{signed}"


def read_evidence_token(token: str, user_id: uuid.UUID) -> str:
    try:
        if not token or len(token) > 2048:
            raise ValueError("Invalid evidence reference")
        encoded, supplied_signature = token.split(".", 1)
        expected = hmac.new(settings.JWT_SECRET_KEY.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        expected_signature = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("Invalid evidence reference")
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("u") != str(user_id) or int(payload.get("e", 0)) < int(time.time()):
            raise ValueError("Evidence reference expired or not owned by this user")
        point_id = payload.get("p")
        if not isinstance(point_id, str) or not point_id:
            raise ValueError("Invalid evidence reference")
        return point_id
    except Exception as exc:
        raise ValueError("Evidence reference is invalid, expired, or not owned by this user") from exc
