import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { MarketInterfaceReviewItem, MarketType } from "../../api/types";
import { Panel } from "./common";
import { BUTTON, INPUT, NO_RIGHT, PRIMARY } from "./ModificationForm";
import { fmt } from "./format";

/**
 * Интерфейс по коду в написании (23.11.2026, раздел 122 ARCHITECTURE.md). Аналитик: «надо, чтобы интерфейсы
 * сверялись с описанием типа и руководством по эксплуатации, и спорные моменты выносились бы на согласование».
 * Таблица кодов типа («RF2400/1 = ZigBee») решает однозначные коды; спорные — во вкладке «На согласование»:
 * подтвердить предложение Совы, исправить или дописать строку в таблицу кодов (решит все такие написания).
 */

const CHANNELS = ["RS", "RF", "PLC", "GSM", "NB-IoT", "LTE", "LoRaWAN", "ZigBee", "Bluetooth", "Wi-Fi", "Ethernet", "н/д"];

interface Fail {
  (err: unknown, fallback: string): void;
}

/** Решение по интерфейсу одного написания: поле с подсказками каналов, «Сохранить», «Снять». */
export function InterfaceChoice({
  variantId,
  raw,
  initial,
  manual,
  canEdit,
  fail,
  onDone,
}: {
  variantId: number;
  raw: string;
  initial: string | null;
  manual: boolean;
  canEdit: boolean;
  fail: Fail;
  onDone: (message: string) => void;
}) {
  const [value, setValue] = useState(initial ?? "");
  const [busy, setBusy] = useState(false);
  const save = async (next: string | null) => {
    setBusy(true);
    try {
      const result = await api.put<{ interface: string | null; review: string | null }>(`/meter-market/variants/${variantId}/interface`, {
        interface: next,
      });
      onDone(
        next === null
          ? `Интерфейс «${raw}» — решение снято${result.review ? ", снова на согласовании" : ""}`
          : `Интерфейс «${raw}» — ${result.interface} (решение аналитика)`,
      );
    } catch (err) {
      fail(err, "Не удалось сохранить интерфейс");
    } finally {
      setBusy(false);
    }
  };
  const listId = `iface-channels-${variantId}`;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        className={`${INPUT} w-56 py-1 text-xs`}
        list={listId}
        value={value}
        disabled={!canEdit || busy}
        placeholder="RS, ZigBee"
        onChange={(e) => setValue(e.target.value)}
      />
      <datalist id={listId}>
        {CHANNELS.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      <button type="button" className={PRIMARY} disabled={!canEdit || busy || !value.trim()} title={canEdit ? "Через запятую: «RS, ZigBee»; «н/д» — интерфейса нет" : NO_RIGHT}
        onClick={() => void save(value.trim())}>
        {initial && value.trim() === initial ? "Подтвердить" : "Сохранить"}
      </button>
      {manual && (
        <button type="button" className={BUTTON} disabled={!canEdit || busy} title={canEdit ? "Вернуть расшифровку по таблице кодов типа" : NO_RIGHT}
          onClick={() => void save(null)}>
          Снять решение
        </button>
      )}
    </div>
  );
}

