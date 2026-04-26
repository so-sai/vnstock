import React from 'react';
import { 
  Card, 
  Text, 
  Metric, 
  Grid, 
  CategoryBar, 
  Flex,
  BadgeDelta,
  Title
} from '@tremor/react';
import { useMacro } from '../hooks/useApi';

const MacroDashboard: React.FC = () => {
  const { data: macro, isLoading, error } = useMacro();

  if (isLoading) return <div className="p-8 text-japandi-earth">Loading the Pulse...</div>;
  if (error) return <div className="p-8 text-japandi-rust">Error loading macro data</div>;

  return (
    <div className="p-8 bg-japandi-oat min-height-screen">
      <div className="mb-8">
        <Title className="text-japandi-earth text-3xl font-bold">Sentinel Dashboard</Title>
        <Text className="text-japandi-earth/60">Real-time Macro Reconnaissance & Regime Analysis</Text>
      </div>

      {/* Regime Gauge */}
      <Card className="mb-8 bg-japandi-warm-sand border-none shadow-sm">
        <Flex>
          <Text className="text-japandi-earth font-semibold uppercase tracking-wider">Regime Score</Text>
          <BadgeDelta deltaType="increase" className="bg-japandi-moss/20 text-japandi-moss">Stable</BadgeDelta>
        </Flex>
        <Metric className="text-japandi-earth mb-4">Risk Level: {macro?.riskLevel}</Metric>
        <CategoryBar
          values={[25, 25, 25, 25]}
          colors={["emerald", "yellow", "orange", "rose"]}
          markerValue={macro?.riskLevel === 'Emerald' ? 12 : 50} // Mock mapping
          className="mt-3"
        />
      </Card>

      {/* Macro Grid */}
      <Grid numItemsLg={3} className="gap-6">
        <Card className="bg-white border-none shadow-sm p-6">
          <Text className="text-japandi-muted-clay">USD/CNH (FX Nexus)</Text>
          <Metric className="text-japandi-earth">{macro?.usdCnh}</Metric>
          <Flex className="mt-4">
            <Text className="text-xs text-japandi-muted-clay">Offshore Yuan Rate</Text>
            <BadgeDelta deltaType="moderateIncrease" size="xs">0.2%</BadgeDelta>
          </Flex>
        </Card>

        <Card className="bg-white border-none shadow-sm p-6">
          <Text className="text-japandi-muted-clay">Copper Price (Growth Proxy)</Text>
          <Metric className="text-japandi-earth">${macro?.copperPrice}</Metric>
          <Flex className="mt-4">
            <Text className="text-xs text-japandi-muted-clay">LME Futures</Text>
            <BadgeDelta deltaType="moderateDecrease" size="xs">-1.1%</BadgeDelta>
          </Flex>
        </Card>

        <Card className="bg-white border-none shadow-sm p-6">
          <Text className="text-japandi-muted-clay">Interbank O/N (Liquidity)</Text>
          <Metric className="text-japandi-earth">{macro?.interbankRate}%</Metric>
          <Flex className="mt-4">
            <Text className="text-xs text-japandi-muted-clay">VND Interbank Rate</Text>
            <BadgeDelta deltaType="unchanged" size="xs">Neutral</BadgeDelta>
          </Flex>
        </Card>
      </Grid>

      {/* System Intel */}
      <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card className="bg-japandi-warm-sand border-none shadow-sm">
          <Title className="text-japandi-earth">SBV Action</Title>
          <Text className="mt-2 text-japandi-earth/80">
            Current stance: <span className="font-bold text-japandi-moss">{macro?.sbvAction}</span>
          </Text>
          <Text className="mt-4 text-sm text-japandi-earth/60 italic">
            "The State Bank of Vietnam is currently maintaining a neutral liquidity stance, monitoring DXY movements."
          </Text>
        </Card>

        <Card className="bg-japandi-warm-sand border-none shadow-sm">
          <Title className="text-japandi-earth">DXY Index</Title>
          <Metric className="text-japandi-earth mt-2">{macro?.dxyIndex}</Metric>
          <Text className="mt-2 text-xs text-japandi-muted-clay">US Dollar Strength Index</Text>
        </Card>
      </div>
    </div>
  );
};

export default MacroDashboard;
