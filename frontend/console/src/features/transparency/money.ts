// How money, tokens and time read in the AI calls and Cost tabs.

/** US dollars, with enough places that a fraction of a cent still reads. */
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Price not set";
  if (value === 0) return "$0.00";
  if (Math.abs(value) < 0.01) return "$" + value.toFixed(4);
  return "$" + value.toFixed(2);
}

export function tokens(value: number | null | undefined): string {
  if (!value) return "0";
  return value >= 10_000 ? (value / 1000).toFixed(1) + "k" : value.toLocaleString();
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  if (ms >= 60_000) return Math.round(ms / 60_000) + " min";
  return ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : ms + " ms";
}
