import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useSemanticColors } from "@/lib/theme";
import type { PerformancePoint } from "@/types";

const compact = new Intl.NumberFormat("fr-FR", {
  notation: "compact",
  maximumFractionDigits: 2,
});
const usd = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

type Props = {
  points: PerformancePoint[];
  positive: boolean;
};

export function PnlChart({ points, positive }: Props) {
  const colors = useSemanticColors();
  if (points.length < 2) {
    return (
      <div className="grid h-[280px] place-items-center text-sm text-muted-foreground">
        Historique P&amp;L en cours de construction
      </div>
    );
  }

  const color = positive ? colors.profit : colors.loss;
  const data = points.map((point) => ({
    ...point,
    label: new Date(point.time).toLocaleString("fr-FR", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }),
  }));

  return (
    <div
      className="h-[280px] w-full"
      role="img"
      aria-label="Évolution du P&L Hyperliquid sur la période sélectionnée"
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 12, right: 4, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.28} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke={colors.grid} />
          <XAxis dataKey="time" hide />
          <YAxis
            tickFormatter={(value) => compact.format(Number(value))}
            axisLine={false}
            tickLine={false}
            width={42}
            tick={{ fill: colors.axis, fontSize: 10 }}
          />
          <Tooltip
            cursor={{ stroke: colors.crosshair, strokeDasharray: "4 4" }}
            content={({ active, payload }) => {
              const item = payload?.[0]?.payload as
                | { value: number; label: string }
                | undefined;
              if (!active || !item) return null;
              return (
                <div className="rounded-2xl border border-border bg-card/95 px-3 py-2 text-card-foreground shadow-2xl backdrop-blur-xl">
                  <p className="text-[10px] text-muted-foreground">{item.label}</p>
                  <p className="mt-1 font-mono text-sm font-medium">
                    {usd.format(item.value)}
                  </p>
                </div>
              );
            }}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={2.4}
            fill="url(#pnlFill)"
            dot={false}
            activeDot={{
              r: 4,
              fill: color,
              stroke: colors.background,
              strokeWidth: 2,
            }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
