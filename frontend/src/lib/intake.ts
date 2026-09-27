import type { Intake } from "./types";

export const PRIORITY_SUGGESTIONS = ["maternity benefits", "accident cover", "chronic conditions from day one", "no room rent capping", "global cover for travel", "wellness and OPD", "cost control / co-pay options"];

/** A name stored on the run or the intake. Blank and the backend placeholder are missing, not a company. */
export function storedCompanyName(...candidates: Array<string | null | undefined>): string | null {
  for (const raw of candidates) {
    const name = (raw ?? "").trim();
    if (name && name !== "?") return name;
  }
  return null;
}

/** The advisor's inputs as short chips, in the order they matter. Missing intake yields no chips. */
export function intakeChips(intake?: Intake | null): string[] {
  const out: string[] = [];
  if (!intake) return out;
  if (intake.industry) out.push(intake.industry);
  if (intake.geography) out.push(intake.geography);
  if (intake.employee_count) out.push(`${intake.employee_count.toLocaleString()} employees`);
  for (const p of intake.client_priorities || []) out.push(p);
  return out;
}
