import { cn } from "@/lib/utils";

/** Keyboard key hint, e.g. <Kbd>←</Kbd>. */
export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return <kbd className={cn("kbd", className)}>{children}</kbd>;
}
