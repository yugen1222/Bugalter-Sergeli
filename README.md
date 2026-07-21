# Материальный бухгалтер — бот и сайт в одном Render Service

Один процесс одновременно запускает:

- Telegram-бота;
- сайт аналитики;
- ежедневные напоминания;
- одну общую SQLite-базу `material_accountant.db`.

## Render

Создайте только **один Web Service**.

### Build Command

```bash
pip install -r requirements.txt
```

### Start Command

```bash
python combined_app.py
```

Не используйте отдельно `python main.py` и не создавайте отдельный Background Worker.

## Environment Variables

Добавьте:

```env
BOT_TOKEN=токен_из_BotFather
TIMEZONE=Asia/Tashkent

# Можно оставить пустым — тогда бот доступен всем
ALLOWED_USER_IDS=

# Telegram ID администраторов через запятую
ADMIN_USER_IDS=

REMINDER_TIME=20:00

WEB_USERNAME=admin
WEB_PASSWORD=придумайте_надёжный_пароль
SECRET_KEY=любая_длинная_случайная_строка
```

`PORT` вручную добавлять не нужно — Render создаёт его автоматически.

## Как открыть сайт

После успешного деплоя Render покажет адрес формата:

```text
https://имя-сервиса.onrender.com
```

При открытии браузер запросит:

- логин из `WEB_USERNAME`;
- пароль из `WEB_PASSWORD`.

## Важный момент про SQLite

Бот и сайт теперь используют одну базу, потому что работают внутри одного сервиса.

Но файловая система Render без Persistent Disk может очищаться при новом развёртывании или переносе сервиса. Для надёжного постоянного хранения нужно:

1. подключить Persistent Disk к этому Web Service; или
2. позже перевести базу на PostgreSQL.

## Структура

```text
combined_app.py          единый запуск сайта и бота
main.py                  логика Telegram-бота
web_app.py               сайт аналитики
database.py              база данных
excel_export.py          Excel-отчёты
products.json            категории и товары
templates/               страницы сайта
requirements.txt         зависимости
```
