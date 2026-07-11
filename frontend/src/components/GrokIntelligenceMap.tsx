import { useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  BrainCircuit,
  Minus,
  Radio,
  Sparkles,
} from "lucide-react";
import type { Cycle } from "@/types";

export function GrokIntelligenceMap({ latest }: { latest?: Cycle }) {
  const plans = latest?.state.decision?.playbook?.payload?.plans ?? [];
  const assets = latest?.state.market_snapshot?.assets ?? [];
  const decisions = latest?.state.decision?.trader?.decisions ?? [];
  const provider = latest?.state.decision?.provider ?? "inconnu";
  const isGrok = provider.toLowerCase().includes("grok");
  const [selected, setSelected] = useState("");
  const active =
    selected && plans.some((item) => item.symbol === selected)
      ? selected
      : plans[0]?.symbol;
  const plan = plans.find((item) => item.symbol === active);
  const asset = assets.find((item) => item.symbol === active);
  const decision = decisions.find((item) => item.symbol === active);
  const ranked = useMemo(
    () =>
      plans
        .map((item) => ({
          ...item,
          strength: Math.round(item.conviction * 100),
        }))
        .sort((a, b) => b.strength - a.strength),
    [plans],
  );
  if (!plan || !asset) return null;
  const tone =
    plan.bias === "LONG"
      ? "#30d158"
      : plan.bias === "SHORT"
        ? "#ff6961"
        : "#8e8e93";
  const trend = Math.min(
    100,
    Math.round(
      ((Math.min(asset.adx_4h, 60) / 60) * 0.55 +
        Math.min(Math.abs(asset.ret_4h_pct) / 3, 1) * 0.45) *
        100,
    ),
  );
  const channel = Math.round((asset.donchian_pos_4h ?? 0.5) * 100);
  return (
    <section className="mt-6 overflow-hidden border-y border-white/[.07] py-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#bf5af2] opacity-40" />
              <span className="relative h-2 w-2 rounded-full bg-[#bf5af2]" />
            </span>
            <p className="eyebrow">
              {isGrok ? "Grok Intelligence Map" : "Intelligence Map · fallback déterministe"}
            </p>
          </div>
          <h2 className="mt-2 text-xl font-semibold tracking-[-.03em]">
            Lecture vivante du marché
          </h2>
          <p className="mt-1 max-w-xl text-[11px] text-white/35">
            Régime, conviction et décision reconstruits depuis la dernière
            analyse structurée · source {provider}.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {ranked.map((item) => (
            <button
              key={item.symbol}
              onClick={() => setSelected(item.symbol)}
              className={`rounded-full px-3 py-1.5 text-[10px] transition duration-300 ${active === item.symbol ? "bg-white text-black shadow-[0_0_30px_rgba(255,255,255,.12)]" : "bg-white/[.04] text-white/35 hover:bg-white/[.08] hover:text-white/70"}`}
            >
              {item.symbol}
              <span className="ml-1.5 font-mono opacity-55">
                {item.strength}
              </span>
            </button>
          ))}
        </div>
      </header>
      <div className="mt-6 grid gap-6 lg:grid-cols-[220px_1fr_260px]">
        <div className="flex items-center gap-5 lg:flex-col lg:items-start">
          <Conviction value={plan.conviction} color={tone} />
          <div>
            <p className="text-[10px] text-white/25">Biais stratégique</p>
            <div
              className="mt-2 flex items-center gap-2"
              style={{ color: tone }}
            >
              {plan.bias === "LONG" ? (
                <ArrowUpRight className="h-5 w-5" />
              ) : plan.bias === "SHORT" ? (
                <ArrowDownRight className="h-5 w-5" />
              ) : (
                <Minus className="h-5 w-5" />
              )}
              <strong className="text-2xl">{plan.bias}</strong>
            </div>
            <p className="mt-2 font-mono text-[10px] text-white/30">
              Allocation {Math.round(plan.risk_alloc * 100)}%
            </p>
          </div>
        </div>
        <div className="relative min-h-[210px] overflow-hidden rounded-[22px] bg-[radial-gradient(circle_at_50%_45%,rgba(191,90,242,.10),transparent_58%)] px-5 py-4">
          <div className="absolute inset-x-10 top-1/2 h-px bg-gradient-to-r from-transparent via-[#bf5af2]/40 to-transparent" />
          <div className="relative grid h-full grid-cols-[1fr_auto_1fr] items-center gap-3">
            <Thought
              icon={Radio}
              label="Marché"
              value={`${asset.ret_4h_pct >= 0 ? "+" : ""}${asset.ret_4h_pct.toFixed(2)}% · 4h`}
              color="#64d2ff"
            />
            <div className="relative grid h-20 w-20 place-items-center rounded-full border border-[#bf5af2]/35 bg-[#bf5af2]/10 shadow-[0_0_50px_rgba(191,90,242,.12)]">
              <span className="absolute inset-2 animate-pulse rounded-full border border-[#bf5af2]/20" />
              <BrainCircuit className="h-8 w-8 text-[#bf5af2]" />
            </div>
            <Thought
              icon={Sparkles}
              label="Décision"
              value={`${decision?.action ?? "HOLD"}${decision?.direction ? ` ${decision.direction}` : ""}`}
              color={tone}
            />
          </div>
          <div className="relative mt-4 grid grid-cols-3 gap-5">
            <Signal label="Force tendance" value={trend} color="#bf5af2" />
            <Signal label="Canal Donchian" value={channel} color="#64d2ff" />
            <Signal
              label="Confiance trader"
              value={Math.round((decision?.confidence ?? 0) * 100)}
              color={tone}
            />
          </div>
        </div>
        <aside className="space-y-3">
          <Metric
            label="ADX 4h"
            value={asset.adx_4h.toFixed(1)}
            hint={asset.adx_4h >= 25 ? "tendance active" : "régime faible"}
          />
          <Metric
            label="Funding / h"
            value={`${asset.funding_1h_pct.toFixed(4)}%`}
            hint={asset.funding_1h_pct > 0 ? "longs paient" : "shorts paient"}
          />
          <Metric
            label="Distance EMA20"
            value={`${(asset.dist_ema20_4h_atr ?? 0).toFixed(2)} ATR`}
            hint={`spread ${(asset.spread_bps ?? 0).toFixed(2)} bps`}
          />
          <div className="pt-2">
            <p className="text-[9px] font-semibold uppercase tracking-[.16em] text-white/25">
              Thèse de Grok
            </p>
            <p className="mt-2 text-[11px] leading-relaxed text-white/50">
              {plan.thesis}
            </p>
          </div>
        </aside>
      </div>
    </section>
  );
}

