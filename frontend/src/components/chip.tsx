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
        size === "xs" ? "h-6 px-2 text-xs" : "h-7 px-2.5 text-sm",
        selected ? "border-marsh-white bg-marsh-white text-marsh-navy" : "border-hairline-strong bg-transparent text-body hover:border-hairline-bright hover:bg-raised hover:text-ink",
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
