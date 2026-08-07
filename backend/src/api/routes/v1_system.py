"""v1_system.py — API endpoint: vận hành hệ thống (EOD run, health).

Tuân thủ:
  - CLI-First Law: gọi run_eod_pipeline() từ eod_runner, không nhúng logic.
  - Song ngữ API: trả về HCI format với localization EN/VI.
  - SSE Decoupled Architecture (cho long-running EOD):
      POST → asyncio.Queue ← Background Thread (ACID) → StreamingResponse
      ACID transaction hoàn toàn cách ly khỏi I/O mạng.
"""

import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()

router = APIRouter()


# ── Request Models ──────────────────────────────────────────────────


class EODRunRequest(BaseModel):
    as_of_date: str | None = None
    force: bool = False
    catchup: bool = True


# ── EOD Blocking Endpoint (sync, for CLI) ──────────────────────────


@router.post("/system/eod-run")
async def trigger_eod_run(
    as_of_date: str | None = Query(None, description="YYYY-MM-DD"),
    force: bool = Query(False, description="Bỏ qua idempotency check"),
    catchup: bool = Query(True, description="Bù ngày nợ trước khi xử lý"),
):
    """Kích hoạt EOD Pipeline thủ công (blocking).

    Chạy toàn bộ chu trình: catch-up → paper trading → settle → CA → MtM → ledger.
    Trả về kết quả dạng song ngữ HCI.
    """
    try:
        from src.database.acid import ResourceLockedException, TransactionTimeout
        from src.engine.eod_runner import run_eod_pipeline

        result = run_eod_pipeline(
            as_of_date=as_of_date,
            force=force,
            catchup=catchup,
        )
    except ResourceLockedException as e:
        raise HTTPException(
            status_code=423,
            detail={
                "status": "SKIPPED",
                "signal": "RESOURCE_LOCKED",
                "message": str(e),
                "localization": {
                    "vi": {"name": "Đã bỏ qua", "tooltip": "Tiến trình EOD khác đang chạy. Thử lại sau."},
                    "en": {"name": "Skipped", "tooltip": "Another EOD process is running. Retry later."},
                },
            },
        )
    except TransactionTimeout as e:
        raise HTTPException(
            status_code=408,
            detail={
                "status": "TIMEOUT",
                "signal": "TRANSACTION_TIMEOUT",
                "message": str(e),
                "localization": {
                    "vi": {"name": "Quá thời gian", "tooltip": "Giao dịch EOD vượt quá SLA. Kiểm tra log telemetry."},
                    "en": {"name": "Timeout", "tooltip": "EOD transaction exceeded SLA. Check telemetry logs."},
                },
            },
        )
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        raise HTTPException(status_code=500, detail=f"EOD run failed: {str(e)}")

    status = result.get("status", "FAILED")
    status_signal = "SUCCESS" if status == "success" else "FAILED"
    status_color = "#44BB44" if status == "success" else "#FF4444"

    return {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "as_of_date": result.get("as_of_date"),
        "correlation_id": result.get("correlation_id", result.get("corr_id")),
        "result": {
            "signal": status_signal,
            "color_code": status_color,
            "localization": {
                "vi": {
                    "name": "Thành công" if status == "success" else "Thất bại",
                    "tooltip": "EOD pipeline chạy thành công."
                    if status == "success"
                    else "EOD pipeline thất bại. Kiểm tra telemetry exceptions.",
                },
                "en": {
                    "name": "Success" if status == "success" else "Failed",
                    "tooltip": "EOD pipeline completed successfully."
                    if status == "success"
                    else "EOD pipeline failed. Check telemetry exceptions.",
                },
            },
        },
        "details": {
            k: v for k, v in result.items() if k not in ("status", "as_of_date", "correlation_id", "corr_id", "timestamp")
        },
    }


# ── SSE Streaming Endpoint (decoupled from ACID) ───────────────────


@router.post("/system/eod-run/stream")
async def trigger_eod_run_stream(body: EODRunRequest):
    """Kích hoạt EOD Pipeline với SSE streaming progress.

    Decoupled Architecture (3 lớp cách ly):
      1. POST handler → tạo Queue + launch Background Task (asyncio.create_task).
      2. Background Task → chạy ACID transaction trong ThreadPool,
         đẩy progress vào Queue qua call_soon_threadsafe.
      3. SSE Emitter → đọc Queue, yield về Client. KHÔNG chạm CSDL.

    ACID transaction (global_transaction) hoàn toàn cách ly khỏi
    tốc độ mạng của Client. Queue đầy không làm sập tiến trình định lượng.

    Frontend dùng @microsoft/fetch-event-source (POST-compatible EventSource).
    """
    from src.database.acid import ResourceLockedException, TransactionTimeout

    queue: asyncio.Queue = asyncio.Queue()

    async def _background_worker():
        """Vòng đời: queue START → run_in_executor → queue SUCCESS/ERROR."""
        try:
            await queue.put(
                {
                    "progress": 0,
                    "stage": "QUEUED",
                    "message": "Xếp hàng chờ xử lý...",
                    "as_of_date": body.as_of_date,
                }
            )

            loop = asyncio.get_running_loop()

            def _run_eod_in_thread():
                """Chạy EOD pipeline trong ThreadPool — cách ly khỏi asyncio."""
                try:
                    from src.engine.eod_runner import run_eod_pipeline

                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "progress": 5,
                            "stage": "START",
                            "message": "Bắt đầu EOD pipeline...",
                        },
                    )

                    result = run_eod_pipeline(
                        as_of_date=body.as_of_date,
                        force=body.force,
                        catchup=body.catchup,
                    )

                    status = result.get("status", "FAILED")
                    is_ok = status == "success"
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "progress": 100,
                            "stage": "SUCCESS" if is_ok else "FAILED",
                            "message": "EOD pipeline hoàn tất!" if is_ok else "EOD pipeline thất bại.",
                            "result": result,
                        },
                    )
                except ResourceLockedException as e:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "progress": -1,
                            "stage": "SKIPPED",
                            "message": str(e),
                        },
                    )
                except TransactionTimeout as e:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "progress": -1,
                            "stage": "TIMEOUT",
                            "message": str(e),
                        },
                    )
                except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "progress": -1,
                            "stage": "ERROR",
                            "message": str(e),
                        },
                    )

            await loop.run_in_executor(None, _run_eod_in_thread)
        except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error, OSError) as e:
            await queue.put(
                {
                    "progress": -1,
                    "stage": "FATAL",
                    "message": f"Background worker crash: {e}",
                }
            )

    asyncio.create_task(_background_worker())

    async def event_generator():
        """SSE Emitter — chỉ đọc Queue, không chạm CSDL."""
        while True:
            msg = await queue.get()
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            if msg.get("stage") in ("SUCCESS", "FAILED", "SKIPPED", "TIMEOUT", "ERROR", "FATAL"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Health Check ────────────────────────────────────────────────────


@router.get("/system/health")
async def system_health():
    """Kiểm tra sức khỏe hệ thống (Health Check song ngữ)."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "localization": {
            "vi": {"name": "Sức khỏe hệ thống", "tooltip": "Hệ thống đang hoạt động bình thường."},
            "en": {"name": "System Health", "tooltip": "System is operating normally."},
        },
    }
