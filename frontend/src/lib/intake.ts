import type { Intake } from "./types";

export const PRIORITY_SUGGESTIONS = ["maternity benefits", "accident cover", "chronic conditions from day one", "no room rent capping", "global cover for travel", "wellness and OPD", "cost control / co-pay options"];

/** The advisor's inputs as short chips, in the order they matter. */
export function intakeChips(intake: Intake): string[] {
  const out: string[] = [];
  if (intake.industry) out.push(intake.industry);
  if (intake.geography) out.push(intake.geography);
  if (intake.employee_count) out.push(`${intake.employee_count.toLocaleString()} employees`);
  for (const p of intake.client_priorities || []) out.push(p);
  return out;
}
