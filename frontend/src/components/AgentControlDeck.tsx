import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  Radio,
  Search,
  Send,
} from "lucide-react";
import type { DashboardData } from "@/types";

const phaseMeta: Record<string, { label: string; short: string; index: number }> = {
  PAUSED: { label: "Suspendu", short: "Pause", index: 0 },
  WAITING: { label: "En attente", short: "Attente", index: 0 },
  BLOCKED: { label: "Bloqué", short: "Blocage", index: 0 },
  PREPARING: { label: "Préparation", short: "Préparer", index: 0 },
  MARKET_DATA: { label: "Données marché", short: "Marché", index: 0 },
  RESEARCH: { label: "En recherche", short: "Recherche", index: 1 },
  ANALYSIS: { label: "En analyse", short: "Analyse", index: 2 },
  DECISION: { label: "En décision", short: "Décision", index: 3 },
  VALIDATION: { label: "Validation", short: "Valider", index: 3 },
  EXECUTION: { label: "En exécution", short: "Exécuter", index: 4 },
  RECONCILIATION: { label: "Synchronisation", short: "Synchroniser", index: 4 },
  FINALIZING: { label: "Finalisation", short: "Finaliser", index: 4 },
};

const reasonLabels: Record<string, string> = {
  CAPITAL_AVAILABLE: "Capital disponible : l’agent peut rechercher de nouvelles opportunités.",
  INSUFFICIENT_DEPLOYABLE_CAPITAL_PROTECTIONS_ACTIVE:
    "Capital déployable insuffisant : les recherches sont suspendues, les positions restent surveillées.",
  INSUFFICIENT_CAPITAL_NO_POSITION:
    "Capital insuffisant et aucune position ouverte : le prochain contrôle reste planifié.",
  READINESS_TEMPORARILY_UNAVAILABLE:
    "Les données du compte sont temporairement indisponibles.",
  KILL_SWITCH_PAUSED:
    "L’agent est suspendu par l’opérateur. La surveillance des positions continue.",
  KILL_SWITCH_HALTED:
    "Arrêt d’urgence actif : aucun nouveau cycle ni nouvel ordre ne peut être lancé.",
  MAX_INTERVAL: "Cycle déclenché par l’intervalle maximal.",
  MARKET_MOVE: "Cycle déclenché par un mouvement significatif du marché.",
  MATERIAL_EVENT: "Cycle déclenché par un événement matériel détecté.",
  NO_MATERIAL_CHANGE: "Aucun changement matériel depuis la dernière analyse.",
  NON_LIVE_MODE: "Mode simulation : cycle complet autorisé.",
  OUTSIDE_ACTIVATION_WINDOW:
    "L’agent attend la prochaine plage de marché sélectionnée.",
  LIQUIDITY_FILTER_NOT_MET:
    "La plage est ouverte, mais trop peu d’actifs dépassent les seuils de liquidité.",
  LIQUIDITY_DATA_UNAVAILABLE:
    "La sonde de liquidité ne fournit pas encore assez de données fiables.",
  LIQUIDITY_OBSERVATION_PENDING:
    "Une sonde de marché en lecture seule est en cours avant tout appel coûteux.",
  EXCHANGE_NOT_READY:
    "La connexion exchange n’est pas prête pour un nouveau cycle.",
  AUTOMATION_DISABLED:
    "Les cycles d’analyse sont désactivés ; le moniteur de risque reste actif.",
  CONFIGURATION_UPDATED:
    "Nouvelle politique enregistrée ; son évaluation est imminente.",
};

const phaseSteps = [
  { label: "Marché", icon: DatabaseZap },
  { label: "Recherche", icon: Search },
  { label: "Analyse", icon: BrainCircuit },
  { label: "Décision", icon: CheckCircle2 },
  { label: "Exécution", icon: Send },
];

