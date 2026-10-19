import { createContext, useContext, useState, type ReactNode } from "react";
import type { Source } from "../types";

export const SOURCE_LABEL: Record<Source, string> = {
  LIVE_KIND: "Live Kind",
  OFFLINE_SIMULATION: "Offline simulation",
  RECORDED_REPLAY: "Recorded replay",
  TEST_FIXTURE: "Test fixture",
};

const SOURCE_CLASS: Record<Source, string> = {
  LIVE_KIND: "live",
  OFFLINE_SIMULATION: "sim",
  RECORDED_REPLAY: "replay",
  TEST_FIXTURE: "fixture",
};

export function SourceBadge({ source, title }: { source: Source; title?: string }) {
  return (
    <span className={`badge ${SOURCE_CLASS[source]}`} title={title}>
      <span className="dot" aria-hidden="true" />
      {SOURCE_LABEL[source]}
    </span>
  );
}

type Tone = "good" | "critical" | "warning" | "pending" | "info";

const TONE_COLOR: Record<Tone, string> = {
  good: "var(--good)",
  critical: "var(--critical)",
  warning: "var(--warning)",
  pending: "var(--text-muted)",
  info: "var(--accent)",
};

export function Icon({ tone, size = 16 }: { tone: Tone; size?: number }) {
  const color = TONE_COLOR[tone];
  const common = { width: size, height: size, viewBox: "0 0 16 16", "aria-hidden": true, className: "icon" } as const;
  if (tone === "good")
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="7" fill={color} />
        <path d="M4.6 8.3 7 10.6l4.4-5" fill="none" stroke="#0b0b0b" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (tone === "critical")
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="7" fill={color} />
        <path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  if (tone === "warning")
    return (
      <svg {...common}>
        <path d="M8 1.5 15 14H1z" fill={color} />
        <path d="M8 6v3.6" stroke="#0b0b0b" strokeWidth="1.7" strokeLinecap="round" />
        <circle cx="8" cy="11.7" r="0.95" fill="#0b0b0b" />
      </svg>
    );
  if (tone === "info")
    return (
      <svg {...common}>
        <circle cx="8" cy="8" r="7" fill={color} />
        <circle cx="8" cy="8" r="2.6" fill="#0b0b0b" />
      </svg>
    );
  return (
    <svg {...common}>
      <circle cx="8" cy="8" r="6.2" fill="none" stroke={color} strokeWidth="1.6" />
      <path d="M8 4.8V8l2 1.6" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export function Status({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="status">
      <Icon tone={tone} />
      <span>{children}</span>
    </span>
  );
}

export function Panel({
  title,
  sub,
  right,
  children,
  className = "",
}: {
  title: string;
  sub?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <div className="panel-head">
        <div>
          <h2 className="panel-title">{title}</h2>
          {sub ? <div className="panel-sub">{sub}</div> : null}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export const fmt = {
  pct: (value: number | null | undefined) =>
    value === null || value === undefined ? "—" : `${Number.isInteger(value) ? value : value.toFixed(1)}%`,
  ms: (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${Math.round(value)} ms`),
  hash: (value: string | null | undefined, length = 12) => (value ? value.slice(0, length) : "—"),
  time: (iso: string | null | undefined) =>
    iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—",
  dateTime: (iso: string | null | undefined) =>
    iso
      ? new Date(iso).toLocaleString("en-GB", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      : "—",
  duration: (seconds: number | null | undefined) => {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
    if (seconds >= 3600) {
      const hours = Math.floor(seconds / 3600);
      return `${hours}h ${Math.round((seconds - hours * 3600) / 60)}m`;
    }
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${Math.round(seconds - minutes * 60)}s`;
  },
};

// --- tooltip (hover + keyboard focus show the same content) -----------------

interface TipState {
  x: number;
  y: number;
  content: ReactNode;
}

const TipContext = createContext<(tip: TipState | null) => void>(() => undefined);

export function TooltipLayer({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<TipState | null>(null);
  return (
    <TipContext.Provider value={setTip}>
      {children}
      {tip ? (
        <div
          className="tooltip"
          role="tooltip"
          style={{
            left: Math.min(tip.x + 14, window.innerWidth - 296),
            top: Math.max(8, tip.y - 12),
          }}
        >
          {tip.content}
        </div>
      ) : null}
    </TipContext.Provider>
  );
}

export function useTip() {
  const setTip = useContext(TipContext);
  return {
    show: (event: { clientX?: number; clientY?: number; currentTarget: Element }, content: ReactNode) => {
      const rect = event.currentTarget.getBoundingClientRect();
      setTip({ x: event.clientX ?? rect.right, y: event.clientY ?? rect.top, content });
    },
    hide: () => setTip(null),
  };
}
