// The welcome page's right side: Appinventiv's mark at the centre with MITRA
// beneath it, and what MITRA does revolving on a wide, tilted orbit. Items at the front of the orbit are larger and brighter, items at the
// back smaller and dimmer, so it reads as depth rather than a flat spin. The
// orbit pauses while the pointer is over it, and holds still for anyone who
// asks for reduced motion. Illustrative figures.

import {
  AppWindow,
  Captions,
  Check,
  Eye,
  FileSearch,
  FileText,
  Monitor,
  ScanFace,
  ShieldCheck,
  Scale,
  Timer,
  TrendingUp,
  Volume2,
  Workflow,
} from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import aiMark from "../../assets/appinventiv-mark.png";
import { MitraMark } from "../../shell/MitraMark";

function Ring({ value }: { value: number }) {
  const r = 17;
  const c = 2 * Math.PI * r;
  return (
    <span className="cap-ring">
      <svg viewBox="0 0 44 44" aria-hidden="true">
        <circle cx="22" cy="22" r={r} className="cap-ring-track" />
        <circle cx="22" cy="22" r={r} className="cap-ring-fill" strokeDasharray={`${(value / 100) * c} ${c}`} />
      </svg>
      <span>{value}%</span>
    </span>
  );
}

const WAVE = [6, 12, 20, 14, 24, 16, 10, 18, 8, 14, 6];

/** What MITRA does: one compact card each, on the outer orbit. */
const CARDS: { key: string; title: string; icon: ReactNode; body: ReactNode }[] = [
  {
    key: "screen",
    title: "Resume screening",
    icon: <FileSearch size={15} />,
    body: (
      <span className="cap-row">
        <Ring value={82} />
        <span className="cap-line">Fit to the job, name hidden</span>
      </span>
    ),
  },
  {
    key: "voice",
    title: "Interviewer by voice",
    icon: <Volume2 size={15} />,
    body: (
      <>
        <span className="cap-wave" aria-hidden="true">
          {WAVE.map((h, i) => (
            <i key={i} style={{ height: h }} />
          ))}
        </span>
        <span className="cap-line">A plan for each candidate</span>
      </>
    ),
  },
  {
    key: "proctor",
    title: "Proctored live",
    icon: <ShieldCheck size={15} />,
    body: (
      <>
        <span className="cap-checks">
          {[ScanFace, Eye, Monitor, AppWindow].map((Icon, i) => (
            <span key={i} className="cap-check">
              <Icon size={14} aria-hidden="true" />
              <Check size={10} className="cap-ok" aria-hidden="true" />
            </span>
          ))}
        </span>
        <span className="cap-line">Facts for a person, never a score</span>
      </>
    ),
  },
  {
    key: "text",
    title: "Text understanding",
    icon: <FileText size={15} />,
    body: (
      <>
        <span className="cap-doc" aria-hidden="true">
          <i />
          <i className="hl" />
          <i />
        </span>
        <span className="cap-line">Reads the job and the CV</span>
      </>
    ),
  },
  {
    key: "graph",
    title: "Evidence builds",
    icon: <TrendingUp size={15} />,
    body: (
      <>
        <svg className="cap-graph" viewBox="0 0 120 40" aria-hidden="true">
          <path className="graph-area" d="M4 36 L22 30 L40 32 L58 21 L76 23 L94 12 L116 5 L116 38 L4 38 Z" />
          <path className="graph-line" d="M4 36 L22 30 L40 32 L58 21 L76 23 L94 12 L116 5" />
          <circle className="graph-dot" cx="116" cy="5" r="3.2" />
        </svg>
        <span className="cap-line">As the candidate answers</span>
      </>
    ),
  },
];

/** Small glass bubbles drifting in the corners: what it means for the business. */
const BUBBLES = [
  { key: "llm", icon: Timer, label: "Faster time to hire" },
  { key: "gpt", icon: Scale, label: "Consistent interviews" },
  { key: "stt", icon: Captions, label: "Speech to text" },
  { key: "auto", icon: Workflow, label: "Hiring at scale" },
] as const;

/** Seconds per revolution. */
const OUTER_SECONDS = 70;

export function Features() {
  const stage = useRef<HTMLDivElement>(null);
  const items = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const small = window.matchMedia("(max-width: 640px)");
    let frame = 0;
    let paused = false;
    let last = performance.now();
    let t = 0;
    const onEnter = () => (paused = true);
    const onLeave = () => (paused = false);
    el.addEventListener("pointerenter", onEnter);
    el.addEventListener("pointerleave", onLeave);

    const place = () => {
      if (small.matches) {
        items.current.forEach((n) => {
          if (n) n.style.cssText = "";
        });
        return;
      }
      const w = el.clientWidth;
      const h = el.clientHeight;
      // The outer orbit: wide and tilted, its front at the bottom.
      const rx = w * 0.39;
      const ry = h * 0.37;
      const n = CARDS.length;
      items.current.forEach((node, i) => {
        if (!node) return;
        const a = (t / OUTER_SECONDS) * Math.PI * 2 + (i / n) * Math.PI * 2 - Math.PI / 2;
        const depth = (Math.sin(a) + 1) / 2; // 0 at the back, 1 at the front
        const x = Math.cos(a) * rx;
        const y = Math.sin(a) * ry;
        node.style.transform = `translate(calc(-50% + ${x.toFixed(1)}px), calc(-50% + ${y.toFixed(1)}px)) scale(${(0.84 + depth * 0.16).toFixed(3)})`;
        node.style.opacity = (0.74 + depth * 0.26).toFixed(3);
        node.style.zIndex = String(10 + Math.round(depth * 10));
      });
    };

    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (!paused) t += dt;
      place();
    };
    if (still) {
      t = 4;
      place();
    } else frame = requestAnimationFrame(tick);
    const onResize = () => place();
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
      el.removeEventListener("pointerenter", onEnter);
      el.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  return (
    <div className="orbit" ref={stage} aria-label="What MITRA does, built by Appinventiv">
      <svg className="orbit-rings" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <ellipse cx="50" cy="50" rx="39" ry="37" />
      </svg>

      {BUBBLES.map((b) => (
        <span key={b.key} className={`bubble bubble-${b.key}`} aria-hidden="true">
          <b.icon size={15} />
          {b.label}
        </span>
      ))}

      <a className="orbit-core" href="https://appinventiv.com" target="_blank" rel="noreferrer" aria-label="Built by Appinventiv (opens appinventiv.com)">
        <img src={aiMark} alt="" width={124} height={124} />
      </a>

      <div className="orbit-mitra" title="MITRA">
        <MitraMark size={76} title="MITRA" />
      </div>

      {CARDS.map((c, i) => (
        <div
          key={c.key}
          className={`cap orbit-card orbit-${c.key}`}
          ref={(node) => {
            items.current[i] = node;
          }}
        >
          <span className="cap-head">
            {c.icon}
            {c.title}
          </span>
          {c.body}
        </div>
      ))}
    </div>
  );
}
