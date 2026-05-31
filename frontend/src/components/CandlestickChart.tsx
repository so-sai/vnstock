import React, { useMemo } from 'react';
import type { OHLCVCandle } from '../types/interfaces';

interface CandlestickChartProps {
  data: OHLCVCandle[];
  height?: number;
  timeframe?: string;
}

const COLOR_UP = '#00b050';
const COLOR_DOWN = '#ff0000';
const COLOR_WICK_UP = '#00b050';
const COLOR_WICK_DOWN = '#ff0000';

const CandlestickChart: React.FC<CandlestickChartProps> = ({ data, height = 180, timeframe }) => {
  const chart = useMemo(() => {
    if (!data || data.length === 0) return null;

    const candles = data.slice(-30);
    const padding = { top: 10, right: 50, bottom: 20, left: 5 };
    const svgWidth = 380;
    const svgHeight = height;
    const chartWidth = svgWidth - padding.left - padding.right;
    const chartHeight = svgHeight - padding.top - padding.bottom;

    const allHigh = Math.max(...candles.map((c) => c.high));
    const allLow = Math.min(...candles.map((c) => c.low));
    const priceRange = allHigh - allLow || 1;
    const pricePadding = priceRange * 0.05;
    const maxPrice = allHigh + pricePadding;
    const minPrice = allLow - pricePadding;
    const totalRange = maxPrice - minPrice;

    const candleWidth = Math.max(2, (chartWidth / candles.length) * 0.6);
    const gap = chartWidth / candles.length;

    const yScale = (price: number) =>
      padding.top + chartHeight - ((price - minPrice) / totalRange) * chartHeight;

    const gridLines = 4;
    const gridPrices = Array.from({ length: gridLines + 1 }, (_, i) =>
      minPrice + (totalRange * i) / gridLines,
    );

    return {
      candles,
      svgWidth,
      svgHeight,
      padding,
      chartWidth,
      chartHeight,
      candleWidth,
      gap,
      yScale,
      gridPrices,
      maxPrice,
      minPrice,
    };
  }, [data, height]);

  if (!chart) return <div className="text-center py-8 text-japandi-muted-clay text-xs">Không có dữ liệu biểu đồ</div>;

  const { candles, svgWidth, svgHeight, padding, chartWidth, chartHeight, candleWidth, gap, yScale, gridPrices } = chart;

  const tfLabel = timeframe === 'W' ? 'W' : timeframe === 'M' ? 'M' : 'D';

  return (
    <svg width="100%" viewBox={`0 0 ${svgWidth} ${svgHeight}`} className="select-none">
      {/* Timeframe label */}
      <text x={padding.left + 4} y={svgHeight - 4} fontSize="9" fill="var(--japandi-muted-clay, #cec4a7)" fontFamily="monospace" opacity="0.6">
        {tfLabel}
      </text>
      {/* Grid lines + price labels */}
      {gridPrices.map((price, i) => (
        <g key={i}>
          <line
            x1={padding.left}
            y1={yScale(price)}
            x2={svgWidth - padding.right}
            y2={yScale(price)}
            stroke="var(--japandi-muted-clay, #cec4a7)"
            strokeOpacity="0.2"
            strokeDasharray="2,3"
          />
          <text
            x={svgWidth - padding.right + 4}
            y={yScale(price) + 3}
            fontSize="9"
            fill="var(--japandi-muted-clay, #cec4a7)"
            fontFamily="monospace"
          >
            {price.toFixed(0)}
          </text>
        </g>
      ))}

      {/* Candlesticks */}
      {candles.map((c, i) => {
        const x = padding.left + i * gap + gap / 2;
        const isUp = c.close >= c.open;
        const bodyTop = yScale(Math.max(c.open, c.close));
        const bodyBottom = yScale(Math.min(c.open, c.close));
        const bodyHeight = Math.max(1, bodyBottom - bodyTop);
        const color = isUp ? COLOR_UP : COLOR_DOWN;
        const wickColor = isUp ? COLOR_WICK_UP : COLOR_WICK_DOWN;

        return (
          <g key={i}>
            {/* Wick */}
            <line
              x1={x}
              y1={yScale(c.high)}
              x2={x}
              y2={yScale(c.low)}
              stroke={wickColor}
              strokeWidth="1"
            />
            {/* Body */}
            <rect
              x={x - candleWidth / 2}
              y={bodyTop}
              width={candleWidth}
              height={bodyHeight}
              fill={isUp ? color : color}
              stroke={color}
              strokeWidth="0.5"
              rx="0.5"
            />
          </g>
        );
      })}

      {/* Chart border */}
      <rect
        x={padding.left}
        y={padding.top}
        width={chartWidth}
        height={chartHeight}
        fill="none"
        stroke="var(--japandi-muted-clay, #cec4a7)"
        strokeOpacity="0.3"
        rx="2"
      />
    </svg>
  );
};

export default CandlestickChart;
