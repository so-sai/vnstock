import React from 'react';
import Sidebar from './Sidebar';
import { useUIStore } from '../stores/uiStore';
import MacroDashboard from '../pages/MacroDashboard';
import ScreenerPage from '../pages/ScreenerPage';

const AppLayout: React.FC = () => {
  const { activeTab } = useUIStore();

  const renderContent = () => {
    switch (activeTab) {
      case 'macro':
        return <MacroDashboard />;
      case 'screener':
        return <ScreenerPage />;
      case 'models':
        return <div className="p-8 text-japandi-earth">Alpha Models (Under Construction)</div>;
      case 'backtest':
        return <div className="p-8 text-japandi-earth">Time Kernel (Under Construction)</div>;
      default:
        return <MacroDashboard />;
    }
  };

  return (
    <div className="flex min-h-screen bg-japandi-oat">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        {renderContent()}
      </main>
    </div>
  );
};

export default AppLayout;
