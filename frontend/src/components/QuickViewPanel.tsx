import React from 'react';
import { 
  Card, 
  Title, 
  Text, 
  Metric,
  Divider, 
  Badge, 
  Button,
  AreaChart,
  Flex
} from '@tremor/react';
import { X, TrendingUp, ShieldCheck, Activity } from 'lucide-react';
import { useUIStore } from '../stores/uiStore';

const QuickViewPanel: React.FC = () => {
  const { selectedCandidate, isPanelOpen, setPanelOpen } = useUIStore();

  if (!selectedCandidate) return null;

  const mockChartData = [
    { date: '2024-01', Price: selectedCandidate.price * 0.85 },
    { date: '2024-02', Price: selectedCandidate.price * 0.92 },
    { date: '2024-03', Price: selectedCandidate.price * 0.95 },
    { date: '2024-04', Price: selectedCandidate.price },
  ];

  return (
    <div className={`fixed inset-y-0 right-0 w-96 bg-white shadow-2xl transform transition-transform duration-300 ease-in-out z-50 ${isPanelOpen ? 'translate-x-0' : 'translate-x-full'}`}>
      <div className="h-full flex flex-col p-6 overflow-y-auto bg-japandi-oat">
        <div className="flex items-center justify-between mb-8">
          <div>
            <Title className="text-japandi-earth font-bold text-2xl">{selectedCandidate.symbol}</Title>
            <Text className="text-japandi-earth/60">Candidate Reconnaissance</Text>
          </div>
          <button 
            onClick={() => setPanelOpen(false)}
            className="p-2 hover:bg-japandi-muted-clay/20 rounded-full text-japandi-earth transition-colors"
          >
            <X size={24} />
          </button>
        </div>

        <div className="space-y-6">
          <Card className="bg-white border-none shadow-sm p-4">
            <Flex>
              <Text className="text-japandi-muted-clay">Current Price</Text>
              <Badge color="emerald" className="bg-japandi-moss/10 text-japandi-moss">{selectedCandidate.signalV1}</Badge>
            </Flex>
            <Metric className="text-japandi-earth mt-1">{selectedCandidate.price.toLocaleString()} VND</Metric>
          </Card>

          <div>
            <Text className="font-semibold text-japandi-earth mb-3 flex items-center">
              <TrendingUp size={16} className="mr-2 text-japandi-moss" /> Price Trajectory
            </Text>
            <AreaChart
              className="h-48 mt-4"
              data={mockChartData}
              index="date"
              categories={["Price"]}
              colors={["emerald"]}
              showXAxis={true}
              showYAxis={false}
              showLegend={false}
              startEndOnly={true}
            />
          </div>

          <Divider />

          <div>
            <Text className="font-semibold text-japandi-earth mb-4 flex items-center">
              <ShieldCheck size={16} className="mr-2 text-japandi-earth" /> Model Checklist
            </Text>
            <div className="space-y-3">
              {[
                { label: 'Sniper Momentum', status: 'Passed' },
                { label: 'Volume Surge (15D)', status: 'Passed' },
                { label: 'Deep Dive Value', status: 'Analyzing' },
              ].map((check) => (
                <div key={check.label} className="flex justify-between items-center text-sm">
                  <span className="text-japandi-earth/70">{check.label}</span>
                  <Badge 
                    size="xs" 
                    color={check.status === 'Passed' ? 'emerald' : 'yellow'}
                    className={check.status === 'Passed' ? 'bg-japandi-moss/10 text-japandi-moss border-none' : ''}
                  >
                    {check.status}
                  </Badge>
                </div>
              ))}
            </div>
          </div>

          <div className="pt-6">
            <Button 
              className="w-full bg-japandi-earth hover:bg-japandi-earth/90 border-none py-3 text-japandi-oat rounded-xl shadow-lg flex items-center justify-center transition-all"
              icon={Activity}
            >
              Add to Watchlist
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default QuickViewPanel;
