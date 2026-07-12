import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BrainCircuit,
  ChartNoAxesCombined,
  Check,
  ChevronRight,
  CircleDollarSign,
  History,
  Monitor,
  Moon,
  Pause,
  Play,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sun,
  TrendingDown,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ActivationSettings } from "@/components/ActivationSettings";
import type {
  DashboardData,
  HyperliquidReadiness,
  InstrumentRegistryData,
  PerformanceData,
  PositionAnalytics,
  TradeHistoryData,
  TradeMetrics,
} from "@/types";

const usd = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const number = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
const ranges = [
  { id: "day", label: "1J" },
  { id: "week", label: "1S" },
  { id: "month", label: "1M" },
  { id: "all", label: "Tout" },
] as const;
type View =
  | "overview"
  | "positions"
  | "agent"
  | "protections"
  | "history"
  | "settings";
const views: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "overview", label: "Vue d’ensemble", icon: ChartNoAxesCombined },
  { id: "positions", label: "Positions", icon: CircleDollarSign },
  { id: "agent", label: "Agent", icon: BrainCircuit },
  { id: "protections", label: "TP & SL", icon: ShieldCheck },
  { id: "history", label: "Historique", icon: History },
  { id: "settings", label: "Réglages", icon: Settings2 },
];
type ThemePreference = "light" | "dark" | "auto";
const themeStorageKey = "hyperagent-theme";
const PnlChart = lazy(() =>
  import("@/components/PnlChart").then((module) => ({ default: module.PnlChart })),
);
const PositionsPanel = lazy(() =>
  import("@/components/PositionsPanel").then((module) => ({ default: module.PositionsPanel })),
);
const AgentWorkspace = lazy(() =>
  import("@/components/AgentWorkspace").then((module) => ({ default: module.AgentWorkspace })),
);
const TradeHistoryPanel = lazy(() =>
  import("@/components/TradeHistoryPanel").then((module) => ({ default: module.TradeHistoryPanel })),
);

