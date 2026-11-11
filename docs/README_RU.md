# Aiogram Extensions

[English](README.md)

Модульные расширения и утилиты для aiogram 3. В планах — ограничение частоты
запросов, очереди сообщений, пагинация и инструменты наблюдаемости.

**В разработке:** доступны ядро пагинации и aiogram-навигация, конфигурации с
валидацией, типы результатов, callbacks и Redis connection manager. Алгоритмы
limiter, middleware и workers очереди пока не реализованы.

## Начало работы

Нужны Python 3.11–3.14 и [uv](https://docs.astral.sh/uv/). Поддерживаемый диапазон
aiogram — `>=3.29.1,<3.32`. Базовая установка не включает Redis и Taskiq.
Необязательный extra `redis` закрепляет redis-py 8.1.0; `messaging-redis` будет
добавлен при упаковке рабочего транспорта. Пользователи могут устанавливать пакет
через `pip install aiogram-extensions` или `pip install "aiogram-extensions[redis]"`;
uv нужен только для приведённых ниже команд разработки.

Из корня репозитория установите пакет и зависимости для разработки:

```console
uv sync --locked
```

Зависимости разработки включают redis-py для проверки необязательного API.

## Примеры

Запускайте самостоятельные скрипты из корня репозитория в окружении проекта
с зафиксированными зависимостями:

```console
uv run --locked examples/package_info.py
uv run --locked examples/bot_lifecycle.py
uv run --locked examples/contracts.py
uv run --locked examples/redis_lifecycle.py
uv run --locked examples/pagination_core.py
uv run --locked examples/pagination_bot.py
```

- [Информация о пакете](../examples/package_info.py): импорт пакета, проверка его
  лицензии и маркера типизации.
- [Жизненный цикл сессии](../examples/bot_lifecycle.py): открытие и закрытие HTTP-сессии
  aiogram с искусственным токеном, без запросов к Telegram API. Этот пример показывает
  работу зависимости aiogram, а не API расширений.

- [Контракты и callbacks](../examples/contracts.py): конфигурации и явно
  отправленное событие, без запуска limiter или очереди.
- [Redis в обработчике](../examples/redis_lifecycle.py): локальный апдейт `/redis`,
  получение клиента через DI aiogram и построение ключа с префиксом. Нужен redis-py.

- [Ядро пагинации](../examples/pagination_core.py): страницы упорядоченной коллекции,
  асинхронный cursor-адаптер и ограниченные превью обычного текста.
- [Бот с пагинацией](../examples/pagination_bot.py): просмотр страниц без сети и
  готовый роутер `/catalog` с кнопками и форматированным заголовком.
  Для polling задайте переменную окружения `BOT_TOKEN` и выполните
  `uv run --locked examples/pagination_bot.py --live`.
  Режим `--live` требует настоящего токена бота и доступа к Telegram.

Все шесть примеров в режиме по умолчанию работают без учётных данных, сети и внешних сервисов.
Примеры limiter и очереди появятся вместе с реализациями.
Примеры проходят линтер, проверку типов и
[тестирование в отдельных процессах](../tests/integration/test_examples.py).

## Пагинация и utilities

Импорт ядра (`aiogram_extensions.pagination` или `.pagination.core`) не загружает
aiogram и Redis. Telegram UI импортируется отдельно из `.pagination.aiogram`.

### Текстовые списки в aiogram

```python
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from aiogram_extensions.pagination import CursorPage, Page, Paginator
from aiogram_extensions.pagination.aiogram import NavigationConfig, TextPagination

router = Router()


def render(page: Page[str] | CursorPage[str]) -> str:
    return "\n".join(page.items) or "Каталог пуст."


catalog = TextPagination(
    Paginator(["Apple", "Banana", "Cherry"], page_size=2),
    render=render,
    config=NavigationConfig(prefix="catalog", current_text="{page}/{pages}"),
)
router.include_router(catalog.router)


@router.message(Command("catalog"))
async def show_catalog(message: Message) -> None:
    if message.from_user is not None:
        await catalog.answer(message)
```

Подключите `router` к диспетчеру. `answer(message)` возвращает отправленный
`Message` с первой страницей и кнопками. Подключённый роутер отвечает на нажатия,
обновляет исходное сообщение и очищает ссылки при остановке. Листать страницы
может только пользователь, открывший список. Используйте отдельный prefix для
каждого списка. Если в родительском роутере есть обработчик всех callback,
добавьте ему фильтр, чтобы нажатия доходили до роутера пагинации.

`TextPagination` принимает оба вида paginator. Функция отображения возвращает
строку или `Text` из aiogram для форматирования, как в [примере бота](../examples/pagination_bot.py).
Ссылки хранятся в текущем процессе с описанными ниже TTL и лимитом. Для
нестандартного интерфейса, например подписей к фото или сообщений inline-режима,
используйте `Navigation` напрямую.

### Упорядоченные источники и типизированные страницы

`Paginator(source, *, page_size=10)` принимает Python `Sequence` или асинхронный
`OffsetSource`. Коллекции используют `SequenceSource`: поверхностный снимок tuple
в исходном порядке; последующие вставки/удаления в коллекции на него не влияют.
Сами объекты элементов не копируются. Текст и bytes как коллекции отклоняются.

`await paginator.page(number=1)` использует нумерацию с единицы и offset
`(number - 1) * page_size`. Поля неизменяемого `Page`: tuple `items`, `number`, `limit`
и `total`; свойства — `offset`, `total_pages`, `previous_page`, `next_page`.
Пустой источник имеет страницу 1 из 1 без навигации. Положительные целые размеры
и номера страниц не принимают bool/дроби; выход за конец вызывает `PageOutOfRangeError`.

Для своего offset-источника реализуйте
`async fetch(*, offset: int, limit: int) -> OffsetResult[T]`.
Возвращайте `OffsetResult(items=tuple(...), total=...)` с неотрицательным целым total
и точным запрошенным срезом, включая укороченную последнюю страницу.
`SequenceSource.fetch` также поддерживает прямые произвольные неотрицательные
offset и положительные limit. Каждый запрос paginator читает источник заново.
Адаптер обеспечивает детерминированный порядок с уникальным дополнительным ключом,
согласованные строки и count в рамках чтения, неблокирующий I/O. Вставки/удаления
между чтениями могут сдвигать offset-страницы; общего снимка БД между запросами нет.
Несогласованные результаты вызывают `PaginationError`; валидация входа — `ValueError`.

`CursorPaginator(source, *, page_size=10)` принимает `CursorSource` с методом
`async fetch(*, cursor: str | None, limit: int) -> CursorResult[T]`. `page(cursor=None)`
начинает обход. Возвращайте tuple `items` (не больше `limit`), `next_cursor=None`
и `previous_cursor=None` для отсутствующих направлений либо непустые непрозрачные
строки для существующих. Для возврата на первую страницу нужен явный start-токен:
`None` означает отсутствие ссылки previous/next. Неизменяемый `CursorPage` добавляет
запрошенные `cursor` и `limit`; номер страницы и total не вычисляются. Токены,
указывающие на текущий не-None cursor, отклоняются. Адаптер отвечает за keyset-порядок,
проверку/версионирование токенов и консистентность при изменениях; непригодные токены
отклоняйте через `InvalidCursorError`. Остальные ошибки источника и отмена
распространяются без изменений. См. [исполняемый адаптер](../examples/pagination_core.py).

### Aiogram-навигация и жизненный цикл callback

`Navigation(paginator, *, config=None, clock=time.monotonic)` поддерживает оба paginator.
`keyboard(page, *, scope)` возвращает `InlineKeyboardMarkup`, пропуская недоступные
направления, и заменяет ссылки того же scope. Привязывайте scope к сообщению, а для
личного управления — к авторизованному пользователю; перед разрешением заново
вычисляйте его из query.

`NavigationConfig` — неизменяемая dataclass с именованными аргументами:
`prefix="aep1"` (1–32 ASCII-буквы/цифры/`_.-`), `previous_text="←"`, `next_text="→"`,
`current_text="{page}"`, `layout=(("previous", "current", "next"),)`, `ttl=300` секунд,
`max_entries=1000` (не меньше 3 ссылок),
`stale_text="Navigation expired. Open the list again."`, `invalid_text="Invalid navigation."`.
Подписи должны быть непустыми. Layout содержит каждое действие ровно один раз
в непустых рядах. Текущая подпись поддерживает Python format-поля `page`, `pages`,
`total`, `count`; cursor-страницы подставляют `"•"`, `"?"`, `"?"` в первые три.
Для cursor-пагинации используйте форматы, совместимые со строками.

Кнопки содержат только prefix и случайную ASCII-ссылку длиной 24 символа (не более
57 UTF-8 байт), соблюдая [ограничение Telegram в 1–64 байта](https://core.telegram.org/bots/api#inlinekeyboardbutton).
Курсоры остаются в памяти, включая длинные и Unicode-токены. Ссылки истекают на
границе TTL; просроченные записи удаляются при обращении, самые старые вытесняются
при заполнении. `clear(scope=...)` сбрасывает один scope, `clear()` — все.
Состояние локально для экземпляра `Navigation` и теряется после рестарта.
Направляйте callbacks в тот же экземпляр; общего хранилища между процессами нет.
Используйте отдельный prefix для каждого `Navigation` в одном дереве роутеров.

`await navigation.resolve(data, *, scope)` возвращает страницу или `None` для
индикатора текущей. Некорректные данные вызывают `InvalidNavigationError`;
отсутствующие, просроченные, заменённые ссылки, чужой scope и удалённые страницы/
отклонённые курсоры — `StaleNavigationError`. После чтения источника ссылка
проверяется повторно. Оба исключения наследуют `PaginationError`/`ValueError`.
Telegram-запросы не выполняются. `matches(query)` — фильтр роутера по заданному prefix.
`await handle(query, *, scope, render)` возвращает `False` для чужих prefix; свои
query получают ответ и `True`. Невалидные/устаревшие query получают настроенный текст;
индикатор только подтверждается. При валидном переходе после подтверждения вызывается
асинхронный `render(query, page, markup)`; приложение редактирует сообщение и учитывает
различия доступных/inline-сообщений. Ошибки источника, Telegram, render и отмена
распространяются. Конкурентные обновления одного scope сериализуйте в приложении.
При ошибке редактирования создайте клавиатуру заново: сброшенные ссылки не
восстанавливаются. Фоновые задачи очистки и соединения не создаются.

### Превью обычного текста

`aiogram_extensions.utilities.truncate_text(text, *, max_units, suffix="…")` укладывает
обычный текст с маркером усечения в бюджет кодовых единиц UTF-16. В отличие от среза
Python, символы вне BMP учитываются как две единицы; кодовые точки не разрываются.
Примеры пагинации ограничивают им превью строк каталога. Суффикс должен помещаться,
даже если текст не нужно усекать; неверный бюджет вызывает `ValueError`.
Вход должен быть валидным Unicode. Графемные кластеры, HTML/Markdown и готовые
entities не сохраняются: усечение выполняется до экранирования/создания entities.
Это консервативный бюджет текста, а не автоматическая проверка лимитов каждого
Telegram-метода. Для форматирования используйте инструменты aiogram; helper их
не заменяет. См. [смещения Telegram entities](https://core.telegram.org/bots/api#messageentity).

## Контракты этапа 2

Типы и жизненный цикл Redis ниже реализованы. Описанное поведение limiter и очереди —
контракт для этапов 4–6, а не текущая гарантия работы. Корень пакета не экспортирует
функциональный API. Импортируйте из функциональных модулей:

```python
from aiogram_extensions.rate_limit import RateLimitConfig, RateLimitResult, RateRule
from aiogram_extensions.messaging import EnqueueResult, QueueConfig, RetryConfig, TaskState
from aiogram_extensions.integrations import EventDispatcher, TaskEvent
from aiogram_extensions.redis import RedisConfig, RedisConnectionManager

limits = RateLimitConfig(rules=(RateRule(name="incoming", limit=5, window=1),))
queue = QueueConfig(workers=2, maxsize=100, retry=RetryConfig(max_attempts=3))
```

### Конфигурации и результаты

Конфигурации — неизменяемые стандартные dataclasses с именованными аргументами.
Невалидные настройки вызывают `ValueError`. Целочисленные счётчики не принимают
bool и дробные числа. Время задаётся конечным положительным числом секунд, если
не указано иное. Имена, prefix и namespace допускают латиницу, цифры, `_`, `.` и `-`.

| Тип | Поля и значения по умолчанию |
| --- | --- |
| `RateRule` | Обязательные `name`, положительное целое `limit`, положительное `window`; `scope="user_chat"`, `algorithm="fixed_window"` (альтернатива: `sliding_window`). |
| `RateLimitConfig` | Обязательный непустой tuple `rules` с уникальными именами; `on_limit="reject"` или `"wait"`. Неявных квот Telegram нет. |
| `RateLimitResult` | Обязательный bool `allowed`; `retry_after=0`, `triggered_rules=()`. Отказ требует положительного ожидания и имён сработавших правил; разрешённый результат их не содержит. |
| `RetryConfig` | `max_attempts=3`, включая первую попытку; `base_delay=1`, `max_delay=60` (не меньше base), `retry_unknown=False`. |
| `QueueConfig` | `workers=1`, `maxsize=1000`, `overflow="wait"` или `"reject"`, `enqueue_timeout=None` (без дедлайна), `drain_timeout=30`, `max_payload_bytes=262144`, `idempotency_ttl=86400`, `terminal_ttl=86400`, `retry=RetryConfig()`. Счётчики положительные; вместимость всегда ограничена. |
| `EnqueueResult` | Обязательные непустые `task_id`, `generation`; `duplicate=False`. Подтверждает приём, а не доставку. |
| `RedisConfig` | `url="redis://localhost:6379/0"` (скрыт в repr), `prefix="aiogram_extensions"`, `connect_timeout=1`, `socket_timeout=1`, `max_connections=20`. Только `redis://`/`rediss://`, числовая база, без query/fragment в URL. |

### Решения по limiter

Ключи изолируют направление, имя правила, алгоритм и числовой bot ID (не токен).
Процессы с общими именем правила/namespace используют одинаковые настройки;
при изменении настроек меняйте имя правила или namespace. Дополнительные идентификаторы:

| Scope | Идентичность и применимость |
| --- | --- |
| `user_chat` | User ID + chat ID. Incoming middleware пропускает события без любого из них. |
| `bot` | Без дополнительного ID; общий бюджет для чатов и методов. |
| `chat` | Chat ID для любого типа чата. |
| `group` | Chat ID только при явно подтверждённом типе `group`/`supergroup`. Private/channel/неизвестный тип не подходят; одного отрицательного ID недостаточно. Бюджет отдельный для каждой Telegram-группы, а не общий для всех групп. |
| `method` | Имя метода Bot API, общий бюджет между чатами. |
| `custom` | Явный непустой ключ приложения. |

Прямая проверка без необходимых user/chat/method/custom данных вызывает `ValueError`.
Fixed Window начинается с первого принятого события: `[start, start + window)`;
Sliding Window считает принятые события в `(now - window, now]`. Memory использует
монотонные часы, Redis — серверный `TIME`; одно измерение на решение. Устаревшее
состояние удаляется при обращении и ограниченной очистке; Redis также использует TTL.

Все применимые правила проверяются и списываются атомарно. При отказе не списывается
ни одно. `triggered_rules` следует порядку конфигурации; `retry_after` — максимальное
ожидание среди блокирующих правил. Ошибка HTTP не возвращает уже списанную попытку.
Reject возвращает отказ; wait асинхронно ждёт и проверяет заново, не резервируя
вместимость во время сна. Отмена распространяется; уже принятые попытки учитываются.
Ошибка backend вызывает `RateLimitBackendError` (наследник `RateLimitError`), без
отправки и fallback. Outgoing middleware записывает cooldown из `TelegramRetryAfter`
и повторно выбрасывает ошибку; HTTP retries принадлежат очереди или прямому вызывающему
коду. Вложенного retry loop нет. Настройки не гарантируют соблюдение квот Telegram.

### Решения по очереди

`TaskState`: enqueued → started → completed; временная ошибка приводит к
retry_scheduled → started. Исчерпание попыток/постоянная ошибка: failed → dlq;
failed может сохраняться при повторении устойчивой записи в DLQ. Replay создаёт
новое поколение enqueued со сброшенными попытками; старые доставки его не исполняют.

Memory workers запускаются явно. Постановка до старта/после остановки вызывает
`QueueNotRunningError`. `maxsize` ограничивает все принятые незавершённые задачи,
включая активные и отложенные повторы; дубликат не занимает новое место. Reject
вызывает `QueueFullError`; wait ждёт место до `enqueue_timeout`, затем вызывает
ту же ошибку. Отмена до приёма не добавляет задачу. При неизвестном результате
команды Redis проверяйте приём повторением того же idempotency key.

Memory извлекает первые попытки в порядке приёма FIFO; Redis — готовые записи
в порядке публикации stream, который может отличаться от порядка приёма журнала.
Несколько workers/producers и retries не гарантируют порядок завершения или по чату.
Stop закрывает приём и ждёт Memory до `drain_timeout`; затем отменяет и дожидается
workers, переносит незавершённую работу в локальную DLQ и вызывает `DrainTimeoutError`.
Это не отменяет эффект HTTP. Остановка Redis producer не дожидается удалённых workers;
их lifecycle и отдельным pool управляет Taskiq, незавершённая устойчивая работа сохраняется.

Планируемый Redis payload: UTF-8 JSON `{"version": 1, "method": "sendMessage", "params": {...}}`.
Размер всего закодированного envelope ограничен `max_payload_bytes`; неконечные числа
запрещены. Неизвестные версии/методы, некорректные, неподдерживаемые или слишком большие
payload вызывают `SerializationError` до приёма. Обязательные методы: `sendMessage`,
`sendPhoto`, `sendDocument`, `sendVideo`, `sendAudio`, `sendMediaGroup`, `sendLocation`,
`sendContact`. Поддерживаются file ID, URL и общие пути; `FSInputFile` превращается
в `{"$local_file": path, "filename": name}`. Путь должен быть доступен worker.
Redis отклоняет buffered files, потоки и живые Bot/Message/Update/FSM; Memory может
хранить локальные aiogram files. Worker создаёт Bot через factory и закрывает
только принадлежащие ему сессии.

В пределах backend/namespace равные idempotency key/payload возвращают сохранённые
task/generation с `duplicate=True`; различающиеся payload вызывают
`IdempotencyConflictError`. Активные идентичности не истекают. После терминального
исхода `idempotency_ttl` хранит идентичность/fingerprint, независимо от `terminal_ttl`
для результата/тела DLQ. Истёкшие тела недоступны и удаляются ограниченной очисткой
worker или явно. Replay проверяет вместимость и атомарно заменяет запись DLQ новым
поколением; повтор сохранённого replay возвращает его. Memory теряет всё состояние
при завершении процесса. Redis следует [протоколу spike](redis-queue-spike.md).

Каждая попытка резервируется до HTTP. Временные сетевые/серверные ошибки повторяются;
постоянные идут в DLQ, неизвестные повторяются только по настройке. Стандартная
экспоненциальная задержка ограничена `max_delay`; `TelegramRetryAfter.retry_after`
остаётся минимумом даже выше этого предела. Своя политика ограничена `max_attempts`.
DLQ хранит task/generation, payload, категорию причины и число попыток для публичного
просмотра/replay. Retries/ACK принадлежат одному слою. `QueueBackendError` допускает
неизвестный результат приёма/доставки; все ошибки очереди наследуют `QueueError`.
Неизвестный исход HTTP может дублировать эффект в Telegram; exactly-once не обещается.

### Жизненный цикл Redis и события

Оберните polling в `redis_context`, чтобы клиент Redis был доступен обработчикам
aiogram на время работы приложения.

```python
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from redis.asyncio import Redis

from aiogram_extensions.redis import RedisConfig, redis_context

dispatcher = Dispatcher()


@dispatcher.message(Command("redis"))
async def redis_status(message: Message, redis: Redis) -> None:
    await redis.ping()
    await message.answer("Redis доступен.")


async def run(bot: Bot, redis_url: str) -> None:
    async with redis_context(dispatcher, RedisConfig(url=redis_url)):
        await dispatcher.start_polling(bot, handle_as_tasks=False)
```

Для запуска команды установите extra `redis` и укажите доступный сервер Redis.
`redis_context` добавляет клиент в данные диспетчера под именем `redis` и удаляет
его при выходе, в том числе после ошибки. Для другого имени передайте
`key="cache"` и назовите параметр обработчика `cache`. Ключ должен быть свободен
в данных диспетчера. Через `client=...` можно передать существующий клиент, а через
`as manager` получить доступ к `manager.key(...)` для построения ключей с префиксом.
[Офлайн-пример](../examples/redis_lifecycle.py) показывает инъекцию на локальном
апдейте. [Документация DI aiogram](https://docs.aiogram.dev/en/3.31.0/dispatcher/dependency_injection.html)

`RedisConnectionManager(config=None, *, client=None)` заимствует клиент или создаёт
его лениво. Используйте `async with manager` либо `await manager.start()` и
`await manager.aclose()`. Start идемпотентен, не выполняет сетевой I/O и health check;
команды `manager.client` подключаются и передают ошибки redis-py. Собственный клиент
использует декодированные ответы, заданные таймауты/размер pool и ноль автоматических
повторов команд. Настройки заимствованного клиента не меняются. Prefix управляет
ключами в обоих случаях; параметры соединения — только собственным клиентом.
Taskiq сохраняет отдельный pool.

Доступ до старта/после закрытия и повторный старт после закрытия вызывают
`RedisLifecycleError`. Close идемпотентен, включая конкурентные вызовы; неудачное
закрытие можно повторить. Заимствованные ресурсы не закрываются. Делитесь manager
внутри одного lifecycle; вложенные контексты не поддерживаются. `key(namespace,
*parts)` кодирует отдельные непустые строковые части percent-encoding без коллизий
разделителя. Импорты/конфигурации/ключи не требуют extra; создание собственного клиента
без него вызывает `MissingRedisExtraError` с `pip install "aiogram-extensions[redis]"`.
Импорт не открывает соединений. См.
[официальный API lifecycle redis-py](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).

Неизменяемые события из `aiogram_extensions.integrations`:

| Событие | Поля |
| --- | --- |
| `TaskEvent` | Обязательные `kind` (`task_enqueued`, `task_started`, `task_completed`, `task_failed`, `retry_scheduled`, `task_dlq`, `dlq_replayed`), `task_id`, `generation`; `attempt=0`, `retry_after=0`. |
| `LimitExceededEvent` | Обязательные `direction` (`incoming`/`outgoing`), tuple `triggered_rules`, `retry_after`. |
| `BackendErrorEvent` | Обязательные `component` (`redis`/`rate_limit`/`messaging`), `operation` (`connect`/`check`/`enqueue`/`process`/`close`). |

`Event` — объединение типов; `EventCallback` — асинхронная функция, принимающая его
и возвращающая `None`. `EventDispatcher(callbacks=())` фиксирует набор callbacks;
`await emit(event)` ждёт их в порядке регистрации. Обычные исключения дают постоянное
предупреждение в `aiogram_extensions.integrations.events`; остальные callbacks работают.
Тексты исключений/tracebacks, ID, учётные данные и payload не логируются; root logging
не меняется. Отмена и другие наследники `BaseException` распространяются. Медленный
callback задерживает вызывающего; приложение может задать таймаут внутри callback.
Будущие компоненты отправляют события после фиксации переходов; ошибка callback их
не откатывает. Отмена после фиксации может скрыть результат — повторяйте с идемпотентностью.
События — локальные наблюдения, не устойчивый журнал аудита. Пока приложение отправляет
их явно; автоматические события появятся вместе с реализациями компонентов.

## Разработка

Запускайте проверки из корня репозитория:

```console
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

Для сборки wheel и архива с исходниками выполните `uv build`.

### Технический spike Redis-очереди

Для этапа 1 подготовлен изолированный стек: неизменяемый wheel исправленного
транспорта и устойчивый журнал попыток, владения, постановки через outbox, DLQ
и replay. Базовые зависимости не изменились. См.
[отчёт и границы гарантий](redis-queue-spike.md) и
[исследование первичных источников](spike-durability-sources.md) на английском языке.

При запущенном Linux-движке Docker выполните из корня репозитория:

```console
uv sync --directory spikes/redis_queue --locked
uv run --directory spikes/redis_queue --locked python build_transport.py --download
uv sync --directory spikes/redis_queue/verified --locked
uv run --directory spikes/redis_queue/verified --locked pytest -c ../pyproject.toml ../tests --run-redis --durable --transport-wheel -q
uv run --directory spikes/redis_queue --locked pyright
```

Тесты управляют временным Redis с AOF и локальным HTTP API; настоящие учётные
данные бота не нужны. Проверяются аварии worker/Redis и недоступность backend.
Без `--run-redis` сервисные тесты явно пропускаются. Проверенное окружение
устанавливает закреплённый локальный wheel без изменений исходников при запуске.
Это экспериментальные инструменты для дальнейшей реализации библиотеки.
Отправка в Telegram всё ещё может дублироваться при неизвестном результате
внешнего запроса; exactly-once не обещается.

### TDD и правила написания тестов

Тесты автоматически проверяют ожидаемое поведение и обнаруживают регрессии.
Примеры показывают пользователю применение публичного API и управление ресурсами.
Запуск примера не заменяет проверки лимитов, ошибок и граничных случаев;
тестирование примеров помогает сохранять опубликованные инструкции запуска рабочими.

Разрабатывайте функции и исправления небольшими циклами red-green-refactor:
добавьте один тест публичного поведения, запустите его и убедитесь, что он падает
по ожидаемой причине, внесите минимальное изменение, затем выполните рефакторинг,
сохраняя тесты зелёными. Тест регрессии должен сначала воспроизвести ошибку.
Для изменений документации и инструментов используйте соответствующие проверки
вместо искусственно падающих тестов.

Группируйте тесты сначала по уровню, затем по функциональным модулям по мере роста набора:

| Уровень | Назначение | Расположение |
| --- | --- | --- |
| Unit — модульные | Изолированная логика: алгоритмы, валидация, пагинация и решения о повторах; без внешних сервисов и процессов. | `tests/unit/<feature>/` |
| Integration — интеграционные | Совместная работа компонентов: адаптеры aiogram, хранилища, установка пакета и примеры в отдельных процессах. | `tests/integration/`, при необходимости — подпапки модулей |
| End-to-end (E2E) — сквозные | Полный поддерживаемый сценарий, например producer → Redis → worker → локальная подмена Telegram API. | `tests/e2e/<feature>/` |

Основной набор пакета содержит модульные и интеграционные тесты. Папки E2E
создавайте вместе с первыми тестами. Текущая структура:

```text
tests/
├── conftest.py
├── unit/
│   ├── integrations/test_events.py
│   ├── messaging/test_contracts.py
│   ├── pagination/test_core.py
│   ├── rate_limit/test_contracts.py
│   ├── utilities/test_text.py
│   └── test_redis.py
└── integration/
    ├── pagination/
    │   ├── test_bot_example.py
    │   └── test_navigation.py
    ├── test_examples.py
    ├── test_imports.py
    ├── test_redis_context.py
    └── test_packaging.py
```

В `conftest.py` находится общая фикстура пути к репозиторию. Для будущих модулей
используйте, например, `tests/unit/rate_limit/` и `tests/integration/rate_limit/`.
Тест регрессии относится к соответствующему модулю и уровню; отдельная папка
для него не нужна. Называйте тесты `test_<behavior>`, добавляйте аннотации типов
функциям тестов и фикстур,
разделяйте подготовку, действие и проверки. Через публичные интерфейсы проверяйте
наблюдаемый результат, ожидаемые исключения и значимые граничные случаи;
ожидаемые значения задавайте независимо от реализации.

Для однотипных случаев используйте `pytest.mark.parametrize` с понятными ID.
Держите фикстуры рядом с тестами, а при совместном использовании переносите
в ближайший `conftest.py`. Предпочитайте встроенные фикстуры `tmp_path`, `monkeypatch`
и `capsys`; закрывайте ресурсы контекстными менеджерами или yield-фикстурами.
При необходимости подменяйте внешние границы; обычный набор тестов должен работать
без настоящих учётных данных и внешних сервисов.

Асинхронные тесты пишите как `async def`: режим auto в pytest-asyncio также
обрабатывает асинхронные фикстуры с `@pytest.fixture`. Цикл событий изолирован
на уровне каждой функции теста и фикстуры. Предупреждения и неожиданный успех
тестов с `xfail` приводят к провалу проверки. В
[тесте импорта](../tests/integration/test_imports.py) проверяется изоляция импорта, а в
[тестах примеров](../tests/integration/test_examples.py) показаны параметризация и `tmp_path`.
[Тест упаковки](../tests/integration/test_packaging.py) собирает wheel через uv и проверяет
метаданные лицензии и маркер типизации после установки в чистое виртуальное
окружение. Он работает без сети, устанавливает только wheel без зависимостей
и требует локально доступный uv с совместимым сборочным бэкендом.

Полезные команды из корня репозитория:

```console
uv run --locked pytest -q -x
uv run --locked pytest tests/integration/test_imports.py::test_base_import_is_independent -q
uv run --locked pytest tests/integration/test_examples.py -q
uv run --locked pytest -k lifecycle -q
uv run --locked pytest --lf -q
uv run --locked pytest --collect-only -q
```

Эти команды соответственно запускают тесты до первого сбоя, выбирают один тест,
проверяют примеры, фильтруют по имени, повторяют последние упавшие тесты
и показывают список найденных тестов.

Для запуска всех интеграционных тестов выполните `uv run --locked pytest tests/integration -q`,
для проверки только упаковки — `uv run --locked pytest tests/integration/test_packaging.py -q`.

### Планируемое покрытие функциональности

Следующие проверки поведения будут добавляться вместе с реализациями из
[технического задания](TZ.md). Этап 2 уже проверяет конфигурации, результаты,
callbacks, владение Redis, независимые импорты и отсутствие extras.

| Область | Что проверять |
| --- | --- |
| `rate_limit` | Границы Fixed/Sliding Window, независимые ключи пользователь/чат, комбинации правил, время ожидания, конкурентные вызовы, совпадение контрактов Memory/Redis и `TelegramRetryAfter`. |
| `messaging` | Поддерживаемые данные и сериализация, ограничение размера очереди, заявленный порядок, решения о повторах и предел попыток, DLQ/replay, идемпотентность, корректное завершение и восстановление worker после аварии. |
| `pagination` | Пустые данные, первая/последняя страницы и выход за диапазон, семантика offset/cursor, кнопки навигации и ограничения callback data. |
| `integrations` | Документированные данные событий и согласованная обработка ошибок callback. |
| Конфигурация и жизненный цикл Redis | Невалидные настройки, отсутствующие extras, владение и закрытие подключений, пространства ключей, отказы Redis и атомарные обновления. |

Чистую логику проверяйте без сервисов; поведение Redis — на изолированном настоящем
Redis, поведение aiogram — через публичные адаптеры с управляемым транспортом Telegram.
При добавлении сценариев с внешними сервисами предусмотрите явную настройку и выбор
их запуска. Обычный набор тестов не требует настоящих учётных данных бота.
Утилиты получают предметные тесты и примеры вместе с реализацией конкретного helper.
Проверки совместимости охватят поддерживаемую матрицу Python/aiogram; аварийные
и конкурентные тесты будут проверять гарантии, действительно заявленные для каждого бэкенда.

## Планы проекта

[Техническое задание](TZ.md) и [дорожная карта](ROADMAP.md) — временные документы
на русском языке. Примеры API в них описывают запланированное поведение.

## Лицензия

[MIT](../LICENSE).
