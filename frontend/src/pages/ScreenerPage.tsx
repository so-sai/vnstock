import React from 'react';
import { 
  Card, 
  Table, 
  TableHead, 
  TableRow, 
  TableHeaderCell, 
  TableBody, 
  TableCell, 
  Text, 
  Title, 
  Badge,
  Icon
} from '@tremor/react';
import { Diamond } from 'lucide-react';
import { useScreener } from '../hooks/useApi';
import { useUIStore } from '../stores/uiStore';
import QuickViewPanel from '../components/QuickViewPanel';

const ScreenerPage: React.FC = () => {
  const { data: candidates, isLoading, error } = useScreener();
  const { setSelectedCandidate, setPanelOpen } = useUIStore();

  if (isLoading) return <div className="p-8 text-japandi-earth">Scanning for Diamonds...</div>;
  if (error) return <div className="p-8 text-japandi-rust">Error loading screener data</div>;

  const handleRowClick = (candidate: any) => {
    setSelectedCandidate(candidate);
    setPanelOpen(true);
  };

  return (
    <div className="p-8 bg-japandi-oat min-h-screen relative overflow-hidden">
      <div className="mb-8 flex items-center">
        <Icon icon={Diamond} size="xl" className="text-japandi-moss mr-4" />
        <div>
          <Title className="text-japandi-earth text-3xl font-bold">Diamond Screener</Title>
          <Text className="text-japandi-earth/60">Top-tier candidates filtered by Sniper & Deep Dive models</Text>
        </div>
      </div>

      <Card className="bg-white border-none shadow-sm overflow-hidden p-0">
        <Table>
          <TableHead className="bg-japandi-warm-sand/50">
            <TableRow>
              <TableHeaderCell className="text-japandi-earth">Symbol</TableHeaderCell>
              <TableHeaderCell className="text-japandi-earth">Price</TableHeaderCell>
              <TableHeaderCell className="text-japandi-earth">Change %</TableHeaderCell>
              <TableHeaderCell className="text-japandi-earth">Return (6M)</TableHeaderCell>
              <TableHeaderCell className="text-japandi-earth">Signal</TableHeaderCell>
              <TableHeaderCell className="text-japandi-earth text-right">Vol Ratio</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {candidates?.map((item) => (
              <TableRow 
                key={item.symbol} 
                className="hover:bg-japandi-oat/50 transition-colors cursor-pointer"
                onClick={() => handleRowClick(item)}
              >
                <TableCell className="font-bold text-japandi-earth">{item.symbol}</TableCell>
                <TableCell>
                  <Text className="text-japandi-earth">{item.price.toLocaleString()} VND</Text>
                </TableCell>
                <TableCell>
                  <Text className={item.changePercent >= 0 ? "text-japandi-moss" : "text-japandi-rust"}>
                    {item.changePercent >= 0 ? '+' : ''}{item.changePercent}%
                  </Text>
                </TableCell>
                <TableCell>
                  <Text className="text-japandi-earth">{item.return6m}%</Text>
                </TableCell>
                <TableCell>
                  <Badge 
                    color={item.signalV1 === 'Breakout' ? 'emerald' : 'slate'}
                    className={item.signalV1 === 'Breakout' ? "bg-japandi-moss/20 text-japandi-moss border-none" : ""}
                  >
                    {item.signalV1}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <Text className="text-japandi-earth">{item.volumeRatio}x</Text>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <div className="mt-6">
        <Text className="text-xs text-japandi-muted-clay italic">
          * Signals are updated every 15 minutes. Click on a row for deep analysis.
        </Text>
      </div>

      <QuickViewPanel />
    </div>
  );
};

export default ScreenerPage;
