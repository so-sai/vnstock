"""Break-Glass Protocol — Single-Pass Key-Anchored Passphrase.

Không multi-sig, không multi-admin.
Operator duy nhất dùng --passphrase <SECRET> để override.
Cognitive Friction: time-delay lock 15 phút hoặc hash captcha.
"""

import hashlib
import json
import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.portfolio.system_state import is_locked, unlock as _unlock_lock

_BG_PATH = Path(__file__).resolve().parents[3] / "backend" / "data" / "state" / "break_glass.json"


def _read_bg() -> dict:
    try:
        return json.loads(_BG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"active_ticket": None, "tickets": {}}


def _write_bg(data: dict):
    _BG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class BreakGlassProtocol:
    """Single-User Break-Glass với Key-Anchored Passphrase + Time-Delay Fallback."""

    def __init__(self, passphrase: str = "ptck_emergency_override_2026",
                 time_delay_minutes: int = 15):
        self._passphrase_hash = hashlib.sha256(passphrase.encode()).hexdigest()
        self.time_delay_minutes = time_delay_minutes

    def request(self, use_time_delay: bool = False) -> dict:
        """Yêu cầu override ticket.

        Nếu use_time_delay=True → bắt buộc chờ 15 phút (Cognitive Friction).
        Nếu False → operator phải gọi verify với --passphrase đúng.
        """
        bg = _read_bg()
        ticket_id = _random_token(12)
        challenge = _random_token(16)
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        bg["active_ticket"] = ticket_id
        bg["tickets"][ticket_id] = {
            "ticket_id": ticket_id,
            "challenge": challenge,
            "use_time_delay": use_time_delay,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expiry,
            "status": "PENDING",
            "passphrase_hash": self._passphrase_hash,
        }
        _write_bg(bg)
        return {
            "ticket_id": ticket_id,
            "challenge": challenge,
            "use_time_delay": use_time_delay,
            "time_delay_minutes": self.time_delay_minutes if use_time_delay else None,
            "hint": "passphrase phrase or wait time_delay",
            "expires_at": expiry,
        }

    def verify(self, ticket_id: str, passphrase: str | None = None) -> dict:
        bg = _read_bg()
        ticket = bg.get("tickets", {}).get(ticket_id)
        if not ticket:
            return {"success": False, "reason": "TICKET_NOT_FOUND"}
        if ticket["status"] != "PENDING":
            return {"success": False, "reason": f"ALREADY_{ticket['status']}"}

        if ticket.get("use_time_delay"):
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(ticket["created_at"])).total_seconds() / 60.0
            if elapsed < self.time_delay_minutes:
                remaining = self.time_delay_minutes - elapsed
                return {"success": False, "reason": "TIME_DELAY_ACTIVE", "remaining_minutes": round(remaining, 1)}
            ticket["status"] = "APPROVED"
            _write_bg(bg)
            _unlock_lock()
            return {"success": True, "mode": "time_delay"}

        # Passphrase mode
        if not passphrase:
            return {"success": False, "reason": "PASSPHRASE_REQUIRED"}
        ph = hashlib.sha256(passphrase.encode()).hexdigest()
        if ph == ticket.get("passphrase_hash", ""):
            ticket["status"] = "APPROVED"
            _write_bg(bg)
            _unlock_lock()
            return {"success": True, "mode": "passphrase"}
        return {"success": False, "reason": "INVALID_PASSPHRASE"}

    def cancel(self, ticket_id: str) -> dict:
        bg = _read_bg()
        ticket = bg.get("tickets", {}).get(ticket_id)
        if not ticket:
            return {"success": False, "reason": "TICKET_NOT_FOUND"}
        ticket["status"] = "CANCELLED"
        bg["active_ticket"] = None
        _write_bg(bg)
        return {"success": True}


def _random_token(length: int = 12) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
