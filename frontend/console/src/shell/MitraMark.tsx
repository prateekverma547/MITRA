import { useId } from "react";

/**
 * MITRA's mark. The M is one thick, round-ended stroke, the same family as the
 * Appinventiv A. On its two peaks sit two small lenses (it watches the
 * interview, like a camera, and looks back like a pair of eyes), and under
 * its centre a small voice level (it interviews by voice).
 *
 * Quietly alive: the lenses blink now and then and the voice level breathes
 * (shell.css, `.mm-*`); both stop for reduced motion. Below 32px the voice
 * level goes and the stroke thickens, so a 16px tab icon still reads.
 */
export function MitraMark({ size = 32, className, title }: { size?: number; className?: string; title?: string }) {
  const id = useId().replace(/:/g, "");
  const small = size < 32;
  return (
    <svg
      className={`mitra-mark${className ? ` ${className}` : ""}`}
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <defs>
        <linearGradient id={`mm-${id}`} x1="0" y1="0" x2="0.35" y2="1">
          <stop offset="0" style={{ stopColor: "var(--mark-1, #0a8fd0)" }} />
          <stop offset="0.55" style={{ stopColor: "var(--mark-2, #0068a8)" }} />
          <stop offset="1" style={{ stopColor: "var(--mark-3, #003f6e)" }} />
        </linearGradient>
      </defs>

      {/* the M */}
      <path
        d={small ? "M8 56 L18 22 L32 42 L46 22 L56 56" : "M10 55 L19 24 L32 41 L45 24 L54 55"}
        fill="none"
        stroke={`url(#mm-${id})`}
        strokeWidth={small ? 10 : 7.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* two lenses on the peaks: the camera, and a pair of eyes */}
      {[small ? [18, 11] : [19, 12], small ? [46, 11] : [45, 12]].map(([cx, cy], i) => (
        <g key={i} className="mm-eye">
          <circle cx={cx} cy={cy} r={small ? 7 : 5.6} style={{ fill: "var(--mark-3, #003f6e)" }} />
          <circle cx={cx} cy={cy} r={small ? 3.6 : 2.9} style={{ fill: "var(--neon-c, #5cc8f7)" }} />
          <circle cx={cx - (small ? 1.6 : 1.2)} cy={cy - (small ? 1.6 : 1.2)} r={small ? 1.5 : 1.15} fill="#ffffff" />
        </g>
      ))}

      {/* a voice level under the centre: it listens and speaks */}
      {!small && (
        <g style={{ fill: "var(--neon-a, #009ad9)" }}>
          <rect className="mm-bar b1" x="26.2" y="48" width="3" height="8" rx="1.5" />
          <rect className="mm-bar b2" x="30.5" y="45" width="3" height="11" rx="1.5" />
          <rect className="mm-bar b3" x="34.8" y="48" width="3" height="8" rx="1.5" />
        </g>
      )}
    </svg>
  );
}
