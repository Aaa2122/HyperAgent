import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { Maximize2, RotateCcw } from "lucide-react";
import { useSemanticColors } from "@/lib/theme";
import type { TargetAnalytics } from "@/types";

const price = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 4 });
const volume = new Intl.NumberFormat("fr-FR", {
  notation: "compact",
  maximumFractionDigits: 2,
});
type Point = {
  time: number;
  price: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
};
type TargetLevel = number | TargetAnalytics;
type Props = {
  points: Point[];
  entry: number;
  stop: number;
  targets: TargetLevel[];
  mark: number;
  side: string;
};
type WindowId = "1H" | "4H" | "1J" | "1S" | "1M" | "TOUT";
const windows: Array<{ id: WindowId; label: string; seconds: number | null }> =
  [
    { id: "1H", label: "1H", seconds: 3_600 },
    { id: "4H", label: "4H", seconds: 14_400 },
    { id: "1J", label: "1J", seconds: 86_400 },
    { id: "1S", label: "1S", seconds: 604_800 },
    { id: "1M", label: "1M", seconds: 2_592_000 },
    { id: "TOUT", label: "Tout", seconds: null },
  ];

export function PositionChart({
  points,
  entry,
  stop,
  targets,
  mark,
  side,
}: Props) {
  const colors = useSemanticColors();
  const host = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);
  const savedTimeRange =
    useRef<
      ReturnType<IChartApi["timeScale"]>["getVisibleLogicalRange"] extends (
        ...args: any[]
      ) => infer R
        ? R
        : null
    >(null);
  const savedPriceRange = useRef<{ from: number; to: number } | null>(null);
  const savedVolumeRange = useRef<{ from: number; to: number } | null>(null);
  const [windowId, setWindowId] = useState<WindowId>("TOUT");
  const [legend, setLegend] = useState({
    open: 0,
    high: 0,
    low: 0,
    close: mark,
    volume: 0,
  });

  useEffect(() => {
    if (!host.current || !points.length) return;
    const container = host.current;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 500,
      layout: {
        background: { type: ColorType.Solid, color: colors.background },
        textColor: colors.mutedForeground,
        fontFamily: "-apple-system, BlinkMacSystemFont, Inter, sans-serif",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: colors.crosshair,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: colors.chartLabel,
        },
        horzLine: {
          color: colors.crosshair,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: colors.chartLabel,
        },
      },
      leftPriceScale: {
        visible: true,
        borderColor: colors.border,
        scaleMargins: { top: 0.08, bottom: 0.28 },
        entireTextOnly: true,
      },
      rightPriceScale: {
        visible: true,
        borderColor: colors.border,
        scaleMargins: { top: 0.78, bottom: 0.02 },
        entireTextOnly: true,
      },
      timeScale: {
        borderColor: colors.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        barSpacing: 8,
        minBarSpacing: 0.5,
        fixLeftEdge: false,
        lockVisibleTimeRangeOnResize: true,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
      kineticScroll: { mouse: true, touch: true },
      localization: {
        locale: "fr-FR",
        priceFormatter: (value: number) => price.format(value),
      },
    });
    chartApi.current = chart;
    const candles = chart.addSeries(CandlestickSeries, {
      priceScaleId: "left",
      upColor: colors.profit,
      downColor: colors.loss,
      borderUpColor: colors.profit,
      borderDownColor: colors.loss,
      wickUpColor: colors.profit,
      wickDownColor: colors.loss,
      priceLineVisible: true,
      lastValueVisible: true,
      priceFormat: {
        type: "price",
        precision: entry < 10 ? 4 : 2,
        minMove: entry < 10 ? 0.0001 : 0.01,
      },
    });
    const volumes = chart.addSeries(HistogramSeries, {
      priceScaleId: "right",
      priceFormat: { type: "volume" },
      lastValueVisible: true,
      priceLineVisible: false,
      title: "VOL",
    });
    const candleData = points.map((item) => {
      const close = item.close ?? item.price;
      const open = item.open ?? close;
      return {
        time: Math.floor(item.time / 1000) as UTCTimestamp,
        open,
        high: item.high ?? Math.max(open, close),
        low: item.low ?? Math.min(open, close),
        close,
      };
    });
    const volumeData = points.map((item) => {
      const close = item.close ?? item.price;
      const open = item.open ?? close;
      return {
        time: Math.floor(item.time / 1000) as UTCTimestamp,
        value: item.volume ?? 0,
        color: close >= open ? colors.profitSoft : colors.lossSoft,
      };
    });
    candles.setData(candleData);
    volumes.setData(volumeData);
    // Lightweight Charts does not include distant price lines in autoscale.
    // A transparent bounds series keeps SL/TP/entry inside the initial domain.
    const bounds = chart.addSeries(LineSeries, {
      priceScaleId: "left",
      color: "rgba(0,0,0,0)",
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    const normalizedTargets = targets.map((item, index) =>
      typeof item === "number"
        ? {
            level: index + 1,
            price: item,
            status: "ACTIVE",
            hit_at: null,
            realized_pnl_usd: 0,
          }
        : item,
    );
    const levelValues = [
      entry,
      stop,
      mark,
      ...normalizedTargets.map((item) => item.price),
    ];
    const firstTime = candleData[0].time;
    const lastTime = candleData[candleData.length - 1].time;
    bounds.setData(
      firstTime === lastTime
        ? [
            { time: firstTime, value: Math.min(...levelValues) },
            { time: (Number(firstTime) + 300) as UTCTimestamp, value: Math.max(...levelValues) },
          ]
        : [
            { time: firstTime, value: Math.min(...levelValues) },
            { time: lastTime, value: Math.max(...levelValues) },
          ],
    );
    candles
      .priceScale()
      .applyOptions({
        autoScale: true,
        scaleMargins: { top: 0.08, bottom: 0.28 },
      });
    volumes
      .priceScale()
      .applyOptions({
        autoScale: true,
        scaleMargins: { top: 0.78, bottom: 0.02 },
      });
    candles.createPriceLine({
      price: entry,
      color: colors.foregroundStrong,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "ENTRÉE",
    });
    candles.createPriceLine({
      price: stop,
      color: colors.loss,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "SL",
    });
    normalizedTargets.forEach((target) => {
      const hit = target.status === "ACHIEVED";
      candles.createPriceLine({
        price: target.price,
        color: hit ? colors.profitFaint : colors.profit,
        lineWidth: hit ? 2 : 1,
        lineStyle: hit ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
        title: `TP${target.level}${hit ? " ✓" : ""}`,
      });
    });
    const hitMarkers = normalizedTargets
      .filter((target) => target.status === "ACHIEVED" && target.hit_at)
      .map((target) => {
        const requested = Math.floor(
          new Date(target.hit_at as string).getTime() / 1000,
        );
        const nearest = candleData.reduce(
          (best, candle) =>
            Math.abs(Number(candle.time) - requested) <
            Math.abs(Number(best.time) - requested)
              ? candle
              : best,
          candleData[0],
        );
        return {
          time: nearest.time,
          position:
            side === "LONG" ? ("aboveBar" as const) : ("belowBar" as const),
          color: colors.profit,
          shape: "arrowDown" as const,
          text: `TP${target.level} ✓ +${target.realized_pnl_usd.toFixed(2)} $`,
        };
      });
    if (hitMarkers.length) createSeriesMarkers(candles, hitMarkers);
    candles.createPriceLine({
      price: mark,
      color: colors.info,
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: true,
      title: "LIVE",
    });
    if (savedTimeRange.current)
      chart.timeScale().setVisibleLogicalRange(savedTimeRange.current);
    else chart.timeScale().fitContent();
    if (savedPriceRange.current) {
      chart.priceScale("left").setAutoScale(false);
      chart.priceScale("left").setVisibleRange(savedPriceRange.current);
    }
    if (savedVolumeRange.current) {
      chart.priceScale("right").setAutoScale(false);
      chart.priceScale("right").setVisibleRange(savedVolumeRange.current);
    }
    chart.subscribeCrosshairMove((param) => {
      const item = param.seriesData.get(candles) as any;
      const vol = param.seriesData.get(volumes) as any;
      if (item)
        setLegend({
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
          volume: vol?.value ?? 0,
        });
    });
    const observer = new ResizeObserver(([box]) =>
      chart.applyOptions({ width: box.contentRect.width }),
    );
    observer.observe(container);
    return () => {
      savedTimeRange.current = chart.timeScale().getVisibleLogicalRange();
      savedPriceRange.current = chart.priceScale("left").getVisibleRange();
      savedVolumeRange.current = chart.priceScale("right").getVisibleRange();
      observer.disconnect();
      chart.remove();
      chartApi.current = null;
    };
  }, [points, entry, stop, targets, mark, side, colors]);

  function selectWindow(id: WindowId) {
    setWindowId(id);
    const chart = chartApi.current;
    const selection = windows.find((item) => item.id === id);
    if (!chart || !points.length || !selection) return;
    if (!selection.seconds) chart.timeScale().fitContent();
    else {
      const to = Math.floor(
        points[points.length - 1].time / 1000,
      ) as UTCTimestamp;
      chart
        .timeScale()
        .setVisibleRange({
          from: (to - selection.seconds) as UTCTimestamp,
          to,
        });
    }
  }

  const pnlPct = entry
    ? (mark / entry - 1) * 100 * (side === "LONG" ? 1 : -1)
    : 0;
  if (!points.length)
    return (
      <div className="grid h-[500px] place-items-center rounded-2xl border border-dashed border-border text-xs text-muted-foreground">
        Le graphique OHLCV apparaîtra à la prochaine position.
      </div>
    );
  return (
    <div className="my-5 overflow-hidden rounded-[22px] border border-border bg-background shadow-[0_24px_70px_rgba(0,0,0,.14)]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center gap-3">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info opacity-40" />
            <span className="relative h-2 w-2 rounded-full bg-info" />
          </span>
          <span className="text-[10px] font-semibold uppercase tracking-[.15em] text-muted-foreground">
            Marché live
          </span>
          <span className="hidden font-mono text-[10px] text-muted-foreground sm:inline">
            O <b className="text-foreground/65">{price.format(legend.open)}</b> H{" "}
            <b className="text-profit">{price.format(legend.high)}</b> L{" "}
            <b className="text-loss">{price.format(legend.low)}</b> C{" "}
            <b className="text-foreground/75">{price.format(legend.close)}</b> Vol{" "}
            <b className="text-muted-foreground">{volume.format(legend.volume)}</b>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-muted/60 px-2.5 py-1 font-mono text-[10px] text-muted-foreground">
            {price.format(mark)}
          </span>
          <span
            className={`rounded-full px-2.5 py-1 font-mono text-[10px] ${pnlPct >= 0 ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"}`}
          >
            {pnlPct >= 0 ? "+" : ""}
            {pnlPct.toFixed(2)}%
          </span>
          <button
            title="Ajuster tout le contenu"
            aria-label="Ajuster tout le contenu"
            onClick={() => chartApi.current?.timeScale().fitContent()}
            className="icon-button grid h-11 w-11 place-items-center rounded-lg bg-muted/55 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <Maximize2 className="h-3 w-3" />
          </button>
          <button
            title="Réinitialiser les échelles"
            aria-label="Réinitialiser les échelles"
            onClick={() => {
              chartApi.current
                ?.priceScale("left")
                .applyOptions({ autoScale: true });
              chartApi.current
                ?.priceScale("right")
                .applyOptions({ autoScale: true });
              chartApi.current?.timeScale().resetTimeScale();
              setWindowId("TOUT");
            }}
            className="icon-button grid h-11 w-11 place-items-center rounded-lg bg-muted/55 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <RotateCcw className="h-3 w-3" />
          </button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-4 py-2 sm:px-5">
        <LevelChip label="ENTRÉE" value={entry} color={colors.foregroundStrong} />
        <LevelChip label="SL" value={stop} color={colors.loss} />
        {targets.map((item, index) => {
          const target = typeof item === "number"
            ? { level: index + 1, price: item, status: "ACTIVE" }
            : item;
          return <LevelChip
            key={target.level}
            label={`TP${target.level}${target.status === "ACHIEVED" ? " ✓" : ""}`}
            value={target.price}
            color={target.status === "ACHIEVED" ? colors.profitFaint : colors.profit}
          />;
        })}
      </div>
      <div
        ref={host}
        className="h-[500px] w-full cursor-crosshair"
        role="img"
        aria-label={`Graphique professionnel ${side}, prix à gauche, volume à droite`}
      />
      <div className="flex items-center justify-between gap-4 border-t border-border px-4 py-2.5 sm:px-5">
        <div className="flex items-center gap-1">
          {windows.map((item) => (
            <button
              key={item.id}
              onClick={() => selectWindow(item.id)}
              aria-pressed={windowId === item.id}
              className={`min-h-11 rounded-lg px-3 py-1.5 text-[9px] transition ${windowId === item.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"}`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <p className="hidden text-[9px] text-muted-foreground/70 lg:block">
          Tes zooms et échelles sont conservés pendant les mises à jour ·
          Double-clic pour réinitialiser
        </p>
      </div>
    </div>
  );
}

function LevelChip({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <span className="flex items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-2 py-1 font-mono text-[9px] text-muted-foreground">
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: color }}
      />
      <b style={{ color }}>{label}</b>
      {price.format(value)}
    </span>
  );
}
