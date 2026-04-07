# Telegram VPN Bot (Xray VLESS)

Готовый backend-проект Telegram-бота для продажи и автоматического продления VPN-доступа на Xray (VLESS).

## Что реализовано

- Telegram-бот на `aiogram` с inline-кнопками
- Онбординг-доступ: обязательная подписка на канал + принятие правил/политики
- PostgreSQL + ORM (`SQLAlchemy`)
- Баланс в рублях и раздельная логика:
  - пополнение баланса
  - отдельная кнопка покупки месяца (`100 ₽ / 30 дней`)
- Автопродление по балансу (планировщик)
- Автоотключение при нехватке средств
- Реферальная система (+2 дня)
- Стартовый триал (+1 день)
- Админ-панель внутри Telegram (для одного super-admin)
- Интеграция с Xray через API (`adu/rmu`) или через `config.json`
- Платежи TelegaPay (создание paylink + автоматический polling статуса)
- Логи приложения и платежей

## Архитектура

Компоненты на одном VPS:

1. **Xray-core** (уже установлен)
2. **Telegram bot** (`app/main.py`)
3. **PostgreSQL**

Потоки:

- Пользователь в боте пополняет баланс через TelegaPay (метод SBP)
- Бот проверяет статус pending-платежей каждую минуту через TelegaPay API
- Пользователь жмет "Оплатить месяц" -> баланс списывается -> срок продлевается -> юзер включается в Xray
- Планировщик регулярно:
  - проверяет истечения
  - делает автопродление
  - отключает доступ при нехватке средств
  - отправляет уведомления за 24 часа
  - применяет ограничение по устройствам

## Структура проекта

```text
app/
  bot/
    handlers/
      admin.py
      user.py
    keyboards.py
    middlewares.py
    states.py
  db/
    models.py
    repositories.py
    session.py
  services/
    admin_service.py
    billing_service.py
    device_limit_service.py
    payment_service.py
    scheduler_service.py
    user_service.py
    xray_service.py
  utils/
    security.py
    time.py
  config.py
  logging_config.py
  main.py
requirements.txt
.env.example
docker-compose.yml
```

## Таблицы БД

### users

- `id`
- `telegram_id`
- `username`
- `uuid`
- `balance`
- `expiration_date`
- `status` (`active` / `banned`)
- `vpn_enabled`
- `device_limit_blocked`
- `referral_code`
- `referred_by`
- `warning_sent_at`
- `created_at`
- `updated_at`

### payments

- `id`
- `user_id`
- `amount`
- `status` (`pending`, `paid`, `cancelled`, `failed`)
- `provider_label`
- `external_operation_id`
- `created_at`
- `paid_at`

### referrals

- `id`
- `inviter_id`
- `invited_id`
- `bonus_applied`
- `created_at`

## Интеграция с Xray

### Режимы управления

Поддерживаются два режима:

- `XRAY_CONTROL_MODE=api` (рекомендуется): бот использует `xray api adu/rmu`, Xray не перезапускается, активные соединения не рвутся.
- `XRAY_CONTROL_MODE=config`: бот правит `config.json` и применяет изменения через `XRAY_RELOAD_COMMAND` / `XRAY_RESTART_COMMAND`.

В обоих режимах:

- UUID пользователя **постоянный**
- VLESS-ссылка не меняется, меняется только активность в Xray

### Почему API-режим лучше

`xray api adu/rmu` работает через `HandlerService` и добавляет/удаляет пользователей на лету.  
Это исключает микродропы, которые неизбежны при `systemctl restart xray`.

Дополнительно бот периодически делает runtime-sync из БД в Xray API (`XRAY_SYNC_INTERVAL_MINUTES`),  
чтобы после случайного рестарта Xray автоматически восстановить активных пользователей.

Технически бот передает в `adu` временный JSON с inbound `tag` и `clients`, а для удаления использует `rmu -tag=<tag> <email>`.  
`email` в Xray используется как уникальный идентификатор пользователя (`user-<telegram_id>@vpn.local`).

Если нужен fallback, оставляйте `config`-режим.

### API vs JSON

- Для минимальных обрывов используйте `api`-режим.
- Для максимальной простоты эксплуатации можно оставить `config`-режим.
- В коде есть оба варианта, переключение только через `.env`.

### Лимит 4 устройств

В Xray нет идеального штатного лимита "строго 4 устройства" для VLESS.
В проекте добавлен практичный soft-limit:

- основной режим: `xray api statsonlineiplist --json` (текущие online IP пользователя)
- резервный fallback: парсинг `XRAY_ACCESS_LOG_PATH`
- подсчет уникальных IP на пользователя
- при превышении >4:
  - временно отключается VPN
  - отправляется уведомление
- при нормализации подключений доступ возвращается

Для API-режима убедитесь, что в конфиге Xray включены:
- `stats: {}`
- `policy.levels.0.statsUserOnline: true`
- `api.services: ["HandlerService", "StatsService"]`

Рекомендуется ставить `DEVICE_LIMIT_INTERVAL_MINUTES=1`, чтобы блокировка срабатывала быстро.

## Биллинг

