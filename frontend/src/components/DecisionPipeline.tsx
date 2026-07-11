import type { ReactNode } from "react"
import type { AgentDecision, Cycle, DecisionConsequences, RiskReview } from "@/types"

const usd = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD", maximumFractionDigits: 2 })

export function DecisionPipeline({ decision }: { decision?: Cycle["state"]["decision"] }) {
  if (!decision?.initial_trader) return null
  const consequences = new Map((decision.consequence_report?.decisions ?? []).map((item) => [item.symbol, item]))
  const reviews = new Map((decision.risk_review?.reviews ?? []).map((item) => [item.symbol, item]))
  return <section className="mt-6">
    <div className="mb-3 flex items-end justify-between gap-4"><div><p className="eyebrow">Pipeline de décision V2</p><p className="mt-1 text-xs text-white/35">Proposition libre → calcul neutre → une seule revue finale</p></div><span className="rounded-full bg-white/[.05] px-3 py-1 text-[10px] text-white/40">Aucune taille recommandée</span></div>
    <div className="overflow-hidden border-y border-white/[.07]">{decision.initial_trader.decisions.map((initial) => <DecisionRow key={initial.symbol} initial={initial} consequence={consequences.get(initial.symbol)} review={reviews.get(initial.symbol)} final={decision.trader.decisions.find((item) => item.symbol === initial.symbol)} />)}</div>
  </section>
}

function DecisionRow({ initial, consequence, review, final }: { initial: AgentDecision; consequence?: DecisionConsequences; review?: RiskReview; final?: AgentDecision }) {
  const changed = review?.decision === "ADJUST" || review?.decision === "CANCEL"
  return <details className="group border-b border-white/[.06] last:border-0" open={initial.action === "OPEN"}>
    <summary className="grid cursor-pointer list-none items-center gap-3 py-4 sm:grid-cols-[80px_1fr_auto]"><strong className="text-sm">{initial.symbol}</strong><div className="flex flex-wrap items-center gap-2 text-[11px]"><Pill>{initial.action}{initial.direction ? ` ${initial.direction}` : ""}</Pill>{initial.notional_usd ? <span className="font-mono text-white/65">{usd.format(initial.notional_usd)} · {initial.leverage}×</span> : null}<span className="text-white/25">→</span><Pill tone={review?.decision === "CANCEL" ? "red" : changed ? "amber" : "green"}>{review?.decision ?? "KEEP_AS_IS"}</Pill>{final && changed ? <span className="text-white/45">Final : {final.action}{final.notional_usd ? ` · ${usd.format(final.notional_usd)}` : ""}</span> : null}</div><span className="text-[10px] text-white/25 transition group-open:rotate-180">▼</span></summary>
    <div className="grid gap-5 pb-5 text-[11px] lg:grid-cols-3"><Stage title="01 · Proposition"><p className="leading-relaxed text-white/50">{initial.rationale}</p><Line label="Horizon" value={`${initial.horizon_hours ?? 0} h`} /><Line label="Confiance" value={`${Math.round(initial.confidence * 100)} %`} /></Stage><Stage title="02 · Conséquences neutres">{consequence?.action === "OPEN" ? <><Line label="Perte au stop" value={`${usd.format(consequence.stop_loss_usd)} · ${consequence.stop_loss_equity_pct.toFixed(2)} % equity`} /><Line label="Marge" value={usd.format(consequence.margin_used_usd)} /><Line label="Funding estimé" value={usd.format(consequence.funding_estimate_usd)} /><Line label="Frais + slippage" value={usd.format(consequence.fees_estimate_usd + consequence.slippage_estimate_usd)} /><Line label="Liquidation / stop" value={consequence.liquidation_to_stop_atr == null ? "—" : `${consequence.liquidation_to_stop_atr.toFixed(2)} ATR`} /><div className="mt-3 flex gap-2">{consequence.scenarios.map((item) => <span key={item.size_multiplier} className="rounded-lg bg-white/[.035] px-2 py-1 font-mono text-[9px] text-white/40">{item.size_multiplier}× · {item.stop_loss_equity_pct.toFixed(2)}%</span>)}</div></> : <p className="text-white/35">Action non croissante : aucune simulation nécessaire.</p>}</Stage><Stage title="03 · Revue finale"><p className="leading-relaxed text-white/50">{review?.reason ?? "Aucune revue disponible."}</p>{review?.material_new_information?.length ? <ul className="mt-3 space-y-1 text-white/35">{review.material_new_information.map((item) => <li key={item}>• {item}</li>)}</ul> : <p className="mt-3 text-white/25">Aucune information nouvelle matérielle.</p>}</Stage></div>
  </details>
}

function Stage({ title, children }: { title: string; children: ReactNode }) { return <div><p className="mb-3 text-[10px] font-semibold uppercase tracking-[.16em] text-white/25">{title}</p>{children}</div> }
function Line({ label, value }: { label: string; value: string }) { return <div className="mt-2 flex justify-between gap-3 border-b border-white/[.04] pb-1.5"><span className="text-white/30">{label}</span><span className="font-mono text-right text-white/60">{value}</span></div> }
function Pill({ children, tone }: { children: ReactNode; tone?: "green" | "amber" | "red" }) { const color = tone === "green" ? "bg-[#30d158]/10 text-[#30d158]" : tone === "amber" ? "bg-[#ffd60a]/10 text-[#ffd60a]" : tone === "red" ? "bg-[#ff453a]/10 text-[#ff6961]" : "bg-white/[.06] text-white/55"; return <span className={`rounded-full px-2.5 py-1 font-medium ${color}`}>{children}</span> }
