import { useEffect, useState } from "react";
import { Check, ExternalLink, Loader2, Pencil, Sparkles, X } from "lucide-react";

import { api } from "../../api/client";
import type { FederalDistrict, Region, Tender, TenderUpdate } from "../../api/types";
import { ConfidenceBar } from "../ConfidenceBar";
import {
  formatDate,
  formatDateTime,
  formatPrice,
  percentValue,
  statusLabel,
  tenderTypeLabel,
} from "../../utils/format";

const TENDER_TYPE_OPTIONS = [
  { value: "supply_only", label: "Поставка ИПУ" },
  { value: "complex", label: "Комплекс (ПУ + работы)" },
  { value: "works_only", label: "Работы (СМР и ПНР)" },
  { value: "reverification", label: "Переповерка" },
  { value: "other", label: "Прочее" },
];

function MetaItem({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="mt-0.5 text-sm text-zinc-200">{value}</div>
    </div>
  );
}

const selectClass =
  "w-full rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-sm text-white outline-none focus:border-indigo-500";

/**
 * Форма исправления классификации (раздел 5.6 ТЗ, «исправить классификацию»).
 *
 * Отправляются только изменённые поля: пустая строка в селекте — это осознанное «снять
 * значение» (`null`), и слать её вместе с нетронутыми полями значило бы затирать чужие
 * правки, сделанные между открытием карточки и сохранением.
 */
