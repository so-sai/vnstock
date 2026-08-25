/**
 * VI Labels — Static Semantic Overlay (VI-ONLY VIEW LAYER)
 *
 * KHÔNG phải i18n system.
 * CHỈ là map signal key → label tiếng Việt để UI đọc được.
 *
 * Rule: KHÔNG dịch engine, KHÔNG đổi signal key, CHỈ render layer.
 */

export const viLabels: Record<string, string> = {
  // ─── Market Regime ────────────────────────────────────────────
  regime:             "Trạng thái thị trường",
  regime_shift:       "Chuyển đổi trạng thái",
  regime_crisis:      "Khủng hoảng thị trường",
  regime_trending:    "Thị trường có xu hướng",
  regime_ranging:     "Thị trường đi ngang",

  // ─── System Signals ───────────────────────────────────────────
  psr:                "Phản ứng giá hệ thống",
  psr_drift:          "Sai lệch phản ứng giá",
  xray:               "Phân tích Siêu âm",
  xray_alert:         "Cảnh báo Siêu âm",

  // ─── Leadership & Rotation ────────────────────────────────────
  leadership:         "Nhóm dẫn dắt",
  leadership_change:  "Đổi nhóm dẫn dắt",
  rank_jump:          "Nhảy hạng bất ngờ",
  rotation:           "Luân chuyển ngành",
  rotation_risk:      "Rủi ro luân chuyển",

  // ─── Screener & Selection ─────────────────────────────────────
  screener:           "Bộ lọc cổ phiếu",
  screener_breakout:  "Bứt phá Bộ lọc",
  breakout:           "Bứt phá",
  filter:             "Bộ lọc",

  // ─── Volume & Liquidity ───────────────────────────────────────
  volume_spike:       "Thanh khoản đột biến",
  volume:             "Khối lượng",
  liquidity:          "Thanh khoản",
  rvol:               "Khối lượng tương đối",
  liquidity_drain:    "Thanh khoản kiệt quệ",

  // ─── Foreign Flow ─────────────────────────────────────────────
  foreign_flow:       "Dòng vốn ngoại",
  foreign_buy:        "Nước ngoài mua ròng",
  foreign_sell:       "Nước ngoài bán ròng",

  // ─── Price Action ─────────────────────────────────────────────
  momentum:           "Động lượng",
  price_action:       "Hành động giá",
  above_ma50:         "Giá trên MA50",
  below_ma50:         "Giá dưới MA50",
  above_ma200:        "Giá trên MA200",
  below_ma200:        "Giá dưới MA200",

  // ─── Risk & Warning ───────────────────────────────────────────
  risk:               "Rủi ro",
  risk_warning:       "Cảnh báo rủi ro",
  decay:              "Suy yếu xu hướng",
  time_decay:         "Thời gian nhiễm độc",

  // ─── Portfolio ────────────────────────────────────────────────
  position:           "Vị thế",
  portfolio:          "Danh mục",
  holdings:           "Danh sách nắm giữ",
  decision:           "Quyết định",
  decision_override:  "Ghi đè quyết định",

  // ─── Macro ────────────────────────────────────────────────────
  macro:              "Vĩ mô",
  fx:                 "Tỷ giá",
  gold:               "Vàng",
  sbv:                "Ngân hàng Nhà nước",
  interbank:          "Lãi suất liên ngân hàng",
  dxy:                "Chỉ số USD",

  // ─── Technical ────────────────────────────────────────────────
  metric:             "Chỉ số kỹ thuật",
  technical:          "Phân tích kỹ thuật",
  rs_rating:          "Xếp hạng RS",
  rsi:                "Chỉ số RSI",
  z_score:            "Điểm Z",

  // ─── Chart & Context ─────────────────────────────────────────
  chart:              "Biểu đồ",
  context_map:        "Bản đồ ngữ cảnh",
  history:            "Lịch sử",
  table:              "Bảng dữ liệu",
  secondary:          "Phụ trợ",
  supporting:         "Hỗ trợ",
} as const;

/**
 * Helper: lấy label tiếng Việt cho một signal key
 * Fallback: trả về key gốc nếu không tìm thấy
 */
export function vi(key: string): string {
  return viLabels[key.toLowerCase()] ?? key;
}

/**
 * Helper: lấy label tiếng Việt, fallback về label tự定义
 */
export function viOr(key: string, fallback: string): string {
  return viLabels[key.toLowerCase()] ?? fallback;
}
