import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Check,
  Clock3,
  Globe2,
  LoaderCircle,
  RadioTower,
  SlidersHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  ActivationConfig,
  ActivationMode,
  AutomationCommand,
  AutomationStatus,
  CryptoSession,
  UsEquitiesSession,
} from "@/types";

type ActivationSettingsProps = {
  automation: AutomationStatus | null | undefined;
  busy: boolean;
  post: (url: string, body?: object) => Promise<void>;
};

type ActivationDraft = ActivationConfig;

const fallbackConfig: ActivationDraft = {
  mode: "always",
  timezone: "Europe/Paris",
  us_equities_sessions: ["market_open", "first_hours", "before_close"],
  crypto_sessions: ["europe", "us", "europe_us_overlap"],
  liquidity_filter: {
    enabled: false,
    min_24h_volume_usd: 0,
    min_open_interest_usd: 0,
    min_eligible_assets: 1,
  },
};

const modes: Array<{
  value: ActivationMode;
  label: string;
  description: string;
}> = [
  {
    value: "always",
    label: "Toujours actif",
    description: "Analyse continue, sans contrainte de session.",
  },
  {
    value: "us_equities",
    label: "Actions US",
    description: "Suit uniquement les fenêtres choisies à New York.",
  },
  {
    value: "crypto_sessions",
    label: "Sessions crypto",
    description: "Cible les périodes de liquidité crypto sélectionnées.",
  },
  {
    value: "hybrid",
    label: "Hybride",
    description: "Combine fenêtres actions US et sessions crypto.",
  },
];

const usSessions: Array<{
  value: UsEquitiesSession;
  label: string;
  detail: string;
}> = [
  { value: "premarket", label: "Pré-market", detail: "Avant l’ouverture" },
  { value: "market_open", label: "Ouverture", detail: "Impulsion initiale" },
  { value: "first_hours", label: "Premières heures", detail: "Liquidité principale" },
  { value: "before_close", label: "Avant clôture", detail: "Repositionnements" },
  { value: "after_hours", label: "After-hours", detail: "Après la séance" },
];

const cryptoSessions: Array<{
  value: CryptoSession;
  label: string;
  detail: string;
}> = [
  { value: "asia", label: "Asie", detail: "Session APAC" },
  { value: "europe", label: "Europe", detail: "Session européenne" },
  { value: "us", label: "États-Unis", detail: "Session américaine" },
  {
    value: "europe_us_overlap",
    label: "Chevauchement EU / US",
    detail: "Liquidité croisée",
  },
];

const timezones = [
  "Europe/Paris",
  "America/New_York",
  "UTC",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Europe/London",
];

const stateMeta = {
  ACTIVE: {
    label: "Fenêtre active",
    dot: "bg-profit",
    tone: "text-profit",
  },
  WAITING: {
    label: "En attente",
    dot: "bg-warning",
    tone: "text-warning",
  },
  BLOCKED: {
    label: "Activation bloquée",
    dot: "bg-destructive",
    tone: "text-destructive",
  },
} as const;

function copyConfig(config?: ActivationConfig): ActivationDraft {
  if (!config) {
    return {
      ...fallbackConfig,
      us_equities_sessions: [...fallbackConfig.us_equities_sessions],
      crypto_sessions: [...fallbackConfig.crypto_sessions],
      liquidity_filter: { ...fallbackConfig.liquidity_filter },
    };
  }
  return {
    ...config,
    us_equities_sessions: [...config.us_equities_sessions],
    crypto_sessions: [...config.crypto_sessions],
    liquidity_filter: { ...config.liquidity_filter },
  };
}

function formatUsdInput(value: number) {
  return Number.isFinite(value) ? String(value) : "0";
}

function formatNextWindow(automation: AutomationStatus | null | undefined) {
  if (automation?.next_activation_window_local) return automation.next_activation_window_local;
  if (!automation?.next_activation_window_at) return "Aucune fenêtre planifiée";
  const parsed = Date.parse(automation.next_activation_window_at);
  if (!Number.isFinite(parsed)) return automation.next_activation_window_at;
  return new Intl.DateTimeFormat("fr-FR", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(parsed));
}

function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-11 w-16 shrink-0 rounded-full border transition-colors",
        checked ? "border-profit/30 bg-profit" : "border-border bg-muted",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute left-2 top-2 h-7 w-7 rounded-full bg-card shadow-sm transition-transform",
          checked ? "translate-x-6" : "translate-x-0",
        )}
      />
    </button>
  );
}

