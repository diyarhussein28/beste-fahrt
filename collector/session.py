"""Session persistence — القسم 4.2: تسجيل الدخول مرة واحدة، حفظ الجلسة
مشفّرة على القرص، وإعادة تسجيل الدخول تلقائياً مرة واحدة فقط عند انتهائها.

Each platform gets its own state file (derived from STATE_FILE by inserting
the platform name) since multiple platforms now run concurrently with
independent sessions — see collector/monitor.py's `platform_loop`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

from shared.config import get_secrets

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

log = logging.getLogger("collector.session")


def _fernet() -> Fernet:
    secrets = get_secrets()
    key_material = secrets.state_enc_key or f"state-enc:{secrets.admin_secret_key}"
    digest = hashlib.sha256(key_material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _state_path(platform: str) -> Path:
    base = Path(get_secrets().state_file)
    return base.with_name(f"{platform}_{base.name}")


def load_state_dict(platform: str) -> dict | None:
    """Returns the decrypted storage_state dict, or None if no session is saved yet."""
    path = _state_path(platform)
    if not path.exists():
        return None
    try:
        return decrypt_state(path.read_bytes())
    except (InvalidToken, json.JSONDecodeError) as e:
        log.warning("stored session state is unreadable, will re-login", extra={"extra_fields": {"platform": platform, "error": str(e)}})
        return None


def encrypt_state(state: dict) -> bytes:
    return _fernet().encrypt(json.dumps(state).encode("utf-8"))


def decrypt_state(token: bytes) -> dict:
    return json.loads(_fernet().decrypt(token))


async def save_state(ctx: "BrowserContext", platform: str) -> None:
    path = _state_path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = await ctx.storage_state()
    token = encrypt_state(state)
    path.write_bytes(token)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort on filesystems that don't support POSIX permissions
    log.info("session state saved (encrypted)", extra={"extra_fields": {"platform": platform}})


def clear_state(platform: str) -> None:
    """Force a fresh login next cycle — used when we suspect the saved state leaked."""
    path = _state_path(platform)
    if path.exists():
        path.unlink()
