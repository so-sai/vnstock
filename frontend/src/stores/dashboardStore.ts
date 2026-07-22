/**
 * dashboardStore.ts — Zustand store quản lý state dashboard với Selector Subscriptions.
 *
 * Architecture:
 *   - Mỗi slice (sentinel, belief, rejected) là một phần độc lập của store
 *   - Components dùng useShallow() selector để subscribe đúng slice của mình
 *   - subscribeWithSelector middleware cho phép fine-grained subscriptions
 *   - Phân tách UI state (refetch intervals, filters) khỏi data fetching (React Query)
 *
 * Performance:
 *   - GovernorBeliefBridge chỉ subscribe belief slice → không re-render khi sentinel fetch
 *   - RejectedSignalsArchive chỉ subscribe rejected slice → không re-render khi belief fetch
 *   - Chart component (lightweight-charts) dùng React.memo → cách ly hoàn toàn
 */
import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';

// ── Types ──────────────────────────────────────────────────────────

export type RejectedReasonFilter = '' | 'GOVERNOR_LOCK_HDR' | 'ADX_HAIRCUT' | 'BUYING_POWER_INSUFFICIENT' | 'NO_PRICE_DATA';

export interface SentinelSlice {
  sentinelRefetchInterval: number;
  setSentinelRefetchInterval: (ms: number) => void;
}

export interface BeliefSlice {
  beliefRefetchInterval: number;
  setBeliefRefetchInterval: (ms: number) => void;
}

export interface RejectedSlice {
  rejectedRefetchInterval: number;
  setRejectedRefetchInterval: (ms: number) => void;
  rejectedReasonFilter: string;
  setRejectedReasonFilter: (reason: string) => void;
}

export interface DashboardUIState {
  /** Whether dashboard view is ready (data loaded) */
  isDashboardReady: boolean;
  setDashboardReady: (ready: boolean) => void;
  /** Sidebar panel open/close */
  isBeliefPanelOpen: boolean;
  toggleBeliefPanel: () => void;
  isRejectedPanelOpen: boolean;
  toggleRejectedPanel: () => void;
}

export type DashboardStore = SentinelSlice & BeliefSlice & RejectedSlice & DashboardUIState;

// ── Defaults ───────────────────────────────────────────────────────

const DEFAULT_SENTINEL_INTERVAL = 30000;  // 30s
const DEFAULT_BELIEF_INTERVAL = 60000;    // 60s
const DEFAULT_REJECTED_INTERVAL = 60000;  // 60s

// ── Store ──────────────────────────────────────────────────────────

export const useDashboardStore = create<DashboardStore>()(
  subscribeWithSelector((set) => ({
    // Sentinel
    sentinelRefetchInterval: DEFAULT_SENTINEL_INTERVAL,
    setSentinelRefetchInterval: (ms: number) => set({ sentinelRefetchInterval: ms }),

    // Belief
    beliefRefetchInterval: DEFAULT_BELIEF_INTERVAL,
    setBeliefRefetchInterval: (ms: number) => set({ beliefRefetchInterval: ms }),

    // Rejected
    rejectedRefetchInterval: DEFAULT_REJECTED_INTERVAL,
    setRejectedRefetchInterval: (ms: number) => set({ rejectedRefetchInterval: ms }),
    rejectedReasonFilter: '',
    setRejectedReasonFilter: (reason: string) => set({ rejectedReasonFilter: reason }),

    // Dashboard UI
    isDashboardReady: false,
    setDashboardReady: (ready: boolean) => set({ isDashboardReady: ready }),
    isBeliefPanelOpen: true,
    toggleBeliefPanel: () => set((s) => ({ isBeliefPanelOpen: !s.isBeliefPanelOpen })),
    isRejectedPanelOpen: true,
    toggleRejectedPanel: () => set((s) => ({ isRejectedPanelOpen: !s.isRejectedPanelOpen })),
  }))
);

// ── Selector Hooks (useShallow for referential equality) ───────────

/** SentinelTelemetryBar → chỉ subscribe sentinel slice */
export function useSentinelStore() {
  return useDashboardStore(
    useShallow((s) => ({
      refetchInterval: s.sentinelRefetchInterval,
      setRefetchInterval: s.setSentinelRefetchInterval,
    }))
  );
}

/** GovernorBeliefBridge → chỉ subscribe belief slice */
export function useBeliefStore() {
  return useDashboardStore(
    useShallow((s) => ({
      refetchInterval: s.beliefRefetchInterval,
      setRefetchInterval: s.setBeliefRefetchInterval,
    }))
  );
}

/** RejectedSignalsArchive → chỉ subscribe rejected slice */
export function useRejectedStore() {
  return useDashboardStore(
    useShallow((s) => ({
      refetchInterval: s.rejectedRefetchInterval,
      setRefetchInterval: s.setRejectedRefetchInterval,
      reasonFilter: s.rejectedReasonFilter,
      setReasonFilter: s.setRejectedReasonFilter,
    }))
  );
}

/** Dashboard UI state → chỉ subscribe UI slice */
export function useDashboardUI() {
  return useDashboardStore(
    useShallow((s) => ({
      isReady: s.isDashboardReady,
      setReady: s.setDashboardReady,
      isBeliefPanelOpen: s.isBeliefPanelOpen,
      toggleBeliefPanel: s.toggleBeliefPanel,
      isRejectedPanelOpen: s.isRejectedPanelOpen,
      toggleRejectedPanel: s.toggleRejectedPanel,
    }))
  );
}
