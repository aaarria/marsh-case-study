import { cn } from "@/lib/utils";

/** Small caps label. */
export function Kicker({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("kicker", className)} {...props} />;
}

/** Definition list row used in inspectors and passports. */
export function Field({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-0.5 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 break-words text-body", mono && "font-mono text-xs")}>{children ?? <span className="text-quiet">–</span>}</dd>
    </div>
  );
}
