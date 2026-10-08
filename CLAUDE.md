# CLAUDE.md — Sova Scanner / ORACLE-T

Платформа тендерной аналитики для МИРТЕК (счётчики электроэнергии, АСКУЭ): собирает закупки с
площадок, отбирает релевантные профилями и ИИ, разбирает документацию, сопоставляет требования
с каталогом продукции (МИРТЕК и конкуренты) и считает шансы на победу. Интерфейс и все тексты —
на русском. Этот файл — карта проекта, чтобы не перечитывать код в каждой сессии; подробности
по разделам — в `oracle-t/ARCHITECTURE.md` (3000+ строк, оглавление: `grep -n "^## "`).

## Раскладка

```
oracle-tender-main/            ← не git-репозиторий (git status/diff не работают)
├── CLAUDE.md
├── README.md, DEPLOY.md       ← установка (install.sh: server|docker|local), обновление (update.sh)
├── oracle-t-tender-scoring.md ← описание механизма оценки тендеров (профиль компании, продукция, профили)
├── ORACLE-T.html              ← статический прототип интерфейса
└── oracle-t/
    ├── ARCHITECTURE.md        ← главный документ: по разделам, что и почему сделано (с датами)
    ├── README.md              ← запуск, тесты, источники тендеров
    ├── CRITERIA.md            ← критерии соответствия и реестры допуска
    ├── AI_SCORE_PROMPTS.md    ← промпты AI-оценки
    ├── run.sh                 ← локальный стенд: Postgres (Homebrew) + uvicorn :8000 + vite :5173
    ├── docker-compose.yml
    ├── backend/               ← FastAPI + SQLAlchemy 2 (sync) + Alembic, Python 3.11
    └── frontend/              ← React 18 + Vite + TypeScript + Tailwind
```

### Backend (`oracle-t/backend/app`)
- `api/endpoints/*.py` — роутеры (tenders, relevance, analytics, export, sources, company_profile,
  manufacturers, integrations, upper_software, users, logs, notifications, tags, …);
  `api/deps.py` — `get_current_user`, `require_role`, `parse_okpd2`, `resolve_relevance_profiles`.
- `services/*.py` — вся бизнес-логика (эндпоинты тонкие). Ключевые:
  - `tender_service.py` — `TenderFilters`, `_build_conditions`, список/доска/воронка (`count_tenders`,
    funnel). Список, Kanban, аналитика и Excel-выгрузка обязаны фильтровать одинаково — через
    общие `TenderFilters`/`apply_tender_filters`.
  - `relevance_service.py` — движок отбора `match_profile` (исключения → слова → ОКПД2),
    `tokenize`/`tender_text` (title + customer_name + procurement_method), отметка
    `tenders.passed_relevance_filter` общими профилями, `backfill`.
  - `relevance_profile_service.py` — совпадения профилей в `relevance_profile_matches`
    (`refresh_matches`: полный пересчёт при смене `rules_version`, иначе инкремент по
    `tenders.updated_at`), `preview`, `prepare_for_filter` (с `FOR UPDATE`), `can_edit`.
  - `ai_*`, `analysis_service`, `compliance_service`, `tender_analysis` — ИИ-разбор, матрица
    соответствия, AI-оценка; провайдеры YandexGPT, Claude и DeepSeek через RouterAI, GigaChat (задел, ждёт ключа) — `ai_provider_service`.
  - `product_catalog_service`, `fgis_*`, `catalog_*`, `manufacturer_*` — справочник продукции.
- `adapters/` — площадки (`eis*`, `roseltorg`, `sberbank_ast`, `fabrikant`, `tektorg`, `etpgpb`,
  `etprf`, `zakazrf`, `lot_online`, внешние каналы `gosplan`, `seldon`, `tenderplan`) и каталоги
  (`fgis`, `mirtek_catalog`, `manufacturer_site`, `rusprofile`). Интерфейс — `adapters/base.py`,
  реестр — `adapters/registry.py`.
- `core/scheduler.py` (APScheduler: опрос площадок, сроки, уборка), `core/jobs.py` (фоновые задачи).
- `models/` — одна сущность на файл; `alembic/versions/NNNN_name.py` — миграции с нумерацией
  (последняя на 08.10.2026 — `0071_gigachat_provider`), докстринг миграции объясняет «зачем».
- `seed/snapshot.py` — наполнение базы «из коробки» (`python -m app.seed.snapshot prepare`).
- `tests/` — pytest, ~77 файлов, против реальной БД `oraclet_test`.

