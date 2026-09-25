import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";

/**
 * Matrix dot loader (Transitions.dev): a 4×4 grid whose dots share one colour-pulse cycle.
 * A variant is just a table of per-dot delays; CSS lives in globals.css (`.t-matrix`).
 * This is the system's presence mark: it animates only while something is actually running.
 */

export type MatrixVariant = "scan" | "twinkle" | "orbit" | "pulse";
export type AgentState = "idle" | "working" | "done" | "error";

const CYCLE = 1200;
const TWINKLE = [7, 2, 11, 5, 14, 9, 0, 12, 3, 15, 6, 10, 13, 1, 8, 4];
const RING = [1, 2, 7, 11, 14, 13, 8, 4];
const INNER = [5, 6, 9, 10];
const CORNERS = [0, 3, 12, 15];

function delays(variant: MatrixVariant): (number | null)[] {
  const d: (number | null)[] = Array(16).fill(0);
  switch (variant) {
    case "scan":
      return d.map((_, i) => (i % 4) * (CYCLE / 10));
    case "twinkle":
      return d.map((_, i) => TWINKLE.indexOf(i) * (CYCLE / 16));
    case "orbit":
      // Ring dots cycle; the centre holds steady.
      return d.map((_, i) => (RING.includes(i) ? RING.indexOf(i) * (CYCLE / 8) : null));
    case "pulse":
      return d.map((_, i) => (INNER.includes(i) ? 0 : CYCLE * 0.16));
  }
}

export function Matrix({
  variant = "scan",
  state = "working",
  dot = 2,
  rounded = false,
  className,
  title,
  style,
}: {
  variant?: MatrixVariant;
  state?: AgentState;
  /** dot size in px; gap equals dot size, so the mark is 7×dot square */
  dot?: number;
  /** drop the four corners */
  rounded?: boolean;
  className?: string;
  title?: string;
  style?: CSSProperties;
}) {
  const table = delays(variant);
  return (
    <span
      className={cn("t-matrix", className)}
      data-variant={variant}
      data-state={state}
      role="img"
      aria-label={title ?? state}
      style={{ "--matrix-dot": `${dot}px`, ...style } as CSSProperties}
    >
      {table.map((d, i) => {
        const gap = rounded && CORNERS.includes(i);
        // A null delay means "hold steady": no animation, base colour.
        return <i key={i} className={cn(gap && "is-gap")} style={d === null ? { animation: "none" } : ({ "--d": d } as CSSProperties)} />;
      })}
    </span>
  );
}
