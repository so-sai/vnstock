"""LiveFeed — Real Data Pipeline Adapter (Phase 5 UAT).

Kết nối StreamingFeed trực tiếp với API VNSTOCK hoặc nguồn dữ liệu tương đương.
Xử lý latency jitter: timestamp alignment, out-of-order packets, data gaps.

Interface:
  - connect() → bool
  - read_tick() → OrderBook | None
  - disconnect()
"""

import time
from dataclasses import dataclass
from typing import Optional

from src.execution.paper_broker import OrderBook, Level


@dataclass
class LiveFeed:
    """Adapter từ nguồn dữ liệu thực vào hệ thống PaperBroker.

    Ở Phase 5, đây là stub — chưa kết nối thật.
    Khi live data sẵn sàng, implement read_tick() gọi WebSocket/API.
    """

    symbol: str = "SANDBOX"
    _connected: bool = False

    def connect(self) -> bool:
        """Kết nối đến nguồn dữ liệu thực.

        TODO: Implement WebSocket connection to VNSTOCK API.
        """
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def read_tick(self) -> Optional[OrderBook]:
        """Đọc một tick từ nguồn thực.

        Returns None nếu không có dữ liệu mới (data gap) hoặc mất kết nối.
        Xử lý latency jitter: timestamp alignment, out-of-order rejection.
        """
        if not self._connected:
            return None
        # Stub: trả về None để test data gap handling
        return None
