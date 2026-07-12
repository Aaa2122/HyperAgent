import { useEffect, useMemo, useState } from "react";

function color(style: CSSStyleDeclaration, token: string, alpha?: number) {
  const value = style.getPropertyValue(token).trim();
  return `hsl(${value}${alpha == null ? "" : ` / ${alpha}`})`;
}

/** Keeps canvas/SVG chart palettes synchronized with the semantic CSS theme. */
export function useSemanticColors() {
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const observer = new MutationObserver(() => setRevision((value) => value + 1));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  return useMemo(() => {
    const style = getComputedStyle(document.documentElement);
    return {
      background: color(style, "--background"),
      foreground: color(style, "--foreground"),
      foregroundStrong: color(style, "--foreground", 0.82),
      mutedForeground: color(style, "--muted-foreground"),
      card: color(style, "--card"),
      border: color(style, "--border"),
      grid: color(style, "--chart-grid"),
      axis: color(style, "--chart-axis"),
      crosshair: color(style, "--chart-crosshair"),
      chartLabel: color(style, "--chart-label"),
      profit: color(style, "--profit"),
      profitSoft: color(style, "--profit", 0.34),
      profitFaint: color(style, "--profit", 0.45),
      loss: color(style, "--loss"),
      lossSoft: color(style, "--loss", 0.34),
      info: color(style, "--info"),
    };
  }, [revision]);
}