function ClassificationForm({
  tender,
  regions,
  onSave,
  onCancel,
}: {
  tender: Tender;
  regions: Region[];
  onSave: (changes: TenderUpdate) => Promise<void>;
  onCancel: () => void;
}) {
  const [tenderType, setTenderType] = useState(tender.tender_type ?? "");
  const [organizerRegion, setOrganizerRegion] = useState(tender.region_organizer_code ?? "");
  const [deliveryRegion, setDeliveryRegion] = useState(tender.region_delivery_code ?? "");
  const [okpd2, setOkpd2] = useState(tender.okpd2_code ?? "");
  const [isSaving, setIsSaving] = useState(false);

  const submit = async () => {
    const changes: TenderUpdate = {};
    if (tenderType !== (tender.tender_type ?? "")) changes.tender_type = tenderType || null;
    if (organizerRegion !== (tender.region_organizer_code ?? ""))
      changes.region_organizer_code = organizerRegion || null;
    if (deliveryRegion !== (tender.region_delivery_code ?? ""))
      changes.region_delivery_code = deliveryRegion || null;
    if (okpd2.trim() !== (tender.okpd2_code ?? "")) changes.okpd2_code = okpd2.trim() || null;

    if (Object.keys(changes).length === 0) {
      onCancel();
      return;
    }
    setIsSaving(true);
    try {
      await onSave(changes);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="mb-5 rounded-xl border border-indigo-500/25 bg-indigo-500/[0.04] p-4">
      <div className="mb-3 text-sm font-semibold text-indigo-300">Исправление классификации</div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Тип конкурса</label>
          <select
            value={tenderType}
            onChange={(e) => setTenderType(e.target.value)}
            className={selectClass}
          >
            <option value="">— не определён —</option>
            {TENDER_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Код ОКПД2</label>
          <input
            value={okpd2}
            onChange={(e) => setOkpd2(e.target.value)}
            placeholder="26.51.63.130"
            className="w-full rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Регион заказчика</label>
          <select
            value={organizerRegion}
            onChange={(e) => setOrganizerRegion(e.target.value)}
            className={selectClass}
          >
            <option value="">— не определён —</option>
            {regions.map((region) => (
              <option key={region.code} value={region.code}>
                {region.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Регион поставки</label>
          <select
            value={deliveryRegion}
            onChange={(e) => setDeliveryRegion(e.target.value)}
            className={selectClass}
          >
            <option value="">— не определён —</option>
            {regions.map((region) => (
              <option key={region.code} value={region.code}>
                {region.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={() => void submit()}
          disabled={isSaving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          {isSaving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
          Сохранить
        </button>
        <button
          onClick={onCancel}
          disabled={isSaving}
          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5"
        >
          <X size={13} />
          Отмена
        </button>
        <span className="text-[11px] text-zinc-600">
          Федеральный округ подставится по региону заказчика
        </span>
      </div>
    </div>
  );
}

export function TenderOverviewTab({
  tender,
  onSave,
}: {
  tender: Tender;
  onSave: (changes: TenderUpdate) => Promise<void>;
}) {
  const [regions, setRegions] = useState<Region[]>([]);
  const [districts, setDistricts] = useState<FederalDistrict[]>([]);
  const [isEditing, setIsEditing] = useState(false);

  // Справочники грузятся один раз на открытие карточки: 85 регионов меняются раз в
  // пятилетку, дробить их запросом на каждое открытие селекта незачем.
  useEffect(() => {
    let cancelled = false;
    void api.get<Region[]>("/dictionaries/regions").then((rows) => {
      if (!cancelled) setRegions(rows);
    });
    void api.get<FederalDistrict[]>("/dictionaries/federal-districts").then((rows) => {
      if (!cancelled) setDistricts(rows);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const regionName = (code: string | null) =>
    code ? (regions.find((region) => region.code === code)?.name ?? code) : null;
  const districtName = tender.federal_district_code
    ? (districts.find((district) => district.code === tender.federal_district_code)?.name ??
      String(tender.federal_district_code))
    : null;

  return (
    <div>
      <div className="mb-5 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <MetaItem label="Статус" value={statusLabel(tender.status)} />
        <MetaItem label="Способ закупки" value={tender.procurement_method} />
        <MetaItem label="Сумма" value={formatPrice(tender.price, tender.currency)} />
        <MetaItem label="Заказчик" value={tender.customer_name} />
        <MetaItem label="Организатор" value={tender.organizer_name} />
        <MetaItem label="Размещён" value={formatDate(tender.publish_date)} />
        <MetaItem label="Срок подачи заявок" value={formatDateTime(tender.application_end)} />
        <MetaItem label="Код ОКПД2" value={tender.okpd2_code} />
        <MetaItem label="Тип конкурса" value={tenderTypeLabel(tender.tender_type)} />
        <MetaItem label="Регион заказчика" value={regionName(tender.region_organizer_code)} />
        <MetaItem label="Регион поставки" value={regionName(tender.region_delivery_code)} />
        <MetaItem label="Федеральный округ" value={districtName} />
      </div>

      {isEditing ? (
        <ClassificationForm
          tender={tender}
          regions={regions}
          onCancel={() => setIsEditing(false)}
          onSave={async (changes) => {
            await onSave(changes);
            setIsEditing(false);
          }}
        />
      ) : (
        <button
          onClick={() => setIsEditing(true)}
          className="mb-5 flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5"
        >
          <Pencil size={13} />
          Исправить классификацию
        </button>
      )}

      <div className="mb-5 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
        <div className="mb-2 text-xs text-zinc-500">
          Оценка соответствия МИРТЕК — взвешенная по критичности требований, без учёта цены и
          истории торгов (раздел 5.5 ТЗ)
        </div>
        <ConfidenceBar percent={percentValue(tender.win_percentage)} />
      </div>

      {tender.ai_comment && (
        <div className="mb-5 rounded-xl border border-indigo-500/25 bg-indigo-500/[0.04] p-4">
          <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold text-indigo-300">
            <Sparkles size={15} />
            Комментарий ИИ
          </div>
          <p className="whitespace-pre-line text-sm leading-relaxed text-zinc-300">
            {tender.ai_comment}
          </p>
        </div>
      )}

      {tender.source_url && (
        <a
          href={tender.source_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 text-sm text-indigo-400 hover:underline"
        >
          <ExternalLink size={14} />
          Открыть на сайте источника
        </a>
      )}
    </div>
  );
}
