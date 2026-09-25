# API v1 · правила боя v4

Префикс маршрутов — `/api/v1`; версия боевых правил меняется независимо от него.
Время — Unix seconds, денежные поля с суффиксом `_minor` — **целые сотые доли
монеты**: `1000` означает ставку 10. Клиент не округляет баланс до целых монет.

POST принимает JSON-объект. Закрытые методы требуют
`Authorization: Bearer <token>`. Для всех команд, кроме входа, обязателен
`Idempotency-Key: <UUID>`. Повтор после потерянного ответа отправляет тот же
ключ, маршрут и JSON. Тот же ключ с другим содержимым даёт
`409 idempotency_conflict`.

Успешная команда возвращает `{result, state}`: результат операции сохраняется,
а `state` при повторе вычисляется заново. Ошибка имеет вид
`{error: {code, message}}`; сообщение предназначено игроку.

## Методы

| Метод и путь | Точный JSON запроса | Ответ / действие |
| --- | --- | --- |
| `GET /config` | — | `{dev_auth, bot_username, rules_version}`, без входа |
| `POST /auth/dev` | `{user_id, name}` | `{token, state}`; только локальная разработка |
| `POST /auth/telegram` | `{init_data}` | `{token, state}`; исходная подписанная строка Telegram |
| `GET /state` | — | Актуальный state; может завершить истёкшие бои |
| `POST /presence` | `{}` | `result: {ok: true}`; обновляет присутствие и срок очереди |
| `POST /claim/passive` | `{}` | `result: {payout_minor}` |
| `POST /claim/daily` | `{}` | `result: {payout_minor}` |
| `POST /gear/upgrade` | `{slot}`: `helmet`, `armor` или `sword` | `result: {slot, level, cost_minor}` |
| `POST /breed/buy` | `{breed_id}` | Покупает/выбирает породу; `result: {breed_id}` |
| `POST /battle/start` | `{mode: "bot", stake_minor, expected_power}` | `result: {battle_id}` |
| `POST /queue/join` | `{stake_minor}` | `result: {matched: false}` либо `{matched: true, battle_id}` |
| `POST /queue/leave` | `{}` | `result: {ok: true}` |
| `POST /battle/tap` | `{battle_id, taps}` | `taps`: целое 1..90; `result: {accepted}` |
| `GET /leaderboard` | — | `{scope, power: [...], pvp_wins: [...]}` |

Дополнительные/пропущенные поля команды дают `invalid_payload`. `train` и
новый режим `practice` не поддерживаются; стойки в API отсутствуют.

Обычные ставки: `1000, 2500, 5000, 10000`. Только `battle/start` допускает
`stake_minor: 0`, пока `economy.first_free_battle_available` истинно.
Любой первый начатый бой расходует это право. Ставка 0 даёт фиксированную
награду 1500 независимо от исхода; это не обычный приз и не RTP.

`expected_power` должен быть целым и совпадать с текущей серверной мощью.
Он защищает выбор ставки после изменения мощи в другой вкладке; сервер сам
определяет мощь и приз. При расхождении возвращается `409 power_changed`:
нужно обновить состояние и заново подтвердить запуск.

## State

Основные поля нового аккаунта:

```json
{
  "server_time": 1800000000,
  "rules_version": "v4",
  "player": {
    "id": "dev:123",
    "name": "Игрок",
    "is_dev": true,
    "balance_minor": 42000,
    "xp": 0,
    "level": 1,
    "xp_in_level": 0,
    "xp_to_next": 100,
    "power": 100,
    "breed_id": "yard",
    "owned_breeds": ["yard"],
    "gear": {"helmet": 0, "armor": 0, "sword": 0},
    "wins": 0,
    "pvp_wins": 0,
    "losses": 0,
    "battles": 0,
    "referral_code": "ref_..."
  },
  "economy": {
    "first_free_battle_available": true,
    "passive_available_minor": 0,
    "daily_reward_minor": 17000,
    "daily_available": true,
    "next_daily_at": 0,
    "upgrade_costs_minor": {"helmet": 8000, "armor": 8000, "sword": 8000},
    "bot_quotes": [
      {"stake_minor": 1000, "min_payout_minor": 1530, "max_payout_minor": 2160},
      {"stake_minor": 2500, "min_payout_minor": 3825, "max_payout_minor": 5400},
      {"stake_minor": 5000, "min_payout_minor": 7650, "max_payout_minor": 10800},
      {"stake_minor": 10000, "min_payout_minor": 15300, "max_payout_minor": 21600}
    ]
  },
  "presence": {"online": 0, "searching": 0, "development_online": 1},
  "queue": null,
  "battle": null,
  "history": []
}
```

В ответе также всегда есть `catalog`: `coin_scale: 100`, `breeds` с
`price_minor`, `power_multiplier` (1 / 1.2 / 1.5 / 1.9) и целым
`power_multiplier_percent` (100 / 120 / 150 / 190), `slots` и `battle`.
У породы больше нет поля фиксированной `power`: множитель применяется ко всей
сумме базы, уровня и экипировки с округлением вниз один раз.
`slots[].power_per_level` задаёт прирост базовой мощи до множителя породы.
`catalog.battle` имеет вид:

```json
{
  "duration": 10,
  "roulette_duration": 2,
  "tap_cap": 90,
  "tap_bonus_cap": 0.2,
  "stakes_minor": [1000, 2500, 5000, 10000],
  "bot_power_min_ratio": 0.7,
  "bot_power_max_ratio": 1.4,
  "bot_rtp_min": 0.9,
  "bot_rtp_max": 1.05,
  "pvp_pool_return": 0.95,
  "free_reward_minor": 1500
}
```

