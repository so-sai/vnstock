import React from 'react';
import { NavLink } from 'react-router-dom';
import { 
  LayoutDashboard, 
  Search, 
  BarChart3, 
  History,
  ChevronLeft,
  ChevronRight,
  Wallet,
  Globe,
  Activity,
  Eye,
  Zap,
  ClipboardList,
} from 'lucide-react';
import { useUIStore } from '../stores/uiStore';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const navItems = [
  { to: '/nhip-dap-vi-mo', label: 'Nhịp đập Vĩ mô', icon: LayoutDashboard },
  { to: '/bo-loc-kim-cuong', label: 'Bộ lọc Kim cương', icon: Search },
  { to: '/mo-hinh-alpha', label: 'Mô hình Alpha', icon: BarChart3 },
  { to: '/do-rong-thi-truong', label: 'Độ rộng Ngành', icon: Globe },
  { to: '/duong-chi-lich-su', label: 'Dòng chảy Lịch sử', icon: Activity },
  { to: '/kiem-chung-lich-su', label: 'Kiểm chứng Lịch sử', icon: History },
  { to: '/so-tay-danh-muc', label: 'Sổ tay Danh mục', icon: Wallet },
  { to: '/quan-tri-danh-muc', label: 'Quản trị Danh mục', icon: Eye },
  { to: '/trung-tam-hanh-dong', label: 'Trung tâm Hành động', icon: Zap },
  { to: '/bao-cao-tuan', label: 'Báo cáo Tuần', icon: ClipboardList },
] as const;

const Sidebar: React.FC = () => {
  const { sidebarOpen, toggleSidebar } = useUIStore();

  return (
    <aside className={cn(
      "h-screen bg-japandi-warm-sand/80 backdrop-blur-xl border-r border-japandi-muted-clay/50 transition-all duration-300 ease-in-out flex flex-col",
      sidebarOpen ? "w-64" : "w-20"
    )}>
      <div className="p-6 flex items-center justify-between">
        {sidebarOpen && <span className="font-bold text-japandi-earth tracking-tighter text-xl">HỆ THỐNG PHÒNG THỦ</span>}
        <button 
          onClick={toggleSidebar}
          className="p-2 hover:bg-japandi-muted-clay/20 rounded-lg text-japandi-earth"
        >
          {sidebarOpen ? <ChevronLeft size={20} /> : <ChevronRight size={20} />}
        </button>
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/nhip-dap-vi-mo'}
            className={({ isActive }) => cn(
              "w-full flex items-center p-3 rounded-xl transition-all duration-200",
              isActive 
                ? "bg-japandi-earth text-japandi-oat shadow-md" 
                : "text-japandi-earth/70 hover:bg-japandi-muted-clay/20"
            )}
          >
            <item.icon size={22} className={cn(sidebarOpen ? "mr-4" : "mx-auto")} />
            {sidebarOpen && <span className="font-medium">{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {sidebarOpen && (
        <div className="px-4 pb-2">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-japandi-muted-clay" />
            <input
              type="text"
              placeholder="Tra cứu mã CK (Enter)..."
              className="w-full bg-white/60 backdrop-blur-sm border border-japandi-warm-sand rounded-lg pl-9 pr-3 py-2 text-xs font-mono uppercase placeholder:text-japandi-muted-clay/60 focus:outline-none focus:border-japandi-earth transition-colors"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const val = (e.target as HTMLInputElement).value.trim().toUpperCase();
                  if (val) {
                    useUIStore.getState().openXRay(val);
                    (e.target as HTMLInputElement).value = '';
                  }
                }
              }}
            />
          </div>
        </div>
      )}

      <div className="p-6 border-t border-japandi-muted-clay/30">
        <div className={cn("flex items-center", !sidebarOpen && "justify-center")}>
          <div className="w-8 h-8 rounded-full bg-japandi-moss flex items-center justify-center text-white font-bold text-xs">
            AZ
          </div>
          {sidebarOpen && (
            <div className="ml-3">
              <p className="text-sm font-semibold text-japandi-earth">Kiến trúc sư</p>
              <p className="text-xs text-japandi-muted-clay">Phiên bản 0.1.0</p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
