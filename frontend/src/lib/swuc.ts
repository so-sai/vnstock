/**
 * SWUC v1 — Signal Weighting UI Contract
 *
 * KHÔNG tạo meaning mới.
 * CHỈ ánh xạ "độ quan trọng của signal đã có" → visual weight.
 *
 * Core Principle: "One signal — many visual weights, same meaning"
 */

import { vi } from './vi-labels';

export type SignalTier = "CRITICAL" | "HIGH" | "NORMAL" | "LOW" | "BACKGROUND";

export type SignalContext = "screener" | "leadership" | "psr" | "macro" | "xray" | "portfolio" | "decision";

export interface SWUCNode {
  signal: string;
  tier: SignalTier;
  context: SignalContext;
}

/**
 * Visual Contract — mapping cố định từ tier → CSS classes
 */
export const SWUC_STYLES: Record<SignalTier, string> = {
  CRITICAL:   "bg-white/80 backdrop-blur-lg border-white/20 shadow-lg",
  HIGH:       "bg-white/70 backdrop-blur-md border-white/10",
  NORMAL:     "bg-white/60 backdrop-blur-md",
  LOW:        "bg-white/50 backdrop-blur-sm",
  BACKGROUND: "bg-white/30 backdrop-blur-sm opacity-60",
};

/**
 * Resolve signal → tier (rất nhẹ, không phải engine thật)
 *
 * Logic: mapping cố định, không thay đổi RS/screener/leadership calculation
 */
export function resolveTier(signal: string, _context?: SignalContext): SignalTier {
  const s = signal.toUpperCase();

  // CRITICAL — "cảnh báo hệ thống"
  if (s === "PSR_DRIFT")           return "CRITICAL";
  if (s === "REGIME_SHIFT")        return "CRITICAL";
  if (s === "REGIME_CRISIS")       return "CRITICAL";
  if (s === "XRAY_ALERT")          return "CRITICAL";

  // HIGH — "điểm chuyển động"
  if (s === "LEADERSHIP_CHANGE")   return "HIGH";
  if (s === "RANK_JUMP")           return "HIGH";
  if (s === "SCREENER_BREAKOUT")   return "HIGH";
  if (s === "VOLUME_SPIKE")        return "HIGH";
  if (s === "FOREIGN_FLOW")        return "HIGH";

  // NORMAL — "thông tin nền phân tích"
  if (s === "SCREENER")            return "NORMAL";
  if (s === "METRIC")              return "NORMAL";
  if (s === "TABLE")               return "NORMAL";
  if (s === "POSITION")            return "NORMAL";
  if (s === "DECISION")            return "NORMAL";

  // LOW — "phụ trợ"
  if (s === "FILTER")              return "LOW";
  if (s === "SECONDARY")           return "LOW";
  if (s === "SUPPORTING")          return "LOW";

  // BACKGROUND — "bối cảnh"
  if (s === "CHART")               return "BACKGROUND";
  if (s === "CONTEXT_MAP")         return "BACKGROUND";
  if (s === "HISTORY")             return "BACKGROUND";

  return "NORMAL";
}

/**
 * Helper: lấy SWUC class cho một signal
 */
export function swuc(signal: string, context?: SignalContext): string {
  const tier = resolveTier(signal, context);
  return SWUC_STYLES[tier];
}

/**
 * Helper: lấy tier label (để debug hoặc hiển thị)
 */
export function swucLabel(signal: string, context?: SignalContext): SignalTier {
  return resolveTier(signal, context);
}

/**
 * Helper: lấy label tiếng Việt cho signal (VI-ONLY VIEW LAYER)
 */
export function swucVi(signal: string): string {
  return vi(signal);
}

/**
 * Helper: lấy cả tier CSS + label tiếng Việt (dùng cho rendering)
 */
export function swucRender(signal: string, context?: SignalContext): {
  className: string;
  label: string;
  tier: SignalTier;
} {
  return {
    className: swuc(signal, context),
    label: swucVi(signal),
    tier: resolveTier(signal, context),
  };
}
