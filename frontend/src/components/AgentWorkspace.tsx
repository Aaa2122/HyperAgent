import { useState } from "react";
import { BrainCircuit, ExternalLink, ScrollText, Search } from "lucide-react";
import { AgentControlDeck } from "@/components/AgentControlDeck";
import { DecisionPipeline } from "@/components/DecisionPipeline";
import { GrokIntelligenceMap } from "@/components/GrokIntelligenceMap";
import { Hip3MarketsPanel } from "@/components/Hip3MarketsPanel";
import type { DashboardData, InstrumentRegistryData } from "@/types";

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
  instrumentRegistry,
}: {
  data: DashboardData | null;
  latest: Cycle | undefined;
  instrumentRegistry: InstrumentRegistryData | null;
}) {
  const [section, setSection] = useState<AgentSection>("convictions");

  return (
    <>
      <AgentControlDeck data={data} />
      <nav
        role="tablist"
        aria-label="Sections de l’agent"
        className="mt-7 flex w-full gap-1 border-b border-border"
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
              tabIndex={selected ? 0 : -1}
              onClick={() => setSection(id)}
              onKeyDown={(event) => {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                const current = sections.findIndex((item) => item.id === id);
                const delta = event.key === "ArrowRight" ? 1 : -1;
                const next = sections[(current + delta + sections.length) % sections.length];
                setSection(next.id);
                requestAnimationFrame(() => document.getElementById(`agent-tab-${next.id}`)?.focus());
              }}
              className={`relative flex min-h-11 flex-1 items-center justify-center gap-2 px-3 py-3 text-xs transition sm:flex-none sm:px-6 ${
                selected ? "text-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
              {count != null && (
                <span className={`font-mono text-[9px] ${selected ? "text-muted-foreground" : "text-muted-foreground/70"}`}>
                  {count}
                </span>
              )}
              {selected && (
                <span className="absolute inset-x-3 -bottom-px h-px bg-primary shadow-[0_0_12px_rgba(255,255,255,.35)]" />
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
        {section === "research" && (
          <ResearchPanel
            data={data}
            latest={latest}
            instrumentRegistry={instrumentRegistry}
          />
        )}
        {section === "activity" && <ActivityPanel data={data} />}
      </section>
    </>
  );
}

function ResearchPanel({
  data,
  latest,
  instrumentRegistry,
}: {
  data: DashboardData | null;
  latest: Cycle | undefined;
  instrumentRegistry: InstrumentRegistryData | null;
}) {
  const signals = latest?.state.research?.signals ?? [];
  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-border pb-5">
        <div>
          <p className="eyebrow">Scanner d’univers</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-.03em]">Du marché au contexte</h2>
          <p className="mt-2 max-w-xl text-xs leading-relaxed text-muted-foreground">
            Les actifs sont filtrés avant l’appel externe. Ouvrez un signal pour comprendre sa direction et sa conviction.
          </p>
        </div>
        <p className="font-mono text-xs text-muted-foreground">
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
                ? "bg-info/10 text-info"
                : "bg-muted/45 text-muted-foreground"
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
          <p className="mt-3 text-sm leading-relaxed text-foreground/60">
            {latest?.state.decision?.playbook?.payload?.regime_view ??
              "Aucune analyse stratégique disponible pour le moment."}
          </p>
        </div>
        <div className="border-t border-border lg:border-l lg:border-t-0 lg:pl-8">
          {signals.length ? (
            signals.map((signal) => (
              <details key={signal.symbol}>
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-xs font-medium">
                  <span>{signal.symbol} · {signal.direction}</span>
                  <span className="font-mono text-muted-foreground">
                    {(signal.confidence * 100).toFixed(0)}% conviction
                  </span>
                </summary>
                <p className="mt-3 max-w-2xl text-xs leading-relaxed text-muted-foreground">
                  {signal.summary || "Aucune justification fournie."}
                </p>
                <div className="mt-3 grid grid-cols-3 gap-4 text-[9px] text-muted-foreground">
                  <SignalMeter label="Confiance" value={signal.confidence} tone="hsl(var(--info))" />
                  <SignalMeter label="Nouveauté" value={signal.novelty} tone="hsl(var(--insight))" />
                  <SignalMeter label="Manipulation" value={signal.manipulation_risk} tone="hsl(var(--warning))" />
                </div>
                {signal.source_urls.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">
                    {signal.source_urls.map((source, index) => (
                      <a
                        key={source}
                        href={source}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1 text-[10px] text-info/70 transition hover:text-info"
                      >
                        Source {index + 1}<ExternalLink className="h-2.5 w-2.5" />
                      </a>
                    ))}
                  </div>
                ) : (
                  <p className="mt-3 text-[9px] text-warning/60">Aucune source externe vérifiée pour ce signal.</p>
                )}
              </details>
            ))
          ) : (
            <p className="py-8 text-sm text-muted-foreground">Aucun signal de recherche disponible.</p>
          )}
        </div>
      </div>
      <Hip3MarketsPanel registry={instrumentRegistry} />
    </div>
  );
}

function SignalMeter({ label, value, tone }: { label: string; value: number; tone: string }) {
  const width = `${Math.max(0, Math.min(100, value * 100))}%`;
  return (
    <div>
      <div className="flex justify-between gap-2"><span>{label}</span><span className="font-mono">{Math.round(value * 100)}%</span></div>
      <div className="mt-1 h-px bg-muted/60"><div className="h-px transition-all duration-700" style={{ width, backgroundColor: tone }} /></div>
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
      <div className="grid grid-cols-2 gap-x-8 gap-y-5 border-b border-border pb-6 md:grid-cols-4">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <p className="eyebrow">{label}</p>
            <p className="mt-2 font-mono text-lg text-foreground/85">{value}</p>
          </div>
        ))}
      </div>

      <div className="mt-7">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Chronologie des appels</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-[-.03em]">Journal LLM</h2>
          </div>
          <p className="text-[10px] text-muted-foreground">Cliquez pour inspecter le prompt et la réponse</p>
        </div>
        <div className="mt-5 max-h-[660px] overflow-y-auto border-t border-border pr-1">
          {data?.llm_calls?.length ? (
            data.llm_calls.map((call) => (
              <details key={call.call_id}>
                <summary className="flex cursor-pointer list-none flex-col justify-between gap-2 text-xs sm:flex-row sm:items-center">
                  <span className="font-medium">
                    {call.stage}{" "}
                    <span className="ml-2 text-muted-foreground">
                      {call.status}
                      {call.skipped_reason
                        ? ` · ${call.skipped_reason.replaceAll("_", " ")}`
                        : ""}
                    </span>
                  </span>
                  <span className="font-mono text-muted-foreground">
                    <span className="mr-3 text-muted-foreground">
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
                <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-[10px] leading-relaxed text-muted-foreground">
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
            <p className="py-10 text-sm text-muted-foreground">Aucun appel enregistré pour le moment.</p>
          )}
        </div>
      </div>
    </div>
  );
}
