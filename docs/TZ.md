# Техническое задание №1 — Aiogram Extensions (`aiogram-extensions`)

**Назначение:** модульная Python-библиотека расширений для aiogram 3.  
**Дистрибутив:** `aiogram-extensions` · **Импорт:** `aiogram_extensions` · **Лицензия:** MIT  
**Статус:** согласованное техническое задание на MVP и дальнейшее развитие.  
**Актуальность технических источников:** 8 ноября 2026 года.

## 1. Назначение и принципы

**Aiogram Extensions — A collection of powerful extensions and utilities for aiogram.**

Библиотека предоставляет готовые компоненты, сокращающие объём повторяющегося кода при разработке Telegram-ботов: входящий и исходящий rate limiting, очередь отправки сообщений, пагинацию, события наблюдаемости и практические утилиты.

Это один Python-пакет с независимыми функциональными модулями, а не новый фреймворк и не случайная коллекция функций.

Основные принципы:
1. Использовать публичные механизмы aiogram 3 и зрелые сторонние библиотеки.
2. Не дублировать функциональность Python, aiogram или готовых библиотек без дополнительной ценности.
3. Не навязывать Pydantic и способ загрузки конфигурации.
4. Не менять глобальное логирование приложения и не создавать скрытые подключения.
5. Выполнять I/O асинхронно, не блокировать event loop.
6. Документировать реальные гарантии и ограничения; не обещать exactly-once или «защиту от бана».

## 2. Состав MVP

| Модуль | Назначение |
|---|---|
| `rate_limit` | Incoming и outgoing rate limiting |
| `messaging` | Очередь отправки Telegram-сообщений |
| `pagination` | Универсальная и Telegram UI-пагинация |
| `integrations` | Hooks и callbacks для observability |
| `utilities` | Практические Telegram-specific и bot-development helpers |

Дополнительные будущие направления (не входят в MVP): caching, дополнительные callback utilities, error handling, Telegram file helpers, дополнительные retry helpers. Новый модуль добавляется только при наличии самостоятельной пользы.

## 3. Совместимость и версионирование

### Python
Официальная поддержка первого релиза: Python 3.11–3.14. Python 3.15 добавить после проверки совместимости aiogram и зависимостей. Диапазон отразить в `pyproject.toml`, classifiers, документации и CI.

### Aiogram
Поддерживать aiogram 3: актуальную стабильную minor-линию и две предыдущие стабильные minor-линии, если они совместимы с поддерживаемыми Python и зависимостями. Точные границы зависимости фиксируются по успешно пройденной CI-матрице. Не объявлять поддержку непроверенных версий.

При выходе новой minor-версии aiogram: изучить changelog/API, добавить версию в CI, проверить совместимость и обновить ограничения и документацию.

### SemVer
До `1.0.0` — активная разработка. После стабилизации:
- PATCH — исправления без нарушения совместимости;
- MINOR — обратно совместимые возможности;
- MAJOR — несовместимые изменения публичного API.

Каждый релиз сопровождается changelog.

## 4. Структура пакета и публичный API

Рекомендуемая структура:

```text
src/aiogram_extensions/
├── __init__.py
├── rate_limit/
│   ├── incoming/
│   ├── outgoing/
│   └── backends/
├── messaging/
│   ├── queue.py
│   ├── messages/
│   ├── backends/
│   ├── workers/
│   ├── retry.py
│   └── dead_letter.py
├── pagination/
│   ├── core/
│   └── aiogram/
├── integrations/
│   └── events.py
├── utilities/
└── _internal/
```

Это целевая схема, а не требование создавать пустые каталоги. Не складывать весь код в один `utils.py`.

Импортировать публичный API из функциональных модулей:

```python
from aiogram_extensions.messaging import MessageQueue
from aiogram_extensions.rate_limit import RateLimiter
from aiogram_extensions.pagination import Paginator
```

Не экспортировать все объекты из корневого `aiogram_extensions`. Внутреннюю реализацию отделить от публичного API. Для каждого публичного символа документировать назначение, параметры, ошибки и гарантии.

Модуль, которому не требуются типы или API aiogram, не должен импортировать aiogram без необходимости. При этом `aiogram` остаётся обязательной зависимостью дистрибутива — это принятое продуктовое решение.

## 5. Зависимости и установка

Обязательная runtime-зависимость:
- `aiogram`.

Redis и Taskiq не устанавливаются в базовой установке. Целевой UX:

