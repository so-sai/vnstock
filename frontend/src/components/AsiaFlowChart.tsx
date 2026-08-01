import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, type IChartApi } from 'lightweight-charts';
import type { CandlestickSeriesPartialOptions, HistogramSeriesPartialOptions } from 'lightweight-charts';
import type { BarData } from 'lightweight-charts';
import { api } from '../lib/api';
import { downsample4Point } from '../utils/lttb';
import { encodeBars, decodeBars, countInRange, dateStrToTime, STRIDE } from '../utils/bars-store';

interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const CANDLE_OPTS: CandlestickSeriesPartialOptions = {
  upColor: '#10b981',
  downColor: '#ef4444',
  borderUpColor: '#10b981',
  borderDownColor: '#ef4444',
  wickUpColor: '#10b981',
  wickDownColor: '#ef4444',
};

const VOLUME_OPTS: HistogramSeriesPartialOptions = {
  priceFormat: { type: 'volume' as const },
  priceScaleId: 'volume',
};

/** Ngưỡng: nếu visible bars > giá trị này → downsample bằng LTTB */
const ZOOM_THRESHOLD = 5000;
/** Số điểm LTTB target khi zoom-out */
const LTTB_TARGET = 2000;

const AsiaFlowChart: React.FC = () => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const flatRef = useRef<Float64Array>(new Float64Array(0));
  const isUpdatingRef = useRef(false);
  const volumeSeriesRef = useRef<ReturnType<IChartApi['addHistogramSeries']> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;
    const ac = new AbortController();

    api.get<{ ohlcvHistory: Candle[] }>('/xray/VNINDEX?timeframe=D', { signal: ac.signal })
      .then((data) => {
        if (ac.signal.aborted || !chartContainerRef.current) return;
        const container = chartContainerRef.current;

        const candles = data.ohlcvHistory;
        if (!candles || candles.length === 0) {
          setError('Không có dữ liệu nến VNINDEX');
          return;
        }

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

        // Convert Candle[] → BarData[] với numeric time
        const bars: BarData[] = candles.map((c) => ({
          time: dateStrToTime(c.date) as any,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }));

        // Lưu vào Float64Array
        flatRef.current = encodeBars(bars);

        const candlestickSeries = chart.addCandlestickSeries(CANDLE_OPTS);
        candlestickSeries.setData(bars);

        const volumeSeries = chart.addHistogramSeries(VOLUME_OPTS);
        chart.priceScale('volume').applyOptions({
          scaleMargins: { top: 0.8, bottom: 0 },
        });
        volumeSeries.setData(
          candles.map((c) => ({
            time: dateStrToTime(c.date) as any,
            value: c.volume,
            color: c.close >= c.open ? '#10b981' : '#ef4444',
          }))
        );

        chart.timeScale().fitContent();
        chartRef.current = chart;
        volumeSeriesRef.current = volumeSeries;

        // ── Zoom listener: downsample khi visible bars vượt ngưỡng ──
        chart.timeScale().subscribeVisibleTimeRangeChange(() => {
          if (isUpdatingRef.current) return;

          const range = chart.timeScale().getVisibleRange();
          if (!range) return;

          const from = range.from as number;
          const to = range.to as number;
          const visible = countInRange(flatRef.current, from, to);

          if (visible > ZOOM_THRESHOLD) {
            isUpdatingRef.current = true;
            const n = flatRef.current.length / STRIDE;
            const down = downsample4Point(flatRef.current, n, LTTB_TARGET, STRIDE);
            candlestickSeries.setData(down);
            requestAnimationFrame(() => { isUpdatingRef.current = false; });
          } else {
            // Zoom-in: render chính xác viewport
            const n = flatRef.current.length / STRIDE;
            // Binary search tìm index
            let lo = 0, hi = n;
            while (lo < hi) { const m = (lo + hi) >>> 1; if (flatRef.current[m * STRIDE] < from) lo = m + 1; else hi = m; }
            const startIdx = lo;
            hi = n;
            while (lo < hi) { const m = (lo + hi) >>> 1; if (flatRef.current[m * STRIDE] < to) lo = m + 1; else hi = m; }
            const endIdx = lo;

            if (endIdx - startIdx > 0) {
              isUpdatingRef.current = true;
              const window = decodeBars(flatRef.current, startIdx, endIdx);
              candlestickSeries.setData(window);
              requestAnimationFrame(() => { isUpdatingRef.current = false; });
            }
          }
        });

        chartRef.current = chart;
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted || (err instanceof DOMException && err.name === 'AbortError')) return;
        setError('Lỗi tải dữ liệu nến: ' + (err instanceof Error ? err.message : 'unknown'));
      });

    const handleResize = () => {
      if (chartRef.current && chartContainerRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      ac.abort();
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      volumeSeriesRef.current = null;
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
