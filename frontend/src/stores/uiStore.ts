import { create } from 'zustand';

type Tab = 'macro' | 'screener' | 'models' | 'backtest' | 'portfolio';

interface UIState {
  activeTab: Tab;
  sidebarOpen: boolean;
  xraySymbol: string | null;
  setActiveTab: (tab: Tab) => void;
  toggleSidebar: () => void;
  openXRay: (symbol: string) => void;
  closeXRay: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  activeTab: 'macro',
  sidebarOpen: true,
  xraySymbol: null,
  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  openXRay: (symbol) => set({ xraySymbol: symbol }),
  closeXRay: () => set({ xraySymbol: null }),
}));
