"""
IPO HUD WIDGET — Màn hình Chỉ huy 3 Chỉ báo (Thuần Việt 100%)
================================================================================

3 Chỉ báo Rút gọn:
  1. Đèn tín hiệu (🟢🟡🔴) → Khẩu lệnh thực chiến
  2. Áp suất rút vốn (0-100) → Chỉ số LDI (Liquidity Drain Index)
  3. Thời gian nhiễm độc (Countdown) → Time Decay per session

Hiển thị: Compact, dễ đọc 0.5 giây, loại bỏ toàn bộ tiếng Anh lai căng.
================================================================================
"""

import React, { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Clock, TrendingDown, Zap } from 'lucide-react';

interface IPOSignalData {
  symbol: str;
  listing_date: str;
  market_cap_billion: float;
  traffic_light: str;  # "XANH" | "VANG" | "DO"
  valuation_risk_score: float;  # 0-100
  capital_absorption_trend: str;  # "TANG_MANH" | "ON_DINH" | "GIAM"
  secondary_market_pressure: float;  # 0-100
  rotation_risk: str;  # "CAO" | "TRUNG_BINH" | "THAP"
  midcap_smallcap_pressure: float;  # 0-100
  liquidity_regime: str;  # "DONG_TIEN_MO_RONG" | "TANG_GIAN" | "THOAI_LUI"
  regime_modifier: float;  # 0.7-1.2
  days_until_decay: int;  # Countdown phiên
  action_command: dict;  # {"primaryAction": "...", "interpretation": "..."}


const TrafficLightColors = {
  "XANH": {
    bg: "bg-emerald-50",
    border: "border-emerald-300",
    pill: "bg-emerald-600 text-white",
    emoji: "🟢",
    title: "AN TOÀN",
  },
  "VANG": {
    bg: "bg-amber-50",
    border: "border-amber-300",
    pill: "bg-amber-600 text-white",
    emoji: "🟡",
    title: "THẬN TRỌNG",
  },
  "DO": {
    bg: "bg-rose-50",
    border: "border-rose-300",
    pill: "bg-rose-600 text-white",
    emoji: "🔴",
    title: "NGUY HIỂM",
  },
};

const RotationRiskColors = {
  "CAO": "text-rose-600 font-bold",
  "TRUNG_BINH": "text-amber-600 font-bold",
  "THAP": "text-emerald-600 font-bold",
};

const RotationRiskLabel = {
  "CAO": "CAO",
  "TRUNG_BINH": "TRUNG",
  "THAP": "THAP",
};


def calculate_time_decay(days_until_decay: int) -> dict:
  """
  Tính toán hình dạng suy hao theo thời gian
  
  Logic: Áp lực IPO suy giảm theo hàm exp(-t/tau)
  - Khi t=0 (hôm nay): 100% áp lực
  - Khi t=5 (5 phiên nữa): 40% áp lực
  - Khi t>10: Nhỏ hơn 15%, coi như hết tác động
  """
  import math
  
  tau = 3.0  # Time constant (phiên)
  if days_until_decay <= 0:
    decay_factor = 0.0
    status = "HẾT HẠN"
    color = "text-japandi-muted-clay"
  else:
    decay_factor = math.exp(-days_until_decay / tau)
    if days_until_decay <= 3:
      status = "NHẠY CẢM"
      color = "text-rose-600 font-bold"
    elif days_until_decay <= 6:
      status = "TRUNG BÌNH"
      color = "text-amber-600 font-bold"
    else:
      status = "GIẢM DẦN"
      color = "text-emerald-600 font-bold"
  
  return {
    "factor": decay_factor,
    "status": status,
    "color": color,
    "days_remaining": days_until_decay,
  }


def calculate_liquidity_drain_index(
  capital_absorption: str,
  secondary_market_pressure: float,
  rotation_risk: str,
) -> dict:
  """
  Tính chỉ số LDI (Liquidity Drain Index) — Áp suất rút vốn
  
  Logic:
    - TANG_MANH (strong absorption) → 70-100
    - ON_DINH (steady) → 40-70
    - GIAM (decreasing) → 0-40
  
  Điều chỉnh thêm bằng:
    - secondary_market_pressure: Độ yếu của sàn giao dịch chính
    - rotation_risk: Áp lực lên Midcap/Smallcap
  """
  