```bash
uv add aiogram-extensions
uv add "aiogram-extensions[redis]"
uv add "aiogram-extensions[messaging-redis]"
```

Смысл extras:
- базовая установка — функциональность без Redis;
- `redis` — Redis-backed компоненты на `redis-py`;
- `messaging-redis` — Taskiq и Taskiq Redis для Redis Queue;
- Sentry, Prometheus и OpenTelemetry — не входят в MVP.

Точные названия extras закрепить в `pyproject.toml`. При отсутствии нужного extra пользователь получает понятную ошибку с названием extra и командой установки. Базовые модули должны импортироваться без Redis. Не подменять Redis backend на Memory молча.

`uv` можно использовать для разработки библиотеки, но пользователи не должны быть обязаны устанавливать uv для использования установленного Python-пакета.

## 6. Конфигурация, логирование и Redis connection manager

### Конфигурация
Использовать стандартные Python `dataclasses`:
- отдельная типизированная конфигурация каждого модуля;
- разумные defaults;
- валидация некорректных параметров;
- понятные исключения;
- отсутствие обязательной зависимости от Pydantic.

Загрузка `.env`, env vars или Pydantic Settings остаётся ответственностью приложения. Библиотека принимает готовые значения. Не создавать обязательный глобальный `ExtensionsSettings`.

### Логирование
Использовать стандартный `logging`:
- собственный logger namespace у каждого модуля;
- не вызывать `logging.basicConfig`;
- не добавлять глобальные handlers;
- не менять уровень логирования приложения;
- не требовать Loguru/Structlog;
- не логировать bot token, Redis credentials и чувствительное содержимое сообщений.

### Redis Connection Manager
Предусмотреть общий компонент для Redis-backed модулей:
- использовать `redis.asyncio`;
- принимать клиент, созданный пользователем;
- при необходимости создавать клиент по конфигурации;
- отслеживать владельца клиента и закрывать только созданные библиотекой ресурсы;
- поддерживать async lifecycle;
- переиспользовать подключения между собственными модулями;
- иметь настраиваемый key prefix и namespace для модулей.

**Ограничение:** не передавать один физический Redis client в Taskiq broker насильно, если публичный API broker этого не поддерживает. Taskiq может управлять собственным pool. Не обещать общий физический pool между разными библиотеками без подтверждения.

## 7. Модуль `rate_limit`

### Назначение и backends
Две отдельные подсистемы:
1. **Incoming Rate Limiter** — лимит входящей активности пользователей.
2. **Outgoing Rate Limiter** — лимит исходящих вызовов Telegram Bot API.

Для обеих — Memory и Redis backends.

### Алгоритмы MVP
- Fixed Window;
- Sliding Window.

Token Bucket, Leaky Bucket, adaptive throttling, weighted limits и динамическая корректировка — вне MVP.

### Incoming
Основной ключ — пара `user_id + chat_id`.

Требования:
- независимые лимиты по паре пользователь/чат;
- настройки количества событий и временного интервала;
- исключения/allowlist для пользователей, чатов или сценариев;
- структурированный результат: разрешено/отклонено, время ожидания, сработавшее правило;
- интеграция с middleware aiogram;
- возможность прямого вызова публичного API без middleware.

### Outgoing
Поддерживаемые scopes:
- bot-level;
- chat-level;
- group-level;
- method-level;
- пользовательские правила и комбинации.

Требования:
- одновременная проверка всех применимых правил;
- настраиваемые интервалы и количества;
- Memory и Redis;
- интеграция на уровне исходящих запросов через публичный механизм client/session middleware aiogram;
- корректный учёт `TelegramRetryAfter`;
- возможность прямого использования limiter API.

До реализации определить точную семантику `group-level`: что такое группа, как получается group ID и чем правило отличается от chat-level.

Не выдавать defaults за гарантированные лимиты Telegram. Значения по умолчанию должны быть документированными стартовыми настройками, доступными для переопределения.

### Поведение и атомарность
Настраиваемое поведение при превышении: отказ, ожидание/повтор в подходящем контексте, структурированный результат и callback.

Redis check/update должен быть атомарным (например, Lua или другой проверенный Redis-механизм). Зафиксировать семантику комбинированных правил: какие счётчики изменяются при отклонении одним из нескольких правил. Не блокировать event loop синхронным sleep/I/O.

### Приёмка
Проверить:
- одинаковый публичный контракт Memory/Redis;
- конкурентные запросы;
- границы окон и TTL;
- комбинации правил;
- `TelegramRetryAfter`;
- поведение при недоступности Redis;
- отсутствие молчаливого обхода лимита.

