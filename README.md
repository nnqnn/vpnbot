# Telegram VPN Bot (Xray VLESS)

Готовый backend-проект Telegram-бота для продажи и автоматического продления VPN-доступа на Xray (VLESS).

## Что реализовано

- Telegram-бот на `aiogram` с inline-кнопками
- PostgreSQL + ORM (`SQLAlchemy`)
- Баланс в рублях и раздельная логика:
  - пополнение баланса
  - отдельная кнопка покупки месяца (`100 ₽ / 30 дней`)
- Автопродление по балансу (планировщик)
- Автоотключение при нехватке средств
- Реферальная система (+2 дня)
- Стартовый триал (+1 день)
- Админ-панель внутри Telegram (для одного super-admin)
- Интеграция с Xray через правку `config.json` и перезагрузку
- Платежи YooMoney (Quickpay + автоматическая сверка через API)
- Логи приложения и платежей

## Архитектура

Компоненты на одном VPS:

1. **Xray-core** (уже установлен)
2. **Telegram bot** (`app/main.py`)
3. **PostgreSQL**

Потоки:

- Пользователь в боте пополняет баланс через YooMoney
- Бот получает подтверждение оплаты через polling API YooMoney
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

### Текущая реализация

Реализован режим `config`:

- бот добавляет/удаляет пользователя в `settings.clients` нужного inbound (`XRAY_INBOUND_TAG`)
- затем выполняет `XRAY_RELOAD_COMMAND` (обычно `systemctl reload xray`)
- UUID пользователя **постоянный**
- VLESS-ссылка не меняется, меняется только активность в Xray

### Почему так

Для production-поведения важно хранить фактическое состояние в конфиге, чтобы после рестарта Xray пользователи не терялись. Это упрощает эксплуатацию.

### API vs JSON (рекомендация)

- **Для долгосрочной поддержки лучше гибрид:**
  - runtime-операции через API (быстро, без reload)
  - периодическая синхронизация в JSON для persistence
- В текущей версии уже готов надежный JSON-контур и синхронизация состояния при старте.

### Лимит 4 устройств

В Xray нет идеального штатного лимита "строго 4 устройства" для VLESS.
В проекте добавлен практичный soft-limit:

- парсинг `XRAY_ACCESS_LOG_PATH`
- подсчет уникальных IP на пользователя
- при превышении >4:
  - временно отключается VPN
  - отправляется уведомление
- при нормализации подключений доступ возвращается

## Биллинг

- Валюта: RUB
- Пополнение: YooMoney Quickpay ссылка
- Сверка оплаты: API `operation-history` по `label`
- Списание:
  - только при явном нажатии `Оплатить месяц`
  - автоматическое продление по cron при истечении, если баланса хватает
- Цена: `MONTH_PRICE_RUB` (по умолчанию 100)

## Админ-функции

Через `/admin`:

- список пользователей
- баланс пользователя
- выдать/снять дни
- бан/разбан
- выдать бонус (дни + деньги)

## Установка и запуск

## 1) Подготовка окружения

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env` своими значениями.

## 2) PostgreSQL

Можно локально или через docker:

```bash
docker compose up -d db
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

И в `.env`:

- `XRAY_CONFIG_PATH`
- `XRAY_INBOUND_TAG`
- `XRAY_RELOAD_COMMAND`

Пользователь, под которым запускается бот, должен иметь права читать/писать Xray config и выполнять reload.

## 4) Запуск бота

```bash
source .venv/bin/activate
python -m app.main
```

## 5) Systemd (рекомендуется)

Запускайте бота как systemd service для автоперезапуска.

## Безопасность

- Все admin-действия ограничены `SUPER_ADMIN_ID`
- Платеж подтверждается только через серверную проверку `label` и суммы
- Действия логируются в `logs/app.log` и `logs/payments.log`
- Не храните секреты в репозитории (`.env` в `.gitignore`)

## Узкие места и улучшения

1. **YooMoney polling**: лучше перейти на webhook-подтверждение для меньшей задержки.
2. **Лимит устройств**: для "железобетонного" контроля лучше подключить специализированный `limit-ip` слой.
3. **Миграции БД**: добавить полноценные Alembic migration scripts.
4. **Тесты**: добавить unit/integration тесты для биллинга и Xray sync.
5. **Xray API режим**: добавить gRPC runtime-операции как дополнительный backend управления.
