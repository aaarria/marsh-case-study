"use client";

import { Loader2 } from "lucide-react";
import { Callout } from "@/components/callout";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function LoadingBlock({ label = "Loading…", rows = 2 }: { label?: string; rows?: number }) {
  return (
    <div className="space-y-3" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> {label}
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-20 w-full rounded-xl bg-raised" />
      ))}
    </div>
  );
}

export function ErrorBlock({ message, title = "Something went wrong", actions, className }: { message: string; title?: string; actions?: React.ReactNode; className?: string }) {
  return (
    <Callout tone="danger" title={title} actions={actions} className={cn("mb-4", className)}>
      <span className="break-words">{message}</span>
    </Callout>
  );
}