function formatCountdown(seconds: number | null) {
  if (seconds == null) return "—";
  const safe = Math.max(0, seconds);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function formatDuration(seconds?: number | null) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

export function AgentControlDeck({ data }: { data: DashboardData | null }) {
  const automation = data?.automation;
  const [clock, setClock] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const clockOffset = useMemo(() => {
    const server = automation?.server_time ? Date.parse(automation.server_time) : Number.NaN;
    return Number.isFinite(server) ? server - Date.now() : 0;
  }, [automation?.server_time]);

  const running = Boolean(automation?.running && automation?.enabled);
  const rawPhase =
    !running || data?.kill_switch === "PAUSED"
      ? "PAUSED"
      : data?.kill_switch === "HALTED"
        ? "BLOCKED"
        : automation?.activation_state === "BLOCKED"
          ? "BLOCKED"
          : automation?.activation_state === "WAITING"
            ? "WAITING"
        : automation?.phase ||
          (automation?.last_cycle_status === "RUNNING" ? "PREPARING" : "WAITING");
  const phase = phaseMeta[rawPhase] ?? phaseMeta.WAITING;
  const isWorking = running && !["WAITING", "BLOCKED", "PAUSED"].includes(rawPhase);
  const nextAt = automation?.next_cycle_at ? Date.parse(automation.next_cycle_at) : Number.NaN;
  const remaining =
    running && !isWorking && Number.isFinite(nextAt)
      ? Math.max(0, Math.ceil((nextAt - (clock + clockOffset)) / 1000))
      : null;
  const interval = Math.max(1, automation?.cycle_interval_seconds ?? 300);
  const progress = isWorking
    ? 100
    : remaining == null
      ? 0
      : Math.min(100, Math.max(0, (1 - remaining / interval) * 100));
  const killSwitchReason =
    data?.kill_switch && data.kill_switch !== "RUNNING"
      ? `KILL_SWITCH_${data.kill_switch}`
      : data?.cost_policy?.reason?.startsWith("KILL_SWITCH_")
        ? data.cost_policy.reason
        : null;
  const reasonCode = killSwitchReason
    ? killSwitchReason
    : rawPhase === "BLOCKED"
      ? automation?.activation_reason || automation?.phase_detail
      : automation?.activation_reason ||
        automation?.last_cycle_reason ||
        data?.cost_policy?.reason;
  const reason = !running
    ? "Automatisation désactivée par l’opérateur."
    : (reasonCode && reasonLabels[reasonCode]) ||
      automation?.phase_detail ||
      "En attente du prochain cycle planifié.";
  const nextLabel = Number.isFinite(nextAt)
    ? new Intl.DateTimeFormat("fr-FR", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(nextAt))
    : "—";
  const activeStep = isWorking ? phase.index : -1;
  const statusTone = rawPhase === "BLOCKED"
    ? "hsl(var(--warning))"
    : running
      ? "hsl(var(--profit))"
      : "hsl(var(--muted-foreground))";

  return (
    <section
      aria-label="Pilotage temps réel de l’agent"
      className="agent-control-deck mt-7 overflow-hidden border-y border-border py-6"
    >
      <div className="grid gap-6 lg:grid-cols-[.9fr_1.15fr_.9fr] lg:items-center">
        <div>
          <div className="flex items-center gap-2.5">
            <span
              className={`relative grid h-9 w-9 place-items-center rounded-full bg-muted/55 ${isWorking ? "agent-live-orbit" : ""}`}
              style={{ color: statusTone }}
            >
              {isWorking ? <Radio className="h-4 w-4" /> : <Activity className="h-4 w-4" />}
            </span>
            <div>
              <p className="eyebrow">État en temps réel</p>
              <p className="mt-1 flex items-center gap-2 text-sm font-medium">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${isWorking ? "animate-pulse" : ""}`}
                  style={{ backgroundColor: statusTone, boxShadow: `0 0 12px ${statusTone}` }}
                />
                {phase.label}
              </p>
            </div>
          </div>
          <p className="mt-4 max-w-sm text-[11px] leading-relaxed text-muted-foreground">{reason}</p>
        </div>

        <div className="text-center" aria-live="polite">
          <p className="eyebrow">{isWorking ? "Cycle en cours" : "Prochain cycle dans"}</p>
          <p className="mt-2 font-mono text-[clamp(2.6rem,6vw,4.5rem)] font-medium leading-none tracking-[-.065em] tabular-nums">
            {isWorking ? phase.short : formatCountdown(remaining)}
          </p>
          <div className="mx-auto mt-4 h-[2px] max-w-md overflow-hidden rounded-full bg-muted/60">
            <span
              className={`block h-full origin-left rounded-full bg-gradient-to-r from-info to-profit transition-[width] duration-700 ${isWorking ? "agent-progress-flow" : ""}`}
              style={{ width: `${isWorking ? Math.max(18, phase.index * 22) : progress}%` }}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-4 lg:justify-self-end">
          <div>
            <p className="eyebrow">Heure prévue</p>
            <p className="mt-1.5 flex items-center gap-1.5 font-mono text-sm tabular-nums text-foreground/80">
              <Clock3 className="h-3.5 w-3.5 text-muted-foreground" />
              {isWorking ? "maintenant" : nextLabel}
            </p>
          </div>
          <div>
            <p className="eyebrow">Dernier cycle</p>
            <p className="mt-1.5 font-mono text-sm tabular-nums text-foreground/80">
              {formatDuration(automation?.last_cycle_duration_seconds)}
            </p>
          </div>
          <div className="col-span-2 border-t border-border pt-3">
            <p className="text-[10px] text-muted-foreground">
              Dernier statut ·{" "}
              <span className="text-muted-foreground">{automation?.last_cycle_status ?? "aucun cycle"}</span>
            </p>
          </div>
        </div>
      </div>

      <ol className="relative mt-7 grid grid-cols-5 gap-2" aria-label="Étapes du cycle">
        <span className="pointer-events-none absolute left-[10%] right-[10%] top-4 h-px bg-muted/70" />
        {phaseSteps.map(({ label, icon: Icon }, index) => {
          const active = activeStep === index;
          const passed = isWorking && index < activeStep;
          return (
            <li key={label} className="relative z-10 flex flex-col items-center gap-2 text-center">
              <span
                className={`grid h-8 w-8 place-items-center rounded-full border transition-all duration-500 ${
                  active
                    ? "scale-110 border-info/50 bg-info/15 text-info shadow-[0_0_24px_rgba(100,210,255,.18)]"
                    : passed
                      ? "border-profit/30 bg-profit/10 text-profit"
                      : "border-border bg-background text-muted-foreground"
                }`}
              >
                <Icon className={`h-3.5 w-3.5 ${active ? "animate-pulse" : ""}`} />
              </span>
              <span className={`text-[9px] sm:text-[10px] ${active ? "text-foreground/80" : "text-muted-foreground"}`}>
                {label}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
