import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice,
)
from telegram.constants import Currency, ChatType
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    PreCheckoutQueryHandler, ContextTypes, filters,
)

# ====== CONFIGURATION ======
# Set these as environment variables; never publish your bot token.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MANAGER_ID = int(os.getenv("MANAGER_ID", "0"))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "oenxu").lstrip("@")
DB_PATH = os.getenv("DB_PATH", "report_system.sqlite3")

# Telegram Stars prices (XTR). These are configurable example values,
# not a conversion from the RUB prices shown to users.
STAR_PRICES = {
    "7d": int(os.getenv("STARS_7D", "200")),
    "30d": int(os.getenv("STARS_30D", "350")),
    "90d": int(os.getenv("STARS_90D", "550")),
    "life": int(os.getenv("STARS_LIFE", "1000")),
}
PLANS = {
    "7d": {"title": "7 дней", "days": 7, "rub_label": "299 ₽"},
    "30d": {"title": "30 дней", "days": 30, "rub_label": "499 ₽"},
    "90d": {"title": "90 дней", "days": 90, "rub_label": "799 ₽"},
    "life": {"title": "Навсегда", "days": None, "rub_label": "1499 ₽"},
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("report-system")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS payments (
            payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            telegram_charge_id TEXT,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            sub_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_key TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            expires_at TEXT,
            status TEXT NOT NULL,
            payment_id INTEGER,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS complaints (
            complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            target TEXT,
            reason TEXT,
            facts TEXT,
            links TEXT,
            draft TEXT,
            created_at TEXT NOT NULL
        )""")
        # Start subscription IDs at #12345 for a clean new database.
        c.execute("""INSERT OR IGNORE INTO sqlite_sequence(name, seq)
                     VALUES('subscriptions', 12344)""")
        c.execute("""INSERT OR IGNORE INTO sqlite_sequence(name, seq)
                     VALUES('payments', 0)""")
        c.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_owner(uid: int) -> bool:
    return OWNER_ID > 0 and uid == OWNER_ID


def is_manager(uid: int) -> bool:
    return MANAGER_ID > 0 and uid == MANAGER_ID


def is_staff(uid: int) -> bool:
    return is_owner(uid) or is_manager(uid)


def register_user(user):
    with db() as c:
        c.execute("""INSERT INTO users(user_id, username, first_name, joined_at)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username, first_name=excluded.first_name""",
            (user.id, user.username or "", user.first_name or "", now_iso()))


def main_keyboard(uid):
    rows = [
        [InlineKeyboardButton("🛡  Choc akkaynta", callback_data="complaint_start")],
        [InlineKeyboardButton("✦  Подписка и тарифы", callback_data="subscription")],
        [InlineKeyboardButton("⌁  Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")],
    ]
    if is_owner(uid):
        rows.append([InlineKeyboardButton("👑 Админ-панель", callback_data="admin")])
    elif is_manager(uid):
        rows.append([InlineKeyboardButton("🛠 Панель менеджера", callback_data="manager")])
    return InlineKeyboardMarkup(rows)


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⌂  Главное меню", callback_data="home")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        register_user(update.effective_user)
    context.user_data.pop("complaint", None)
    await update.effective_message.reply_text(
        "◈ REPORT SYSTEM\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "ТВОЙ ЦЕНТР УПРАВЛЕНИЯ\n\n"
        "Выбери раздел ниже. Все функции собраны в одном месте.",
        reply_markup=main_keyboard(update.effective_user.id),
    )


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    register_user(q.from_user)
    context.user_data.pop("complaint", None)
    await q.edit_message_text(
        "◈ REPORT SYSTEM\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "ТВОЙ ЦЕНТР УПРАВЛЕНИЯ\n\n"
        "Выбери раздел ниже. Все функции собраны в одном месте.",
        reply_markup=main_keyboard(q.from_user.id),
    )


async def subscription_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lines = [
        "✦ ПОДПИСКА",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "Выбери подходящий тариф.",
        "Оплата цифровых функций внутри Telegram — через Stars.",
        "",
        "Доступ активируется после подтверждения оплаты.",
    ]
    rows = []
    for key, plan in PLANS.items():
        rows.append([InlineKeyboardButton(
            f'✦ {plan["title"]}  ·  {STAR_PRICES[key]} ⭐',
            callback_data=f"buy:{key}"
        )])
    rows.append([InlineKeyboardButton("⌂  Главное меню", callback_data="home")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def send_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    q = update.callback_query
    await q.answer()
    if key not in PLANS:
        return
    plan = PLANS[key]
    stars = STAR_PRICES[key]
    payload = f"subscription:{key}:{q.from_user.id}:{int(datetime.now().timestamp())}"
    prices = [LabeledPrice(label=f"Подписка: {plan['title']}", amount=stars)]
    await context.bot.send_invoice(
        chat_id=q.message.chat_id,
        title=f"REPORT SYSTEM — {plan['title']}",
        description=f"Доступ к функциям REPORT SYSTEM на срок: {plan['title']}.",
        payload=payload,
        provider_token="",  # Required empty provider token for Telegram Stars.
        currency="XTR",
        prices=prices,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Назад", callback_data="subscription")
        ]]),
    )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    try:
        parts = query.invoice_payload.split(":")
        valid = len(parts) == 4 and parts[0] == "subscription"
        valid = valid and parts[1] in PLANS and int(parts[2]) == query.from_user.id
    except (ValueError, IndexError):
        valid = False
    await query.answer(ok=valid, error_message=None if valid else "Платёж не прошёл проверку. Открой меню подписки и попробуй снова.")


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    payment = msg.successful_payment
    try:
        kind, key, uid, _stamp = payment.invoice_payload.split(":")
        if kind != "subscription" or int(uid) != user.id or key not in PLANS:
            raise ValueError("Invalid payment payload")
    except (ValueError, TypeError):
        log.warning("Unexpected successful payment payload from user %s", user.id)
        await msg.reply_text("Платёж получен, но его данные требуют проверки администратором.")
        return

    with db() as c:
        cur = c.execute("""INSERT INTO payments
            (user_id,payload,currency,amount,status,telegram_charge_id,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (user.id, payment.invoice_payload, payment.currency, payment.total_amount,
             "confirmed", payment.telegram_payment_charge_id, now_iso()))
        payment_id = cur.lastrowid

        plan = PLANS[key]
        start_dt = datetime.now(timezone.utc)
        if plan["days"] is None:
            expires = None
        else:
            # Extend from the current expiry if a live subscription exists.
            row = c.execute("""SELECT expires_at FROM subscriptions
                WHERE user_id=? AND status='active' AND expires_at IS NOT NULL
                ORDER BY sub_id DESC LIMIT 1""", (user.id,)).fetchone()
            if row and row["expires_at"]:
                try:
                    old_exp = datetime.fromisoformat(row["expires_at"])
                    if old_exp > start_dt:
                        start_dt = old_exp
                except ValueError:
                    pass
            expires = (start_dt + timedelta(days=plan["days"])).isoformat(timespec="seconds")

        cur = c.execute("""INSERT INTO subscriptions
            (user_id,plan_key,starts_at,expires_at,status,payment_id,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (user.id, key, now_iso(), expires, "active", payment_id, now_iso()))
        sub_id = cur.lastrowid
        c.commit()

    expiry_text = "Без срока окончания" if expires is None else expires.replace("T", " ")[:16] + " UTC"
    await msg.reply_text(
        f"✅ Оплата подтверждена!\n\nПодписка: #{sub_id}\nТариф: {plan['title']}\n"
        f"Оплачено: {payment.total_amount} ⭐\nОкончание: {expiry_text}",
        reply_markup=main_keyboard(user.id),
    )
    if OWNER_ID > 0:
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"💳 Новая оплата\nПользователь: {user.full_name} (@{user.username or 'нет username'})\n"
                f"ID: {user.id}\nСумма: {payment.total_amount} {payment.currency}\n"
                f"Подписка: #{sub_id} · {plan['title']}"
            )
        except Exception:
            log.exception("Could not notify owner")


async def complaint_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["complaint"] = {"step": "target"}
    await q.edit_message_text(
        "🛡 ПОДГОТОВКА ОБРАЩЕНИЯ\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Шаг 1 из 3 · Материал\n\n"
        "Отправь @username или ссылку на публичный профиль/канал. "
        "Используй только реальные материалы, которые можно проверить.",
        reply_markup=back_keyboard(),
    )


async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.effective_message or not update.effective_message.text:
        return
    register_user(user)
    state = context.user_data.get("complaint")
    if not state:
        return
    text = update.effective_message.text.strip()
    step = state.get("step")
    if step == "target":
        state["target"] = text[:300]
        state["step"] = "reason"
        await update.effective_message.reply_text(
            "🛡 ПОДГОТОВКА ОБРАЩЕНИЯ\n\n"
            "Шаг 2 из 3 · Причина\n"
            "Выбери наиболее подходящую причину:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Мошенничество", callback_data="reason:Мошенничество")],
                [InlineKeyboardButton("Спам", callback_data="reason:Спам")],
                [InlineKeyboardButton("Выдаёт себя за другого", callback_data="reason:Выдаёт себя за другого")],
                [InlineKeyboardButton("Нарушение авторских прав", callback_data="reason:Нарушение авторских прав")],
                [InlineKeyboardButton("Другая причина", callback_data="reason:Другая причина")],
                [InlineKeyboardButton("⌂  Главное меню", callback_data="home")],
            ]),
        )
    elif step == "facts":
        state["facts"] = text[:2500]
        state["step"] = "links"
        await update.effective_message.reply_text(
            "🔗 ССЫЛКИ НА МАТЕРИАЛЫ\n\n"
            "Добавь ссылки на конкретные материалы, если они есть. "
            "Если ссылок нет, напиши «нет»."
        )
    elif step == "links":
        state["links"] = text[:1500]
        target = state.get("target", "")
        reason = state.get("reason", "")
        facts = state.get("facts", "")
        links = text
        draft = (
            "Здравствуйте. Прошу проверить следующий материал на предмет возможного нарушения правил платформы.\n\n"
            f"Профиль/материал: {target}\nПричина обращения: {reason}\n"
            f"Факты и описание: {facts}\nСсылки на материалы: {links}\n\n"
            "Прошу проверить указанные сведения и принять решение в соответствии с правилами платформы. "
            "Я подтверждаю, что описал(а) известные мне факты добросовестно."
        )
        with db() as c:
            c.execute("""INSERT INTO complaints
                (user_id,target,reason,facts,links,draft,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (user.id, target, reason, facts, links, draft, now_iso()))
            c.commit()
        context.user_data.pop("complaint", None)
        await update.effective_message.reply_text(
            "✓ ЧЕРНОВИК ГОТОВ\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Проверь факты и ссылки перед отправкой. Это только черновик; бот не отправляет жалобу автоматически.\n\n"
            "—— ТЕКСТ ОБРАЩЕНИЯ ——\n" + draft,
            reply_markup=main_keyboard(user.id),
        )


async def reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    state = context.user_data.get("complaint")
    if not state or state.get("step") != "reason":
        await q.edit_message_text("Начни подготовку обращения из главного меню.", reply_markup=back_keyboard())
        return
    reason = q.data.split(":", 1)[1]
    state["reason"] = reason
    state["step"] = "facts"
    await q.edit_message_text(
        "Опиши конкретные факты: что произошло, когда и где. Не добавляй догадки или неподтверждённые обвинения."
    )


def money_summary():
    with db() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        active = c.execute("""SELECT COUNT(*) n FROM subscriptions WHERE status='active'
            AND (expires_at IS NULL OR expires_at > ?)""", (now_iso(),)).fetchone()["n"]
        payments = c.execute("""SELECT currency, SUM(amount) total, COUNT(*) n
            FROM payments WHERE status='confirmed' GROUP BY currency""").fetchall()
        pending = c.execute("SELECT COUNT(*) n FROM payments WHERE status='pending'").fetchone()["n"]
    payment_lines = [f"• {r['total']} {r['currency']} ({r['n']} платежей)" for r in payments] or ["• Пока нет подтверждённых платежей"]
    return users, active, pending, "\n".join(payment_lines)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_owner(q.from_user.id):
        await q.edit_message_text("⛔ Нет доступа.", reply_markup=back_keyboard())
        return
    users, active, pending, totals = money_summary()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Найти пользователя", callback_data="admin_user_search")],
        [InlineKeyboardButton("📊 Обновить статистику", callback_data="admin")],
        [InlineKeyboardButton("🧾 Последние подписки", callback_data="admin_subs")],
        [InlineKeyboardButton("🛠 Назначение менеджера", callback_data="admin_manager_help")],
        [InlineKeyboardButton("⌂  Главное меню", callback_data="home")],
    ])
    await q.edit_message_text(
        "◈ АДМИН-ПАНЕЛЬ\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 Пользователей: {users}\n"
        f"✦ Активных подписок: {active}\n"
        f"◷ Ожидают проверки: {pending}\n\n"
        f"💳 ПОДТВЕРЖДЁННЫЕ ПЛАТЕЖИ\n{totals}\n\n"
        "Суммы отображаются отдельно по каждой валюте; автоматического пересчёта в рубли нет.",
        reply_markup=kb,
    )


async def manager_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_staff(q.from_user.id):
        await q.edit_message_text("⛔ Нет доступа.", reply_markup=back_keyboard())
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Найти пользователя", callback_data="staff_user_search")],
        [InlineKeyboardButton("🧾 Последние подписки", callback_data="staff_subs")],
        [InlineKeyboardButton("📨 Последние обращения", callback_data="staff_complaints")],
        [InlineKeyboardButton("⌂  Главное меню", callback_data="home")],
    ])
    await q.edit_message_text(
        "◈ ПАНЕЛЬ МЕНЕДЖЕРА\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Рабочие инструменты\n"
        "• Просмотр пользователей\n"
        "• История подписок\n"
        "• Последние обращения\n\n"
        "Доступ ограничен назначенными правами.",
        reply_markup=kb,
    )


