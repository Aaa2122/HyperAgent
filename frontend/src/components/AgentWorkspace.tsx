import { useState } from "react";
import { BrainCircuit, ScrollText, Search } from "lucide-react";
import { AgentControlDeck } from "@/components/AgentControlDeck";
import { DecisionPipeline } from "@/components/DecisionPipeline";
import { GrokIntelligenceMap } from "@/components/GrokIntelligenceMap";
import type { DashboardData } from "@/types";

const usd = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const number = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });

type AgentSection = "convictions" | "research" | "activity";
type Cycle = DashboardData["cycles"][number];

const sections = [
  { id: "convictions" as const, label: "Convictions", icon: BrainCircuit },
  { id: "research" as const, label: "Recherche", icon: Search },
  { id: "activity" as const, label: "Activité", icon: ScrollText },
];

export function AgentWorkspace({
  data,
  latest,
}: {
  data: DashboardData | null;
  latest: Cycle | undefined;
}) {
  const [section, setSection] = useState<AgentSection>("convictions");

  return (
    <>
      <AgentControlDeck data={data} />
      <nav
        role="tablist"
        aria-label="Sections de l’agent"
        className="mt-7 flex w-full gap-1 border-b border-white/[.07]"
      >
        {sections.map(({ id, label, icon: Icon }) => {
          const selected = section === id;
          const count =
            id === "research"
              ? latest?.state.research?.signals?.length
              : id === "activity"
                ? data?.llm_calls?.length
                : latest?.state.decision?.trader.decisions?.length;
          return (
            <button
              key={id}
              id={`agent-tab-${id}`}
              role="tab"
              aria-selected={selected}
              aria-controls={`agent-panel-${id}`}
              onClick={() => setSection(id)}
              className={`relative flex min-h-11 flex-1 items-center justify-center gap-2 px-3 py-3 text-xs transition sm:flex-none sm:px-6 ${
                selected ? "text-white" : "text-white/35 hover:text-white/70"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
              {count != null && (
                <span className={`font-mono text-[9px] ${selected ? "text-white/40" : "text-white/20"}`}>
                  {count}
                </span>
              )}
              {selected && (
                <span className="absolute inset-x-3 -bottom-px h-px bg-white shadow-[0_0_12px_rgba(255,255,255,.35)]" />
              )}
            </button>
          );
        })}
      </nav>

      <section
        key={section}
        id={`agent-panel-${section}`}
        role="tabpanel"
        aria-labelledby={`agent-tab-${section}`}
        className="panel-enter pt-7"
      >
        {section === "convictions" && (
          <div>
            <GrokIntelligenceMap latest={latest} />
            <DecisionPipeline decision={latest?.state.decision} />
          </div>
        )}
        {section === "research" && <ResearchPanel data={data} latest={latest} />}
        {section === "activity" && <ActivityPanel data={data} />}
      </section>
    </>
  );
}

function ResearchPanel({
  data,
  latest,
}: {
  data: DashboardData | null;
  latest: Cycle | undefined;
}) {
  const signals = latest?.state.research?.signals ?? [];
  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-white/[.06] pb-5">
        <div>
          <p className="eyebrow">Scanner d’univers</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-.03em]">Du marché au contexte</h2>
          <p className="mt-2 max-w-xl text-xs leading-relaxed text-white/40">
            Les actifs sont filtrés avant l’appel externe. Ouvrez un signal pour comprendre sa direction et sa conviction.
          </p>
        </div>
        <p className="font-mono text-xs text-white/35">
          {data?.universe_scan?.filter((item) => item.selected).length ?? 0} transmis à Grok
        </p>
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        {data?.universe_scan?.map((item) => (
          <span
            key={item.symbol}
            title={`Score ${item.score.toFixed(2)} · spread ${item.spread_bps.toFixed(2)} bps · ${item.reason}`}
            className={`rounded-full px-3 py-1.5 text-[10px] ${
              item.selected
                ? "bg-[#64d2ff]/10 text-[#64d2ff]"
                : "bg-white/[.035] text-white/30"
            }`}
          >
            {item.symbol}
            <b className="ml-1.5 font-mono">{item.score.toFixed(2)}</b>
            {item.selected && " ✓"}
          </span>
        ))}
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-[.72fr_1.28fr]">
        <div>
          <p className="eyebrow">Lecture du stratège</p>
          <p className="mt-3 text-sm leading-relaxed text-white/60">
            {latest?.state.decision?.playbook?.payload?.regime_view ??
              "Aucune analyse stratégique disponible pour le moment."}
          </p>
        </div>
        <div className="border-t border-white/[.06] lg:border-l lg:border-t-0 lg:pl-8">
          {signals.length ? (
            signals.map((signal) => (
              <details key={signal.symbol}>
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-xs font-medium">
                  <span>{signal.symbol} · {signal.direction}</span>
                  <span className="font-mono text-white/40">
                    {(signal.confidence * 100).toFixed(0)}% conviction
                  </span>
                </summary>
                <p className="mt-3 max-w-2xl text-xs leading-relaxed text-white/45">
                  {signal.summary || "Aucune justification fournie."}
                </p>
              </details>
            ))
          ) : (
            <p className="py-8 text-sm text-white/35">Aucun signal de recherche disponible.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function ActivityPanel({ data }: { data: DashboardData | null }) {
  const metrics = [
    ["Coût aujourd’hui", usd.format(data?.llm_costs?.today_usd ?? 0)],
    ["Appels / évités", `${data?.llm_costs?.call_count ?? 0} / ${data?.llm_costs?.skipped_count ?? 0}`],
    ["Tokens entrée", number.format(data?.llm_costs?.input_tokens ?? 0)],
    ["Tokens cache", number.format(data?.llm_costs?.cached_tokens ?? 0)],
  ];
  return (
    <div className="mx-auto max-w-6xl">
      <div className="grid grid-cols-2 gap-x-8 gap-y-5 border-b border-white/[.06] pb-6 md:grid-cols-4">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <p className="eyebrow">{label}</p>
            <p className="mt-2 font-mono text-lg text-white/85">{value}</p>
          </div>
        ))}
      </div>

      <div className="mt-7">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Chronologie des appels</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-[-.03em]">Journal LLM</h2>
          </div>
          <p className="text-[10px] text-white/30">Cliquez pour inspecter le prompt et la réponse</p>
        </div>
        <div className="mt-5 max-h-[660px] overflow-y-auto border-t border-white/[.06] pr-1">
          {data?.llm_calls?.length ? (
            data.llm_calls.map((call) => (
              <details key={call.call_id}>
                <summary className="flex cursor-pointer list-none flex-col justify-between gap-2 text-xs sm:flex-row sm:items-center">
                  <span className="font-medium">
                    {call.stage}{" "}
                    <span className="ml-2 text-white/30">
                      {call.status}
                      {call.skipped_reason
                        ? ` · ${call.skipped_reason.replaceAll("_", " ")}`
                        : ""}
                    </span>
                  </span>
                  <span className="font-mono text-white/40">
                    <span className="mr-3 text-white/30">
                      {new Intl.DateTimeFormat("fr-FR", {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      }).format(new Date(call.created_at))}
                      {" · "}
                    </span>
                    {usd.format(call.cost_usd)} · {call.latency_ms} ms
                  </span>
                </summary>
                <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-[10px] leading-relaxed text-white/45">
                  {JSON.stringify(
                    {
                      prompt: call.prompt,
                      réponse: call.response,
                      outils: call.tool_usage,
                      raison: call.skipped_reason,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            ))
          ) : (
            <p className="py-10 text-sm text-white/35">Aucun appel enregistré pour le moment.</p>
          )}
        </div>
      </div>
    </div>
  );
}
