import type { SiType } from "../../api/types";

export type SiTypeGroupKey = "pending" | "confirmed" | "expired" | "offScope";

/** В какую группу списка кодов СИ попадает тип: вид прибора и срок свидетельства
 * важнее подтверждения — подтверждение не оживляет истёкший тип. */
export function siTypeGroup(s: SiType): SiTypeGroupKey {
  if (!s.is_electricity_meter) return "offScope";
  if (s.approval_state === "expired" || s.approval_state === "inactive") return "expired";
  if (!s.verified_by_user && s.source !== "manual") return "pending";
  return "confirmed";
}
