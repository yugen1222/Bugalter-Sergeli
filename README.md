# Бот материального бухгалтера

Telegram-бот для ежедневной фиксации:

- выполнено ли выравнивание;
- были ли позиции с минусом;
- название и количество каждой позиции;
- удалось ли найти причину;
- причина минуса;
- комментарий;
- дневной и месячный отчёт.

## 1. Создание бота

1. Откройте в Telegram `@BotFather`.
2. Отправьте команду `/newbot`.
3. Скопируйте полученный токен.

## 2. Запуск на компьютере

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

Установите зависимости:

```bash
pip install -r requirements.txt
```

Скопируйте `.env.example` в `.env` и вставьте токен:

```env
BOT_TOKEN=ВАШ_ТОКЕН
TIMEZONE=Asia/Tashkent
ALLOWED_USER_IDS=
```

Запустите:

```bash
python main.py
```

## 3. Ограничение доступа

Чтобы ботом пользовался только материальный бухгалтер, укажите его Telegram ID:

```env
ALLOWED_USER_IDS=123456789
```

Для нескольких пользователей:

```env
ALLOWED_USER_IDS=123456789,987654321
```

Если поле пустое, бот доступен всем.

## 4. Render

Создайте новый Background Worker.

- Build Command:

```bash
pip install -r requirements.txt
```

- Start Command:

```bash
python main.py
```

Добавьте переменные окружения:

- `BOT_TOKEN`
- `TIMEZONE=Asia/Tashkent`
- `ALLOWED_USER_IDS`

## База данных

Файл базы:

```text
material_accountant.db
```

Таблицы:

- `daily_checks` — ежедневное выравнивание;
- `minus_items` — минусовые позиции и причины.

Эта структура уже подходит для подключения будущего сайта аналитики.
