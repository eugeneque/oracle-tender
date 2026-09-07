import { ExternalLink, RefreshCw } from "lucide-react";

import type { TenderCard, TenderCardTable } from "../../api/types";

/**
 * Данные карточки закупки прямо с сайта источника: реквизиты и адрес заказчика, контактное
 * лицо, порядок предоставления документации, лоты, изменения, протоколы, договоры, журнал
 * событий.
 *
 * Разделы показываются в том же порядке и с теми же названиями, что на сайте: пользователь
 * сверяет карточку с первоисточником, и любая «своя» группировка полей заставляла бы его
 * искать соответствие. Поэтому же поля не фильтруются — показываем всё, что отдала страница.
 */
export function TenderCardTab({
  card,
  isLoading,
  onRefresh,
  isRefreshing,
  tableKeys,
}: {
  card: TenderCard | null;
  isLoading: boolean;
  onRefresh: () => void;
  isRefreshing: boolean;
  tableKeys?: string[];
}) {
  if (isLoading) {
    return <div className="py-8 text-center text-sm text-zinc-500">Загружаю карточку закупки…</div>;
  }

  if (!card || (card.sections.length === 0 && Object.keys(card.tables).length === 0)) {
    return (
      <div className="py-8 text-center text-sm text-zinc-500">
        Карточка закупки недоступна: у этой закупки нет реестрового номера в ЕИС, а площадка
        не отдаёт подробности без входа в личный кабинет.
      </div>
    );
  }

  const tables = Object.entries(card.tables).filter(
    ([key]) => !tableKeys || tableKeys.includes(key),
  );
  const showSections = !tableKeys;

  return (
    <div className="space-y-5">
      {showSections && (
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-zinc-500">
            {card.fetched_at
              ? `Данные с сайта источника, получены ${new Date(card.fetched_at).toLocaleString("ru-RU")}`
              : "Данные с сайта источника"}
          </span>
          <button
            onClick={onRefresh}
            disabled={isRefreshing}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          >
            <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
            {isRefreshing ? "Обновляю…" : "Обновить с сайта"}
          </button>
        </div>
      )}

      {showSections &&
        card.sections.map((section) => (
          <section key={section.title} className="rounded-xl border border-white/[0.08] bg-white/[0.02]">
            <h3 className="border-b border-white/[0.06] px-4 py-2.5 text-sm font-semibold text-zinc-100">
              {section.title}
            </h3>
            <dl className="grid gap-x-6 gap-y-3 px-4 py-3 sm:grid-cols-2">
              {section.fields.map(([name, value], index) => (
                <div key={`${name}-${index}`} className="min-w-0">
                  <dt className="text-xs text-zinc-500">{name}</dt>
                  <dd className="mt-0.5 break-words text-sm text-zinc-200">{value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}

      {tables.map(([key, table]) => (
        <CardTableBlock key={key} table={table} url={card.tab_urls[key]} />
      ))}
    </div>
  );
}

function CardTableBlock({ table, url }: { table: TenderCardTable; url?: string }) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.02]">
      <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-2.5">
        <h3 className="text-sm font-semibold text-zinc-100">{table.title}</h3>
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 text-xs text-indigo-400 hover:underline"
          >
            <ExternalLink size={12} />
            на сайте
          </a>
        )}
      </div>

      {table.rows.length === 0 ? (
        <div className="px-4 py-3 text-sm text-zinc-500">Записей нет.</div>
      ) : (
        // Таблицы источника бывают широкими (в журнале событий — длинные формулировки),
        // поэтому прокрутка внутри блока, а не растягивание всего окна.
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            {table.headers.length > 0 && (
              <thead className="border-b border-white/[0.06] text-zinc-500">
                <tr>
                  {table.headers.map((header, index) => (
                    <th key={`${header}-${index}`} className="px-4 py-2 font-medium">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {table.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-t border-white/[0.04] align-top">
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="px-4 py-2 text-zinc-300">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
