"use client";

import { cn } from "@/lib/utils";

/**
 * Toggle chip. Selected chips fill with ink (Chronicle's filled-black button); the rest are ghost-outlined.
 * The selected state is the fill alone (plus aria-pressed): no icon appears, so a chip never changes width
 * and its neighbours never move when it is toggled.
 */
export function Chip({ selected, onClick, children, title, className, size = "sm" }: { selected?: boolean; onClick?: () => void; children: React.ReactNode; title?: string; className?: string; size?: "xs" | "sm" }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      title={title}
      onClick={onClick}
      className={cn(
        "focus-ring inline-flex items-center whitespace-nowrap rounded-md border font-medium transition-[background-color,border-color,color,transform] duration-(--dur-fast) active:scale-[0.98]",
        size === "xs" ? "h-8 px-3 text-xs" : "h-9 px-3.5 text-sm",
        selected ? "border-marsh-navy bg-marsh-navy text-white" : "border-marsh-navy/25 bg-white text-marsh-navy hover:border-marsh-navy hover:bg-marsh-cream",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function ChipGroup({ label, children, className }: { label?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {label && <span className="mr-0.5 text-xs text-quiet">{label}</span>}
      {children}
    </div>
  );
}