function storedTheme(): ThemePreference {
  try {
    const value = localStorage.getItem(themeStorageKey);
    if (value === "light" || value === "dark" || value === "auto") return value;
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
  return "auto";
}

export default function App() {
  const [theme, setTheme] = useState<ThemePreference>(storedTheme);
  const [data, setData] = useState<DashboardData | null>(null);
  const [performance, setPerformance] = useState<PerformanceData | null>(null);
  const [analytics, setAnalytics] = useState<PositionAnalytics | null>(null);
  const [readiness, setReadiness] = useState<HyperliquidReadiness | null>(null);
  const [tradeHistory, setTradeHistory] = useState<TradeHistoryData | null>(null);
  const [tradeMetrics, setTradeMetrics] = useState<TradeMetrics | null>(null);
  const [instrumentRegistry, setInstrumentRegistry] =
    useState<InstrumentRegistryData | null>(null);
  const [view, setView] = useState<View>(
    () => {
      const candidate =
        (window.location.hash.slice(1) as View) ||
        (localStorage.getItem("dashboard-view") as View);
      return views.some((item) => item.id === candidate) ? candidate : "overview";
    },
  );
  const [range, setRange] = useState<keyof PerformanceData["ranges"]>("day");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const dark = theme === "dark" || (theme === "auto" && media.matches);
      document.documentElement.classList.toggle("dark", dark);
      document.documentElement.dataset.theme = theme;
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute("content", dark ? "#08080a" : "#f6f7f9");
    };
    applyTheme();
    try {
      localStorage.setItem(themeStorageKey, theme);
    } catch {
      // The selected theme still applies for the current session.
    }
    media.addEventListener("change", applyTheme);
    return () => media.removeEventListener("change", applyTheme);
  }, [theme]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const urls = [
        "/api/dashboard",
        "/api/performance",
        "/api/positions/analytics",
        "/api/integrations/hyperliquid/readiness",
      ];
      const results = await Promise.allSettled(
        urls.map(async (url) => {
          const response = await fetch(url);
          if (!response.ok) throw new Error(url);
          return response.json();
        }),
      );
      const successful = results.filter(
        (result) => result.status === "fulfilled",
      );
      if (results[0].status === "fulfilled") setData(results[0].value);
      if (results[1].status === "fulfilled") setPerformance(results[1].value);
      if (results[2].status === "fulfilled") setAnalytics(results[2].value);
      if (results[3].status === "fulfilled") setReadiness(results[3].value);
      if (!successful.length) throw new Error("API indisponible");
      setError(
        successful.length === results.length
          ? ""
          : "Certaines données temps réel sont temporairement mises en cache",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur inconnue");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (view !== "history") return;
    let cancelled = false;
    const loadHistory = async () => {
      const results = await Promise.allSettled(
        ["/api/trades", "/api/trades/metrics"].map(async (url) => {
          const response = await fetch(url);
          if (!response.ok) throw new Error(url);
          return response.json();
        }),
      );
      if (cancelled) return;
      if (results[0].status === "fulfilled") setTradeHistory(results[0].value);
      if (results[1].status === "fulfilled") setTradeMetrics(results[1].value);
    };
    void loadHistory();
    const timer = window.setInterval(() => void loadHistory(), 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [view]);

  useEffect(() => {
    if (view !== "agent") return;
    let cancelled = false;
    const loadInstruments = async () => {
      try {
        const response = await fetch("/api/instruments");
        if (!response.ok) return;
        const registry = await response.json();
        if (!cancelled) setInstrumentRegistry(registry);
      } catch {
        // The dedicated registry explains upstream availability in its payload.
      }
    };
    void loadInstruments();
    const timer = window.setInterval(() => void loadInstruments(), 60000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [view]);

  useEffect(() => {
    localStorage.setItem("dashboard-view", view);
    if (window.location.hash !== `#${view}`) window.location.hash = view;
  }, [view]);
  useEffect(() => {
    const onHashChange = () => {
      const candidate = window.location.hash.slice(1) as View;
      if (views.some((item) => item.id === candidate)) setView(candidate);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (view !== "agent") return;
    let cancelled = false;
    const refreshRuntime = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch("/api/automation/status");
        if (!response.ok) return;
        const automation = await response.json();
        if (!cancelled)
          setData((current) =>
            current ? { ...current, automation } : current,
          );
      } catch {
        // The full dashboard refresh owns error reporting; this lightweight
        // poll must never replace otherwise valid cached data.
      }
    };
    void refreshRuntime();
    const timer = window.setInterval(() => void refreshRuntime(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [view]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (["INPUT", "TEXTAREA"].includes((event.target as HTMLElement).tagName))
        return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < views.length) setView(views[index].id);
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        const navigation = document.querySelector<HTMLElement>(
          '[aria-label="Navigation principale"]',
        );
        if (!navigation?.contains(document.activeElement)) return;
        const current = views.findIndex((item) => item.id === view);
        const delta = event.key === "ArrowRight" ? 1 : -1;
        const next = views[(current + delta + views.length) % views.length];
        setView(next.id);
        window.requestAnimationFrame(() =>
          document.getElementById(`nav-${next.id}`)?.focus(),
        );
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view]);

  async function post(url: string, body?: object) {
    setBusy(true);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok)
        throw new Error((await response.json()).detail ?? "Action refusée");
      await refresh();
      setNotice("Action appliquée avec succès");
      window.setTimeout(() => setNotice(""), 2600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur inconnue");
    } finally {
      setBusy(false);
    }
  }

  const pnlRange = performance?.ranges?.[range];
  const currentPnl =
    Math.abs(pnlRange?.current_pnl_usd ?? 0) < 0.005
      ? 0
      : (pnlRange?.current_pnl_usd ?? 0);
  const openPnl =
    data?.positions.reduce(
      (sum, item) => sum + (item.unrealized_pnl_usd ?? 0),
      0,
    ) ?? 0;
  const exposure =
    data?.positions.reduce((sum, item) => sum + item.notional_usd, 0) ?? 0;
  const activeProtections =
    data?.protections.filter((item) => item.status === "ACTIVE") ?? [];
  const latest = data?.cycles.find((cycle) => cycle.state.decision);
  const assets = latest?.state.market_snapshot?.assets ?? [];
  const decisions = latest?.state.decision?.trader.decisions ?? [];
  const healthy =
    readiness?.ready_for_orders && data?.kill_switch === "RUNNING";
  const nextCycle = useMemo(() => {
    if (!data?.automation?.running) return "—";
    if (
      data.automation.phase &&
      !["WAITING", "PAUSED", "BLOCKED"].includes(data.automation.phase)
    )
      return "En cours";
    if (data.automation.next_cycle_at)
      return new Date(data.automation.next_cycle_at).toLocaleTimeString(
        "fr-FR",
        { hour: "2-digit", minute: "2-digit" },
      );
    if (!data.automation.last_cycle_finished_at) return "Imminent";
    return new Date(
      new Date(data.automation.last_cycle_finished_at).getTime() +
        data.automation.cycle_interval_seconds * 1000,
    ).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }, [data?.automation]);

  return (
    <div className="min-h-screen bg-background text-foreground selection:bg-foreground/20">
      <div className="app-ambient pointer-events-none fixed inset-0" />
      <main className="relative mx-auto flex min-h-screen max-w-[1600px] flex-col px-4 py-4 sm:px-7 lg:px-10">
        <header className="flex items-center justify-between rounded-[22px] border border-border bg-muted/45 px-4 py-3 backdrop-blur-2xl">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-primary-foreground">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <p className="text-[15px] font-semibold tracking-[-.02em]">
                Hyperliquid Intelligence
              </p>
              <p className="text-[10px] text-muted-foreground">
                Autonomous trading system
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setView("settings")}
              className="hidden min-h-11 items-center gap-2 rounded-full bg-muted/55 px-3 py-2 text-[11px] text-muted-foreground transition hover:bg-accent hover:text-accent-foreground sm:flex"
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${healthy ? "bg-profit shadow-[0_0_12px_#30d158]" : "bg-warning"}`}
              />
              {healthy ? "Mainnet opérationnel" : "Vérification requise"}
              <ChevronRight className="h-3 w-3" />
            </button>
            <Button
              aria-label="Actualiser les données"
              variant="ghost"
              size="sm"
              className="icon-button rounded-full text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              onClick={() => void refresh()}
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`}
              />
            </Button>
          </div>
        </header>

        <nav
          role="tablist"
          className="my-4 flex gap-1 overflow-x-auto rounded-2xl border border-border bg-muted/40 p-1.5"
          aria-label="Navigation principale"
        >
          {views.map(({ id, label, icon: Icon }, index) => (
            <button
              role="tab"
              id={`nav-${id}`}
              aria-controls={`panel-${id}`}
              aria-selected={view === id}
              tabIndex={view === id ? 0 : -1}
              title={`Raccourci ${index + 1}`}
              key={id}
              onClick={() => setView(id)}
              className={`nav-tab flex min-h-11 min-w-fit flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-xs ${view === id ? "active bg-primary text-primary-foreground shadow-lg" : "text-muted-foreground hover:bg-accent hover:text-foreground"}`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
              <span
                className={`hidden text-[9px] lg:inline ${view === id ? "text-primary-foreground/70" : "text-muted-foreground/60"}`}
              >
                {index + 1}
              </span>
            </button>
          ))}
        </nav>
        {error && (
          <div
            role="alert"
            className="mb-4 rounded-2xl border border-loss/25 bg-loss/10 px-4 py-3 text-sm text-loss"
          >
            {error}
          </div>
        )}
        {notice && (
          <div
            role="status"
            className="toast-enter fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-2xl border border-profit/30 bg-card/95 px-4 py-3 text-xs text-profit shadow-2xl backdrop-blur-xl"
          >
            <Check className="h-4 w-4" />
            {notice}
          </div>
        )}

        <section
          id={`panel-${view}`}
          role="tabpanel"
          aria-labelledby={`nav-${view}`}
          className="workspace-panel panel-enter flex-1"
        >
          <Suspense fallback={<WorkspaceFallback />}>
          {view === "overview" && (
            <Overview
              analytics={analytics}
              setView={setView}
              data={data}
              readiness={readiness}
              pnlRange={pnlRange}
              range={range}
              setRange={setRange}
              currentPnl={currentPnl}
              openPnl={openPnl}
              exposure={exposure}
              nextCycle={nextCycle}
              assets={assets}
              decisions={decisions}
            />
          )}
          {view === "positions" && (
            <PositionsPanel data={data} analytics={analytics} />
          )}
          {view === "agent" && (
            <AgentPanel
              data={data}
              latest={latest}
              instrumentRegistry={instrumentRegistry}
            />
          )}
          {view === "protections" && (
            <Protections
              data={data}
              protections={activeProtections}
              busy={busy}
              post={post}
            />
          )}
          {view === "history" && (
            <TradeHistoryPanel history={tradeHistory} metrics={tradeMetrics} />
          )}
          {view === "settings" && (
            <SettingsPanel
              data={data}
              busy={busy}
              post={post}
              theme={theme}
              setTheme={setTheme}
            />
          )}
          </Suspense>
        </section>
      </main>
    </div>
  );
}