async def ask_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_staff(q.from_user.id):
        await q.edit_message_text("⛔ Нет доступа.", reply_markup=back_keyboard())
        return
    context.user_data["search_user"] = True
    await q.edit_message_text("Отправь Telegram ID пользователя (только цифры).", reply_markup=back_keyboard())


async def find_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.pop("search_user", False):
        return
    if not is_staff(update.effective_user.id):
        return
    raw = update.effective_message.text.strip()
    if not raw.isdigit():
        await update.effective_message.reply_text("ID должен состоять из цифр. Попробуй ещё раз.")
        context.user_data["search_user"] = True
        return
    uid = int(raw)
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        pays = c.execute("""SELECT currency, SUM(amount) total, COUNT(*) n FROM payments
            WHERE user_id=? AND status='confirmed' GROUP BY currency""", (uid,)).fetchall()
        subs = c.execute("""SELECT sub_id,plan_key,status,expires_at FROM subscriptions
            WHERE user_id=? ORDER BY sub_id DESC LIMIT 10""", (uid,)).fetchall()
    if not u:
        await update.effective_message.reply_text("Пользователь не найден в базе бота.")
        return
    paid = "\n".join(f"• {p['total']} {p['currency']} ({p['n']} платежей)" for p in pays) or "• Нет подтверждённых платежей"
    sub_lines = "\n".join(
        f"• #{s['sub_id']} — {PLANS.get(s['plan_key'], {}).get('title', s['plan_key'])}; {s['status']}; "
        f"{s['expires_at'] or 'без срока'}" for s in subs
    ) or "• Подписок нет"
    await update.effective_message.reply_text(
        f"◈ КАРТОЧКА ПОЛЬЗОВАТЕЛЯ\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Имя: {u['first_name']}\n"
        f"Username: @{u['username'] or 'не указан'}\n"
        f"Telegram ID: {uid}\n\n"
        f"💳 ОПЛАТЫ ПО ВАЛЮТАМ\n{paid}\n\n"
        f"✦ ПОСЛЕДНИЕ ПОДПИСКИ\n{sub_lines}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⌂  Главное меню", callback_data="home")]]),
    )


