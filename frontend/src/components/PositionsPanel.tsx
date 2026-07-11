import { useEffect, useState } from "react";
import { Check, CircleDollarSign, Clock3, Target } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { PositionChart } from "@/components/PositionChart";
import type {
  DashboardData,
  PositionAnalytics,
  TargetAnalytics,
} from "@/types";

const usd = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 4,
});
const number = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 4 });
const date = new Intl.DateTimeFormat("fr-FR", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function PositionsPanel({
  data,
  analytics,
}: {
  data: DashboardData | null;
  analytics: PositionAnalytics | null;
}) {
  const [selected, setSelected] = useState("");
  useEffect(() => {
    if (!selected && data?.positions[0]) setSelected(data.positions[0].symbol);
  }, [data?.positions, selected]);
  if (!data?.positions.length)
    return (
      <div className="grid min-h-[520px] place-items-center text-center">
        <div>
          <CircleDollarSign className="mx-auto h-7 w-7 text-white/20" />
          <h2 className="mt-4 text-lg font-semibold">
            Aucune position ouverte
          </h2>
          <p className="mt-2 text-xs text-white/35">
            Les prochaines positions apparaîtront ici avec leur graphique
            professionnel.
          </p>
        </div>
      </div>
    );
  const active = selected || data.positions[0].symbol;
  const position =
    data.positions.find((item) => item.symbol === active) ?? data.positions[0];
  const detail = analytics?.positions.find(
    (item) => item.symbol === position.symbol,
  );
  const nextTarget = detail?.targets_analytics.find(
    (item) => item.status !== "ACHIEVED",
  );
  const hitCount =
    detail?.targets_analytics.filter((item) => item.status === "ACHIEVED")
      .length ?? 0;
  const pnl = position.unrealized_pnl_usd ?? 0;
  const totalNet = detail?.total_trade_net_pnl_usd ?? pnl;
  return (
    <div>
      <header className="border-b border-white/[.06] pb-5">
        <p className="text-[9px] font-semibold uppercase tracking-[.19em] text-white/30">
          Portfolio
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-[-.04em]">
          Positions ouvertes
        </h1>
        <p className="mt-1 text-xs text-white/35">
          Historique des objectifs, P&L réalisé et capital restant en temps
          réel.
        </p>
      </header>
      <div className="mt-5 flex gap-5 border-b border-white/[.06]">
        {data.positions.map((item) => (
          <button
            key={item.symbol}
            onClick={() => setSelected(item.symbol)}
            className={`border-b-2 px-1 pb-3 text-xs transition ${active === item.symbol ? "border-white text-white" : "border-transparent text-white/35 hover:text-white/70"}`}
          >
            {item.symbol}
            <span
              className={`ml-2 font-mono ${(item.unrealized_pnl_usd ?? 0) >= 0 ? "text-[#30d158]" : "text-[#ff453a]"}`}
            >
              {usd.format(item.unrealized_pnl_usd ?? 0)}
            </span>
          </button>
        ))}
      </div>
      <article key={position.symbol} className="panel-enter pt-7">
        <div className="flex flex-wrap justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-3xl font-semibold">{position.symbol}</h2>
              <Badge className="border-0 bg-white/10 text-white/60">
                {position.side} · {position.leverage}×
              </Badge>
              {hitCount > 0 && (
                <Badge className="border-0 bg-[#30d158]/10 text-[#30d158]">
                  <Check className="mr-1 h-3 w-3" />
                  {hitCount} TP atteint{hitCount > 1 ? "s" : ""}
                </Badge>
              )}
            </div>
            <p className="mt-2 text-xs text-white/35">
              Entrée {number.format(position.entry_px)} · Mark{" "}
              {number.format(position.mark_px ?? 0)} · Position restante{" "}
              {usd.format(position.notional_usd)}
            </p>
          </div>
          <div className="text-right">
            <p
              className={`font-mono text-3xl ${totalNet >= 0 ? "text-[#30d158]" : "text-[#ff453a]"}`}
            >
              {totalNet >= 0 ? "+" : ""}
              {usd.format(totalNet)}
            </p>
            <p className="mt-1 text-[11px] text-white/35">
              P&L net total du trade · latent {usd.format(pnl)}
            </p>
          </div>
        </div>
        {detail && (
          <PositionChart
            points={detail.chart}
            entry={position.entry_px}
            stop={position.invalidation_px}
            targets={detail.targets_analytics}
            mark={position.mark_px ?? position.entry_px}
            side={position.side}
          />
        )}
        {detail && (
          <TradeLifecycle
            targets={detail.targets_analytics}
            closedFraction={detail.closed_fraction_pct}
          />
        )}
        <div className="grid grid-cols-2 gap-x-8 gap-y-6 border-t border-white/[.06] py-6 sm:grid-cols-4 xl:grid-cols-8">
          <Datum
            label="P&L réalisé"
            value={usd.format(detail?.realized_pnl_usd ?? 0)}
            tone={(detail?.realized_pnl_usd ?? 0) >= 0 ? "green" : "red"}
          />
          <Datum
            label="P&L latent"
            value={usd.format(pnl)}
            tone={pnl >= 0 ? "green" : "red"}
          />
          <Datum
            label="Frais du trade"
            value={`-${usd.format(detail?.trade_fees_usd ?? 0)}`}
            tone="red"
          />
          <Datum
            label="Funding net"
            value={usd.format(detail?.funding_net_usd ?? 0)}
            tone={(detail?.funding_net_usd ?? 0) >= 0 ? "green" : "red"}
          />
          <Datum
            label="Position clôturée"
            value={`${detail?.closed_fraction_pct.toFixed(1) ?? "0"}%`}
          />
          <Datum
            label="Distance prochain TP"
            value={nextTarget ? `${nextTarget.distance_pct.toFixed(2)}%` : "—"}
          />
          <Datum
            label="Distance au stop"
            value={`${detail?.distance_to_stop_pct.toFixed(2) ?? "—"}%`}
          />
          <Datum
            label="Liquidation"
            value={
              detail?.distance_to_liquidation_pct != null
                ? `${detail.distance_to_liquidation_pct.toFixed(2)}%`
                : "—"
            }
          />
        </div>
      </article>
    </div>
  );
}

function TradeLifecycle({
  targets,
  closedFraction,
}: {
  targets: TargetAnalytics[];
  closedFraction: number;
}) {
  return (
    <section className="mb-6 border-y border-white/[.06] py-5">
      <div className="mb-4 flex items-end justify-between">
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-[.18em] text-white/25">
            Cycle de vie du trade
          </p>
          <p className="mt-1 text-xs text-white/45">
            Chaque objectif est confirmé par un fill Hyperliquid, pas uniquement
            par le passage du prix.
          </p>
        </div>
        <span className="font-mono text-xs text-white/50">
          {closedFraction.toFixed(1)}% clôturé
        </span>
      </div>
      <div className="relative grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {targets.map((target) => (
          <TargetStep key={target.level} target={target} />
        ))}
      </div>
    </section>
  );
}

