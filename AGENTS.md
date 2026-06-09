# AGENT COGNITIVE BOOTLOADER

> **STATUS:** Kit v1.2.5 GOLD | Phase 3 Complete | Full-Stack Quant OS
> **WARNING:** This file is the operating constitution. `kit` is the authority.
> **SENTINEL LAW:** All entry-points MUST use the `_hydrate_path()` v2.2 protocol (AGENTS.md Anchor) to ensure absolute portability.

### 🧭 1. Mandatory Startup Sequence

Before taking action in this repository:

```powershell
python backend/src/utils/sentinel_check.py; kit recall
```

1. Run the startup sequence above exactly as written.
2. **SENTINEL LAW v2.2:** Every executable .py file MUST include the `_hydrate_path()` v2.2 (AGENTS.md Anchor) header. Anchor on `AGENTS.md + backend is_dir` — NEVER use `.kit or src or screener.py` (false positive in backend/src).
3. Only open docs for syntax/reference after memory hydration.
4. If `kit` is missing, check your Global PATH.
5. **ALWAYS** clear `__pycache__` after modifying `_hydrate_path()`.

## 🛠️ 2. Minimal Navigation

1. `AGENTS.md` (Constitutional Laws)
2. `.kit/docs/reference.md` (Exact Syntax)
3. `.kit/local_brain.db` (The local brain SQLite store)
4. `.kit/scripts/kitf.ps1` (Friction Logger)

## 🧠 3. Iron Laws Of Memory

1. **Markdown is volatile.** `.kit/local_brain.db` is the source of truth.
2. **Log Friction.** Use `kit learn --tag friction` or `kit learn --tag pattern` to capture decisions.
3. **Inspect First.** Never guess paths or symbol structure.
4. **Evidence over Truth.** Treat structural signals as observations to be reasoned over, not as final truths. Log all structural noise or drifts into the `.kit` system.
5. **Architectural Alignment:**
    - **Kit** = Software Brain (Memory/Logic)
    - **Vantage** = Hardware Sensor (Structure/Forensics)
    - **Git** = Time Kernel (History)

## ⚡ 4. Fast Start

- `kit recall` -> Hydrate memory
- `kit context` -> View local awareness
- `kit learn --tag pattern "..."` -> Capture patterns
- `kit learn --tag friction "..."` -> Log bugs/issues

## 🔄 5. Operational Rituals

### 📅 Daily Loop (Stateless Worker)
1. **Recall:** `kit recall`
2. **Think:** `kit-agent ask`
3. **Act & Verify:** `kit recall; kit doctor --heal; kit learn`
4. **Friction Logging:** `kit learn --tag friction --namespace "area" "description"`

### 🧠 Weekly Reflection
`kit stats; kit where; kit doctor --heal; kit recall`

**Weekly Questions:**
- *Invariant Unused:* Invariant nào không bao giờ được recall?
- *Invariant Ignored:* Invariant nào bị agent bỏ qua?
- *Repeated Decisions:* Quyết định nào lặp lại?
- *High Friction Zones:* Vùng nào friction dày đặc?

### 📊 Monthly Synthesis
Generate 3 outputs:
1. `invariants_top_10`: Real-world task triggers (not theoretical).
2. `decision_patterns`: Repeated reasoning paths/fixes.
3. `failure_modes`: Categorized (auth, config, logic, infra).

## 🛡️ 6. System Modes
- **Sealing:** `kit stats` + `vantage seal` (Freeze knowledge).
- **Purge:** Reset cognitive space for a new 'Desert Journey'.
- **Verification:** `kit doctor --heal` (Check & repair).

## 🏗️ 7. Project Architecture (Phase 3 Complete)

### Completed Phases:
- **Phase 1:** Core Engines (Regime, Breadth, RS, Screener, Momentum, Mean Reversion)
- **Phase 2:** Full-Stack (FastAPI 12 endpoints + React 5 pages, Zero TS Errors, 1.1MB JS)
- **Phase 3:** Data Pipeline & Automation (daily_updater.py, db_maintenance.py, setup_scheduler.py)

### Key Path Resolution (v2.2):
- `PROJECT_ROOT` = parent of `AGENTS.md`
- `backend_dir` = `PROJECT_ROOT/backend`
- `DATA_DIR` = `backend/data` (NOT `PROJECT_ROOT/data`)
- `LIBS_DIR` = `backend/libs` (NOT `PROJECT_ROOT/libs`)
- `PORTFOLIO_PATH` = `backend/src/portfolio/my_portfolio.json`

## 🚨 8. Operator Protocol — CLI-First Law (Layer 2–3)

> **BẮT BUỘC:** Đây là quy tắc cứng. Vi phạm = architecture drift.

### 🔑 Core Principle