`tap_bonus_cap` описывает прибавку эффективной мощи в PvP. Формула ботов
отдельная: [игровой дизайн](GAME_DESIGN.md#бой-с-ботом).
`bot_quotes` — границы фиксированного приза при текущей мощи, а не сумма
выигрыша выбранного будущего соперника. Точный приз приходит в `battle.wager`.

В `player.wins` остаются все победы. Для рейтинга живого PvP использовать
`pvp_wins`, который исключает ботов и аккаунты разработки.
`upgrade_costs_minor[slot]` равен `null` на максимальном уровне предмета.

## Очередь и присутствие

Очередь имеет вид `{joined_at, expires_at, stake_minor}`. Без обновления она
истекает через 60 секунд; heartbeat продлевает её максимум до 120 секунд от
начала поиска. Присутствие считается свежим 45 секунд. Видимому клиенту
достаточно посылать `presence` примерно раз в 15 секунд.

Пара состоит из аккаунтов одного типа (Telegram либо разработка), с одинаковой
ставкой и близкой мощью. Вход оплачивается только при создании матча.
Выход и истечение очереди не требуют возврата, поскольку списания ещё не было.
Реальные `online` и `searching` не включают разработческие аккаунты.

## Состояние боя

`battle` и элементы `history` используют одну форму:

```text
{
  id, mode: "bot" | "online", status: "active" | "finished",
  rules_version, created_at, starts_at, ends_at, tap_cap,
  you: {name, power, breed_id, taps},
  opponent: {name, power, breed_id, taps, is_bot},
  wager: {stake_minor, win_payout_minor},
  roulette: null | {min_power, max_power},
  current_win_probability: null | number, // server odds from accepted taps, v3/v4
  win_probability: null | number,
  result: null | {won, xp, payout_minor, net_minor}
}
```

У бота `roulette` задаёт диапазон анимации; точный соперник уже сохранён
в `opponent`. `created_at` — начало двухсекундной подготовки,
`starts_at` — начало 10-секундного боя. Состояние до `starts_at` тоже
называется `active`; отдельного изменяемого состояния «рулетка» нет.

`wager.win_payout_minor` фиксирован до боя и **включает ставку**.
`result.payout_minor` — реально зачисленная сумма,
`result.net_minor = payout_minor - stake_minor`.
В бесплатном первом бою обе суммы равны 1500 при любом исходе.
Для платного поражения `payout_minor` равен 0.

Случайное число не выдаётся клиенту. Итоговая `win_probability` раскрывается
после расчёта и относится к стороне текущего игрока. До расчёта это `null`.
`current_win_probability` — число от 0 до 1 по сохранённым мощностям и принятым
кликам для v3/v4; у v1/v2 это `null`. Поле вычисляется и во время подготовки,
но интерфейс показывает его после `starts_at`. В PvP оно учитывает клики
обоих игроков. Это вероятность победы, а не RTP; случайное число и будущий
результат остаются скрытыми. После завершения сохраняется итоговая
`win_probability` по стороне игрока.
Закрытие приложения не отменяет бой. Последний бой остаётся в state до нового,
история содержит до десяти завершённых боёв.

Касания v4 принимаются только при `starts_at <= server_time < ends_at`.
Лимита скорости нет: одна пачка может содержать все 90. Общий предел считается
сервером для каждой стороны; `accepted` может быть меньше отправленного.
До старта возвращается 0, после конца — `409 battle_finished`.
Повтор ключа возвращает исходное `accepted` и не добавляет клики снова.
Размер запроса ограничен 1..90, а остаток полезных кликов определяется
`battle.tap_cap` выбранного движка: в сохранённом v3-бою это по-прежнему 80.

## Рейтинги

`scope` равен `telegram` или `development`. Каждый из массивов `power`
и `pvp_wins` содержит до 50 строк:

```text
{id, name, power, pvp_wins, level}
```

Первая таблица сортируется по мощи, затем по PvP-победам; вторая наоборот.
Последний критерий — стабильный `id`. Реальные рейтинги исключают аккаунты
разработки. Разработческие матчи не увеличивают `pvp_wins` даже в локальной таблице.

## Совместимость и ошибки

`state.rules_version` и `catalog` описывают текущие правила v4. При этом
`battle.rules_version` может оставаться `v3`: такой бой сохраняет прежние
снимки мощи, приз, формулу шанса и лимит 80 кликов. Для его интерфейса нужно
использовать `battle.tap_cap` и сроки самого боя, а не подставлять лимит 90
из нового каталога. Миграция 004 не пересчитывает уже созданные бои.

Старые бои v1/v2 возвращаются с исходным `rules_version`, своим `tap_cap`
и временем. Возможны исторический `mode: "practice"`, поле `stance` в
снимке и `coins/medals/legacy` в результате. Их нельзя трактовать как новые
команды: UI использует нормализованные `payout_minor/net_minor`.
Старые успешные UUID/JSON продолжают возвращать сохранённый результат
операции; свежий state всегда использует текущий контракт v4. Старую страницу
после обновления сервера нужно перезагрузить.

Типичные коды: `401 unauthorized`, `400 invalid_payload`,
`400 invalid_stake`, `400 invalid_taps`, `409 insufficient_funds`,
`409 player_busy`, `409 free_battle_used`, `409 power_changed`,
`409 battle_finished`, `409 daily_not_ready`, `409 idempotency_conflict`.
После timeout/5xx повторять исходную команду с прежним UUID; новый ключ
не является способом проверить, была ли уже выполнена операция.
