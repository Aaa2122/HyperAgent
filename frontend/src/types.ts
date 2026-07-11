export type MarketAsset = {
  symbol: string;
  mark_px: number;
  ret_4h_pct: number;
  adx_4h: number;
  funding_1h_pct: number;
  data_age_seconds: number;
};

export type Cycle = {
  cycle_id: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  state: {
    market_snapshot?: { as_of: string; assets: MarketAsset[] };
    guardrail_verdicts?: Array<{
      symbol: string;
      action: string;
      verdict: string;
      reasons: string[];
      notional_usd: number;
      leverage: number;
    }>;
    executions?: Array<{
      intent_id: string;
      symbol: string;
      status: string;
      duplicate_prevented: boolean;
    }>;
    research?: {
      as_of: string;
      signals: Array<{
        symbol: string;
        direction: string;
        confidence: number;
        novelty: number;
        manipulation_risk: number;
        summary: string;
        source_urls: string[];
      }>;
    };
    strategy_signals?: Array<{
      symbol: string;
      strategy: string;
      score: number;
      rationale: string;
    }>;
    decision?: {
      provider: string;
      playbook: {
        payload: { regime_view: string; changes_vs_previous: string };
      };
      trader: { decisions: AgentDecision[] };
      initial_trader?: { decisions: AgentDecision[] };
      consequence_report?: {
        disclaimer: string;
        decisions: DecisionConsequences[];
      };
      risk_review?: { reviews: RiskReview[] };
    };
  };
};

export type AgentDecision = {
  symbol: string;
  action: string;
  direction?: string;
  confidence: number;
  leverage: number;
  notional_usd?: number;
  horizon_hours?: number;
  rationale: string;
};
export type DecisionConsequences = {
  symbol: string;
  action: string;
  proposed_notional_usd: number;
  stop_loss_usd: number;
  stop_loss_equity_pct: number;
  margin_used_usd: number;
  liquidation_px_estimate?: number | null;
  liquidation_to_stop_atr?: number | null;
  funding_estimate_usd: number;
  fees_estimate_usd: number;
  slippage_estimate_usd: number;
  adverse_move_1atr_usd: number;
  adverse_move_2atr_usd: number;
  adverse_move_3atr_usd: number;
  operational_facts: Record<string, boolean | number | string>;
  scenarios: Array<{
    size_multiplier: number;
    notional_usd: number;
    stop_loss_usd: number;
    stop_loss_equity_pct: number;
    margin_used_usd: number;
    funding_estimate_usd: number;
    fees_estimate_usd: number;
    slippage_estimate_usd: number;
  }>;
};
export type RiskReview = {
  symbol: string;
  decision: "KEEP_AS_IS" | "ADJUST" | "CANCEL";
  material_new_information: string[];
  reason: string;
  adjusted_decision?: AgentDecision | null;
};

