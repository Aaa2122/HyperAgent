import { useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  BrainCircuit,
  CircleCheck,
  CircleX,
  Gauge,
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
  const provenance =
    latest?.state.decision?.provenance ??
    latest?.state.decision_provenance ??
    (provider.toLowerCase().includes("grok") ? "GROK" : "RULE_FALLBACK");
  const decisionStatus =
    latest?.state.decision?.status ?? latest?.state.decision_status ?? "NOMINAL";
  const isGrok = provenance === "GROK" || provenance === "CACHE";
  const [selected, setSelected] = useState("");
  const active =
    selected && plans.some((item) => item.symbol === selected)
      ? selected
      : plans[0]?.symbol;
  const plan = plans.find((item) => item.symbol === active);
  const asset = assets.find((item) => item.symbol === active);
  const decision = decisions.find((item) => item.symbol === active);
  const diagnostic = latest?.state.decision?.conviction_diagnostics?.find(
    (item) => item.symbol === active,
  );
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
      ? "hsl(var(--profit))"
      : plan.bias === "SHORT"
        ? "hsl(var(--loss))"
        : "hsl(var(--muted-foreground))";
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
    <section className="mt-6 overflow-hidden border-y border-border py-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-40 ${decisionStatus === "DEGRADED" ? "bg-warning" : "bg-insight"}`} />
              <span className={`relative h-2 w-2 rounded-full ${decisionStatus === "DEGRADED" ? "bg-warning" : "bg-insight"}`} />
            </span>
            <p className="eyebrow">
              {isGrok ? "Grok Intelligence Map" : "Intelligence Map · fallback déterministe"}
            </p>
          </div>
          <h2 className="mt-2 text-xl font-semibold tracking-[-.03em]">
            Lecture vivante du marché
          </h2>
          <p className="mt-1 max-w-xl text-[11px] text-muted-foreground">
            Régime, conviction et décision reconstruits depuis la dernière
            analyse structurée · source {provider}.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <span className={`rounded-full px-3 py-1 text-[9px] font-semibold tracking-[.08em] ${decisionStatus === "DEGRADED" ? "bg-warning/10 text-warning" : "bg-profit/10 text-profit"}`}>
            {decisionStatus} · {provenance.replaceAll("_", " ")}
          </span>
          <div className="flex flex-wrap justify-end gap-1.5">
            {ranked.map((item) => (
              <button
                key={item.symbol}
                onClick={() => setSelected(item.symbol)}
                aria-pressed={active === item.symbol}
                className={`min-h-11 rounded-full px-3 py-1.5 text-[10px] transition duration-300 ${active === item.symbol ? "bg-primary text-primary-foreground shadow-sm" : "bg-muted/50 text-muted-foreground hover:bg-accent hover:text-foreground"}`}
              >
                {item.symbol}
                <span className="ml-1.5 font-mono opacity-55">
                  {item.strength}
                </span>
              </button>
            ))}
          </div>
        </div>
      </header>
      <div className="mt-6 grid gap-6 lg:grid-cols-[220px_1fr_260px]">
        <div className="flex items-center gap-5 lg:flex-col lg:items-start">
          <Conviction value={plan.conviction} color={tone} />
          <div>
            <p className="text-[10px] text-muted-foreground">Biais stratégique</p>
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
            <p className="mt-2 font-mono text-[10px] text-muted-foreground">
              Allocation {Math.round(plan.risk_alloc * 100)}%
            </p>
          </div>
        </div>
        <div className="relative min-h-[210px] overflow-hidden rounded-[22px] bg-[radial-gradient(circle_at_50%_45%,rgba(191,90,242,.10),transparent_58%)] px-5 py-4">
          <div className="absolute inset-x-10 top-1/2 h-px bg-gradient-to-r from-transparent via-insight/40 to-transparent" />
          <div className="relative grid h-full grid-cols-[1fr_auto_1fr] items-center gap-3">
            <Thought
              icon={Radio}
              label="Marché"
              value={`${asset.ret_4h_pct >= 0 ? "+" : ""}${asset.ret_4h_pct.toFixed(2)}% · 4h`}
              color="hsl(var(--info))"
            />
            <div className="relative grid h-20 w-20 place-items-center rounded-full border border-insight/35 bg-insight/10 shadow-[0_0_50px_rgba(191,90,242,.12)]">
              <span className="absolute inset-2 animate-pulse rounded-full border border-insight/20" />
              <BrainCircuit className="h-8 w-8 text-insight" />
            </div>
            <Thought
              icon={Sparkles}
              label="Décision"
              value={`${decision?.action ?? "HOLD"}${decision?.direction ? ` ${decision.direction}` : ""}`}
              color={tone}
            />
          </div>
          <div className="relative mt-4 grid grid-cols-3 gap-5">
            <Signal label="Force tendance" value={trend} color="hsl(var(--insight))" />
            <Signal label="Canal Donchian" value={channel} color="hsl(var(--info))" />
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
            <p className="text-[9px] font-semibold uppercase tracking-[.16em] text-muted-foreground">
              Thèse de Grok
            </p>
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              {plan.thesis}
            </p>
          </div>
        </aside>
      </div>
      {diagnostic && (
        <div className="mt-6 border-t border-border pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Gauge className="h-3.5 w-3.5 text-muted-foreground" />
              <p className="eyebrow">Pourquoi {diagnostic.level.toLowerCase()} ?</p>
            </div>
            <span className={`flex items-center gap-1.5 text-[10px] ${diagnostic.actionable ? "text-profit" : "text-warning"}`}>
              {diagnostic.actionable ? <CircleCheck className="h-3 w-3" /> : <CircleX className="h-3 w-3" />}
              {diagnostic.actionable ? "Plan actionnable" : "Plan non actionnable"}
            </span>
          </div>
          <div className="mt-3 grid gap-x-8 sm:grid-cols-2">
            {diagnostic.reasons.map((reason) => (
              <details key={`${diagnostic.symbol}-${reason.code}`} className="group border-b border-border py-3">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-[11px]">
                  <span className="text-foreground/60">{reasonTitle(reason.code)}</span>
                  <span className={`text-[8px] font-semibold tracking-[.1em] ${impactTone(reason.impact)}`}>
                    {impactLabel(reason.impact)}
                  </span>
                </summary>
                <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">{reason.message}</p>
                {Object.keys(reason.evidence).length > 0 && (
                  <p className="mt-2 font-mono text-[9px] text-muted-foreground">
                    {Object.entries(reason.evidence).map(([key, value]) => `${key}: ${formatEvidence(value)}`).join(" · ")}
                  </p>
                )}
              </details>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

const reasonTitles: Record<string, string> = {
  PLAN_IS_FLAT: "Aucun biais directionnel",
  BELOW_PLAN_CONVICTION_THRESHOLD: "Conviction sous le seuil d’action",
  NO_DIRECTIONAL_TECHNICAL_EDGE: "Pas d’avantage technique directionnel",
  CONFLICTING_TECHNICAL_SIGNALS: "Signaux techniques contradictoires",
  TECHNICAL_ALIGNMENT: "Convergence technique",
  TECHNICAL_PLAN_MISMATCH: "Technique opposée au plan",
  NO_VERIFIED_CATALYST: "Aucun catalyseur vérifié",
  RESEARCH_SIGNAL_NEUTRAL: "Recherche externe neutre",
  RESEARCH_PLAN_CONFLICT: "Recherche opposée au plan",
  RESEARCH_ALIGNMENT: "Recherche alignée avec le plan",
  MANIPULATION_RISK_DOMINATES: "Risque de manipulation dominant",
};

function reasonTitle(code: string) {
  return reasonTitles[code] ?? code.replaceAll("_", " ").toLowerCase();
}

function impactLabel(impact: string) {
  return impact === "SUPPORTS" ? "SOUTIENT" : impact === "BLOCKS" ? "BLOQUE" : impact === "REDUCES" ? "RÉDUIT" : "NEUTRE";
}

function impactTone(impact: string) {
  return impact === "SUPPORTS" ? "text-profit" : impact === "BLOCKS" ? "text-loss" : impact === "REDUCES" ? "text-warning" : "text-muted-foreground";
}

function formatEvidence(value: unknown) {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  return String(value);
}

function Conviction({ value, color }: { value: number; color: string }) {
  const pct = Math.round(value * 100);
  return (
    <div
      className="grid h-28 w-28 shrink-0 place-items-center rounded-full"
      style={{
        background: `conic-gradient(${color} ${pct * 3.6}deg, hsl(var(--border)) 0deg)`,
      }}
    >
      <div className="grid h-[92px] w-[92px] place-items-center rounded-full bg-background">
        <div className="text-center">
          <p className="font-mono text-2xl">{pct}</p>
          <p className="text-[8px] uppercase tracking-[.14em] text-muted-foreground">
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
      <p className="mt-2 text-[9px] uppercase tracking-[.15em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-mono text-xs text-foreground/70">{value}</p>
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
      <div className="flex justify-between text-[9px] text-muted-foreground">
        <span>{label}</span>
        <span className="font-mono">{value}</span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted/55">
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
    <div className="flex items-end justify-between border-b border-border pb-2">
      <div>
        <p className="text-[9px] text-muted-foreground">{label}</p>
        <p className="mt-1 font-mono text-sm text-foreground/75">{value}</p>
      </div>
      <span className="text-[9px] text-muted-foreground">{hint}</span>
    </div>
  );
}