/** Вкладка справочника «На согласование». */
export function InterfaceReviewQueue({ canEdit, report, fail }: { canEdit: boolean; report: (m: string) => void; fail: Fail }) {
  const [data, setData] = useState<{ items: MarketInterfaceReviewItem[]; total: number } | null>(null);
  const [types, setTypes] = useState<Record<number, MarketType>>({});
  const [codesFor, setCodesFor] = useState<number | null>(null);
  const load = useCallback(() => {
    api
      .get<{ items: MarketInterfaceReviewItem[]; total: number }>("/meter-market/interface-review")
      .then(setData)
      .catch((err) => fail(err, "Не удалось загрузить очередь согласования"));
  }, [fail]);
  useEffect(load, [load]);
  const openCodes = async (typeId: number) => {
    if (codesFor === typeId) {
      setCodesFor(null);
      return;
    }
    try {
      const list = await api.get<MarketType[]>(`/meter-market/types`);
      setTypes(Object.fromEntries(list.map((t) => [t.id, t])));
      setCodesFor(typeId);
    } catch (err) {
      fail(err, "Не удалось открыть таблицу кодов");
    }
  };
  const done = (message: string) => {
    report(message);
    load();
  };
  if (!data) return <div className="py-16 text-center text-sm text-zinc-600">Загружаю очередь…</div>;
  return (
    <Panel
      title={`Интерфейс на согласовании: ${fmt(data.total)} написаний`}
      subtitle="Сова расшифровала код интерфейса в написании по таблице кодов типа, но не уверена: код не расшифрован, канал зависит от номера модуля (это знает руководство по эксплуатации) или расшифровка по описанию типа расходится со справочником. Пока решения нет, в отчёте — интерфейс модификации."
    >
      {data.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">Спорных интерфейсов нет</p>
      ) : (
        <div className="divide-y divide-white/[0.05]">
          {data.items.map((item) => (
            <div key={item.id} className="py-3">
              <div className="whitespace-pre-wrap font-mono text-sm text-zinc-100">«{item.raw}»</div>
              <div className="mt-0.5 text-xs text-zinc-500">
                {item.mit_number} {item.notation} · {item.manufacturer ?? "производитель не указан"} ·{" "}
                <span className="tabular-nums text-zinc-300">{fmt(item.count)}</span> поверок
                {!item.in_report && " · тип не в отчёте"}
              </div>
              <div className="mt-1 text-xs text-amber-300/90">{item.review}</div>
              <div className="mt-0.5 text-xs text-zinc-500">
                Предложение Совы: <span className="text-zinc-200">{item.suggested ?? "нет"}</span>
                {" · "}сейчас в отчёте (модификация «{item.modification ?? "не размечено"}»):{" "}
                <span className="text-zinc-200">{item.modification_interface ?? "нет данных"}</span>
              </div>
              {item.note && <div className="mt-0.5 text-xs text-zinc-600">{item.note}</div>}
              <div className="mt-2 flex flex-wrap items-center gap-3">
                <InterfaceChoice variantId={item.id} raw={item.raw} initial={item.suggested} manual={false} canEdit={canEdit} fail={fail} onDone={done} />
                <button type="button" className="text-xs text-indigo-300 hover:text-indigo-200" onClick={() => void openCodes(item.type_id)}>
                  {codesFor === item.type_id ? "Скрыть таблицу кодов" : "Таблица кодов типа…"}
                </button>
              </div>
              {codesFor === item.type_id && types[item.type_id] && (
                <InterfaceCodes type={types[item.type_id]} canEdit={canEdit} report={done} fail={fail} onSaved={() => setCodesFor(null)} />
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/** Таблица кодов интерфейсов типа: по строке на код. Сохранение пересчитывает все написания типа. */
export function InterfaceCodes({
  type,
  canEdit,
  report,
  fail,
  onSaved,
}: {
  type: MarketType;
  canEdit: boolean;
  report: (m: string) => void;
  fail: Fail;
  onSaved: () => void;
}) {
  const [value, setValue] = useState(type.interface_codes ?? "");
  const [busy, setBusy] = useState(false);
  const dirty = value.trim() !== (type.interface_codes ?? "").trim();
  const save = async () => {
    setBusy(true);
    try {
      await api.patch(`/meter-market/types/${type.id}`, { interface_codes: value });
      report(`Таблица кодов интерфейсов ${type.mit_number} сохранена — интерфейсы написаний пересчитаны`);
      onSaved();
    } catch (err) {
      fail(err, "Не удалось сохранить таблицу кодов");
    } finally {
      setBusy(false);
    }
  };
  const draft = async () => {
    setBusy(true);
    try {
      const result = await api.get<{ draft: string }>(`/meter-market/types/${type.id}/interface-codes/draft`);
      setValue((current) => (current.trim() ? `${current.trim()}\n${result.draft}` : result.draft));
    } catch (err) {
      fail(err, "Не удалось взять коды из описания типа");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 rounded-xl border border-white/[0.06] px-3 py-2">
      <label className="text-xs text-zinc-500" htmlFor={`iface-codes-${type.id}`}>
        Коды интерфейсов в обозначении — по ним интерфейс берётся из самого написания, а не из модификации
        {type.interface_review > 0 && <span className="text-amber-400"> · на согласовании {type.interface_review}</span>}
      </label>
      <textarea
        id={`iface-codes-${type.id}`}
        className={`${INPUT} mt-1 min-h-[6rem] w-full font-mono text-xs`}
        placeholder={"RS485 = RS\nRF433/n = RF\nRF2400/1 = ZigBee\nG/n = ? GSM  # канал зависит от модуля"}
        value={value}
        disabled={!canEdit}
        onChange={(e) => setValue(e.target.value)}
      />
      <p className="mt-1 text-xs text-zinc-600">
        «КОД = канал»; «/n» — любой номер модуля; «= ? канал» — на согласование с предложением; «A + B = ? …» — сочетание кодов;
        после «#» — комментарий («# из описания типа» — строка не от аналитика: расхождение с модификацией уйдёт на согласование).
        Нет кода в написании — «н/д». Пустая таблица — интерфейс из модификации.
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <button type="button" className={PRIMARY} disabled={!canEdit || !dirty || busy} title={canEdit ? undefined : NO_RIGHT} onClick={() => void save()}>
          Сохранить и пересчитать
        </button>
        <button type="button" className={BUTTON} disabled={!canEdit || busy} title={canEdit ? "Черновик из блоков «Основной / Дополнительные интерфейсы» описания типа" : NO_RIGHT}
          onClick={() => void draft()}>
          Заполнить из описания типа
        </button>
      </div>
    </div>
  );
}
