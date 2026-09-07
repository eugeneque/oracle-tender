export type UserRole = "admin" | "user";

export interface User {
  id: string;
  username: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export type SourceStatus = "active" | "pending_access" | "disabled";
export type AdapterStatus = "not_implemented" | "implemented" | "blocked";
export type AvailabilityStatus = "available" | "unavailable" | null;

/** Типы источников, которые наполняют справочник продукции, а не ленту закупок
 * (`CATALOG_SOURCE_TYPES` в app/models/source.py). `/sources` отдаёт и те, и другие одним
 * списком, поэтому всё, что считает или фильтрует площадки закупок, обязано их отбросить —
 * иначе в счётчиках появляются сайты производителей. */
export const CATALOG_SOURCE_TYPES: string[] = ["fgis", "manufacturer_site"];

/** Сколько площадок закупок описано в разделе 4.1 ТЗ — знаменатель для плиток «источников
 * доступно». Используется только как запасное значение, пока список не загружен. */
export const TENDER_SOURCES_TOTAL = 12;

export interface Source {
  id: string;
  key: string;
  name: string;
  url: string;
  type: string;
  status: SourceStatus;
  polling_schedule: string;
  adapter_key: string | null;
  adapter_status: AdapterStatus;
  note: string | null;
  last_polled_at: string | null;
  availability_status: AvailabilityStatus;
  availability_checked_at: string | null;
  availability_error: string | null;
  created_at: string;
  updated_at: string;
}

/** Учётные данные площадки (раздел «Пользовательские данные» настроек). Пароля здесь нет —
 * API его не отдаёт ни в каком виде, только маску `password_masked`. */
export interface SourceCredential {
  id: string;
  source_id: string;
  source_key: string;
  source_name: string;
  label: string;
  username: string;
  password_masked: string;
  notes: string | null;
  is_active: boolean;
  updated_at: string;
  updated_by: string | null;
}

export interface SourceCredentialInput {
  source_id?: string;
  label?: string;
  username?: string;
  password?: string;
  notes?: string | null;
  is_active?: boolean;
}

export interface SourcePollResult {
  source_key: string;
  found: number;
  created: number;
  updated: number;
  errors: number;
}

// --- Справочник продукции (Этап 4, раздел 5.3 ТЗ) ---

export interface Manufacturer {
  id: string;
  legal_name: string;
  brand_name: string | null;
  website: string | null;
  is_mirtek: boolean;
  created_at: string;
}

export type SiTypeSource = "auto_search" | "manual" | "import";

export type ReviewStatus = "ok" | "needs_review";

export interface SiType {
  id: string;
  manufacturer_id: string;
  si_code: string;
  description_type_url: string | null;
  has_description_type_text: boolean;
  source: SiTypeSource;
  verified_by_user: boolean;
  // «Системе не хватило оснований решить самой»: реестр вернул несколько кандидатов или
  // кандидата не того вида измерений, либо истекает свидетельство об утверждении типа.
  // Не путать с `verified_by_user` — там «человек подтвердил корректность».
  review_status: ReviewStatus;
  review_reason: string | null;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Product {
  id: string;
  manufacturer_id: string;
  si_type_id: string | null;
  model_name: string;
  // Обозначение модели отдельно от наименования: наименование — как у производителя,
  // код — то, по чему прибор сопоставляется с Госреестром.
  model_code: string | null;
  article: string | null;
  device_type: string | null;
  // Заводское исполнение («Таганрог», «Владивосток»). У одной модели исполнения
  // различаются сроком службы и комплектом документов, поэтому это отдельные записи.
  execution: string | null;
  status: string;
  source_url: string | null;
  data_source: string | null;
  // Характеристики с сайта производителя, которым не нашлось поля в Приложении C.
  extra_specifications: Record<string, string>;
  review_status: ReviewStatus;
  review_reason: string | null;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export type CharacteristicSource =
  | "fgis_description_type"
  | "manufacturer_site"
  | "user_manual"
  | "manual_entry";

export interface Characteristic {
  id: string;
  product_id: string;
  group_name: string;
  field_name: string;
  value: string | null;
  source: CharacteristicSource;
  confidence: number | null;
  verified_by_user: boolean;
  updated_at: string;
}

export interface ExtractionOutcome {
  saved: number;
  skipped_unknown_field: number;
  skipped_protected: number;
  chunks_processed: number;
  chunks_failed: number;
}

export interface ManualExtractionOutcome {
  manual_url: string | null;
  manual_title: string | null;
  extraction: ExtractionOutcome;
  message: string | null;
}

export interface CatalogSite {
  adapter_key: string;
  manufacturer_legal_name: string;
  base_url: string;
  categories: number;
  // Профиль сайта может быть описан, а строки производителя в справочнике не быть.
  manufacturer_id: string | null;
}

export interface CatalogSyncOutcome {
  products_created: number;
  products_updated: number;
  characteristics_saved: number;
  ai_extracted: number;
  si_types_linked: number;
  marked_for_review: number;
  cards_failed: number;
  errors: string[];
}

export interface LinkSiTypesOutcome {
  linked: number;
  already_linked: number;
  // Не ошибка: код СИ для модели может быть просто ещё не найден автопоиском.
  not_found: number;
  needs_review: number;
  details: string[];
}

export interface ManualIngestOutcome {
  processed: number;
  // Пропуски разделены по причинам: повторный запуск (уже разобрано), отсутствие ссылки на
  // карточке и сознательное соблюдение robots.txt сайта.
  skipped_have_data: number;
  skipped_no_link: number;
  skipped_by_robots: number;
  failed: number;
  characteristics_saved: number;
  messages: string[];
}

export interface CatalogTask {
  id: string;
  adapter_key: string;
  reason: string;
  status: string;
  model_name: string | null;
  message: string | null;
  created_at: string;
}

export interface CatalogImportOutcome {
  manufacturers_matched: number;
  si_types_created: number;
  si_types_updated: number;
  products_created: number;
  errors: string[];
}

export interface YandexAiStudioSettings {
  is_configured: boolean;
  api_key_masked: string | null;
  folder_id: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface YandexConnectionTestResult {
  success: boolean;
  message: string;
}

export type TenderKanbanStatus = "collecting_bids" | "evaluation" | "completed" | "cancelled";

/** Этап внутреннего пайплайна МИРТЕК (раздел 5.6 ТЗ, решение 03.09.2026).
 *
 * Не путать с `TenderKanbanStatus`: тот описывает состояние закупки на площадке
 * («идёт приём заявок»), этот — что с ней сделали мы («заявка подана»). Поля ортогональны:
 * доска Kanban группирует по этапу, статус остался фильтром и бейджем карточки. */
export type TenderStage =
  | "ai_selected"
  | "under_review"
  | "application_submitted"
  | "won"
  | "lost"
  | "rejected";

export const STAGE_LABELS: Record<TenderStage, string> = {
// Этап `ai_selected` показывается как «Новая» (04.09.2026), а не «AI отобрал».
// Прежняя подпись обманывала: это значение по умолчанию, которое получает КАЖДАЯ собранная
// закупка, никакого решения модели за ним нет. Решение модели — отдельная метка «Подобрано
// ИИ» (`tenders.ai_relevant`), и две разные вещи под похожими названиями путали работу.
  ai_selected: "Новая",
  under_review: "На проверке",
  application_submitted: "Заявка подана",
  won: "Выиграли",
  lost: "Проиграли",
  rejected: "Отклонён",
};

/** Порядок колонок доски — последовательность работы, а не алфавит. */
export const STAGE_ORDER: TenderStage[] = [
  "ai_selected",
  "under_review",
  "application_submitted",
  "won",
  "lost",
  "rejected",
];

export interface TenderSourceRef {
  key: string;
  name: string;
}

export interface Tender {
  id: string;
  external_id: string;
  title: string;
  customer_name: string | null;
  organizer_name: string | null;
  procurement_method: string | null;
  status: TenderKanbanStatus | null;
  price: string | null;
  currency: string;
  application_start: string | null;
  application_end: string | null;
  publish_date: string | null;
  okpd2_code: string | null;
  source_url: string | null;
  source: TenderSourceRef;
  // Поля этапов 5-6: до запуска анализа документации остаются пустыми.
  tender_type: TenderTypeCode | null;
  region_organizer_code: string | null;
  region_delivery_code: string | null;
  federal_district_code: number | null;
  ai_comment: string | null;
  /** Прежний статус релевантности — вычисляемый алиас поверх `stage` (раздел 7 ТЗ).
   * В БД такой колонки больше нет, но во внешнем API поле сохранено. */
  relevance_status: RelevanceStatus;
  stage: TenderStage;
  registry_number: string | null;
  customer_contact_name: string | null;
  customer_contact_phone: string | null;
  customer_contact_email: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
  /** Итоговая AI-оценка по профилю (раздел 5.5.1 ТЗ) — главная метрика списка.
   * `null` — расчёт ещё не выполнялся. */
  ai_score: string | null;
  ai_verdict: VerdictCode | null;
  /** Процент победителя МИРТЕК (этап 6) — деталь уровня матрицы соответствия, а не главная
   * метрика: с 03.09.2026 в списке показывается `ai_score`. */
  win_percentage: string | null;
  requirements_count: number;
  /** Решение модели «это правда наша закупка». `null` — не проверяли (не то же, что false). */
  ai_relevant: boolean | null;
  ai_relevance_reason: string | null;
  ai_relevance_confidence: number | null;
  created_at: string;
  updated_at: string;
}

// --- Анализ тендера (Этапы 5-6, разделы 5.4-5.5 ТЗ) ---

export type TenderTypeCode =
  | "supply_only"
  | "complex"
  | "works_only"
  | "reverification"
  | "other";

export type RelevanceStatus = "new" | "confirmed" | "rejected";

export type Criticality = "critical" | "important" | "minor";

export type ComplianceStatusCode = "meets" | "partial" | "not_meets" | "no_data";

export type ComplianceSourceCode =
  | "si_type"
  | "product_catalog"
  | "user_manual_fallback"
  | "ai_semantic";

export interface Requirement {
  id: string;
  tender_id: string;
  text: string;
  normalized_text: string | null;
  criticality: Criticality;
  category: string | null;
  verified_by_user: boolean;
  created_at: string;
}

export interface ComplianceEntry {
  id: string;
  requirement_id: string;
  manufacturer_id: string;
  manufacturer_name: string;
  status: ComplianceStatusCode;
  explanation: string | null;
  source: ComplianceSourceCode;
  confidence: string | null;
  needs_human_review: boolean;
}

export interface WinPercentageRow {
  manufacturer_id: string;
  manufacturer_name: string;
  is_mirtek: boolean;
  percentage: string;
  reason_summary: string | null;
  requirements_total: number;
  requirements_scored: number;
  calculated_at: string;
}

export interface ComplianceMatrix {
  requirements: Requirement[];
  entries: ComplianceEntry[];
  win_percentages: WinPercentageRow[];
}

/**
 * Фоновая задача анализа или расчёта соответствия. Анализ и расчёт больше не выполняются
 * в HTTP-запросе: карточка тендера получает задачу сразу и опрашивает её состояние,
 * а итог приходит текстом в `message`.
 */
export interface BackgroundJob {
  id: string;
  kind: "tender_analysis" | "tender_evaluation";
  status: "queued" | "running" | "success" | "error";
  tender_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
  message: string | null;
}

export interface TenderPage {
  items: Tender[];
  total: number;
  limit: number;
  offset: number;
  /** Сколько тендеров каждого статуса попало в выборку ЦЕЛИКОМ, а не на текущую страницу:
   * подсчёт по `items` всегда давал в сумме ровно размер страницы. Ключ `unclassified` —
   * тендеры без статуса. */
  status_counts: Record<string, number>;
}

/** Доска Kanban. Колонки набираются на сервере независимо друг от друга, поэтому у доски
 * нет пагинации: `columns` — начало каждой колонки, `counts` — сколько в ней всего по
 * текущим фильтрам. Ключ и там, и там — статус (`unclassified` — тендеры без статуса). */
export interface TenderBoard {
  columns: Record<string, Tender[]>;
  counts: Record<string, number>;
  total: number;
  per_column: number;
}

/** Правка классификации человеком (раздел 5.6 ТЗ). Отправляются только изменённые поля:
 * `null` означает «снять значение», поэтому отсутствие ключа и `null` — разные вещи. */
export interface TenderUpdate {
  tender_type?: string | null;
  region_organizer_code?: string | null;
  region_delivery_code?: string | null;
  federal_district_code?: number | null;
  okpd2_code?: string | null;
  relevance_status?: RelevanceStatus | null;
  stage?: TenderStage | null;
  assignee_id?: string | null;
}

export type HistoryKind = "field_change" | "comment";

export interface TenderHistoryEntry {
  id: string;
  kind: HistoryKind;
  field_name: string | null;
  field_label: string | null;
  old_value: string | null;
  new_value: string | null;
  comment: string | null;
  user_id: string | null;
  user_name: string | null;
  created_at: string;
}

export interface Region {
  code: string;
  name: string;
  federal_district_code: number;
}

export interface FederalDistrict {
  code: number;
  name: string;
}

export interface TenderStats {
  total: number;
  by_status: Record<string, number>;
}

export type DocumentParseStatus = "pending" | "success" | "error";

export type DocumentClassCode =
  | "tz_description"
  | "ssr"
  | "contract"
  | "notice"
  | "protocol"
  | "other";

export interface TenderDocument {
  id: string;
  file_name: string;
  file_type: string | null;
  parse_status: DocumentParseStatus;
  parse_error: string | null;
  downloaded_at: string | null;
  has_text: boolean;
  /** Автоклассификация типа документа (раздел 5.6 ТЗ, вкладка «Документы»). */
  document_class: DocumentClassCode | null;
  document_class_label: string | null;
  /** Звёздочка «приоритетный источник для расчёта»: помеченный файл уходит в разбор первым. */
  is_priority_source: boolean;
}

// --- Аналитика (раздел 5.6 ТЗ, дашборд) ---

export interface AnalyticsSummary {
  total: number;
  total_amount: string;
  with_price: number;
  average_amount: string;
  deadline_soon: number;
  analysed: number;
  /** Средняя AI-оценка по профилю (раздел 5.5.1 ТЗ) — главная метрика с 03.09.2026. */
  average_ai_score: string | null;
  average_win_percentage: string | null;
  by_status: Record<string, number>;
  by_relevance: Record<string, number>;
  by_type: Record<string, number>;
}

/** Точка ряда динамики. `month` — первое число месяца (YYYY-MM-DD). */
export interface MonthPoint {
  month: string;
  count: number;
  amount: string;
  average_ai_score: string | null;
  average_win_percentage: string | null;
}

export interface RegionRow {
  code: string | null;
  name: string;
  federal_district: string | null;
  count: number;
  amount: string;
}

export interface CustomerRow {
  name: string;
  count: number;
  amount: string;
}

export interface LabelledPercentage {
  label: string;
  percentage: string | null;
  count: number;
}

export interface WinBreakdown {
  by_region: LabelledPercentage[];
  by_type: LabelledPercentage[];
}

/** Плитка состояния системы. `tone` задаёт цвет, `progress` (0..1) — шкалу заполнения. */
export type WidgetTone = "ok" | "warn" | "danger" | "neutral";

export interface AnalyticsWidget {
  key: string;
  title: string;
  value: string;
  caption: string;
  tone: WidgetTone;
  progress: number | null;
}

export interface AnalyticsOverview {
  summary: AnalyticsSummary;
  monthly: MonthPoint[];
  regions: RegionRow[];
  customers: CustomerRow[];
  win_breakdown: WinBreakdown;
  widgets: AnalyticsWidget[];
}

/** Сводка ИИ по всем разделам. При сбое модели заполнено только `error`. */
export interface AiSummary {
  generated_at: string;
  headline: string | null;
  highlights: string[];
  risks: string[];
  actions: string[];
  error: string | null;
}

export interface RegionResponsible {
  region_code: string;
  region_name: string | null;
  responsible_name: string | null;
  manager_name: string | null;
  updated_at: string;
}

/** Строка журнала операций (раздел 5.9 ТЗ) — раздел «Логирование» в настройках. */
export interface LogEntry {
  id: string;
  timestamp: string;
  level: "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  component: string;
  action: string;
  result: string;
  details: string | null;
  user_name: string | null;
}

export interface LogPage {
  items: LogEntry[];
  total: number;
}

export interface LogFacets {
  components: string[];
  levels: string[];
}

/** Настройки почтового канала уведомлений (раздел 5.8 ТЗ). Пароль наружу не отдаётся. */
export interface NotificationSettings {
  is_enabled: boolean;
  is_configured: boolean;
  smtp_host: string | null;
  smtp_port: number;
  smtp_security: "ssl" | "starttls" | "none";
  smtp_username: string | null;
  has_password: boolean;
  from_address: string | null;
  recipients: string | null;
  admin_recipients: string | null;
  trigger_new_relevant: boolean;
  trigger_high_ai_score: boolean;
  trigger_deadline_soon: boolean;
  trigger_critical_error: boolean;
  ai_score_threshold: number;
  deadline_days_threshold: number;
  updated_at: string | null;
  updated_by: string | null;
}

/** Запись журнала уведомлений: что система отправила, кому и с каким исходом. */
export interface NotificationEntry {
  id: string;
  created_at: string;
  trigger: string;
  trigger_title: string;
  status: "sent" | "failed" | "skipped";
  channel: string;
  recipients: string | null;
  subject: string;
  body: string | null;
  /**
   * Оформленное тело — только у писем, написанных администратором в редакторе.
   * HTML очищен на сервере по белому списку тегов (`app/services/email_html.py`),
   * поэтому его можно рендерить как разметку, а не как текст.
   */
  body_html: string | null;
  error: string | null;
  tender_id: string | null;
  /** Автор ручной рассылки; у писем по триггерам пусто — их отправила система. */
  sent_by: string | null;
}

/** Адрес для подсказок в поле получателей: кому система уже писала, когда и сколько раз. */
export interface KnownRecipient {
  address: string;
  name: string | null;
  last_sent_at: string | null;
  sent_count: number;
}

/** Ключ доступа внешней системы к API (раздел 5.10 ТЗ — задел под Bitrix24). */
export interface ApiClient {
  id: string;
  name: string;
  key_prefix: string;
  is_active: boolean;
  created_at: string;
  last_used_at: string | null;
}

/** Ответ на выпуск ключа — единственный раз, когда виден сам ключ. */
export interface ApiClientCreated extends ApiClient {
  key: string;
}

/** Раздел карточки закупки с сайта источника: заголовок и пары «поле — значение». */
export interface TenderCardSection {
  title: string;
  fields: [string, string][];
}

export interface TenderCardTable {
  title: string;
  headers: string[];
  rows: string[][];
}

/**
 * Карточка закупки в том виде, в каком её показывает сайт источника: сведения о заказчике и
 * контактах, предоставление документации, лоты, изменения и разъяснения, протоколы,
 * договоры, журнал событий.
 */
export interface TenderCard {
  sections: TenderCardSection[];
  tables: Record<string, TenderCardTable>;
  tab_urls: Record<string, string>;
  fetched_at: string | null;
  insights: TenderInsights | null;
}

export interface InsightItem {
  title: string;
  detail: string;
  severity: "high" | "medium" | "low" | "info";
  evidence: string | null;
}

/** Разбор карточки моделью: риски, пробелы в данных и восстановленные значения. */
export interface TenderInsights {
  summary: string;
  risks: InsightItem[];
  data_gaps: InsightItem[];
  filled_fields: { field: string; value: string; source: string; confidence: number }[];
  checklist: string[];
  generated_at: string | null;
  applied_fields?: string[];
}


// --- AI-оценка по профилю (раздел 5.5.1 ТЗ, решение 03.09.2026) ---

export type VerdictCode = "go" | "go_with_reservations" | "no_go";

export type WeakPointSeverity = "significant" | "moderate" | "minor";

/** Ссылка, из которой сложилось число измерения. Без неё цифру нельзя проверить — раздел
 * 5.5.1 ТЗ требует прослеживаемости каждой цифры до источника. */
export interface EvidenceItem {
  type:
    | "requirement"
    | "tender_document"
    | "company_profile_field"
    | "similar_tender"
    | "tender_field"
    /** Реальный протокол участия МИРТЕК в похожей закупке — основная опора History. */
    | "company_participation";
  ref_id: string;
  note: string | null;
}

export interface WeakPoint {
  severity: WeakPointSeverity;
  severity_label: string;
  text: string;
}

export interface RecommendedStrategy {
  verdict: string;
  price: string;
  first_step: string;
}

/** Оценка целиком. `history_score === null` — «недостаточно данных», а НЕ ноль: измерение
 * исключается из итога, а не штрафует его (раздел 5.5.1 ТЗ). */
export interface AiProfileScore {
  id: string;
  tender_id: string;
  history_score: string | null;
  history_comment: string | null;
  history_evidence: EvidenceItem[];
  task_score: string | null;
  task_comment: string | null;
  task_evidence: EvidenceItem[];
  competencies_score: string | null;
  competencies_comment: string | null;
  competencies_evidence: EvidenceItem[];
  overall_score: string | null;
  summary: string | null;
  verdict: VerdictCode | null;
  verdict_label: string | null;
  weak_points: WeakPoint[];
  recommended_strategy: RecommendedStrategy | null;
  similar_tender_ids: string[];
  /** Записи истории участий, на которых построено измерение History. */
  participation_ids: string[];
  company_profile_snapshot: Record<string, unknown> | null;
  calculated_at: string;
}

// --- Профиль компании (раздел 7 ТЗ) ---

export interface CompanyLicense {
  name: string;
  number?: string | null;
  issued_at?: string | null;
  valid_until?: string | null;
  issuer?: string | null;
}

export interface CompanyPastProject {
  work_type: string;
  customer?: string | null;
  volume?: string | null;
  year?: number | null;
  description?: string | null;
}

/** Откуда взялось значение поля и подтвердил ли его человек (раздел 7 ТЗ). */
export interface FieldSource {
  source: "auto_search" | "manual";
  verified_by_user: boolean;
}

export interface CompanyProfile {
  id: string;
  manufacturer_id: string;
  legal_name: string | null;
  inn: string | null;
  kpp: string | null;
  ogrn: string | null;
  registration_date: string | null;
  legal_address: string | null;
  field_sources: Record<string, FieldSource>;
  years_of_experience: number | null;
  licenses: CompanyLicense[];
  past_projects: CompanyPastProject[];
  bank_requisites: Record<string, unknown> | null;
  letterhead_file_path: string | null;
  updated_at: string;
  /** Считает бэкенд: правило «чем профиль считается заполненным» живёт в одном месте. */
  is_filled: boolean;
  /** Без ИНН историю участий не синхронизировать — она запрашивается именно по нему. */
  has_inn: boolean;
}

export interface CompanyProfileUpdate {
  legal_name?: string | null;
  inn?: string | null;
  kpp?: string | null;
  ogrn?: string | null;
  registration_date?: string | null;
  legal_address?: string | null;
  field_sources?: Record<string, FieldSource>;
  years_of_experience?: number | null;
  licenses?: CompanyLicense[];
  past_projects?: CompanyPastProject[];
  bank_requisites?: Record<string, unknown> | null;
}

// --- Вкладки карточки (раздел 5.6 ТЗ) ---

export interface ExtraSection {
  key: string;
  title: string;
  points: string[];
}

export interface TenderExtraSections {
  sections: ExtraSection[];
  generated_at: string | null;
}

/** Данные вкладки «Расчёт». Все поля могут быть пустыми: пока агрегаты по нише не собраны,
 * вкладка честно говорит «анализ ещё не запускался» вместо нулей. */
export interface NicheStatistics {
  okpd2_code: string;
  region_code: string | null;
  sample_size: number | null;
  avg_participants: string | null;
  single_participant_share: string | null;
  median_price_reduction_pct: string | null;
  usual_submission_days: number | null;
  top_winners: Array<{
    manufacturer_name: string;
    wins_count?: number;
    win_share_pct?: number;
    typical_discount_pct?: number;
  }>;
  source: string;
  calculated_at: string;
}

export interface SimilarTender {
  tender_id: string;
  title: string;
  customer_name: string | null;
  price: string | null;
  publish_date: string | null;
  similarity_score: string;
}

// --- История участий (раздел 5.6 «Моя компания», раздел 7 ТЗ) ---

/** Три исхода, а не два: снятие с торгов по формальной причине (`disqualified`) — не то же
 * самое, что проигрыш по существу (`lost`), и лечится не ценой, а оформлением заявки. */
export type ParticipationOutcome = "won" | "lost" | "disqualified" | "unknown";

export interface CompanyParticipation {
  id: string;
  tender_id: string | null;
  external_tender_id: string | null;
  tender_title: string | null;
  customer_name: string | null;
  customer_org_id: string | null;
  our_inn: string | null;
  our_bid: string | null;
  price_drop_pct: string | null;
  competitors_count: number | null;
  outcome: ParticipationOutcome;
  outcome_label: string;
  final_contract_value: string | null;
  executed_at: string | null;
  lessons_learned_md: string | null;
  source: "eis_contracts" | "manual";
  source_label: string;
  last_synced_at: string | null;
  created_at: string;
}

export interface ParticipationSummary {
  total: number;
  won: number;
  lost: number;
  disqualified: number;
  unknown: number;
  /** Доля побед среди участий с ИЗВЕСТНЫМ исходом; null, когда таких нет. */
  win_rate: string | null;
  /** Вся история пришла из источника, который знает только о победах (реестр контрактов
   * ЕИС). Тогда 100% — свойство источника, а не факт о компании, и интерфейс обязан это
   * сказать рядом с цифрой. */
  wins_only_data: boolean;
  last_synced_at: string | null;
}

export interface ParticipationList {
  items: CompanyParticipation[];
  summary: ParticipationSummary;
}

export interface ParticipationWrite {
  tender_id?: string | null;
  external_tender_id?: string | null;
  tender_title?: string | null;
  customer_name?: string | null;
  our_inn?: string | null;
  our_bid?: string | null;
  price_drop_pct?: string | null;
  competitors_count?: number | null;
  outcome: ParticipationOutcome;
  final_contract_value?: string | null;
  executed_at?: string | null;
  lessons_learned_md?: string | null;
}

export interface ParticipationSyncResult {
  fetched: number;
  created: number;
  updated: number;
  matched_tenders: number;
  /** Второй шаг: закупки с поданной заявкой, у которых выяснялся исход. */
  checked_submitted: number;
  /** Проигрыши, выведенные из пайплайна: в открытых реестрах их нет. */
  losses_found: number;
}

/** Кандидат автопоиска. Ничего не сохраняет до подтверждения человеком. */
export interface EgrulCandidate {
  legal_name: string | null;
  inn: string | null;
  /** Есть только у rusprofile: в выдаче ЕГРЮЛ КПП отсутствует. */
  kpp: string | null;
  ogrn: string | null;
  registration_date: string | null;
  legal_address: string | null;
  /** Откуда взят кандидат — показывается рядом, чтобы подтверждали зная источник. */
  source: "egrul" | "rusprofile";
  source_url: string | null;
}

// --- Профиль релевантности (раздел 5.1.1 ТЗ) ---

export interface KeywordGroup {
  id: string;
  name: string;
  /** Синтаксис: `слово*` — по основе, `(a* b*)~N` — в пределах N слов друг от друга. */
  keywords: string[];
  /** Совпало — группа не срабатывает, даже если совпали положительные ключи. */
  exclusion_keywords: string[];
  okpd2_codes: string[] | null;
  /** Фразы, которыми группа ходит в поиск площадок (без стемминга и близости). */
  search_queries: string[];
  is_active: boolean;
}

export interface RelevanceProfile {
  groups: KeywordGroup[];
  search_queries: string[];
}

export interface RelevanceRecalcResult {
  processed: number;
  passed: number;
  rejected: number;
}

export interface AiCheckResult {
  checked: number;
  relevant: number;
  rejected: number;
  failed: number;
  messages: string[];
  pending: number;
}