function Overview({
  analytics,
  setView,
  data,
  readiness,
  pnlRange,
  range,
  setRange,
  currentPnl,
  openPnl,
  exposure,
  nextCycle,
  assets,
  decisions,
}: any) {
  const positive = currentPnl >= 0;
  const fundingNet = analytics?.funding_net_usd ?? 0;
  const apiCost = data?.llm_costs?.today_usd ?? 0;
  const economicNet = openPnl + fundingNet - apiCost;
  return (
    <div className="grid h-full gap-6 xl:grid-cols-[1.6fr_.7fr]">
      <div className="flex min-h-[620px] flex-col">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="eyebrow">P&amp;L Perps période · Hyperliquid</p>
            <div className="mt-2 flex items-end gap-3">
              <h1 className="text-5xl font-semibold tracking-[-.055em] sm:text-6xl">
                {usd.format(currentPnl)}
              </h1>
              <span
                className={`mb-2 flex items-center text-sm ${positive ? "text-profit" : "text-loss"}`}
              >
                {positive ? (
                  <TrendingUp className="mr-1 h-4 w-4" />
                ) : (
                  <TrendingDown className="mr-1 h-4 w-4" />
                )}
                {range === "day"
                  ? "aujourd’hui"
                  : ranges.find((item) => item.id === range)?.label}
              </span>
            </div>
          </div>
          <div className="flex rounded-full bg-muted/55 p-1" role="group" aria-label="Période du graphique P&L">
            {ranges.map((item) => (
              <button
                key={item.id}
                onClick={() => setRange(item.id)}
                aria-pressed={range === item.id}
                className={`min-h-10 rounded-full px-4 py-1.5 text-[11px] ${range === item.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"}`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1">
          <PnlChart points={pnlRange?.pnl ?? []} positive={positive} />
        </div>
        <div className="grid grid-cols-2 gap-x-7 gap-y-5 border-t border-border pt-5 sm:grid-cols-3 xl:grid-cols-6">
          <Metric
            label="P&L latent"
            value={usd.format(openPnl)}
            tone={openPnl >= 0 ? "positive" : "negative"}
          />
          <Metric
            label="Funding net"
            value={usd.format(fundingNet)}
            tone={fundingNet >= 0 ? "positive" : "negative"}
          />
          <Metric
            label="Coût xAI"
            value={`-${usd.format(apiCost)}`}
            tone="negative"
          />
          <Metric
            label="Résultat économique net"
            value={usd.format(economicNet)}
            tone={economicNet >= 0 ? "positive" : "negative"}
          />
          <Metric
            label="Cash disponible"
            value={usd.format(
              data?.cost_policy?.available_collateral_usd ??
                readiness?.available_collateral_usd ??
                0,
            )}
          />
          <Metric label="Exposition" value={usd.format(exposure)} />
        </div>
      </div>
      <aside className="border-t border-border pt-6 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
        <p className="eyebrow">État du système</p>
        <h2 className="mt-2 text-2xl font-semibold">Agent autonome</h2>
        <div className="mt-6 space-y-3">
          <ActionRow
            onClick={() => setView("settings")}
            label="Automatisation"
            value={data?.automation?.running ? "Active" : "Arrêtée"}
            good={data?.automation?.running}
          />
          <ActionRow
            onClick={() => setView("settings")}
            label="Prochain cycle"
            value={nextCycle}
          />
          <ActionRow
            onClick={() => setView("positions")}
            label="Positions"
            value={String(data?.positions.length ?? 0)}
          />
          <ActionRow
            onClick={() => setView("agent")}
            label="Coût xAI aujourd’hui"
            value={usd.format(data?.llm_costs?.today_usd ?? 0)}
          />
          <ActionRow
            onClick={() => setView("agent")}
            label="Politique d’appel"
            value={data?.cost_policy?.reason?.replaceAll("_", " ") ?? "—"}
            good={data?.cost_policy?.run}
          />
          <ActionRow
            onClick={() => setView("settings")}
            label={`Grok · seuil ${usd.format(data?.cost_policy?.threshold_usd ?? 10)}`}
            value={
              data?.cost_policy?.run
                ? "Actif automatiquement"
                : "En pause automatiquement"
            }
            good={data?.cost_policy?.run}
          />
        </div>
        <div className="mt-8">
          <p className="eyebrow">Marchés</p>
          <div className="mt-3 divide-y divide-border">
            {assets.map((asset: any) => {
              const decision = decisions.find(
                (item: any) => item.symbol === asset.symbol,
              );
              return (
                <button
                  onClick={() =>
                    setView(
                      data?.positions.some(
                        (item: any) => item.symbol === asset.symbol,
                      )
                        ? "positions"
                        : "agent",
                    )
                  }
                  key={asset.symbol}
                  className="group flex w-full items-center justify-between py-3 text-left transition hover:pl-2"
                >
                  <div>
                    <p className="text-sm font-medium">{asset.symbol}</p>
                    <p className="text-[10px] text-muted-foreground">
                      ADX {asset.adx_4h.toFixed(1)} · Funding{" "}
                      {asset.funding_1h_pct.toFixed(4)}%
                    </p>
                  </div>
                  <div className="flex items-center gap-3 text-right">
                    <div>
                      <p className="font-mono text-sm">
                        {number.format(asset.mark_px)}
                      </p>
                      <p className="text-[10px] text-muted-foreground">
                        {decision?.action ?? "—"}
                      </p>
                    </div>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/0 transition group-hover:text-muted-foreground" />
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </aside>
    </div>
  );
}

function AgentPanel({
  data,
  latest,
  instrumentRegistry,
}: {
  data: DashboardData | null;
  latest: DashboardData["cycles"][number] | undefined;
  instrumentRegistry: InstrumentRegistryData | null;
}) {
  return (
    <div>
      <PanelHeader
        eyebrow="Agent explicable"
        title="Intelligence & journal"
        subtitle="Chaque recherche, décision, contrôle et coût est consultable au même endroit."
      />
      <AgentWorkspace
        data={data}
        latest={latest}
        instrumentRegistry={instrumentRegistry}
      />
    </div>
  );
}

function Protections({
  data,
  protections,
  busy,
  post,
}: {
  data: DashboardData | null;
  protections: DashboardData["protections"];
  busy: boolean;
  post: (url: string, body?: object) => Promise<void>;
}) {
  return (
    <div>
      <PanelHeader
        eyebrow="Protection exchange-side"
        title="Take profits & stop-loss"
        subtitle="Ordres protecteurs actifs, regroupés par position."
      />
      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_.38fr]">
        <div className="grid content-start gap-4 sm:grid-cols-2">
          {[...new Set(protections.map((item) => item.symbol))].map(
            (symbol) => (
              <div
                key={symbol}
                className="rounded-[22px] border border-border bg-muted/45 p-5"
              >
                <div className="flex items-center justify-between">
                  <h3 className="text-xl font-semibold">{symbol}</h3>
                  <Badge className="border-0 bg-profit/10 text-profit">
                    Actifs
                  </Badge>
                </div>
                <div className="mt-4 space-y-2">
                  {protections
                    .filter((item) => item.symbol === symbol)
                    .map((item) => (
                      <div
                        key={item.protection_id}
                        className="flex items-center justify-between rounded-xl bg-muted/50 px-4 py-3"
                      >
                        <div className="flex items-center gap-3">
                          <span
                            className={`grid h-8 min-w-8 place-items-center rounded-full text-[10px] font-semibold ${item.kind === "SL" ? "bg-loss/10 text-loss" : "bg-profit/10 text-profit"}`}
                          >
                            {item.kind}
                            {item.kind === "TP" ? item.level_index : ""}
                          </span>
                          <div>
                            <p className="text-xs">
                              {item.kind === "SL"
                                ? "Stop total"
                                : `Objectif ${(item.size_fraction * 100).toFixed(0)}%`}
                            </p>
                            <p className="text-[10px] text-muted-foreground">
                              {item.status}
                            </p>
                          </div>
                        </div>
                        <p className="font-mono text-sm">
                          {number.format(item.trigger_px)}
                        </p>
                      </div>
                    ))}
                </div>
              </div>
            ),
          )}
          {!protections.length && (
            <Empty
              icon={ShieldCheck}
              title="Aucune protection"
              text="Aucun TP ou stop actif actuellement."
            />
          )}
        </div>
        <aside className="rounded-[22px] border border-border bg-muted/40 p-5">
          <p className="eyebrow">Moniteur de risque</p>
          <h3 className="mt-2 text-lg font-semibold">
            Déterministe · sans prompt
          </h3>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            Réconciliation Hyperliquid toutes les{" "}
            {data?.automation?.risk_monitor_interval_seconds ?? 10} secondes.
            Les protections restent actives même si l’application est arrêtée.
          </p>
          <div className="mt-5 space-y-3">
            <InfoRow label="État" value={data?.risk_monitor?.status ?? "—"} />
            <InfoRow label="Protections" value={String(protections.length)} />
            <InfoRow label="Coût xAI" value="0 $" good />
          </div>
          <button
            disabled={busy}
            onClick={() =>
              void post("/api/killswitch", {
                state: "HALTED",
                reason: "Arrêt d’urgence depuis le dashboard",
                actor: "dashboard",
              })
            }
            className="mt-8 w-full rounded-xl border border-loss/25 bg-loss/10 px-4 py-3 text-xs font-medium text-loss hover:bg-loss/10"
          >
            Arrêt d’urgence
          </button>
        </aside>
      </div>
    </div>
  );
}

function SettingsPanel({
  data,
  busy,
  post,
  theme,
  setTheme,
}: {
  data: DashboardData | null;
  busy: boolean;
  post: (url: string, body?: object) => Promise<void>;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
}) {
  const themeChoices: Array<{
    value: ThemePreference;
    label: string;
    description: string;
    icon: typeof Sun;
  }> = [
    { value: "light", label: "Clair", description: "Contraste lumineux", icon: Sun },
    { value: "dark", label: "Sombre", description: "Confort nocturne", icon: Moon },
    { value: "auto", label: "Auto", description: "Suit le système", icon: Monitor },
  ];
  return (
    <div>
      <PanelHeader
        eyebrow="Control Center"
        title="Pilotage de l’agent"
        subtitle="Automatisation, cadence et actions opérateur."
      />
      <div className="mt-8 mx-auto max-w-3xl">
        <div className="rounded-[22px] border border-border bg-card/65 p-6 shadow-sm">
          <fieldset>
            <legend className="text-lg font-semibold">Apparence</legend>
            <p className="mt-1 text-xs text-muted-foreground">
              Le mode automatique suit les préférences de votre appareil.
            </p>
            <div className="mt-4 grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Thème de l’interface">
              {themeChoices.map(({ value, label, description, icon: Icon }) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={theme === value}
                  onClick={() => setTheme(value)}
                  className="theme-swatch justify-start text-left"
                >
                  <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
                  <span>
                    <span className="block font-medium text-current">{label}</span>
                    <span className={`block text-[10px] ${theme === value ? "text-primary-foreground/70" : "text-muted-foreground"}`}>
                      {description}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </fieldset>
          <div className="my-7 border-t border-border" />
          <div className="flex items-center justify-between">
            <div>
              <p className="text-lg font-semibold">Automatisation LIVE</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Cycles autonomes et moniteur déterministe
              </p>
            </div>
            <button
              disabled={busy}
              onClick={() =>
                void post("/api/automation", {
                  enabled: !data?.automation?.running,
                })
              }
              className={`relative h-11 w-16 rounded-full transition ${data?.automation?.running ? "bg-profit" : "bg-accent"}`}
              role="switch"
              aria-checked={Boolean(data?.automation?.running)}
              aria-label={data?.automation?.running ? "Désactiver l’automatisation" : "Activer l’automatisation"}
            >
              <span
                className={`absolute top-2 h-7 w-7 rounded-full bg-card shadow-sm transition ${data?.automation?.running ? "left-8" : "left-2"}`}
              />
            </button>
          </div>
          <div className="mt-7 grid gap-6 sm:grid-cols-2">
            <Choice
              label="Analyse Grok"
              value={data?.automation?.cycle_interval_seconds ?? 300}
              options={[
                ["1 min", 60],
                ["5 min", 300],
                ["15 min", 900],
              ]}
              onChange={(value) =>
                void post("/api/automation", { cycle_interval_seconds: value })
              }
            />
            <Choice
              label="Moniteur risque"
              value={data?.automation?.risk_monitor_interval_seconds ?? 10}
              options={[
                ["5 sec", 5],
                ["10 sec", 10],
                ["30 sec", 30],
              ]}
              onChange={(value) =>
                void post("/api/automation", {
                  risk_monitor_interval_seconds: value,
                })
              }
            />
          </div>
          <div className="mt-7 grid gap-3 sm:grid-cols-3">
            <Button
              disabled={busy || data?.kill_switch !== "RUNNING"}
              onClick={() => void post("/api/cycles/run")}
              className="h-11 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90"
            >
              <Play className="h-3.5 w-3.5" />
              Analyser
            </Button>
            <Button
              disabled={busy}
              variant="outline"
              onClick={() =>
                void post("/api/killswitch", {
                  state: data?.kill_switch === "RUNNING" ? "PAUSED" : "RUNNING",
                  reason: "Transition opérateur",
                  actor: "dashboard",
                })
              }
              className="h-11 rounded-xl border-border bg-muted/45 text-foreground hover:bg-accent"
            >
              <Pause className="h-3.5 w-3.5" />
              {data?.kill_switch === "RUNNING" ? "Pause" : "Reprendre"}
            </Button>
            <Button
              disabled={busy}
              variant="outline"
              onClick={() => void post("/api/execution/reconcile")}
              className="h-11 rounded-xl border-border bg-muted/45 text-foreground hover:bg-accent"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Réconcilier
            </Button>
          </div>
        </div>
        <div className="mt-10">
          <ActivationSettings
            automation={data?.automation}
            busy={busy}
            post={post}
          />
        </div>
      </div>
    </div>
  );
}

function PanelHeader({
  eyebrow,
  title,
  subtitle,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="flex items-end justify-between gap-4 border-b border-border pb-5">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-[-.04em]">
          {title}
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>
      </div>
    </div>
  );
}

function WorkspaceFallback() {
  return (
    <div className="grid min-h-[420px] place-items-center" role="status" aria-live="polite">
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <RefreshCw aria-hidden="true" className="h-4 w-4 animate-spin" />
        Chargement de la vue…
      </div>
    </div>
  );
}
function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative";
}) {
  return (
    <div>
      <p className="text-[10px] text-muted-foreground">{label}</p>
      <p
        className={`mt-1.5 truncate font-mono text-sm font-medium ${tone === "positive" ? "text-profit" : tone === "negative" ? "text-loss" : "text-foreground/90"}`}
      >
        {value}
      </p>
    </div>
  );
}
function InfoRow({
  label,
  value,
  good,
}: {
  label: string;
  value: string;
  good?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl bg-muted/45 px-4 py-3">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span
        className={`max-w-[60%] truncate text-right text-xs font-medium ${good ? "text-profit" : "text-foreground/80"}`}
      >
        {value}
      </span>
    </div>
  );
}
function ActionRow({
  label,
  value,
  good,
  onClick,
}: {
  label: string;
  value: string;
  good?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="group flex w-full items-center justify-between gap-4 rounded-xl bg-muted/45 px-4 py-3 text-left transition hover:bg-accent active:scale-[.99]"
    >
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="flex min-w-0 items-center gap-2">
        <span
          className={`truncate text-right text-xs font-medium ${good ? "text-profit" : "text-foreground/80"}`}
        >
          {value}
        </span>
        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/60 transition group-hover:translate-x-0.5 group-hover:text-foreground" />
      </span>
    </button>
  );
}
function Choice({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: number;
  options: Array<[string, number]>;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <p className="mb-2 text-[11px] text-muted-foreground">{label}</p>
      <div className="grid grid-cols-3 rounded-xl bg-muted/50 p-1">
        {options.map(([text, option]) => (
          <button
            key={option}
            onClick={() => onChange(option)}
            className={`rounded-lg py-2.5 text-[10px] transition ${value === option ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}
function Empty({
  icon: Icon,
  title,
  text,
}: {
  icon: typeof Wallet;
  title: string;
  text: string;
}) {
  return (
    <div className="grid min-h-[520px] place-items-center text-center">
      <div>
        <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-muted/55">
          <Icon className="h-6 w-6 text-muted-foreground" />
        </div>
        <h2 className="mt-4 text-lg font-semibold">{title}</h2>
        <p className="mx-auto mt-2 max-w-sm text-xs leading-relaxed text-muted-foreground">
          {text}
        </p>
      </div>
    </div>
  );
}