- Валюта: RUB
- Пополнение: TelegaPay `create_paylink` (метод `SBP`)
- Проверка статуса: endpoint `check_status` (каждую минуту)
- Списание:
  - только при явном нажатии `Оплатить месяц`
  - автоматическое продление по cron при истечении, если баланса хватает
- Цена: `MONTH_PRICE_RUB` (по умолчанию 100)

## Админ-функции

Через `/admin`:

- список пользователей
- баланс пользователя
- выдать/снять дни
- выдать дни всем пользователям
- бан/разбан
- выдать бонус (дни + деньги)
- массовая рассылка всем пользователям

## Установка и запуск

## 1) Подготовка окружения

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env` своими значениями.

Минимально для платежей TelegaPay:

- `TELEGAPAY_BASE_URL` (например `https://secure.telegapay.link/api/v1`)
- `TELEGAPAY_API_KEY`
- `TELEGAPAY_RETURN_URL` (можно на бота в Telegram)
- `PAYMENT_POLL_INTERVAL_SECONDS=60`

Минимально для онбординга пользователей:

- `REQUIRED_CHANNEL` (например `@kvpnpublic`)
- `REQUIRED_CHANNEL_URL`
- `RULES_URL`
- `SUPPORT_URL`

## 2) PostgreSQL

Можно локально или через docker:

```bash
docker compose up -d db
```

Если на сервере нет Compose v2-плагина (ошибка с `docker compose`), используйте:

```bash
docker-compose up -d db
```

Или установите плагин:

```bash
apt-get update
apt-get install -y docker-compose-plugin
```

## 3) Проверка Xray-конфига

В вашем `config.json` должен быть inbound с нужным `tag`, например:

```json
{
  "inbounds": [
    {
      "tag": "vless-in",
      "protocol": "vless",
      "settings": {
        "clients": []
      }
    }
  ]
}
```

Для `XRAY_CONTROL_MODE=api` обязательно включите API в Xray:

```json
{
  "api": {
    "tag": "api",
    "services": [
      "HandlerService",
      "StatsService"
    ]
  },
  "inbounds": [
    {
      "tag": "api",
      "listen": "127.0.0.1",
      "port": 10085,
      "protocol": "dokodemo-door",
      "settings": {
        "address": "127.0.0.1"
      }
    }
  ],
  "routing": {
    "rules": [
      {
        "inboundTag": [
          "api"
        ],
        "outboundTag": "api"
      }
    ]
  }
}
```

И в `.env`:

- `XRAY_CONFIG_PATH`
- `XRAY_INBOUND_TAG`
- `XRAY_CONTROL_MODE`
- `XRAY_API_SERVER`
- `XRAY_API_TIMEOUT_SECONDS`
- `XRAY_API_ENABLED`
- `XRAY_SYNC_INTERVAL_MINUTES`

Для `api`-режима пользователю бота нужен доступ к бинарнику `xray` и локальному API-порту.  
Для `config`-режима дополнительно нужны права на запись `config.json` и reload/restart Xray.

## 4) Запуск бота

```bash
source .venv/bin/activate
python -m app.main
```

## 5) Systemd (рекомендуется)

Готовый unit-файл лежит в `deploy/systemd/tgvpn-bot.service`.

Скопируйте его в systemd:

```bash
sudo cp deploy/systemd/tgvpn-bot.service /etc/systemd/system/tgvpn-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now tgvpn-bot
```

Проверка:

```bash
sudo systemctl status tgvpn-bot
sudo journalctl -u tgvpn-bot -f
```

## 6) Аварийный runtime-resync (без restart Xray)

Если нужно массово восстановить пользователей в Xray runtime из БД:

```bash
cd /home/tgvpn
source .venv/bin/activate
python3 scripts/resync_xray_runtime.py
```

Что делает скрипт:
- безопасный режим по умолчанию: upsert активных пользователей в runtime;
- при `--rebuild`: удаляет всех managed пользователей и добавляет обратно активных;
- не выполняет `systemctl restart xray`.

Для жесткого полного пересбора:

```bash
python3 scripts/resync_xray_runtime.py --rebuild
```

Если изменили `.env` или код:

```bash
sudo systemctl restart tgvpn-bot
```

Важно: unit сейчас запускается от `root`.
- Для `config`-режима это обязательно (правка `xray config` + `reload/restart`).
- Для `api`-режима можно запускать от отдельного пользователя, если ему доступен бинарник `xray` и локальный API-порт.

## Безопасность

- Все admin-действия ограничены `SUPER_ADMIN_ID`
- Платеж подтверждается только через серверную проверку `transaction_id` и статуса транзакции
- Действия логируются в `logs/app.log` и `logs/payments.log`
- Не храните секреты в репозитории (`.env` в `.gitignore`)

## Узкие места и улучшения

1. **TelegaPay polling**: для меньшей задержки можно добавить webhook-обработчик `/webhook`.
2. **Лимит устройств**: для "железобетонного" контроля лучше подключить специализированный `limit-ip` слой.
3. **Миграции БД**: добавить полноценные Alembic migration scripts.
4. **Тесты**: добавить unit/integration тесты для биллинга и Xray sync.
5. **Xray API режим**: добавить gRPC runtime-операции как дополнительный backend управления.
