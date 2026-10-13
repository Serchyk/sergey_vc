# Мини-синхронизатор заявок

Локальный веб-сервис: забирает сырые заявки с `https://test.ooobs.ru/api/orders`,
нормализует телефоны, дедуплицирует по номеру и отдаёт результат по HTTP.

## Запуск

```powershell
.\run.ps1
```

или вручную:

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Результат: <http://127.0.0.1:8000/> — веб-интерфейс (кнопки слева, вывод справа),
API отдаёт JSON по <http://127.0.0.1:8000/result>.

## Интерфейс

Тёмная тема, слева панель управления, справа область вывода:

* **Синхронизировать** — забрать заявки с источника (`POST /sync`), обновить всё;
* **Показать результат** — перечитать результат из базы (`GET /result`);
* **Диагностика** — состояние источника, число запусков (`GET /health`);
* **Отклонённые** — какие заявки отброшены и почему, со счётчиком на кнопке
  (`GET /admin/rejected`);
* **Вид**: «Таблица клиентов» (поиск по имени/телефону/почте) или «Сырой JSON»
  с копированием в буфер;
* **Автосинхронизация** — переключатель, обновление раз в 60 секунд.

Вид панели сохраняется в адресе: `/#table`, `/#json`, `/#rejected`.

Правая часть: карточки статистики (получено / уникальных / дублей / отброшено),
таблица клиентов с суммами и JSON-ответ сервиса.

Файлы интерфейса: `app/static/index.html`, `app/static/styles.css`, `app/static/app.js`
(чистый JS, без сборки и внешних CDN).

## Ответ `/result`

```json
{
  "clients": [
    { "name": "Олег Кузнецов",   "phone": "+79031112233", "email": "",                  "amount": 7500 },
    { "name": "Мария С.",        "phone": "+79160000001", "email": "maria@example.com", "amount": 200 },
    { "name": "Дмитрий Лебедев", "phone": "+79164455667", "email": "dmitry@example.com","amount": 12000 },
    { "name": "Анна В.",         "phone": "+79219998877", "email": "",                  "amount": 1500 },
    { "name": "Елена Морозова",  "phone": "+79261234567", "email": "elena@example.com", "amount": 9000 },
    { "name": "Сергей Зайцев",   "phone": "+79991110000", "email": "sergey@example.com","amount": 450 },
    { "name": "Иван П.",         "phone": "+79991234567", "email": "",                  "amount": 5000 }
  ],
  "stats": {
    "received": 17,
    "unique": 7,
    "duplicates_merged": 5,
    "invalid_dropped": 5
  }
}
```

## Эндпоинты

| Метод | Путь | Назначение |
|-------|------|------------|
| GET  | `/result`, `/api/result`, `/clients` | требуемый JSON `{clients, stats}` |
| POST | `/sync`, `/api/sync` | синхронизироваться сейчас (идемпотентно), вернуть тот же JSON |
| GET  | `/health` | статус, число запусков, время последней синхронизации, sha256 источника |
| GET  | `/admin/rejected` | какие заявки отброшены и почему (отладка) |
| GET  | `/docs` | OpenAPI |

## Логика обработки

1. **Нормализация телефона** (`sync_orders/core.py:normalize_phone`)
   — оставляются только цифры; ведущая `8` заменяется на `7`;
   результат валиден, только если получилось ровно **11 цифр** с кодом `7`.
   Всё остальное (`123`, `+7 999`, `не номер`, пустая строка, 10-значный `9005551212`,
   номера на 12+ цифр, зарубежные коды) отбрасывается как невалидное.
2. **Дедупликация по телефону** (`sync_orders/core.py:group_winners`)
   — в группе остаётся запись с максимальным `amount`; при равенстве —
   более поздняя по `created_at`; при полном равенстве — бóльший `id`
   (чтобы порядок входных данных не влиял на результат).
3. **Статистика**: `received` = все заявки, `invalid_dropped` = отброшенные,
   `duplicates_merged` = валидные минус уникальные, `unique` = размер `clients`.
   Всегда сходится: `received = unique + duplicates_merged + invalid_dropped`.

## Идемпотентность

Результат — чистая функция от содержимого `/api/orders`. Синхронизация пишет
снимок целиком в одну транзакцию SQLite (`clients.phone` — PRIMARY KEY,
таблица очищается и перезаполняется), поэтому повторный запуск:

* не создаёт новых строк (проверено тестом: `COUNT(clients)` не растёт);
* возвращает побайтово тот же JSON;
* инкрементит счётчик `runs` в `/health` и пишет запись в `sync_runs`.

Проверка:

```bash
curl -s http://127.0.0.1:8000/result > a.json
curl -s -X POST http://127.0.0.1:8000/sync > b.json
curl -s http://127.0.0.1:8000/result > c.json
diff a.json b.json && diff b.json c.json && echo IDEMPOTENT
```

Тесты:

```bash
python -m unittest discover -s tests -v
```

## Структура

```
app/main.py            FastAPI: /result, /sync, /health, /admin/rejected
app/static/index.html  UI: кнопки слева, вывод справа
app/static/styles.css  тёмная тема, адаптив
app/static/app.js      загрузка данных, поиск, переключение вида, тосты
sync_orders/core.py    нормализация, дедупликация, подсчёт статистики
sync_orders/storage.py SQLite: атомарная замена снимка, история запусков
sync_orders/fetcher.py HTTP-клиент к /api/orders с ретраями
sync_orders/synchronizer.py  связка fetch → transform → persist, защита от гонок
tests/test_sync.py     юнит-тесты + тесты идемпотентности
tests/raw_orders.json  снимок реального ответа источника
```

## Переменные окружения

| Переменная | По умолчанию | Смысл |
|------------|--------------|-------|
| `ORDERS_SOURCE_URL` | `https://test.ooobs.ru/api/orders` | источник заявок |
| `ORDERS_DB` | `data/sync.db` | файл SQLite |
| `ORDERS_REFRESH_SECONDS` | `0` (выкл) | фоновая периодическая синхронизация, секунды |