async def recent_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_staff(q.from_user.id):
        await q.edit_message_text("⛔ Нет доступа.", reply_markup=back_keyboard())
        return
    with db() as c:
        rows = c.execute("""SELECT s.*,u.username FROM subscriptions s
            LEFT JOIN users u ON u.user_id=s.user_id ORDER BY s.sub_id DESC LIMIT 15""").fetchall()
    if not rows:
        body = "Подписок пока нет."
    else:
        body = "\n".join(
            f"#{r['sub_id']} · {r['user_id']} (@{r['username'] or 'нет username'}) · "
            f"{PLANS.get(r['plan_key'], {}).get('title', r['plan_key'])} · {r['status']}"
            for r in rows
        )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin" if is_owner(q.from_user.id) else "manager")]])
    await q.edit_message_text("🧾 Последние подписки\n\n" + body, reply_markup=kb)


async def recent_complaints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_staff(q.from_user.id):
        await q.edit_message_text("⛔ Нет доступа.", reply_markup=back_keyboard())
        return
    with db() as c:
        rows = c.execute("SELECT * FROM complaints ORDER BY complaint_id DESC LIMIT 10").fetchall()
    body = "\n\n".join(
        f"Обращение #{r['complaint_id']} · пользователь {r['user_id']}\n"
        f"Цель: {r['target']}\nПричина: {r['reason']}\nФакты: {r['facts'][:250]}"
        for r in rows
    ) or "Обращений пока нет."
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="manager")]])
    await q.edit_message_text(body[:3900], reply_markup=kb)