### Frontend (`oracle-t/frontend/src`)
- `pages/` — `Tenders.tsx` (главная, ~2000 строк: фильтры, профили, воронка, виды списка),
  `TenderFull`, `Analytics`, `Catalog`, `Company`, `Integrations`, `Settings`, `Users`, `Logs`,
  `Home` («Что нового?»), `Changelog`.
- `components/` — `ProfilePicker`, `RelevanceProfilesModal` (+ `ProfilesManager`, редактор
  профиля с предпросмотром), `SelectionFunnelBar`, `SelectionGuide`, `TenderDetailPanel`, …
- `api/` — клиент (`api.get/post/put/delete`, `ApiError`) и `types.ts`.
- `content/changelog.ts` — **журнал изменений: при каждой правке, видимой пользователю, добавлять
  запись сверху** (`new` / `improved` / `fixed`, дата ISO). `content/homeSlides.ts` — плитки
  «Что нового?» для крупных нововведений.
- Дизайн-система — скилл `oracle-t-design` (стиль Apple, Inter, монохром + один акцент).

## Отбор тендеров (частый источник багов)

Слои: **Собрано → Профили → Модель (ИИ) → Фильтры = в списке** (воронка над списком).

- **Профили отбора** (`relevance_profiles`): общие (`is_default`, правит админ, 9 штук — бывшие
  группы `search_keyword_groups`) и личные (`owner_id`). Поля: `keywords`, `exclusion_keywords`,
  `okpd2_codes`, `match_mode` (any/all по словам), `okpd2_mode` (`narrow` — нужны и слова, и код;
  `either` — достаточно одного), `source_keys` (пусто = все площадки), `is_active`.
- `okpd2_mode='narrow'` (по умолчанию у новых профилей) **отбрасывает закупки без кода ОКПД2** —
  а код есть лишь у ~5% закупок (заполняется ИИ-анализом). Это задумано (тест
  `test_keywords_and_okpd2_narrow_each_other`), но объясняет «профиль почти ничего не находит».
- Профиль сужает только закупки своих площадок; несколько профилей на площадке — «любой»
  (объединение) или «все» (`_profiles_condition`).
- Фронт: `filters.relevanceProfileIds === null` → «общие по умолчанию»
  (`relevance_profile_default=true`); массив → явный выбор (`relevance_profile=<id>`…).
  Выбор личного профиля поверх умолчания **заменяет** общие (`applyProfileSelection` в
  `Tenders.tsx`, фикс 08.10.2026) — раньше дописывался к ним и в режиме «любой» ничего не сужал.
- Фразы сбора («что спрашиваем у площадок») — отдельная таблица `collection_terms`, к отбору
  отношения не имеют.
- «Релевантен» как решение человека — `tenders.relevance_status`; не путать с
  `passed_relevance_filter` (профили) и `ai_relevant` (модель).

## Запуск и проверки

```bash
cd oracle-t && ./run.sh                 # http://localhost:5173, API http://localhost:8000/health
```
- БД: Postgres 15 из Homebrew (`/opt/homebrew/opt/postgresql@15/bin/psql`), база/пользователь/
  пароль `oraclet`. Быстрая проверка данных: `PGPASSWORD=oraclet psql -h localhost -U oraclet oraclet`.
- Тесты бэкенда: `cd oracle-t/backend && POSTGRES_HOST=localhost .venv/bin/pytest -q`
  (нужна база `oraclet_test`).
- Фронт: `cd oracle-t/frontend && npm run build` (tsc + vite), `npm run lint`.
- На этой машине `backend/.venv` и `frontend/node_modules` могут отсутствовать — тогда их
  создаёт `run.sh` (venv на python3.11, `npm install`). Без них ни pytest, ни tsc не запустить.
- Docker в системе не установлен; стенд — только через `run.sh`.
- Админ: логин/пароль в `oracle-t/.env` (`BOOTSTRAP_ADMIN_*`), сброс — `backend/reset_admin.py`.

## Соглашения

- Комментарии и докстринги — по-русски, объясняют «почему» (часто с датой и ссылкой на раздел ТЗ
  или замечание тестировщика). Сохранять этот стиль и плотность.
- Новая колонка/таблица — только через новую миграцию Alembic со следующим номером.
- Важные решения дописываются новым разделом в `oracle-t/ARCHITECTURE.md`.
- Ошибки пользователю — понятным текстом (`HTTPException(detail="…")`), не пустой выдачей.
- В сессии активен хук GateGuard: перед первой правкой файла он требует перечислить импортёров
  файла, затронутые функции и процитировать инструкцию пользователя.
