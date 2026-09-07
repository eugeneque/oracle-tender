# ORACLE-T — развёртывание и эксплуатация

Инструкция для инженера, который ставит и обслуживает систему на сервере.
Разработчикам — [oracle-t/README.md](oracle-t/README.md), устройство системы —
[oracle-t/ARCHITECTURE.md](oracle-t/ARCHITECTURE.md).

---

## 1. Что разворачивается

| Компонент | Что это | Где слушает |
|---|---|---|
| `frontend` | React + Vite, собирается в статику, раздаётся nginx | 80 (наружу) |
| `backend` | FastAPI + Uvicorn, внутри — планировщик и очередь фоновых задач | 127.0.0.1:8000 |
| `db` | PostgreSQL 15, база `oraclet` | локально |

Фронтенд ходит в API относительным путём `/api/...`, nginx срезает префикс и проксирует на
backend. Статика и API живут на одном origin — отдельного домена для API не нужно, CORS не
настраивается.

**Планировщик работает внутри процесса backend**, отдельного воркера или cron нет. Расписание
жёстко привязано к `Europe/Moscow` независимо от часового пояса сервера:

| Задача | Время (МСК) | Настройка |
|---|---|---|
| Опрос площадок | 09:00 и 14:00 | `SCHEDULER_MORNING_TIME` / `SCHEDULER_AFTERNOON_TIME` |
| Пинг доступности источников | раз в минуту | — |
| Чистка старых логов | 03:15 | — |
| Ревалидация карточек ФГИС | 04:00 | `FGIS_REVALIDATION_TIME` |
| Обход каталогов производителей | пн 05:00 | `MIRTEK_CATALOG_SYNC_*` |

Отсюда следствие: **процесс backend должен работать постоянно**, а не подниматься по запросу.

---

## 2. Требования к серверу

- **ОС:** Ubuntu 22.04/24.04, Debian 12, RHEL/Rocky/Alma 9. Нужен systemd.
- **CPU/RAM:** 2 vCPU / 4 ГБ минимум, 4 vCPU / 8 ГБ комфортно. Память едят OCR сканов и
  headless-браузер Playwright, который поднимается для части площадок.
- **Диск:** 20 ГБ на старте. Установка занимает ~4 ГБ (venv, браузер, `node_modules`), дальше
  растёт каталог скачанной документации `oracle-t/backend/storage` — планируйте по объёму
  тендеров, это основной потребитель места.
- **Python:** 3.11+. Debian 12, Ubuntu 24.04 и Rocky 9 несут подходящий из коробки; на
  Ubuntu 22.04 системный — 3.10, установщик доставит `python3.11` из universe.
- **Node.js:** 18+ — только на время сборки фронтенда.
- **Права:** установка требует `sudo`. Репозиторий клонируйте **обычным пользователем**, а
  установщик запускайте через `sudo` — служба будет работать от этого пользователя, а не от
  root.

---

## 3. Установка, вариант А — нативно (рекомендуется)

```bash
git clone https://github.com/eugeneque/oracle-tender.git oracle-tender
cd oracle-tender
sudo ./install.sh --mode server
```

Установщик за девять шагов делает всё сам: ставит системные пакеты (PostgreSQL, tesseract с
русским языком, `bsdtar`, nginx, Node.js), генерирует `.env` с секретами, поднимает базу и
роль, собирает Python-окружение с браузером Playwright, накатывает миграции, собирает
фронтенд, заводит службу systemd и сайт nginx и в конце проверяет, что API и страница
отвечают.

Без интерактивных вопросов (для Ansible и прочей автоматизации):

```bash
sudo ./install.sh --mode server -y
```

Полезные ключи: `--http-port 8080` (порт интерфейса), `--backend-port` (порт API),
`--skip-playwright` (не ставить браузер, ~400 МБ — часть площадок станет недоступна),
`--dry-run` (показать план, ничего не менять), `--help`.

**Что появляется в системе**

| Объект | Путь |
|---|---|
| Служба backend | `/etc/systemd/system/oracle-t-backend.service` |
| Сайт nginx | `/etc/nginx/sites-available/oracle-t.conf` (или `conf.d`) |
| Статика интерфейса | `/var/www/oracle-t` |
| Конфигурация и секреты | `<repo>/oracle-t/.env` и `<repo>/oracle-t/backend/.env`, права 600 |
| Ключ шифрования паролей площадок | `<repo>/oracle-t/.credentials_key`, права 600 |
| Документы тендеров | `<repo>/oracle-t/backend/storage` |
| Логи приложения | `<repo>/oracle-t/backend/logs` + journald |

