import React from 'react';
import Sidebar from './Sidebar';
import XRayDrawer from './XRayDrawer';
import { Outlet } from 'react-router-dom';
import { useUIStore } from '../stores/uiStore';

const AppLayout: React.FC = () => {
  const { xraySymbol, closeXRay } = useUIStore();

  return (
    <div className="flex min-h-screen bg-japandi-oat">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
      <XRayDrawer symbol={xraySymbol} onClose={closeXRay} />
    </div>
  );
};

export default AppLayout;
