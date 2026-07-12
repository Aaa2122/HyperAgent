import { useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  History,
  Search,
} from "lucide-react";
import type {
  ClosedTrade,
  TradeHistoryData,
  TradeMetrics,
} from "@/types";

const usd = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const price = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 6 });
const date = new Intl.DateTimeFormat("fr-FR", {
  day: "2-digit",
  month: "short",
  year: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

type OutcomeFilter = "ALL" | ClosedTrade["outcome"];
type Sort = "date" | "pnl" | "return";

export function TradeHistoryPanel({
  history,
  metrics,
}: {
  history: TradeHistoryData | null;
  metrics: TradeMetrics | null;
}) {
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState<OutcomeFilter>("ALL");
  const [sort, setSort] = useState<Sort>("date");

  const trades = useMemo(() => {
    const needle = query.trim().toUpperCase();
    return [...(history?.trades ?? [])]
      .filter((trade) => !needle || trade.symbol.toUpperCase().includes(needle))
      .filter((trade) => outcome === "ALL" || trade.outcome === outcome)
      .sort((left, right) => {
        if (sort === "pnl") return right.net_pnl_usd - left.net_pnl_usd;
        if (sort === "return") return right.margin_return_pct - left.margin_return_pct;
        return Date.parse(right.closed_at) - Date.parse(left.closed_at);
      });
  }, [history?.trades, outcome, query, sort]);

  const stats = [
    ["Trades clôturés", String(metrics?.total_trades ?? history?.total ?? 0)],
    ["Taux de réussite", `${(metrics?.win_rate_pct ?? 0).toFixed(1)}%`],
    ["PnL net cumulé", usd.format(metrics?.cumulative_net_pnl_usd ?? 0)],
    [
      "Profit factor",
      metrics?.profit_factor == null ? "—" : metrics.profit_factor.toFixed(2),
    ],
    ["Gain moyen", usd.format(metrics?.avg_win_usd ?? 0)],
    ["Perte moyenne", usd.format(metrics?.avg_loss_usd ?? 0)],
    ["Drawdown réalisé", usd.format(metrics?.max_drawdown_usd ?? 0)],
  ];

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="eyebrow">Journal d’exécution</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-.045em] sm:text-4xl">
            Historique des trades
          </h1>
          <p className="mt-2 max-w-xl text-xs leading-relaxed text-muted-foreground">
            Round trips reconstruits depuis les fills Hyperliquid, frais et sorties partielles inclus.
          </p>
        </div>
        {history?.as_of && (
          <p className="font-mono text-[10px] text-muted-foreground">
            Synchronisé {date.format(new Date(history.as_of))}
          </p>
        )}
      </div>

      <div className="mt-7 grid grid-cols-2 gap-x-7 gap-y-5 border-y border-border py-6 md:grid-cols-4 xl:grid-cols-7">
        {stats.map(([label, value], index) => (
          <div key={label} className={index ? "md:border-l md:border-border md:pl-5" : ""}>
            <p className="eyebrow">{label}</p>
            <p className="mt-2 font-mono text-base text-foreground/85">{value}</p>
          </div>
        ))}
      </div>

      <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <label className="flex min-h-11 flex-1 items-center gap-2 border-b border-border text-muted-foreground sm:max-w-xs">
          <span className="sr-only">Rechercher un actif</span>
          <Search className="h-3.5 w-3.5" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Rechercher un actif"
            className="h-11 w-full bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground/70"
          />
        </label>
        <div className="flex flex-wrap gap-2">
          {(["ALL", "PROFIT", "LOSS", "BREAK_EVEN"] as const).map((value) => (
            <button
              key={value}
              onClick={() => setOutcome(value)}
              aria-pressed={outcome === value}
              className={`min-h-11 rounded-full px-3 text-[10px] transition ${
                outcome === value
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted/50 text-muted-foreground hover:bg-accent hover:text-foreground"
              }`}
            >
              {value === "ALL" ? "Tous" : value === "PROFIT" ? "Profits" : value === "LOSS" ? "Pertes" : "Neutres"}
            </button>
          ))}
          <label className="relative flex min-h-11 items-center rounded-full bg-muted/50 px-3 text-[10px] text-muted-foreground">
            <select
              value={sort}
              onChange={(event) => setSort(event.target.value as Sort)}
              className="appearance-none bg-transparent pr-5 outline-none"
              aria-label="Trier les trades"
            >
              <option value="date">Plus récents</option>
              <option value="pnl">PnL net</option>
              <option value="return">Rendement marge</option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 h-3 w-3" />
          </label>
        </div>
      </div>

      <div className="mt-5 border-t border-border">
        {trades.length ? (
          trades.map((trade) => <TradeRow key={trade.trade_id} trade={trade} />)
        ) : (
          <div className="grid min-h-64 place-items-center text-center">
            <div>
              <span className="mx-auto grid h-11 w-11 place-items-center rounded-full bg-muted/50 text-muted-foreground">
                <History className="h-4 w-4" />
              </span>
              <p className="mt-4 text-sm font-medium">Aucun trade clôturé</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Les positions terminées apparaîtront automatiquement ici.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TradeRow({ trade }: { trade: ClosedTrade }) {
  const positive = trade.net_pnl_usd > 0;
  const neutral = trade.outcome === "BREAK_EVEN";
  return (
    <details className="group">
      <summary className="grid min-h-16 cursor-pointer list-none grid-cols-[1fr_auto] items-center gap-4 md:grid-cols-[.7fr_.8fr_.8fr_.75fr_.75fr_auto]">
        <div className="flex items-center gap-3">
          <span className={`grid h-8 w-8 place-items-center rounded-full ${positive ? "bg-profit/10 text-profit" : neutral ? "bg-muted/55 text-muted-foreground" : "bg-loss/10 text-loss"}`}>
            {positive ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
          </span>
          <div>
            <p className="text-sm font-medium">{trade.symbol}</p>
            <p className="text-[10px] text-muted-foreground">{trade.side} · {trade.leverage}×</p>
          </div>
        </div>
        <div className="hidden md:block">
          <p className="font-mono text-xs">{price.format(trade.avg_entry_px)}</p>
          <p className="text-[9px] text-muted-foreground">entrée moyenne</p>
        </div>
        <div className="hidden md:block">
          <p className="font-mono text-xs">{price.format(trade.avg_exit_px)}</p>
          <p className="text-[9px] text-muted-foreground">sortie moyenne</p>
        </div>
        <div className="hidden md:block">
          <p className="text-xs">{date.format(new Date(trade.closed_at))}</p>
          <p className="text-[9px] text-muted-foreground">clôture</p>
        </div>
        <div className="hidden md:block">
          <p className={`font-mono text-xs ${trade.margin_return_pct >= 0 ? "text-profit" : "text-loss"}`}>
            {trade.margin_return_pct >= 0 ? "+" : ""}{trade.margin_return_pct.toFixed(2)}%
          </p>
          <p className="text-[9px] text-muted-foreground">sur marge</p>
        </div>
        <div className="text-right">
          <p className={`font-mono text-sm ${positive ? "text-profit" : neutral ? "text-muted-foreground" : "text-loss"}`}>
            {positive ? "+" : ""}{usd.format(trade.net_pnl_usd)}
          </p>
          <p className="text-[9px] text-muted-foreground">net</p>
        </div>
      </summary>
      <div className="grid gap-5 pb-5 pt-2 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-4">
        <Detail label="Taille initiale" value={`${trade.initial_size} · ${usd.format(trade.initial_notional_usd)}`} />
        <Detail label="PnL brut / frais" value={`${usd.format(trade.gross_pnl_usd)} / -${usd.format(Math.abs(trade.fees_usd))}`} />
        <Detail
          label="Funding"
          value={`${usd.format(trade.funding_usd)}${trade.funding_source ? ` · ${trade.funding_source.replaceAll("_", " ")}` : ""}`}
        />
        <Detail label="Clôture" value={trade.close_reason.replaceAll("_", " ")} />
        <Detail label="Auteur" value={trade.decision_author.replaceAll("_", " ")} />
        {(trade.thesis || trade.rationale) && (
          <div className="sm:col-span-2 lg:col-span-4">
            <p className="eyebrow">Thèse et retour d’expérience</p>
            <p className="mt-2 max-w-4xl leading-relaxed text-muted-foreground">
              {trade.thesis || trade.rationale}
            </p>
          </div>
        )}
      </div>
    </details>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className="mt-1.5 font-mono text-foreground/65">{value}</p>
    </div>
  );
}
