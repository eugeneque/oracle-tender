import { useState } from "react";
import { BadgeCheck, ChevronRight, FileDown, Loader2 } from "lucide-react";

import type { SiType } from "../../api/types";
import { formatDate } from "../../utils/format";
import { Badge } from "./Badge";
import { type SiTypeGroupKey as GroupKey, siTypeGroup } from "./siTypeGroup";

const SI_SOURCE_LABELS: Record<string, string> = {
  auto_search: "автопоиск",
  manual: "вручную",
  import: "импорт",
};

// Раньше все коды шли одним списком, а три разных случая — неоднозначность реестра,
// «не электросчётчик», истёкшее свидетельство — выглядели одинаковой пометкой «требует ручной
// проверки». Группы отвечают на вопрос «что с этим кодом делать».
const GROUPS: { key: GroupKey; title: string; about: string; collapsed: boolean }[] = [
  {
    key: "pending",
    title: "Ждут подтверждения",
    about:
      "Найдены автопоиском в ФГИС. Проверьте, что код относится к этому производителю, и подтвердите: " +
      "подтверждённую запись повторный автопоиск не перезаписывает, а сама она переходит в «Действующие».",
    collapsed: false,
  },
  {
    key: "confirmed",
    title: "Действующие",
    about: "Подтверждены человеком или заведены вручную; свидетельство об утверждении типа действует.",
    collapsed: false,
  },
  {
    key: "expired",
    title: "Свидетельство истекло",
    about:
      "Приборы этих типов нельзя предлагать в новую закупку. Подтверждение этого не меняет — срок берётся из реестра. " +
      "Если Росстандарт продлит свидетельство, ревалидация принесёт новый срок и код сам вернётся в действующие.",
    collapsed: true,
  },
  {
    key: "offScope",
    title: "Не электросчётчики",
    about:
      "Теплосчётчики, счётчики воды и газа, УСПД и прочее, что реестр отдаёт по производителю. " +
      "Записи сохранены, но к моделям каталога не привязываются.",
    collapsed: true,
  },
];

function ApprovalBadge({ s }: { s: SiType }) {
  if (s.approval_state === "inactive") return <Badge tone="red">неактуален в реестре</Badge>;
  if (s.approval_state === "expired" && s.valid_to) return <Badge tone="red">свидетельство истекло {formatDate(s.valid_to)}</Badge>;
  if (s.approval_state === "expiring" && s.valid_to)
    return (
      <Badge tone="amber">
        истекает {formatDate(s.valid_to)} · {s.approval_days_left} дн.
      </Badge>
    );
  if (s.approval_state === "valid" && s.valid_to) return <Badge tone="zinc">до {formatDate(s.valid_to)}</Badge>;
  return null;
}

type Props = {
  siTypes: SiType[];
  isAdmin: boolean;
  busy: string | null;
  onVerify: (s: SiType) => void;
  onFetchDescriptionType: (s: SiType) => void;
};