  base_score = {
    "TANG_MANH": 75,
    "ON_DINH": 50,
    "GIAM": 25,
  }.get(capital_absorption, 50)
  
  # Điều chỉnh bằng secondary market pressure (weight: 0.3)
  pressure_factor = (secondary_market_pressure / 100.0) * 0.3
  
  # Điều chỉnh bằng rotation risk (weight: 0.2)
  rotation_factor = {
    "CAO": 0.2,
    "TRUNG_BINH": 0.1,
    "THAP": 0.0,
  }.get(rotation_risk, 0.1)
  
  ldi_score = min(100, base_score + pressure_factor * 100 + rotation_factor * 100)
  
  # Phân loại mức độ
  if ldi_score >= 70:
    label = "NẶNG"
    color = "text-rose-600 font-bold"
  elif ldi_score >= 50:
    label = "TRUNG"
    color = "text-amber-600 font-bold"
  else:
    label = "NHẸ"
    color = "text-emerald-600 font-bold"
  
  return {
    "score": round(ldi_score, 1),
    "label": label,
    "color": color,
  }


const IPOHUDWidget: React.FC = () => {
  // Fetch IPO signal từ backend
  const { data: response, isLoading } = useQuery({
    queryKey: ["ipo_hud"],
    queryFn: async () => {
      const res = await fetch("/api/intelligence/ipo-signal/");
      if (!res.ok) throw new Error("IPO HUD load failed");
      return res.json();
    },
    refetchInterval: 60000,  // Live update 1 phút
    staleTime: 30000,
  });

  const ipo: IPOSignalData | null = response?.ipo_signal || null;

  // Tính toán Time Decay & LDI
  const decayData = useMemo(
    () => (ipo ? calculate_time_decay(ipo.days_until_decay) : null),
    [ipo]
  );

  const ldiData = useMemo(
    () =>
      ipo
        ? calculate_liquidity_drain_index(
            ipo.capital_absorption_trend,
            ipo.secondary_market_pressure,
            ipo.rotation_risk
          )
        : null,
    [ipo]
  );

  // Loading state
  if (isLoading || !ipo) {
    return (
      <div className="bg-japandi-warm-sand/20 border border-japandi-muted-clay/20 rounded-lg p-4 animate-pulse">
        <span className="text-japandi-muted-clay text-xs font-mono">
          ⏳ Đang phân tích IPO...
        </span>
      </div>
    );
  }

  const trafficLight = TrafficLightColors[ipo.traffic_light];

  // ─────────────────────────────────────────────────────────────────────
  // RENDER: 3 KHỐI CHỈ BÁO CHÍNH
  // ─────────────────────────────────────────────────────────────────────

  return (
    <div className={`bg-white/60 border ${trafficLight.border} rounded-lg overflow-hidden`}>
      {/* HEADER */}
      <div className={`${trafficLight.bg} px-4 py-3 border-b ${trafficLight.border} flex items-center justify-between`}>
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} className="text-japandi-rust" />
          <span className="text-[10px] font-mono tracking-wider text-japandi-earth/70 uppercase">
            🧿 Tín hiệu IPO — {ipo.symbol}
          </span>
        </div>
        <span className="text-2xl">{trafficLight.emoji}</span>
      </div>

      {/* BODY */}
      <div className="p-4 space-y-4">
        {/* ═══════════════════════════════════════════════════════════════ */}
        {/* KHỐI 1: ĐÈN TÍN HIỆU (Traffic Light) */}
        {/* ═══════════════════════════════════════════════════════════════ */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Zap size={13} className="text-japandi-rust" />
            <span className="text-[9px] font-mono tracking-wider text-japandi-muted-clay/70 uppercase">
              1. Tín hiệu tổng
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className={`${trafficLight.pill} px-2.5 py-1 rounded text-xs font-bold uppercase`}>
              {trafficLight.title}
            </span>
            <span className="text-[10px] text-japandi-muted-clay/60 font-mono">
              {ipo.action_command.primaryAction}
            </span>
          </div>
          <p className="text-xs text-japandi-earth/70 leading-relaxed bg-japandi-oat/40 p-2 rounded">
            {ipo.action_command.interpretation}
          </p>
        </div>

        <div className="border-t border-japandi-muted-clay/15" />

        {/* ═══════════════════════════════════════════════════════════════ */}
        {/* KHỐI 2: ÁP SUẤT RÚT VỐN (LDI — Liquidity Drain Index) */}
        {/* ═══════════════════════════════════════════════════════════════ */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <TrendingDown size={13} className="text-japandi-rust" />
            <span className="text-[9px] font-mono tracking-wider text-japandi-muted-clay/70 uppercase">
              2. Áp suất rút vốn
            </span>
          </div>
          
          {/* Progress bar */}
          <div className="space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-[10px] text-japandi-earth font-mono font-bold">
                {ldiData.label}
              </span>
              <span className={`text-sm font-mono font-bold ${ldiData.color}`}>
                {ldiData.score}/100
              </span>
            </div>
            <div className="w-full bg-japandi-muted-clay/20 rounded-full h-2 overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${
                  ldiData.score >= 70
                    ? "bg-rose-500"
                    : ldiData.score >= 50
                    ? "bg-amber-500"
                    : "bg-emerald-500"
                }`}
                style={{ width: `${ldiData.score}%` }}
              />
            </div>
          </div>

          {/* Detail rows */}
          <div className="text-[9px] space-y-1 pt-2">
            <div className="flex justify-between text-japandi-muted-clay/70">
              <span>├─ Hấp thụ vốn:</span>
              <span className="font-mono text-japandi-earth">
                {
                  {
                    TANG_MANH: "🔴 MẠNH",
                    ON_DINH: "🟡 ỔN",
                    GIAM: "🟢 GIẢM",
                  }[ipo.capital_absorption_trend]
                }
              </span>
            </div>
            <div className="flex justify-between text-japandi-muted-clay/70">
              <span>├─ Áp suất sàn giao dịch:</span>
              <span className="font-mono text-japandi-earth">{ipo.secondary_market_pressure.toFixed(0)}%</span>
            </div>
            <div className="flex justify-between text-japandi-muted-clay/70">
              <span>└─ Rủi ro Midcap/Smallcap:</span>
              <span className={`font-mono ${RotationRiskColors[ipo.rotation_risk]}`}>
                {RotationRiskLabel[ipo.rotation_risk]}
              </span>
            </div>
          </div>
        </div>

        <div className="border-t border-japandi-muted-clay/15" />

        {/* ═══════════════════════════════════════════════════════════════ */}
        {/* KHỐI 3: THỜI GIAN NHIỄM ĐỘC (Time Decay Countdown) */}
        {/* ═══════════════════════════════════════════════════════════════ */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Clock size={13} className="text-japandi-rust" />
            <span className="text-[9px] font-mono tracking-wider text-japandi-muted-clay/70 uppercase">
              3. Thời gian nhiễm độc
            </span>
          </div>

          {/* Countdown display */}
          <div className="flex items-end gap-3">
            <div className="text-center">
              <span className={`text-3xl font-mono font-bold ${decayData.color}`}>
                {decayData.days_remaining}
              </span>
              <span className="text-[8px] text-japandi-muted-clay block">phiên</span>
            </div>
            <div className="flex-1 space-y-1 pb-1">
              <div className="text-[9px] font-mono text-japandi-muted-clay/70">
                Trạng thái: <span className={decayData.color}>{decayData.status}</span>
              </div>
              <div className="w-full bg-japandi-muted-clay/20 rounded-full h-1.5 overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-rose-500 via-amber-500 to-emerald-500 transition-all duration-300"
                  style={{ width: `${Math.max(0, (decayData.days_remaining / 10) * 100)}%` }}
                />
              </div>
            </div>
          </div>

          {/* Interpretation */}
          <p className="text-[8px] text-japandi-earth/70 leading-tight">
            {decayData.days_remaining > 0
              ? `💡 Áp lực này sẽ tự động giảm dần. ${decayData.days_remaining <= 3 ? "⚠️ HẠN KỲ GẦN!" : "Còn thời gian để phòng thủ."}`
              : "✅ Áp lực IPO đã kết thúc. Hệ thống gỡ bỏ phòng thủ."}
          </p>
        </div>

        {/* FOOTER: Thông tin thêm */}
        <div className="border-t border-japandi-muted-clay/15 pt-2">
          <div className="text-[8px] text-japandi-muted-clay/60 font-mono flex justify-between">
            <span>Quy mô: {ipo.market_cap_billion.toFixed(0)}Tỷ</span>
            <span>Modifier: {ipo.regime_modifier.toFixed(2)}x</span>
            <span>Regime: {ipo.liquidity_regime}</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default IPOHUDWidget;
