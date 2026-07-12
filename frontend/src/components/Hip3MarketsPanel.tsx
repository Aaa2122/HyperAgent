import { Clock3, Landmark, LockKeyhole } from "lucide-react";
import type { InstrumentRegistryData } from "@/types";

const number = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("fr-FR", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const sessionLabels: Record<string, string> = {
  pre_market: "Pré-market",
  regular: "Marché US ouvert",
  after_hours: "After-hours",
  closed: "Marché US fermé",
};

const venueLabels: Record<string, string> = {
  available: "Négociable",
  halted: "Suspendu",
  delisted: "Retiré",
  data_unavailable: "Données indisponibles",
  not_listed: "Non listé",
};

export function Hip3MarketsPanel({
  registry,
}: {
  registry: InstrumentRegistryData | null;
}) {
  if (!registry) {
    return (
      <div className="mt-10 border-t border-border py-8">
        <p className="eyebrow">Actions US · HIP-3</p>
        <p className="mt-3 text-xs text-muted-foreground">
          Découverte du DEX xyz en attente. Aucun actif n’est activé automatiquement.
        </p>
      </div>
    );
  }

  const session = sessionLabels[registry.session.status] ?? registry.session.status;
  const sessionActive = registry.session.status === "regular";
  return (
    <section className="mt-10 border-t border-border pt-7" aria-label="Marchés actions américaines HIP-3">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Landmark className="h-3.5 w-3.5 text-info" />
            <p className="eyebrow">Actions US · perpétuels HIP-3</p>
          </div>
          <h3 className="mt-2 text-xl font-semibold tracking-[-.025em]">
            DEX {registry.venue.name}
          </h3>
          <p className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
            <Clock3 className="h-3 w-3" />
            {session} · {registry.session.timezone}
          </p>
        </div>
        <span className="flex items-center gap-1.5 rounded-full bg-info/10 px-3 py-1.5 text-[10px] text-info">
          <LockKeyhole className="h-3 w-3" />
          Lecture seule · PAPER
        </span>
      </div>

      <div className="mt-6 grid gap-x-6 sm:grid-cols-2 xl:grid-cols-4">
        {registry.instruments.map((instrument) => {
          const available = instrument.venue_status === "available";
          return (
            <article
              key={instrument.instrument_id}
              className="group border-b border-border py-4 transition hover:border-foreground/25"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold">{instrument.symbol}</p>
                  <p className="mt-0.5 font-mono text-[9px] text-muted-foreground">
                    {instrument.venue_symbol}
                  </p>
                </div>
                <span className={`flex items-center gap-1 text-[9px] ${available ? "text-profit" : "text-muted-foreground"}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${available ? "bg-profit" : "bg-foreground/20"}`} />
                  {venueLabels[instrument.venue_status] ?? instrument.venue_status}
                </span>
              </div>
              <div className="mt-4 flex items-end justify-between gap-3">
                <div>
                  <p className="font-mono text-base text-foreground/80">
                    {instrument.mark_px == null ? "—" : `$${number.format(instrument.mark_px)}`}
                  </p>
                  <p className="mt-1 text-[9px] text-muted-foreground">mark indicatif</p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {instrument.day_notional_volume_usd == null
                      ? "—"
                      : `$${compact.format(instrument.day_notional_volume_usd)}`}
                  </p>
                  <p className="mt-1 text-[9px] text-muted-foreground">
                    volume 24 h{instrument.max_leverage ? ` · ${instrument.max_leverage}×` : ""}
                  </p>
                </div>
              </div>
              {!sessionActive && available && (
                <p className="mt-3 text-[9px] leading-relaxed text-muted-foreground">
                  Venue disponible, référence actions actuellement hors séance régulière.
                </p>
              )}
            </article>
          );
        })}
      </div>
      {registry.warnings.length > 0 && (
        <p className="mt-4 text-[9px] text-warning/70">
          {registry.warnings.map((warning) => warning.replaceAll("_", " ")).join(" · ")}
        </p>
      )}
    </section>
  );
}
