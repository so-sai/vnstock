import React from 'react';
import { 
  LayoutDashboard, 
  Search, 
  BarChart3, 
  History,
  ChevronLeft,
  ChevronRight
} from 'lucide-react';
import { useUIStore } from '../stores/uiStore';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const Sidebar: React.FC = () => {
  const { activeTab, setActiveTab, sidebarOpen, toggleSidebar } = useUIStore();

  const navItems = [
    { id: 'macro', label: 'Macro Pulse', icon: LayoutDashboard },
    { id: 'screener', label: 'Diamond Screener', icon: Search },
    { id: 'models', label: 'Alpha Models', icon: BarChart3 },
    { id: 'backtest', label: 'Time Kernel', icon: History },
  ] as const;

  return (
    <aside className={cn(
      "h-screen bg-japandi-warm-sand border-r border-japandi-muted-clay transition-all duration-300 ease-in-out flex flex-col",
      sidebarOpen ? "w-64" : "w-20"
    )}>
      <div className="p-6 flex items-center justify-between">
        {sidebarOpen && <span className="font-bold text-japandi-earth tracking-tighter text-xl">SENTINEL</span>}
        <button 
          onClick={toggleSidebar}
          className="p-2 hover:bg-japandi-muted-clay/20 rounded-lg text-japandi-earth"
        >
          {sidebarOpen ? <ChevronLeft size={20} /> : <ChevronRight size={20} />}
        </button>
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            className={cn(
              "w-full flex items-center p-3 rounded-xl transition-all duration-200",
              activeTab === item.id 
                ? "bg-japandi-earth text-japandi-oat shadow-md" 
                : "text-japandi-earth/70 hover:bg-japandi-muted-clay/20"
            )}
          >
            <item.icon size={22} className={cn(sidebarOpen ? "mr-4" : "mx-auto")} />
            {sidebarOpen && <span className="font-medium">{item.label}</span>}
          </button>
        ))}
      </nav>

      <div className="p-6 border-t border-japandi-muted-clay/30">
        <div className={cn("flex items-center", !sidebarOpen && "justify-center")}>
          <div className="w-8 h-8 rounded-full bg-japandi-moss flex items-center justify-center text-white font-bold text-xs">
            AZ
          </div>
          {sidebarOpen && (
            <div className="ml-3">
              <p className="text-sm font-semibold text-japandi-earth">Architect</p>
              <p className="text-xs text-japandi-muted-clay">System Ver. 1.2.4</p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