## 8. Модуль `messaging`

### Назначение
Единый API очереди отправки Telegram-сообщений с двумя backends:
1. **Memory Queue** — `asyncio.Queue` и локальные workers.
2. **Redis Queue** — Taskiq + Taskiq Redis `RedisStreamBroker` на Redis Streams.

Универсальная очередь произвольных задач может быть добавлена в будущем, но не входит в MVP.

### Выбранный стек и технический риск
- Taskiq — task/worker engine.
- Taskiq Redis `RedisStreamBroker` — Redis backend.
- `redis-py` / `redis.asyncio` — Redis client для остальных модулей.
- Не переписывать workers и Redis transport Taskiq.

На 8 ноября 2026 года опубликованный Taskiq 0.13.0 по метаданным PyPI всё ещё имеет статус **Alpha**. Taskiq Redis 1.2.4 опубликован 28 сентября 2026 года. Официальная страница Taskiq Redis указывает, что Stream broker поддерживает acknowledgements; Pub/Sub и ListQueue не поддерживают acknowledgements и могут потерять задачу при внезапном завершении worker. Но acknowledgements сами по себе не доказывают готовую DLQ или все recovery guarantees. Перед разработкой провести technical spike на закреплённых версиях. См. источники в разделе 18.

В частности, проверить аварийное восстановление при hard kill, `XAUTOCLAIM`, `idle_timeout`, долгие задания, повторную доставку и поведение при сетевых ошибках. Изучить release notes и открытые issues taskiq-redis. Если требования MVP нельзя обеспечить выбранной версией Taskiq, это блокирующий вопрос для согласования, а не повод заявить неподтверждённую гарантию.

### Два уровня API

**Simple API** — готовый `MessageQueue`, минимальная конфигурация.

Пример для Memory:

```python
queue = MessageQueue(config=queue_config, backend="memory")
await queue.start()
await queue.send_message(chat_id=123456, text="Hello!")
await queue.stop()
```

Для Redis producer и workers работают в разных процессах. Живой объект `Bot` не сериализуется и не передаётся через Redis. Worker создаёт Bot на основании настроек и секретов своего процесса. Точную сигнатуру конструктора закрепить после прототипа. Не требовать `bot=bot` в Redis producer, если объект там не нужен.

**Advanced API** позволяет:
- передавать собственный Taskiq broker;
- настраивать workers и сериализацию;
- передавать собственную Bot factory;
- переопределять retry policy;
- подключать hooks/callbacks;
- использовать документированные расширения Taskiq.

Не делать внутренние детали Taskiq частью стабильного публичного API без необходимости.

### Жизненный цикл workers

**Memory:**
- `queue.start()` запускает локальных workers;
- `queue.stop()` прекращает приём и выполняет graceful shutdown;
- несколько workers;
- FIFO в пределах одной очереди при заявленной семантике;
- configurable maxsize и поведение при переполнении.

**Redis:**
- producer живёт в процессе приложения;
- workers запускаются отдельно через Taskiq CLI;
- библиотека предоставляет broker configuration, worker entrypoint и команду запуска;
- Redis workers не запускаются автоматически внутри процесса бота;
- Bot создаётся и закрывается внутри worker process.

### Методы отправки
MVP должен охватывать:
- `send_message`;
- `send_photo`;
- `send_document`;
- `send_video`;
- `send_audio`;
- `send_media_group`;
- отправку location;
- отправку contact;
- другие стандартные send-методы, если для них обеспечен корректный сериализуемый контракт.

Редактирование и удаление сообщений в MVP не входят.

### Файлы и сериализация
Для Redis Queue поддерживать:
- `file_id`;
- URL;
- локальный путь.

Локальный путь должен быть доступен worker через shared filesystem/volume или иной настроенный механизм.

Для Redis в MVP не поддерживать `BufferedInputFile`, произвольные файловые потоки, живые объекты `Bot`, `Message`, `Update`, `FSMContext` и другие несериализуемые runtime objects.

Memory backend может принимать локальные объекты, если они не пересекают границу процесса и это документировано.

### Retry и ошибки
Политика настраиваемая:
- временные ошибки повторяются;
- `TelegramRetryAfter` учитывает `retry_after`;
- постоянные ошибки по умолчанию не повторяются;
- неизвестные ошибки обрабатываются согласно настройке;
- число попыток ограничено;
- задержки/backoff настраиваются;
- пользователь может передать собственную retry policy.

Не повторять безусловно все исключения.

