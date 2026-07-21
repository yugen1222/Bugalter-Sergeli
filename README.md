# Бот материального бухгалтера — версия 2

## Что добавлено

- выбор имени сотрудника перед работой;
- возможность добавить новое имя прямо в боте;
- выбор смены;
- обязательное фото результата выравнивания;
- поиск товара по первым буквам;
- готовый каталог товаров в `products.json`;
- причина минуса;
- имя ответственного сотрудника;
- комментарий;
- месячная аналитика;
- аналитика по сменам;
- Excel-отчёт с графиками;
- ежедневное напоминание;
- сайт аналитики.

## Render: бот

Создайте **Background Worker**:

```text
Build Command: pip install -r requirements.txt
Start Command: python main.py
```

Переменные окружения:

```env
BOT_TOKEN=...
TIMEZONE=Asia/Tashkent
ALLOWED_USER_IDS=...
ADMIN_USER_IDS=...
REMINDER_TIME=20:00
```

## Render: сайт

Для сайта создайте отдельный **Web Service** из того же GitHub:

```text
Build Command: pip install -r requirements.txt
Start Command: gunicorn web_app:app
```

Переменные:

```env
WEB_USERNAME=admin
WEB_PASSWORD=придумайте_пароль
SECRET_KEY=любая_длинная_строка
```

## Важно про базу

Бот и сайт должны работать с одной базой `material_accountant.db`.

На Render для постоянного хранения SQLite потребуется Persistent Disk.
Если бот и сайт создаются как два разных сервиса, у них не будет общей SQLite-базы.
Для полноценной работы сайта лучше следующим этапом подключить PostgreSQL.

## Каталог товаров

Откройте `products.json` и замените примерный список на ваши реальные позиции.