function Conviction({ value, color }: { value: number; color: string }) {
  const pct = Math.round(value * 100);
  return (
    <div
      className="grid h-28 w-28 shrink-0 place-items-center rounded-full"
      style={{
        background: `conic-gradient(${color} ${pct * 3.6}deg, rgba(255,255,255,.055) 0deg)`,
      }}
    >
      <div className="grid h-[92px] w-[92px] place-items-center rounded-full bg-[#09090b]">
        <div className="text-center">
          <p className="font-mono text-2xl">{pct}</p>
          <p className="text-[8px] uppercase tracking-[.14em] text-white/25">
            conviction
          </p>
        </div>
      </div>
    </div>
  );
}
function Thought({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: typeof Radio;
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="text-center">
      <Icon className="mx-auto h-4 w-4" style={{ color }} />
      <p className="mt-2 text-[9px] uppercase tracking-[.15em] text-white/25">
        {label}
      </p>
      <p className="mt-1 font-mono text-xs text-white/70">{value}</p>
    </div>
  );
}
function Signal({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div>
      <div className="flex justify-between text-[9px] text-white/25">
        <span>{label}</span>
        <span className="font-mono">{value}</span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[.05]">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{
            width: `${value}%`,
            backgroundColor: color,
            boxShadow: `0 0 12px ${color}`,
          }}
        />
      </div>
    </div>
  );
}
function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="flex items-end justify-between border-b border-white/[.05] pb-2">
      <div>
        <p className="text-[9px] text-white/25">{label}</p>
        <p className="mt-1 font-mono text-sm text-white/75">{value}</p>
      </div>
      <span className="text-[9px] text-white/25">{hint}</span>
    </div>
  );
}