```
User/Agent request
       ↓
  [1] Đọc SYSTEM_MANIFEST.yaml  ← Layer 2: Procedure
       ↓
  [2] Tìm CLI tương ứng
       ↓
  [3] Gọi: python ptck.py <command>   ← Layer 3: Policy
       ↓
  [4] Chỉ fallback import trực tiếp nếu CLI không có
       ↓
  [5] Báo cáo vào .kit/friction nếu thiếu entrypoint
```

### 📋 Nghiêm cấm

- ❌ Viết script `.py` tạm để test / query / check
- ❌ Tự `sys.path.insert()` thủ công
- ❌ Import module trực tiếp từ CLI / scratch
- ❌ Tạo `check_market.py`, `test_*.py`, `quick_*.py` ở temp

### ✅ Bắt buộc

- ✅ Đọc `SYSTEM_MANIFEST.yaml` trước mọi hành động
- ✅ Gọi `python ptck.py <command>` cho mọi thao tác
- ✅ Nếu cần operation mới → thêm vào `ptck.py` + `SYSTEM_MANIFEST.yaml`

### 📖 Ví dụ

```bash
# Thay vì viết script test → dùng CLI:
python ptck.py market          # Market snapshot
python ptck.py regime          # Regime analysis
python ptck.py report weekly   # Báo cáo tuần
python ptck.py daily-close     # Daily closer
python ptck.py telemetry reputation --refresh  # Refresh reputation
python ptck.py snapshot        # Ảnh chụp thị trường duy nhất
python ptck.py confidence      # Độ tin cậy của quyết định
python ptck.py status          # Health check
python ptck.py gold            # Gold dashboard
```

### 🗺️ SYSTEM_MANIFEST.yaml

File `SYSTEM_MANIFEST.yaml` ở project root là **single source of truth** cho mọi entrypoint. Gồm:

- `entrypoints`: danh sách tất cả CLI commands
- Mỗi entrypoint có: `description`, `cli`, `category`
- CLI command ghi đúng cú pháp (kể cả arguments)

Cấu trúc:
```yaml
entrypoints:
  market_snapshot:
    description: "Snapshot tổng quan thị trường"
    cli: "python ptck.py market"
    category: market
```

### ⚠️ Hậu quả nếu vi phạm

Sau 6 tháng nếu mỗi Agent đều tự viết script tạm:
- `check_market.py`, `test_regime.py`, `quick_snapshot.py`...
- Mỗi file import khác nhau, path khác nhau, config khác nhau
- Không ai biết đâu là entrypoint thật
- **Architecture Drift** — hệ thống mất khả năng vận hành

Đây là lý do luật này là **cứng** (hard constraint), không phải khuyến nghị.

### Critical Debug Patterns (stored in .kit):
- **NoneType API 500:** Always wrap `float()` with try/except in service layer
- **Unit Mismatch:** `entry_price * 1000` to normalize thousands → raw VND
- **Windows Encoding:** `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`
- **Hydrate Path Anchor:** Use `AGENTS.md + backend is_dir` — unique to project root
- **Package Manager (Bun):** Node.js and npm have been fully purged from the system. Always use `bun` (e.g. `bun install`, `bun run build`, `bun run dev`) for all frontend package management and building. Do NOT run `npm` or `yarn`.
- **Bộ tự đánh giá độ tin cậy (45% TRUNG_BINH):** 5 yếu tố có trọng số: thị trường rõ ràng(0.25) + cấu trúc lành mạnh(0.25) + tín hiệu đồng thuận(0.25) + biến động ổn định(0.15) + tín hiệu đáng tin(0.10). Tạm ngưng kết luận khi điểm <0.30 hoặc entropy>2.5+số trụ≤1. Dùng `python ptck.py confidence` để chạy. Output key tiếng Việt: `điểm_tin_cậy`, `mức_đánh_giá`, `tạm_ngưng_kết_luận`.

- **Thứ tự ưu tiên quyết định (CAO→THẤP):**
  1. Cấu trúc thị trường (VỠ CẤU TRÚC → DỪNG NGOÀI, không xét gì thêm)
  2. Cảnh báo sớm (chuyển pha mạnh → hạ mức hành động)
  3. Trạng thái thị trường (regime — ngữ cảnh, không override)
  4. Độ tin cậy — đo lường, không tự veto
  5. Lớp bảo vệ quyết định (decision_guard.py) — kiểm tra an toàn cuối, có quyền chặn nếu độ tin cậy < 30% hoặc entropy > 2.5 + trụ ≤ 1
  Nguyên tắc dữ liệu: regime tính 1 lần duy nhất, dùng chung cho mọi tầng. Không đọc regime từ file cache. Không recompute regime giữa chừng. Toàn bộ pipeline dùng `market_snapshot.tao_anh_chup()` làm ảnh chụp duy nhất mỗi lần chạy — chứa regime + structural + cảnh báo sớm. Dùng `python ptck.py snapshot` để xem.

<!-- GENERATED BY KIT START -->
<!-- GENERATED BY KIT END -->
