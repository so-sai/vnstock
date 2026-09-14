# Source Discovery — WGC Gold ETF Flows (H2 series: ETF_DEMAND_TONNES / ETF_FLOWS_USD)

## Kết luận
**Nguồn có thể dùng được: JSON API công khai `fsapi.gold.org`.** File xlsx WGC **private**
(cần login WGC account) — không dùng được. API chỉ trả **1 snapshot hiện tại** (asOfDate),
**không có historical vintage parameter**, Wayback không capture file ETF.

→ Chiến lược PIT: **ingest snapshot hiện tại làm baseline (publication_date = fetch date) +
forward crawl hàng tuần/tháng để tự dựng vintage ladder từ giờ**. Không có PIT thật cho
quá khứ (historical obs 2003-2026 chỉ "biết" từ ngày crawl baseline).

## Checklist nguồn (đã kiểm tra)
| Item | Kết quả |
|---|---|
| URL chính thức | `https://www.gold.org/goldhub/data/gold-etfs-holdings-and-flows` |
| File xlsx | **PRIVATE** (class `download-icon private xlsx`, cần login; WAF 403 urllib) |
| JSON API | `https://fsapi.gold.org/api/v11/charts/etfv2/revised/{flows-chart2,holdings-chart2,archive-tablegroup/all}` — **200 OK public** |
| Vintage parameter | **KHÔNG có** — `break-cache`/nid/date vô hiệu, chỉ 1 snapshot |
| Wayback | file xlsx chưa từng capture; chỉ capture trang HTML (20260622142329) |
| Format | JSON, monthly (282 pts, 2003-02→2026-07) + weekly (2018→) + quarterly/yearly |
| Geography | Regional: North America / Europe / Asia / Other (giữ riêng, không cộng) |
| Unit | tonnes (demand) + usd (fund flows), cả 2 |
| Gross vs net | **Net** flows; demand = Δ holdings (đã verify table JSON) |
| Publication timestamp | `asOfDate` = data cutoff (2026-08-07), KHÔNG phải pub date |
| Historical revisions | Có (snapshot hiện tại là final revised), nhưng không có ladder quá khứ |
| Methodology | Chưa đọc được PDF (403); đã suy diễn từ data + table JSON |
| Missing months | Dữ liệu bắt đầu 2003-02; trước đó không có |
| Duplicates | Idempotent qua UNIQUE index (gold_h2_series) |
| WAF | urllib tới gold.org 403, nhưng fsapi.gold.org **mở** |

## Semantics (quan trọng)
- **`ETF_DEMAND_TONNES`** = Δ holdings (physical demand, tonnes) — khớp series "Demand (tonnes)".
- **`ETF_FLOWS_USD`** = net money flow (USD) — **KHÁC** demand do FX-hedged funds mechanics.
- Hai khái niệm khác nhau → **giữ 2 series riêng biệt**, không merge.
- Entity = region (North America/Europe/Asia/Other). Global chỉ aggregate ở feature layer.

## Guardrail PIT áp dụng
1. `publication_date` = **fetch date** (ngày crawl), KHÔNG lấy asOfDate làm pub — tránh look-ahead.
2. Không forward-fill obs thiếu; skip.
3. Provenance ghi `as_of=<asOfDate>` để tách data cutoff với pub date.
4. Forward crawl: mỗi lần chạy adapter → snapshot mới → vintage mới cho các obs.

## Cách chạy
```bash
# crawl (mỗi tuần/tháng, lưu snapshot mới)
python src/research/wgc_etf_adapter.py --fetch --db data/gold_h2.db
# ingest tất cả snapshot tích lũy (vintage ladder forward)
python src/research/wgc_etf_adapter.py --ingest --db data/gold_h2.db --pub-date <fetch date>
```

## Manifest snapshot baseline (reproducibility — snapshot KHÔNG commit, gitignored qua `data/`)
```text
source_url = https://fsapi.gold.org/api/v11/charts/etfv2/revised/flows-chart2
fetch_date = 2026-08-16
asOfDate   = 2026-08-07
content_sha256 = e8edeb5a23fbc80e60573504d5d0641d5b35c8a05d2714b2dedcfc582c9879bb
rows       = 2256
obs_range  = 2003-02 → 2026-07
regions    = 4 (North America / Europe / Asia / Other)
series     = 2 (ETF_DEMAND_TONNES / ETF_FLOWS_USD)
pub_date   = 2026-08-16 (fetch date)
```
**Phân loại publication_date:** `2026-08-16` là **first-known-to-our-system** (PIT-safe
operational vintage), CHƯA chứng minh là publisher-publication-date. Forward crawl sẽ nâng
confidence điểm này. Không nâng cấp semantics provenance khi chưa có bằng chứng.

## Điều chưa làm được (ghi nhận, không bịa)
- Methodology PDF (403) — dựa trên chứng cứ data, không đọc được tài liệu gốc.
- Không xác nhận chính xác lịch publish từng tháng (snippet: monthly trong 1 tuần sau month-end).
- Không có vintages quá khứ — historical obs không PIT-clean.