### Delivery semantics и idempotency
Для Redis Queue целевая модель — **at-least-once**, если фактический broker/recovery path подтверждает это тестами.

Предусмотреть `task_id` или `idempotency_key`, защиту от повторного enqueue в пределах заданной политики хранения ключей, ограниченные попытки, DLQ и replay.

Не обещать exactly-once. Idempotency key предотвращает повторную постановку в пределах реализованного механизма, но не гарантирует отсутствие дубликата в Telegram: при сетевом timeout Telegram мог принять запрос, хотя worker не получил ответ.

Memory queue:
- теряется при рестарте процесса;
- не распределяется между процессами;
- не имеет durability уровня Redis;
- не должна рекламироваться как эквивалент Redis backend по гарантиям.

### Dead Letter Queue (DLQ)
В MVP требуется:
- перемещение окончательно неуспешных задач в DLQ;
- сохранение причины ошибки и числа попыток;
- API просмотра записей DLQ;
- повторная постановка задачи из DLQ;
- hooks/callbacks для перемещения и replay;
- документированные TTL, срок хранения и очистка.

Acknowledgement в Redis Streams не равен DLQ. DLQ реализуется отдельным слоем после проверки pending entries, reclaim/recovery и retry в выбранных версиях Taskiq. Не заявлять автоматическое восстановление, пока интеграционный тест с принудительным завершением worker не пройден.

### Приёмочные сценарии очереди
1. Отправка каждого поддерживаемого типа сообщения.
2. `TelegramRetryAfter`.
3. Временная ошибка и ограниченные повторы.
4. Постоянная ошибка без бесконечных повторов.
5. Превышение числа попыток и запись в DLQ.
6. Просмотр DLQ и replay.
7. Принудительное завершение worker во время обработки Redis task.
8. Проверка reclaim/recovery pending task.
9. Отсутствие потери задачи в поддерживаемом аварийном сценарии.
10. Повторная постановка с одинаковым idempotency key.
11. Сетевой timeout и документирование риска дубля.
12. Graceful shutdown Memory workers.
13. Раздельный запуск Redis producer и workers.
14. Корректное создание/закрытие Bot session в worker.

## 9. Модуль `pagination`

Два уровня:

### `pagination.core`
- offset/limit;
- cursor-based;
- Python collections;
- адаптеры к источникам данных;
- типизированный результат: текущая страница, размер, next/previous и метаданные;
- пустые результаты и граничные случаи.

Core не должен требовать Telegram Message, CallbackQuery или markup objects.

### `pagination.aiogram`
- inline keyboard navigation;
- next/previous;
- отображение текущей страницы;
- callback query helpers/handlers;
- настраиваемый текст и layout;
- встраивание в пользовательский handler.

Callback data должна укладываться в ограничения Telegram. Не помещать в неё длинные курсоры или сериализованные объекты. Для больших данных использовать короткий идентификатор и соответствующее хранилище.

### Приёмка
Проверить первую/последнюю страницы, пустой набор, страницу за диапазоном, cursor semantics и соответствие callback data ограничениям Telegram.

## 10. Модуль `integrations` и наблюдаемость

### MVP
Реализовать hooks/callbacks, не готовые monitoring exporters.

События:
- task enqueued/started/completed/failed;
- retry scheduled;
- task moved to DLQ;
- DLQ replayed;
- incoming/outgoing limit exceeded;
- backend connection error.

Контракт событий типизированный и документированный. Ошибка пользовательского callback не должна незаметно ломать основную операцию. Зафиксировать политику обработки исключений callback.

### Вне MVP
- Prometheus exporter;
- OpenTelemetry tracing;
- Sentry SDK;
- собственная logging framework.

Архитектура должна позволять добавить эти интеграции позднее.

## 11. Модуль `utilities`

Добавлять только практически полезные helpers, не дублирующие стандартную библиотеку или зрелые пакеты. Потенциальные задачи: Telegram text/entities formatting, работа с датами и временными зонами в bot-сценариях, deep links, нормализация Telegram-specific данных, небольшие async helpers.

Каждый utility должен иметь ясное назначение, типизированный публичный API, тесты, пример и описанные ограничения. Не добавлять функции «на всякий случай».

## 12. Тестирование

### Обязательный минимум MVP
- unit-тесты основных модулей;
- интеграционные тесты Redis;
- тесты очереди, retry, DLQ и worker recovery;
- матрица Python/aiogram;
- mock Telegram Bot API там, где реальный запрос не нужен;
- проверки отсутствующих extras и невалидной конфигурации.