function TargetStep({ target }: { target: TargetAnalytics }) {
  const hit = target.status === "ACHIEVED";
  return (
    <div
      className={`relative rounded-xl px-4 py-3 ${hit ? "bg-[#30d158]/[.07]" : "bg-white/[.025]"}`}
    >
      <div className="flex items-center justify-between">
        <span
          className={`grid h-7 w-7 place-items-center rounded-full ${hit ? "bg-[#30d158] text-black" : "bg-white/[.06] text-white/35"}`}
        >
          {hit ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Target className="h-3.5 w-3.5" />
          )}
        </span>
        <span
          className={`text-[9px] font-semibold uppercase tracking-[.14em] ${hit ? "text-[#30d158]" : "text-white/25"}`}
        >
          {hit ? "Exécuté" : target.status}
        </span>
      </div>
      <p className="mt-3 font-mono text-base">
        TP{target.level} · {number.format(target.price)}
      </p>
      {hit ? (
        <>
          <p className="mt-1 text-[10px] text-white/40">
            Fill moyen {number.format(target.average_fill_px ?? target.price)} ·{" "}
            {usd.format(target.filled_notional_usd)} clôturés
          </p>
          <p className="mt-2 font-mono text-xs text-[#30d158]">
            +{usd.format(target.realized_pnl_usd)} réalisé
          </p>
          <p className="mt-1 flex items-center gap-1 text-[9px] text-white/25">
            <Clock3 className="h-2.5 w-2.5" />
            {target.hit_at ? date.format(new Date(target.hit_at)) : "—"}
          </p>
        </>
      ) : (
        <>
          <p className="mt-1 text-[10px] text-white/35">
            Distance {target.distance_pct.toFixed(2)}% ·{" "}
            {target.reward_r.toFixed(2)}R
          </p>
          <div className="mt-3 h-1 overflow-hidden rounded-full bg-white/[.06]">
            <div
              className="h-full rounded-full bg-white/30"
              style={{ width: `${target.progress_pct}%` }}
            />
          </div>
        </>
      )}
    </div>
  );
}

function Datum({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "green" | "red";
}) {
  return (
    <div>
      <p className="text-[10px] text-white/30">{label}</p>
      <p
        className={`mt-1.5 font-mono text-sm ${tone === "green" ? "text-[#30d158]" : tone === "red" ? "text-[#ff6961]" : "text-white/80"}`}
      >
        {value}
      </p>
    </div>
  );
}
