import { MitraMark } from "./MitraMark";

/** MITRA's mark, with the name set beside it. */
export function Brand({ size = 26 }: { size?: number }) {
  return (
    <span className="brand">
      <MitraMark size={size} />
      <span className="brand-name">MITRA</span>
    </span>
  );
}
