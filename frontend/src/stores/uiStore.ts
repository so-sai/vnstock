import { create } from 'zustand';

import type { DiamondCandidate } from '../types/interfaces';

type Tab = 'macro' | 'screener' | 'models' | 'backtest';

interface UIState {
  activeTab: Tab;
  sidebarOpen: boolean;
  selectedCandidate: DiamondCandidate | null;
  isPanelOpen: boolean;
  setActiveTab: (tab: Tab) => void;
  toggleSidebar: () => void;
  setSelectedCandidate: (candidate: DiamondCandidate | null) => void;
  setPanelOpen: (open: boolean) => void;
}

export const useUIStore = create<UIState>((set) => ({
  activeTab: 'macro',
  sidebarOpen: true,
  selectedCandidate: null,
  isPanelOpen: false,
  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSelectedCandidate: (candidate) => set({ selectedCandidate: candidate }),
  setPanelOpen: (open) => set({ isPanelOpen: open }),
}));