export function SiTypeGroups({ siTypes, isAdmin, busy, onVerify, onFetchDescriptionType }: Props) {
  const [open, setOpen] = useState<Record<GroupKey, boolean>>(
    () => Object.fromEntries(GROUPS.map((g) => [g.key, !g.collapsed])) as Record<GroupKey, boolean>
  );

  return (
    <div className="space-y-4">
      {GROUPS.map((group) => {
        const items = siTypes.filter((s) => siTypeGroup(s) === group.key);
        if (items.length === 0) return null;
        const expanded = open[group.key];
        return (
          <section key={group.key}>
            <button
              onClick={() => setOpen((prev) => ({ ...prev, [group.key]: !prev[group.key] }))}
              className="flex items-center gap-1.5 text-sm font-medium text-zinc-200 hover:text-zinc-50"
            >
              <ChevronRight size={14} className={`transition-transform ${expanded ? "rotate-90" : ""}`} />
              {group.title}
              <span className="font-normal tabular-nums text-zinc-500">{items.length}</span>
            </button>
            <p className="mt-0.5 pl-5 text-[11px] leading-relaxed text-zinc-500">{group.about}</p>
            {expanded && (
              <table className="mt-1 w-full text-left text-sm">
                <tbody>
                  {items.map((s) => (
                    <SiTypeRow
                      key={s.id}
                      s={s}
                      group={group.key}
                      isAdmin={isAdmin}
                      busy={busy}
                      onVerify={onVerify}
                      onFetchDescriptionType={onFetchDescriptionType}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </section>
        );
      })}
    </div>
  );
}

function SiTypeRow({
  s,
  group,
  isAdmin,
  busy,
  onVerify,
  onFetchDescriptionType,
}: {
  s: SiType;
  group: GroupKey;
  isAdmin: boolean;
  busy: string | null;
  onVerify: (s: SiType) => void;
  onFetchDescriptionType: (s: SiType) => void;
}) {
  return (
    <tr className="border-t border-white/[0.06] first:border-t-0">
      <td className="whitespace-nowrap py-2 pr-4 align-top font-mono text-zinc-100">{s.si_code}</td>
      <td className="py-2 pr-4 align-top">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="zinc">{SI_SOURCE_LABELS[s.source] ?? s.source}</Badge>
          {/* В «Ждут подтверждения» и «Действующих» статус подтверждения и так задан группой. */}
          {(group === "expired" || group === "offScope") && s.verified_by_user && (
            <Badge tone="green">
              <BadgeCheck size={11} /> подтверждён
            </Badge>
          )}
          {group !== "offScope" && <ApprovalBadge s={s} />}
          {group === "offScope" && s.type_name && <span className="text-[11px] text-zinc-500">{s.type_name}</span>}
          {s.has_description_type_text && <Badge tone="green">описание типа загружено</Badge>}
          {s.tested_modifications.length > 0 && (
            <span title={s.tested_modifications.join("\n")}>
              <Badge tone="zinc">исполнений в реестре: {s.tested_modifications.length}</Badge>
            </span>
          )}
          {s.description_type_changed_at && (
            // Новая редакция «Описания типа» — изменение, о котором заказчик
            // просил узнавать; дата обязательна.
            <span
              title={`Редакция ${s.description_type_version ?? "—"}; характеристики разнесены из редакции ${
                s.description_type_extracted_version ?? "—"
              }`}
            >
              <Badge tone={s.description_type_extracted_version === s.description_type_version ? "zinc" : "amber"}>
                описание типа изменилось {formatDate(s.description_type_changed_at)}
              </Badge>
            </span>
          )}
          {s.review_status === "needs_review" && group !== "offScope" && (
            // Настоящая неоднозначность реестра; подробности со списком кандидатов — в подсказке.
            <span title={s.review_reason ?? undefined}>
              <Badge tone="amber">⚠ неоднозначно в реестре</Badge>
            </span>
          )}
        </div>
      </td>
      {isAdmin && (
        <td className="whitespace-nowrap py-2 text-right align-top">
          <div className="flex justify-end gap-2">
            {s.description_type_url && (
              <button
                onClick={() => onFetchDescriptionType(s)}
                disabled={busy === `fetch-${s.id}`}
                className="flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[11px] text-zinc-300 hover:bg-white/5 disabled:opacity-50"
              >
                {busy === `fetch-${s.id}` ? <Loader2 size={11} className="animate-spin" /> : <FileDown size={11} />}
                описание типа
              </button>
            )}
            {s.source !== "manual" && (
              <button
                onClick={() => onVerify(s)}
                disabled={busy === `si-${s.id}`}
                className={`rounded border px-2 py-0.5 text-[11px] disabled:opacity-50 ${
                  group === "pending"
                    ? "border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10"
                    : "border-white/10 text-zinc-300 hover:bg-white/5"
                }`}
              >
                {s.verified_by_user ? "снять подтверждение" : "подтвердить"}
              </button>
            )}
          </div>
        </td>
      )}
    </tr>
  );
}
