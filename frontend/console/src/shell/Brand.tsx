import mark from "../assets/mitra-mark.png";

/** The M mark from MITRA's logo, with the name set beside it. */
export function Brand({ size = 26 }: { size?: number }) {
  return (
    <span className="brand">
      <img src={mark} alt="" width={size} height={size} />
      <span className="brand-name">MITRA</span>
    </span>
  );
}