Redis tests запускаются на изолированном Redis service/container в CI. Unit tests не требуют настоящего Telegram token.

### Следующий этап
Добавить нагрузочные и конкурентные тесты, fault injection, тестирование отказов/восстановления Redis, performance и regression testing.

## 13. Документация

Полноценная документация должна содержать:
1. Installation и Quick Start.
2. Общую архитектуру и принципы.
3. Руководство по каждому модулю.
4. API Reference.
5. Memory vs Redis.
6. Запуск и lifecycle queue workers.
7. Retry, idempotency, DLQ и delivery semantics.
8. Rate limiting rules и примеры.
9. Pagination core и aiogram adapter.
10. Hooks/callbacks.
11. Примеры интеграции с aiogram.
12. FAQ/troubleshooting.
13. Migration notes для несовместимых релизов.

## 14. CI, качество и релизы

GitHub Actions должны выполнять:
- Ruff lint;
- Ruff format check;
- Pyright;
- pytest;
- Python/aiogram compatibility matrix;
- Redis integration tests;
- сборку wheel и sdist;
- проверку документации.

Релиз запрещён, если обязательные проверки не пройдены. Лицензия MIT, SemVer, changelog. Автоматическая публикация в PyPI может появиться после MVP.

## 15. Безопасность и эксплуатация

- Не логировать токены, пароли и чувствительные payload.
- Ограничивать размер сериализуемых задач.
- Настраивать Redis key prefix и TTL.
- Не допускать бесконечных retry loops.
- Не скрывать ошибки backend.
- Не заявлять durability для Memory.
- Не открывать произвольные локальные файлы вне явного file API.
- Не включать секреты в task labels, `task_id` или логи.
- Корректно закрывать клиентские соединения и Bot sessions.

## 16. Критерии готовности MVP

MVP готов, если:
1. Пакет устанавливается на Python 3.11–3.14.
2. Базовый импорт работает без Redis/Taskiq/Sentry.
3. Каждый модуль имеет документированный публичный API.
4. Incoming/outgoing rate limiter работает на Memory и Redis.
5. Fixed/Sliding Window покрыты тестами.
6. Redis limiter проверен на конкурентность.
7. Memory Queue поддерживает несколько workers и graceful shutdown.
8. Redis Queue использует `RedisStreamBroker` и проходит аварийные интеграционные тесты.
9. DLQ/replay реализованы и проверены; иначе релиз блокируется до согласования.
10. Pagination core не зависит от Telegram UI.
11. Aiogram pagination соблюдает ограничения callback data.
12. Hooks/callbacks типизированы и документированы.
13. CI проходит на заявленной матрице.
14. Есть полная документация и примеры.
15. MIT License и changelog присутствуют.

## 17. Обязательные технические проверки до основной реализации

Разработчик должен провести spike/prototype и зафиксировать результат по пунктам:
1. Закреплённые версии Taskiq/taskiq-redis, совместимость и API.
2. Recovery после hard kill Redis worker.
3. `XAUTOCLAIM`, `idle_timeout` и долгие задачи.
4. Совместимость Taskiq retry с Telegram error classification.
5. DLQ/replay поверх broker без конфликта с acknowledgements.
6. At-least-once и риск дублей.
7. Семантика `group-level` outgoing rate limit.
8. Семантика комбинированных rate limit rules.
9. Lifecycle Redis clients и Taskiq broker pool.
10. Bot factory и сериализация всех поддерживаемых типов сообщений.

## 18. Технические источники

- [aiogram documentation](https://docs.aiogram.dev/en/latest/)
- [aiogram client session middleware](https://docs.aiogram.dev/en/latest/api/session/middleware.html)
- [aiogram FSM storages](https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/storages.html)
- [Taskiq documentation](https://taskiq-python.github.io/)
- [Taskiq on PyPI](https://pypi.org/project/taskiq/)
- [Taskiq Redis on PyPI](https://pypi.org/project/taskiq-redis/)
- [Taskiq Redis README](https://github.com/taskiq-python/taskiq-redis)
- [Taskiq Redis releases](https://github.com/taskiq-python/taskiq-redis/releases)
- [Taskiq Redis issue #131](https://github.com/taskiq-python/taskiq-redis/issues/131)
- [redis-py documentation](https://redis.readthedocs.io/)
- [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Python Packaging User Guide](https://packaging.python.org/)
- [uv documentation](https://docs.astral.sh/uv/)

**Конец ТЗ №1.**