function SessionPicker<T extends string>({
  legend,
  help,
  options,
  selected,
  disabled,
  onChange,
}: {
  legend: string;
  help: string;
  options: Array<{ value: T; label: string; detail: string }>;
  selected: T[];
  disabled: boolean;
  onChange: (sessions: T[]) => void;
}) {
  const toggle = (value: T) => {
    onChange(
      selected.includes(value)
        ? selected.filter((session) => session !== value)
        : [...selected, value],
    );
  };

  return (
    <fieldset className="border-t border-border py-5 first:border-t-0">
      <legend className="sr-only">{legend}</legend>
      <div className="grid gap-4 md:grid-cols-[minmax(150px,.38fr)_1fr] md:gap-8">
        <div>
          <p className="text-sm font-medium text-foreground">{legend}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{help}</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {options.map((option) => {
            const active = selected.includes(option.value);
            return (
              <label
                key={option.value}
                className={cn(
                  "flex min-h-14 items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                  active
                    ? "border-primary/35 bg-primary/[.06] text-foreground"
                    : "border-border bg-transparent text-muted-foreground hover:bg-accent/55",
                  disabled && "cursor-not-allowed opacity-50",
                )}
              >
                <input
                  type="checkbox"
                  checked={active}
                  disabled={disabled}
                  onChange={() => toggle(option.value)}
                  className="sr-only"
                />
                <span
                  aria-hidden="true"
                  className={cn(
                    "grid h-5 w-5 shrink-0 place-items-center rounded-md border transition-colors",
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-input bg-background",
                  )}
                >
                  {active ? <Check className="h-3.5 w-3.5" strokeWidth={2.5} /> : null}
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-medium">{option.label}</span>
                  <span className="mt-0.5 block text-[10px] text-muted-foreground">
                    {option.detail}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </div>
    </fieldset>
  );
}

function NumericField({
  id,
  label,
  hint,
  value,
  min,
  max,
  step,
  disabled,
  suffix,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: number;
  min: number;
  max?: number;
  step: number;
  disabled: boolean;
  suffix: string;
  onChange: (value: number) => void;
}) {
  return (
    <label htmlFor={id} className="block min-w-0">
      <span className="text-xs font-medium text-foreground">{label}</span>
      <span className="mt-0.5 block text-[10px] text-muted-foreground">{hint}</span>
      <span className="mt-2 flex h-10 items-center rounded-lg border border-input bg-background px-3 focus-within:ring-2 focus-within:ring-ring">
        <input
          id={id}
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          value={formatUsdInput(value)}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            if (Number.isFinite(parsed)) onChange(parsed);
          }}
          className="min-w-0 flex-1 bg-transparent font-mono text-xs tabular-nums text-foreground outline-none disabled:cursor-not-allowed"
        />
        <span className="ml-2 text-[10px] font-medium text-muted-foreground">{suffix}</span>
      </span>
    </label>
  );
}

export function ActivationSettings({ automation, busy, post }: ActivationSettingsProps) {
  const sourceFingerprint = JSON.stringify(
    automation?.activation_config ?? fallbackConfig,
  );
  const [draft, setDraft] = useState<ActivationDraft>(() =>
    copyConfig(automation?.activation_config),
  );

  useEffect(() => {
    setDraft(copyConfig(automation?.activation_config));
  }, [sourceFingerprint]);

  const sourceConfig = automation?.activation_config ?? fallbackConfig;
  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(sourceConfig),
    [draft, sourceConfig],
  );
  const showUsSessions = draft.mode === "us_equities" || draft.mode === "hybrid";
  const showCryptoSessions = draft.mode === "crypto_sessions" || draft.mode === "hybrid";
  const state = stateMeta[automation?.activation_state ?? "WAITING"];
  const hasEmptyRequiredGroup =
    (showUsSessions && draft.us_equities_sessions.length === 0) ||
    (showCryptoSessions && draft.crypto_sessions.length === 0);
  const timezoneValid = draft.timezone.trim().includes("/") || draft.timezone.trim() === "UTC";
  const hybridNeedsLiquidity =
    draft.mode === "hybrid" && !draft.liquidity_filter.enabled;
  const canSave =
    dirty &&
    !busy &&
    !hasEmptyRequiredGroup &&
    !hybridNeedsLiquidity &&
    timezoneValid;

  const save = async () => {
    if (!canSave) return;
    const command: AutomationCommand = {
      activation_mode: draft.mode,
      activation_timezone: draft.timezone.trim(),
      us_equities_sessions: draft.us_equities_sessions,
      crypto_sessions: draft.crypto_sessions,
      liquidity_filter_enabled: draft.liquidity_filter.enabled,
      liquidity_min_24h_volume_usd: Math.max(
        0,
        draft.liquidity_filter.min_24h_volume_usd,
      ),
      liquidity_min_open_interest_usd: Math.max(
        0,
        draft.liquidity_filter.min_open_interest_usd,
      ),
      liquidity_min_eligible_assets: Math.min(
        8,
        Math.max(1, Math.round(draft.liquidity_filter.min_eligible_assets)),
      ),
    };
    await post("/api/automation", command);
  };

  return (
    <section aria-labelledby="activation-settings-title" className="w-full">
      <header className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">Planification intelligente</p>
          <h2 id="activation-settings-title" className="mt-1.5 text-xl font-semibold tracking-tight">
            Plages d’activation
          </h2>
          <p className="mt-1 max-w-xl text-xs leading-relaxed text-muted-foreground">
            Lance les analyses quand les marchés ciblés sont actifs et suffisamment liquides.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs" aria-live="polite">
          <span className={cn("h-2 w-2 rounded-full", state.dot)} aria-hidden="true" />
          <span className={cn("font-medium", state.tone)}>{state.label}</span>
        </div>
      </header>

      <div className="grid gap-4 border-b border-border py-5 md:grid-cols-[1fr_auto] md:items-center">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground">
            <Clock3 className="h-4 w-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              {automation?.activation_reason || "Configuration en attente du serveur"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Prochaine fenêtre · {formatNextWindow(automation)}
            </p>
          </div>
        </div>
        {automation?.cycle_policy ? (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <RadioTower className="h-3.5 w-3.5" aria-hidden="true" />
            <span>
              Politique cycle · {String(automation.cycle_policy.strategy ?? automation.cycle_policy.trigger ?? "automatique")}
            </span>
          </div>
        ) : null}
      </div>

      <fieldset className="border-b border-border py-5">
        <legend className="sr-only">Mode d’activation</legend>
        <div className="grid gap-4 md:grid-cols-[minmax(150px,.38fr)_1fr] md:gap-8">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Globe2 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              Mode
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              Définit les horloges de marché qui autorisent un cycle complet.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {modes.map((mode) => {
              const active = draft.mode === mode.value;
              return (
                <label
                  key={mode.value}
                  className={cn(
                    "relative flex min-h-[68px] items-start gap-3 rounded-lg border px-3 py-3 transition-colors",
                    active
                      ? "border-primary/35 bg-primary/[.06]"
                      : "border-border hover:bg-accent/55",
                    busy && "cursor-not-allowed opacity-50",
                  )}
                >
                  <input
                    type="radio"
                    name="activation-mode"
                    value={mode.value}
                    checked={active}
                    disabled={busy}
                    onChange={() => setDraft((current) => ({ ...current, mode: mode.value }))}
                    className="sr-only"
                  />
                  <span
                    aria-hidden="true"
                    className={cn(
                      "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border",
                      active ? "border-primary" : "border-input",
                    )}
                  >
                    {active ? <span className="h-2.5 w-2.5 rounded-full bg-primary" /> : null}
                  </span>
                  <span>
                    <span className="block text-xs font-medium text-foreground">{mode.label}</span>
                    <span className="mt-1 block text-[10px] leading-relaxed text-muted-foreground">
                      {mode.description}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </div>
      </fieldset>

      <div className="grid gap-4 border-b border-border py-5 md:grid-cols-[minmax(150px,.38fr)_1fr] md:gap-8">
        <div>
          <label htmlFor="activation-timezone" className="text-sm font-medium text-foreground">
            Fuseau horaire
          </label>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            Identifiant IANA utilisé pour calculer les fenêtres locales.
          </p>
        </div>
        <div>
          <input
            id="activation-timezone"
            list="activation-timezones"
            value={draft.timezone}
            disabled={busy}
            aria-invalid={!timezoneValid}
            aria-describedby="activation-timezone-hint"
            onChange={(event) =>
              setDraft((current) => ({ ...current, timezone: event.target.value }))
            }
            className="h-10 w-full rounded-lg border border-input bg-background px-3 font-mono text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          />
          <datalist id="activation-timezones">
            {timezones.map((timezone) => (
              <option key={timezone} value={timezone} />
            ))}
          </datalist>
          <p
            id="activation-timezone-hint"
            className={cn("mt-1.5 text-[10px]", timezoneValid ? "text-muted-foreground" : "text-destructive")}
          >
            {timezoneValid ? "Exemple : Europe/Paris ou America/New_York." : "Saisissez un fuseau IANA valide."}
          </p>
        </div>
      </div>

      {showUsSessions ? (
        <SessionPicker
          legend="Fenêtres actions US"
          help="Au moins une fenêtre doit rester sélectionnée."
          options={usSessions}
          selected={draft.us_equities_sessions}
          disabled={busy}
          onChange={(sessions) =>
            setDraft((current) => ({ ...current, us_equities_sessions: sessions }))
          }
        />
      ) : null}

      {showCryptoSessions ? (
        <SessionPicker
          legend="Sessions crypto"
          help="Au moins une session doit rester sélectionnée."
          options={cryptoSessions}
          selected={draft.crypto_sessions}
          disabled={busy}
          onChange={(sessions) =>
            setDraft((current) => ({ ...current, crypto_sessions: sessions }))
          }
        />
      ) : null}

      <fieldset className="border-t border-border py-5">
        <legend className="sr-only">Filtre de liquidité</legend>
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground">
              <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
            </span>
            <div>
              <p className="text-sm font-medium text-foreground">Filtre de liquidité</p>
              <p className="mt-1 max-w-xl text-xs leading-relaxed text-muted-foreground">
                Attend qu’un nombre minimum d’actifs dépasse les seuils avant d’appeler les agents coûteux.
              </p>
            </div>
          </div>
          <Toggle
            checked={draft.liquidity_filter.enabled}
            disabled={busy}
            label="Activer le filtre de liquidité"
            onChange={(enabled) =>
              setDraft((current) => ({
                ...current,
                liquidity_filter: { ...current.liquidity_filter, enabled },
              }))
            }
          />
        </div>

        <div
          className={cn(
            "mt-5 grid gap-4 sm:grid-cols-3",
            !draft.liquidity_filter.enabled && "opacity-45",
          )}
        >
          <NumericField
            id="liquidity-volume"
            label="Volume 24 h minimum"
            hint="Par actif éligible"
            value={draft.liquidity_filter.min_24h_volume_usd}
            min={0}
            step={100000}
            disabled={busy || !draft.liquidity_filter.enabled}
            suffix="USD"
            onChange={(min_24h_volume_usd) =>
              setDraft((current) => ({
                ...current,
                liquidity_filter: { ...current.liquidity_filter, min_24h_volume_usd },
              }))
            }
          />
          <NumericField
            id="liquidity-open-interest"
            label="Open interest minimum"
            hint="Par actif éligible"
            value={draft.liquidity_filter.min_open_interest_usd}
            min={0}
            step={100000}
            disabled={busy || !draft.liquidity_filter.enabled}
            suffix="USD"
            onChange={(min_open_interest_usd) =>
              setDraft((current) => ({
                ...current,
                liquidity_filter: { ...current.liquidity_filter, min_open_interest_usd },
              }))
            }
          />
          <NumericField
            id="liquidity-assets"
            label="Actifs minimum"
            hint="Entre 1 et 8"
            value={draft.liquidity_filter.min_eligible_assets}
            min={1}
            max={8}
            step={1}
            disabled={busy || !draft.liquidity_filter.enabled}
            suffix="ACTIFS"
            onChange={(min_eligible_assets) =>
              setDraft((current) => ({
                ...current,
                liquidity_filter: { ...current.liquidity_filter, min_eligible_assets },
              }))
            }
          />
        </div>
      </fieldset>

      {hasEmptyRequiredGroup ? (
        <p role="alert" className="border-t border-border py-3 text-xs text-destructive">
          Sélectionnez au moins une plage pour chaque marché activé.
        </p>
      ) : null}
      {hybridNeedsLiquidity ? (
        <p role="alert" className="border-t border-border py-3 text-xs text-destructive">
          Le mode hybride nécessite le filtre de liquidité pour éviter les cycles coûteux hors conditions.
        </p>
      ) : null}

      <footer className="flex flex-col-reverse gap-3 border-t border-border pt-5 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[10px] text-muted-foreground" aria-live="polite">
          {dirty ? "Modifications non enregistrées" : "Configuration synchronisée"}
        </p>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={busy || !dirty}
            onClick={() => setDraft(copyConfig(automation?.activation_config))}
          >
            Annuler
          </Button>
          <Button type="button" disabled={!canSave} onClick={() => void save()}>
            {busy ? (
              <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            )}
            {busy ? "Enregistrement…" : "Enregistrer"}
          </Button>
        </div>
      </footer>
    </section>
  );
}
