"""StalePositionManager — Quản lý vốn kẹt (Double Signal) với FIFO hạch toán.

FIFO: Mỗi campaign cũ là một lớp chi phí riêng, không gộp giá vốn.
Write-off LIFO: Campaign mới nhất thanh lý trước.
Escrow Cache: Mọi proceeds bị giam đến khi lock gỡ.
Persistence Lock: system_state.json — reboot không unlock.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.portfolio.system_state import lock as _lock_state, unlock as _unlock_state, update_escrow

_DATA_DIR = Path(__file__).resolve().parents[3] / "backend" / "data"
_STALE_PATH: Path = _DATA_DIR / "stale_positions.json"


def _set_stale_path(path: Path):
    global _STALE_PATH
    _STALE_PATH = path


@dataclass
class StaleLayer:
    """Một lớp vốn kẹt từ một chiến dịch cũ (FIFO layer)."""
    campaign_id: str
    stale_pct: float
    entry_price: float = 0.0
    quantity: float = 0.0
    current_price: float = 0.0
    realized_pnl: float = 0.0
    status: str = "STALE"  # STALE | WRITTEN_OFF | RECLAIMED


@dataclass
class EscrowEntry:
    source: str       # "writeoff" | "dividend" | "rights"
    campaign_id: str
    amount: float
    description: str = ""


class StalePositionManager:
    """FIFO hạch toán + LIFO write-off + Escrow Cache.

    - Các lớp stale được xếp theo thứ tự campaign (v1 → v2 → v3)
    - Write-off: lấy lớp mới nhất (LIFO) nhưng báo cáo P&L từng lớp riêng (FIFO)
    - Escrow Cache: giữ tiền, không cho chạm vào Total_Equity cho đến khi unlock
    """

    def __init__(self, total_capital: float = 1_000_000, max_drawdown_pct: float = 0.25):
        self.total_capital = total_capital
        self.max_drawdown_pct = max_drawdown_pct
        self._load()

    # ── Persistence ───────────────────────────────────────

    def _load(self):
        try:
            data = json.loads(_STALE_PATH.read_text(encoding="utf-8"))
            self._layers: list[StaleLayer] = [StaleLayer(**l) for l in data.get("layers", [])]
            self._escrow: list[EscrowEntry] = [EscrowEntry(**e) for e in data.get("escrow", [])]
        except (FileNotFoundError, json.JSONDecodeError):
            self._layers: list[StaleLayer] = []
            self._escrow: list[EscrowEntry] = []

    def _save(self):
        _STALE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "layers": [
                {"campaign_id": l.campaign_id, "stale_pct": l.stale_pct,
                 "entry_price": l.entry_price, "quantity": l.quantity,
                 "current_price": l.current_price, "realized_pnl": l.realized_pnl,
                 "status": l.status}
                for l in self._layers
            ],
            "escrow": [
                {"source": e.source, "campaign_id": e.campaign_id,
                 "amount": e.amount, "description": e.description}
                for e in self._escrow
            ],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        _STALE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── FIFO: Ingest stale layer ──────────────────────────

    def ingest_stale(self, campaign_id: str, stale_pct: float) -> dict:
        """Ghi nhận một lớp stale mới. (FIFO — append vào cuối danh sách)"""
        pct = max(0.0, min(1.0, stale_pct))
        self._layers.append(StaleLayer(campaign_id=campaign_id, stale_pct=pct))
        self._save()

        if self.total_stale_pct >= self.max_drawdown_pct:
            _lock_state(self.total_stale_pct)

        return {
            "campaign_id": campaign_id,
            "stale_pct": pct,
            "total_stale_pct": self.total_stale_pct,
            "hard_shutdown": self.hard_shutdown,
            "layers": len(self._layers),
        }

    # ── LIFO Write-off ────────────────────────────────────

    def writeoff_lifo(self) -> dict:
        """Thanh lý campaign mới nhất trước (LIFO = đảo ngược FIFO)."""
        stale = [l for l in self._layers if l.status == "STALE"]
        if not stale:
            return {"written": [], "total_proceeds": 0.0}

        layer = stale[-1]  # LIFO: mới nhất
        proceeds = layer.current_price * layer.quantity if layer.current_price > 0 else 0.0
        layer.realized_pnl = proceeds - (layer.entry_price * layer.quantity)
        layer.status = "WRITTEN_OFF"

        self._escrow.append(EscrowEntry(
            source="writeoff",
            campaign_id=layer.campaign_id,
            amount=proceeds,
            description=f"LIFO write-off {layer.campaign_id}",
        ))
        update_escrow(self.escrow_balance)
        self._save()

        # Nếu tổng stale đã dưới ngưỡng → unlock
        if self.total_stale_pct < self.max_drawdown_pct and not self.hard_shutdown:
            _unlock_state()

        return {
            "written": [{"campaign_id": layer.campaign_id, "stale_pct": layer.stale_pct,
                         "proceeds": round(proceeds, 2), "realized_pnl": round(layer.realized_pnl, 2)}],
            "total_proceeds": round(proceeds, 2),
            "remaining_stale_pct": self.total_stale_pct,
            "escrow_balance": self.escrow_balance,
            "hard_shutdown": self.hard_shutdown,
        }

    # ── Reclaim ───────────────────────────────────────────

    def reclaim(self, campaign_id: str) -> dict:
        """Hoàn trả một lớp stale về campaign active.

        Ghi nhận đầy đủ P&L của chu kỳ cũ → mở vị thế mới độc lập.
        Không gộp giá vốn.
        """
        for layer in self._layers:
            if layer.campaign_id == campaign_id and layer.status == "STALE":
                # Realized P&L = current_value - entry_value
                layer.realized_pnl = (layer.current_price - layer.entry_price) * layer.quantity
                layer.status = "RECLAIMED"

                self._escrow.append(EscrowEntry(
                    source="reclaim",
                    campaign_id=campaign_id,
                    amount=0.0,
                    description=f"Reclaim {campaign_id} — closed old position",
                ))
                self._save()
                if self.total_stale_pct < self.max_drawdown_pct:
                    _unlock_state()
                return {"success": True, "campaign_id": campaign_id, "status": "RECLAIMED",
                        "realized_pnl": round(layer.realized_pnl, 2)}
        return {"success": False, "reason": "LAYER_NOT_FOUND"}

    # ── Corporate Actions ─────────────────────────────────

    def record_corporate_action(self, campaign_id: str, source: str, amount: float) -> dict:
        """Ghi nhận dividend / rights → Escrow Cache."""
        amount = max(0.0, amount)
        self._escrow.append(EscrowEntry(
            source=source, campaign_id=campaign_id, amount=amount,
            description=f"Corporate action: {source} on {campaign_id}",
        ))
        update_escrow(self.escrow_balance)
        self._save()
        return {"source": source, "amount": round(amount, 2), "escrow_balance": self.escrow_balance}

    # ── Properties ────────────────────────────────────────

    @property
    def total_stale_pct(self) -> float:
        return round(sum(l.stale_pct for l in self._layers if l.status == "STALE"), 4)

    @property
    def hard_shutdown(self) -> bool:
        return self.total_stale_pct >= self.max_drawdown_pct

    @property
    def escrow_balance(self) -> float:
        return round(sum(e.amount for e in self._escrow), 2)

    @property
    def stale_pcts(self) -> list[float]:
        return [l.stale_pct for l in self._layers if l.status == "STALE"]

    def status(self) -> dict:
        return {
            "total_layers": len(self._layers),
            "stale_layers": len([l for l in self._layers if l.status == "STALE"]),
            "stale_pcts": self.stale_pcts,
            "total_stale_pct": self.total_stale_pct,
            "escrow_balance": self.escrow_balance,
            "hard_shutdown": self.hard_shutdown,
            "critical_lock": self.hard_shutdown,
        }
