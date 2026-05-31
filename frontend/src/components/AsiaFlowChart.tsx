import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType } from 'lightweight-charts';
import type { IChartApi } from 'lightweight-charts';
import { api } from '../lib/api';

interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const AsiaFlowChart: React.FC = () => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.get<{ ohlcvHistory: Candle[] }>('/api/xray/VNINDEX?timeframe=D').then((data) => {
      if (cancelled || !chartContainerRef.current) return;
      const candles = data.ohlcvHistory;
      if (!candles || candles.length === 0) {
        setError('Không có dữ liệu nến VNINDEX');
        return;
      }
      try {
        const container = chartContainerRef.current;
        if (!container) return;
        const chart = createChart(container, {
          layout: {
            background: { type: ColorType.Solid, color: '#ffffff' },
            textColor: '#999',
            fontSize: 10,
            fontFamily: 'ui-monospace, monospace',
          },
          grid: {
            vertLines: { color: '#f0f0f0' },
            horzLines: { color: '#f0f0f0' },
          },
          width: container.clientWidth,
          height: 360,
          crosshair: { mode: 0 },
          timeScale: {
            timeVisible: false,
            borderColor: '#e5e7eb',
          },
          rightPriceScale: {
            borderColor: '#e5e7eb',
            scaleMargins: { top: 0.05, bottom: 0.25 },
          },
        });

        const candlestickSeries = chart.addCandlestickSeries({
          upColor: '#10b981',
          downColor: '#ef4444',
          borderUpColor: '#10b981',
          borderDownColor: '#ef4444',
          wickUpColor: '#10b981',
          wickDownColor: '#ef4444',
        });

        candlestickSeries.setData(
          candles.map((c) => ({
            time: c.date,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }))
        );

        const volumeSeries = chart.addHistogramSeries({
          priceFormat: { type: 'volume' },
          priceScaleId: 'volume',
        });

        chart.priceScale('volume').applyOptions({
          scaleMargins: { top: 0.8, bottom: 0 },
        });

        volumeSeries.setData(
          candles.map((c) => ({
            time: c.date,
            value: c.volume,
            color: c.close >= c.open ? '#10b981' : '#ef4444',
          }))
        );

        chart.timeScale().fitContent();
        chartRef.current = chart;
      } catch (e) {
        setError('Lỗi khởi tạo đồ thị: ' + (e instanceof Error ? e.message : 'unknown'));
      }

      const handleResize = () => {
        if (chartRef.current && container) {
          chartRef.current.applyOptions({ width: container.clientWidth });
        }
      };
      window.addEventListener('resize', handleResize);
      return () => {
        window.removeEventListener('resize', handleResize);
      };
    }).catch((err) => {
      if (!cancelled) setError('Lỗi tải dữ liệu nến: ' + err.message);
    });

    return () => {
      cancelled = true;
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, []);

  if (error) {
    return (
      <div className="flex items-center justify-center h-96 text-xs text-red-400 font-mono">
        {error}
      </div>
    );
  }

  return (
    <div ref={chartContainerRef} className="w-full h-96" />
  );
};

export default AsiaFlowChart;
