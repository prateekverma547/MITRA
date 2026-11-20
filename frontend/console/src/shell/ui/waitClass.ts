/** How long someone has waited, as a notification priority: today is fine, a day or two needs attention, three or more is urgent. */
export function waitClass(days: number): string {
  return days >= 3 ? "wait long" : days >= 1 ? "wait soon" : "wait fresh";
}