export type DashboardData = {
  mode: string;
  decision_provider: string;
  xai_model: string;
  trading_profile: string;
  max_model_leverage: number;
  x_search_enabled: boolean;
  paper_equity_usd: number;
  market_provider: string;
  market_quality_warnings: string[];
  universe_scan?: Array<{ symbol: string; score: number; spread_bps: number; ret_4h_pct: number; oi_usd: number; selected: boolean; reason: string }>;
  hyperliquid_network: string;
  hyperliquid_execution_network: string;
  hyperliquid_account_configured: boolean;
  hyperliquid_account?: {
    account_value: number;
    withdrawable: number;
    total_notional: number;
    position_count: number;
    account_abstraction: string;
  };
  kill_switch: "RUNNING" | "PAUSED" | "HALTED";
  cycles: Cycle[];
  positions: Array<{
    symbol: string;
    side: string;
    notional_usd: number;
    leverage: number;
    margin_used_usd: number;
    entry_px: number;
    mark_px?: number;
    unrealized_pnl_usd?: number;
    roe_pct?: number;
    liquidation_px?: number;
    invalidation_px: number;
    targets: number[];
    opened_at: string;
  }>;
  intents: Array<{
    intent_id: string;
    cloid: string;
    symbol: string;
    direction: string;
    notional_usd: number;
    leverage: number;
    margin_used_usd: number;
    status: string;
    created_at: string;
  }>;
  protections: Array<{
    protection_id: string;
    cloid: string;
    symbol: string;
    kind: "SL" | "TP";
    level_index: number;
    trigger_px: number;
    size_fraction: number;
    status: string;
  }>;
  automation: {
    enabled: boolean;
    running: boolean;
    cycle_interval_seconds: number;
    risk_monitor_interval_seconds: number;
    last_cycle_started_at?: string | null;
    last_cycle_finished_at?: string | null;
    last_cycle_status: string | null;
    last_risk_monitor_status: string | null;
  };
  events: Array<{
    event_id: number;
    event_type: string;
    severity: string;
    created_at: string;
    payload: Record<string, unknown>;
  }>;
  llm_calls: LlmCall[];
  llm_costs: {
    total_usd: number;
    today_usd: number;
    input_tokens: number;
    output_tokens: number;
    cached_tokens: number;
    call_count: number;
    skipped_count: number;
  };
  cost_policy: {
    run: boolean;
    external_research: boolean;
    strategist_refresh: boolean;
    reason: string;
    available_collateral_usd?: number;
    threshold_usd?: number;
    next_review_in_seconds?: number;
  };
  risk_monitor: {
    status: string;
    as_of?: string;
    prompt_used?: boolean;
    strategy?: string;
  };
};

export type LlmCall = {
  call_id: number;
  cycle_id?: string;
  stage: string;
  provider: string;
  model: string;
  status: string;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  latency_ms: number;
  tool_usage: Record<string, unknown>;
  prompt: Record<string, unknown>;
  response: Record<string, unknown>;
  skipped_reason?: string;
  created_at: string;
};

export type TargetAnalytics = {
  level: number;
  price: number;
  distance_pct: number;
  progress_pct: number;
  reward_r: number;
  status: string;
  hit_at?: string | null;
  average_fill_px?: number | null;
  filled_size: number;
  filled_notional_usd: number;
  realized_pnl_usd: number;
  fees_usd: number;
};
export type PositionAnalytics = {
  positions: Array<
    DashboardData["positions"][number] & {
      interval: string;
      chart: Array<{
        time: number;
        price: number;
        open: number;
        high: number;
        low: number;
        close: number;
        volume: number;
      }>;
      distance_to_stop_pct: number;
      distance_to_liquidation_pct: number | null;
      unrealized_r: number;
      funding_net_usd: number;
      funding_paid_usd: number;
      pnl_after_funding_usd: number;
      initial_size: number;
      closed_size: number;
      closed_fraction_pct: number;
      realized_pnl_usd: number;
      trade_fees_usd: number;
      realized_net_pnl_usd: number;
      total_trade_net_pnl_usd: number;
      targets_analytics: TargetAnalytics[];
    }
  >;
  funding_net_usd: number;
  open_pnl_after_funding_usd: number;
  as_of: string;
};

export type PerformancePoint = { time: number; value: number };

export type PerformanceData = {
  ranges: Record<
    "day" | "week" | "month" | "all",
    { pnl: PerformancePoint[]; volume_usd: number; current_pnl_usd: number }
  >;
  as_of: string | null;
};

export type HyperliquidReadiness = {
  network: "mainnet" | "testnet";
  configured: boolean;
  account?: string;
  signer?: string;
  key_valid?: boolean;
  authorized?: boolean;
  dedicated_api_wallet?: boolean;
  account_value_usd?: number;
  withdrawable_usd?: number;
  available_collateral_usd?: number;
  account_abstraction?: string;
  collateral_source?: string;
  ready_for_orders: boolean;
  blockers: string[];
  error?: string;
};
