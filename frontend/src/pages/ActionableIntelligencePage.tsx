import React from 'react';
import { Title, Text } from '@tremor/react';
import ErrorBoundary from '../components/ErrorBoundary';
import LiveSummaryBar from '../components/LiveSummaryBar';
import PortfolioCoach from '../components/PortfolioCoach';
import OpportunityQueue from '../components/OpportunityQueue';
import ScenarioSimulation from '../components/ScenarioSimulation';
import IPOHUDWidget from '../components/IPOHUDWidget';

const ActionableIntelligencePage: React.FC = () => {
  return (
    <div className="p-8 bg-japandi-oat min-h-screen select-none">
      <div className="mb-6 border-b border-japandi-muted-clay/40 pb-4">
        <div className="flex items-start justify-between">
          <div>
            <Title className="text-japandi-earth text-2xl font-bold tracking-tight">
              Trung tâm Hành động
            </Title>
            <Text className="text-japandi-earth/50 text-xs mt-1 font-mono">
              Trung tâm Hành động Thông minh — Giai đoạn 12
            </Text>
          </div>
          <div className="w-72">
            <ErrorBoundary>
              <LiveSummaryBar />
            </ErrorBoundary>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
        <ErrorBoundary>
          <PortfolioCoach />
        </ErrorBoundary>
        <ErrorBoundary>
          <ScenarioSimulation />
        </ErrorBoundary>
        <ErrorBoundary>
          <IPOHUDWidget />
        </ErrorBoundary>
      </div>

      <ErrorBoundary>
        <OpportunityQueue topN={5} />
      </ErrorBoundary>
    </div>
  );
};

export default ActionableIntelligencePage;
