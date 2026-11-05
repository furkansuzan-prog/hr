import { ApiError } from "./api";

/** Data-management API (/api/v1/manage/*) shapes. */
export type RoleAssignment = { assignment_id: number; role_code: string; scope_type: "tenant" | "legal_entity"; legal_entity_id: number | null; valid_from: string | null; valid_to: string | null; active?: boolean };
export type ManagedUser = { id: number; email: string; full_name: string; is_active: boolean; roles: RoleAssignment[] };
export type Role = { id: number; code: string; name: string; permissions: string[]; is_system?: boolean };
export type IdentityReview = { id: number; reason: string; payload: Record<string, unknown>; candidate_person_id: number | null; import_batch_id: number | null; status: "open" | "merged" | "created" | "rejected" | string; created_at: string };
export const TERMINATION_CATEGORIES = ["voluntary", "employer_initiated", "contract_end", "project_end", "retirement", "death", "other", "unknown"] as const;

/** FastAPI answers an unknown route with exactly "Not Found" / "Method Not Allowed"; resource 404s in this API say
 * "Not found". Only the former means the endpoint is not deployed yet - show a clear message instead of raw text. */
export function manageError(e: unknown, unavailable: string): unknown {
  if (e instanceof ApiError && ((e.status === 404 && e.message === "Not Found") || e.status === 405)) return new Error(unavailable);
  return e;
}