async def manager_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_owner(q.from_user.id):
        await q.edit_message_text("⛔ Нет доступа.", reply_markup=back_keyboard())
        return
    await q.edit_message_text(
        "Чтобы назначить менеджера, задай переменную окружения MANAGER_ID равной его Telegram ID и перезапусти бота.\n"
        "Не выдавай доступ по username: ID надёжнее.",
        reply_markup=back_keyboard(),
    )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    if data == "home":
        return await home_callback(update, context)
    if data == "subscription":
        return await subscription_menu(update, context)
    if data.startswith("buy:"):
        return await send_invoice(update, context, data.split(":", 1)[1])
    if data == "complaint_start":
        return await complaint_start(update, context)
    if data.startswith("reason:"):
        return await reason_callback(update, context)
    if data == "admin":
        return await admin_panel(update, context)
    if data == "manager":
        return await manager_panel(update, context)
    if data in ("admin_user_search", "staff_user_search"):
        return await ask_user_search(update, context)
    if data in ("admin_subs", "staff_subs"):
        return await recent_subs(update, context)
    if data == "staff_complaints":
        return await recent_complaints(update, context)
    if data == "admin_manager_help":
        return await manager_help(update, context)
    await q.answer("Неизвестная команда.", show_alert=True)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("search_user"):
        return await find_user(update, context)
    return await text_input(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled exception: %s", context.error)


def main():
    if not BOT_TOKEN:
        raise SystemExit("ERROR: set BOT_TOKEN environment variable first.")
    if OWNER_ID <= 0:
        raise SystemExit("ERROR: set OWNER_ID to your numeric Telegram user ID.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    log.info("REPORT SYSTEM bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