В конце установщик печатает логин и пароль администратора — **сохраните их сразу**, они же
лежат в `oracle-t/.env`.

---

## 4. Установка, вариант Б — Docker

Когда не хочется ставить пакеты в хост:

```bash
git clone https://github.com/eugeneque/oracle-tender.git oracle-tender
cd oracle-tender
sudo ./install.sh --mode docker
```

Поднимаются три контейнера: `db`, `backend` (образ с tesseract, `bsdtar` и Playwright,
миграции накатываются при старте) и `frontend` (nginx со сборкой Vite). Наружу открыт один
порт — веб-интерфейс; база и API в хостовую сеть не выставлены.

Данные живут в томе `db_data` (база) и в каталоге `oracle-t/storage` (документы).

> **Ключ шифрования в Docker хранится в `.env`** (`CREDENTIALS_ENCRYPTION_KEY`), а не в файле
> `.credentials_key`: файл лежал бы внутри образа и исчезал при каждой пересборке, а вместе с
> ним стали бы нечитаемыми все сохранённые пароли от личных кабинетов ЭТП. Установщик создаёт
> ключ сам. **Потеря этого ключа = потеря всех паролей площадок**, восстановить их нельзя,
> только ввести заново.

---

## 5. HTTPS и домен

Установщик настраивает только HTTP на 80 порту. Для боевой эксплуатации выпустите сертификат:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d tenders.example.ru
```

Certbot сам поправит созданный сайт `oracle-t.conf`. После этого проверьте, что блок
`location /api/` с увеличенными таймаутами и `client_max_body_size 256m` остался на месте —
без них загрузка крупной документации обрывается.

Если перед сервером стоит внешний балансировщик, пробрасывайте `X-Forwarded-Proto`, а порт 80
на самой машине закрывайте.

---

## 6. Первичная настройка (через интерфейс)

Часть параметров сознательно не лежит в `.env` — их задаёт администратор в разделе
«Настройки», и правки не откатываются при перезапуске:

1. **Настройки → Интеграции** — ключ и Folder ID Yandex AI Studio. Без них не работают анализ
   документации и AI-отбор тендеров.
2. **Настройки → Уведомления** — SMTP-ящик и получатели. Нужен именно **SMTP**, не IMAP.
   Проверить соединение, не отправляя письмо:
   `cd oracle-t/backend && ./.venv/bin/python -m app.cli check-mail`
3. **Настройки → Площадки** — логины и пароли личных кабинетов ЭТП. Шифруются ключом
   `.credentials_key` / `CREDENTIALS_ENCRYPTION_KEY`.
4. **Настройки → Профиль компании и релевантность** — от этого зависит, какие тендеры вообще
   попадут в выборку.

Сменить пароль администратора:

```bash
cd oracle-t/backend
./.venv/bin/python -m app.cli reset-admin --password 'НовыйПароль'
```

---

## 7. Эксплуатация

**Нативная установка**

```bash
systemctl status oracle-t-backend
systemctl restart oracle-t-backend
journalctl -u oracle-t-backend -f
tail -f oracle-t/backend/logs/*.log
curl -s localhost:8000/health          # {"status":"ok","database":"ok"}
```

**Docker**

```bash
cd oracle-t
docker compose ps
docker compose logs -f backend
docker compose restart backend
curl -s localhost/api/health
```

**Диагностика прикладного уровня** (в нативной установке — из `oracle-t/backend`,
в Docker — через `docker compose exec backend python -m app.cli ...`):

```bash
./.venv/bin/python -m app.cli list-sources      # площадки и статус адаптеров
./.venv/bin/python -m app.cli ping-sources      # доступность прямо сейчас
./.venv/bin/python -m app.cli poll-source <ключ>  # разовый опрос вне расписания
./.venv/bin/python -m app.cli check-mail        # проверка почтового ящика
```

---

## 8. Обновление

Установщик идемпотентен — он же и апдейтер: переиспользует созданные venv, базу и `.env`,
доставляет новые зависимости, накатывает миграции и пересобирает фронтенд.

```bash
cd oracle-tender
git pull
sudo ./install.sh --mode server -y     # или --mode docker -y
```

Перед обновлением снимите дамп базы (см. ниже). Миграции применяются автоматически;
отката вниз не предусмотрено — откат делается восстановлением дампа.

Служба перезапускается в процессе, короткий простой (десятки секунд) — штатный.

---

## 9. Резервное копирование

Нужно бэкапить **три вещи**, потеря любой из них невосполнима:

| Что | Нативно | Docker |
|---|---|---|
| База | `pg_dump -U oraclet oraclet` | `docker compose exec -T db pg_dump -U oraclet oraclet` |
| Документы тендеров | `oracle-t/backend/storage` | `oracle-t/storage` |
| Секреты | `oracle-t/.env` + `oracle-t/.credentials_key` | `oracle-t/.env` |

Пример ежедневного задания:

```bash
#!/usr/bin/env bash
set -euo pipefail
D=/var/backups/oracle-t/$(date +%F)
mkdir -p "$D"
sudo -u postgres pg_dump -Fc oraclet > "$D/oraclet.dump"
tar czf "$D/storage.tgz" -C /opt/oracle-tender/oracle-t/backend storage
cp /opt/oracle-tender/oracle-t/.env /opt/oracle-tender/oracle-t/.credentials_key "$D/"
chmod -R 600 "$D"
find /var/backups/oracle-t -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

**Восстановление:** развернуть систему установщиком на чистой машине, остановить службу,
вернуть на место `.env` и `.credentials_key` (иначе пароли площадок не расшифруются),
залить дамп `pg_restore -d oraclet oraclet.dump`, распаковать `storage`, запустить службу.

---

## 10. Сетевые доступы

**Входящие:** только 80/443 к nginx. Порты 8000 (API) и 5432 (Postgres) наружу выставлять не
нужно и не следует — у API нет собственной защиты на уровне порта.

**Исходящие:** системе нужен доступ в интернет, иначе сбор тендеров молча не работает.

| Куда | Зачем |
|---|---|
| `zakupki.gov.ru`, `etp.zakazrf.ru`, `roseltorg.ru`, `sberbank-ast.ru`, `fabrikant.ru`, `tektorg.ru`, `gz.lot-online.ru`, `etp.gpb.ru`, `web.etprf.ru` | площадки: сбор тендеров и документации |
| `fgis.gost.ru`, `all-pribors.ru` | Госреестр средств измерений |
| `egrul.nalog.ru`, `rusprofile.ru` | реквизиты заказчиков и конкурентов |
| сайты производителей (`mirtekgroup.com`, `energomera.ru`, `incotexcom.ru` и др.) | наполнение справочника продукции |
| Yandex Cloud (443, HTTPS/gRPC) | Yandex AI Studio: анализ документации и отбор |
| SMTP-сервер организации | уведомления |
| Bitrix24 организации | выгрузка карточек, если интеграция включена |

Если исходящий трафик идёт через прокси, задайте `HTTPS_PROXY`/`NO_PROXY` в юните службы
(`Environment=`) либо в `docker-compose.yml`.

---

## 11. Типовые проблемы

| Симптом | Причина и что делать |
|---|---|
| Установщик остановился на «Python 3.11+ не найден» | ОС старее поддерживаемых. Поставьте `python3.11` вручную или ставьте в режиме `--mode docker` |
| Установщик предупредил «tesseract не установлен» | Пакет не встал (часто на RHEL — нет EPEL). Система работает, но у сканированных PDF не будет извлечённого текста |
| Установщик предупредил «bsdtar не установлен» | Документация в RAR-архивах останется без разбора. Поставьте `libarchive-tools` / `bsdtar` |
| Страница открывается, но всё пусто и в консоли 502 | backend не поднялся: `journalctl -u oracle-t-backend -n 50` |
| 413 при загрузке документа | Правился конфиг nginx и потерялся `client_max_body_size 256m` |
| Тендеры не появляются | Проверьте `ping-sources`, учётные данные площадок в «Настройках» и исходящий доступ. Помните: сбор идёт в 09:00 и 14:00 по Москве |
| Анализ документов не запускается | Не задан ключ Yandex AI Studio в «Настройки → Интеграции» |
| После переезда пароли площадок «неверные» | Не перенесён `.credentials_key` (или `CREDENTIALS_ENCRYPTION_KEY`). Пароли придётся ввести заново |
| Место на диске кончается | Растёт `backend/storage`. Смотрите `du -sh`, старые документы удаляются вместе с тендерами |

Журнал самой установки — `install.log` в корне репозитория, в нём полный вывод всех шагов.
