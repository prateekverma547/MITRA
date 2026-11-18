/** A gradient ring. Decorative: whatever is busy also says so in words. */
export function Spinner({ size = 20 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden="true" />;
}
