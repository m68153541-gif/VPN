import asyncio
import logging
import aiosqlite
import uuid
import aiohttp
import os
from datetime import datetime, timedelta
from threading import Thread
from http.server import SimpleHTTPRequestHandler, HTTPServer
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==========================================
# НАСТРОЙКИ
# ==========================================
BOT_TOKEN = "8555474321:AAEOjb-ar8a0gC34OB7Fi4vT0153GKa2Xz4"
ADMIN_ID = 8513244807
SUPPORT_USERNAME = "@Grty24"

API_URL = "http://de7.h1cloud.net:25288/api"
API_TOKEN = "c35c9cff19894eb48310c353de63011756f5ff47145a44139e544633f3993f23"
INBOUND_ID = "ib_c71acbe9c8" 

API_HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

PRICES = {
    "1_month": {"name": "1 Месяц", "price": 110, "days": 30},
    "3_months": {"name": "3 Месяца", "price": 290, "days": 90},
    "6_months": {"name": "6 Месяцев", "price": 500, "days": 180}
}
DEVICE_PRICE = 20
STAR_TO_RUB = 1.5

# Путь к базе данных (для Render важно сохранять в /data)
DB_PATH = os.getenv("DB_PATH", "vpn_bot.db")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Словарь для хранения состояния (ожидаем ли мы ввод звезд от юзера)
user_states = {}

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=20) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                balance_rub REAL DEFAULT 0.0,
                client_name TEXT,
                expires_at INTEGER,
                device_limit INTEGER DEFAULT 1,
                sub_url TEXT,
                trial_used BOOLEAN DEFAULT 0
            )
        """)
        await db.commit()

async def get_user(tg_id):
    async with aiosqlite.connect(DB_PATH, timeout=20) as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (tg_id,)) as cursor:
            return await cursor.fetchone()

async def update_user_balance(tg_id, amount):
    async with aiosqlite.connect(DB_PATH, timeout=20) as db:
        await db.execute("UPDATE users SET balance_rub = balance_rub + ? WHERE telegram_id = ?", (amount, tg_id))
        await db.commit()
    logging.info(f"[BALANCE LOG] User {tg_id}: {amount:+.2f} ₽")

async def set_user_vpn(tg_id, client_name, expires_at, device_limit, sub_url):
    async with aiosqlite.connect(DB_PATH, timeout=20) as db:
        await db.execute(
            "UPDATE users SET client_name = ?, expires_at = ?, device_limit = ?, sub_url = ? WHERE telegram_id = ?",
            (client_name, expires_at, device_limit, sub_url, tg_id)
        )
        await db.commit()

async def set_trial_used(tg_id):
    async with aiosqlite.connect(DB_PATH, timeout=20) as db:
        await db.execute("UPDATE users SET trial_used = 1 WHERE telegram_id = ?", (tg_id,))
        await db.commit()

# ==========================================
# API ПАНЕЛИ
# ==========================================
async def api_create_client(client_name: str, days: int, device_limit: int):
    payload = {
        "name": client_name,
        "days": days,
        "device_limit": device_limit,
        "manual": True,
        "inbound_ids": [INBOUND_ID]
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_URL}/create", json=payload, headers=API_HEADERS) as response:
            try:
                data = await response.json()
            except:
                data = await response.text()
            return data

# ==========================================
# МЕНЮ
# ==========================================
def get_main_menu(tg_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Купить VPN", callback_data="menu_buy")
    builder.button(text="🔑 Мой VPN", callback_data="menu_my_vpn")
    builder.button(text="💳 Баланс", callback_data="menu_balance")
    builder.button(text="💎 Пополнить", callback_data="menu_topup")
    builder.button(text="🎁 Пробный период", callback_data="menu_trial")
    builder.button(text="🆘 Помощь", url=f"https://t.me/{SUPPORT_USERNAME[1:]}")
    if tg_id == ADMIN_ID:
        builder.button(text="👑 Админ Панель", callback_data="admin_panel")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        async with aiosqlite.connect(DB_PATH, timeout=20) as db:
            await db.execute("INSERT INTO users (telegram_id) VALUES (?)", (message.from_user.id,))
            await db.commit()
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Это **Minion VPN** — новый, быстрый и качественный VPN.\n"
        "Выберите действие в меню ниже:",
        reply_markup=get_main_menu(message.from_user.id), parse_mode="Markdown"
    )

# ==========================================
# ПОКУПКА
# ==========================================
@dp.callback_query(F.data == "menu_buy")
async def process_buy(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    for key, data in PRICES.items():
        builder.button(text=f"{data['name']} - {data['price']}₽", callback_data=f"tariff_{key}")
    builder.button(text="⬅️ Назад", callback_data="back_to_main")
    builder.adjust(1)
    await callback.message.edit_text("Выберите срок подписки:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("tariff_"))
async def process_tariff(callback: CallbackQuery):
    tariff_key = callback.data.split("_")[1]
    t_data = PRICES[tariff_key]
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        total_cost = t_data['price'] + (i - 1) * DEVICE_PRICE
        builder.button(text=f"{i} устр. - {total_cost}₽", callback_data=f"buy_{tariff_key}_{i}")
    builder.button(text="⬅️ Назад", callback_data="menu_buy")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Тариф: **{t_data['name']}**\nСтоимость: {t_data['price']}₽ (1 устройство)\n"
        f"Доп. устройство: +{DEVICE_PRICE}₽\n\nВыберите количество устройств:", 
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("buy_"))
async def process_purchase(callback: CallbackQuery):
    _, tariff_key, devices = callback.data.split("_")
    devices = int(devices)
    t_data = PRICES[tariff_key]
    total_cost = t_data['price'] + (devices - 1) * DEVICE_PRICE
    user = await get_user(callback.from_user.id)
    balance = user[1] if user else 0
    
    if balance >= total_cost:
        await update_user_balance(callback.from_user.id, -total_cost)
        
        unique_suffix = uuid.uuid4().hex[:6]
        client_name = f"user_{callback.from_user.id}_{unique_suffix}"
        
        api_data = await api_create_client(client_name, t_data['days'], devices)
        
        if isinstance(api_data, dict) and api_data.get("ok"):
            client_info = api_data.get("client", {})
            expires_at = client_info.get("expires_at")
            sub_url = client_info.get("sub_url")
            
            if not expires_at:
                expires_at = int((datetime.now() + timedelta(days=t_data['days'])).timestamp())
            
            await set_user_vpn(callback.from_user.id, client_name, expires_at, devices, sub_url)
            dt = datetime.fromtimestamp(expires_at)
            
            await callback.message.edit_text(
                f"✅ **Оплата прошла успешно!**\n\n"
                f"Списано: {total_cost}₽\n"
                f"Устройств: {devices}\n"
                f"Подписка активна до: {dt.strftime('%d.%m.%Y %H:%M')} (МСК)\n\n"
                "Перейдите в раздел 'Мой VPN', чтобы получить ссылку.",
                reply_markup=get_main_menu(callback.from_user.id), parse_mode="Markdown"
            )
        else:
            await update_user_balance(callback.from_user.id, total_cost)
            print(f"[DEBUG] PURCHASE ERROR: {api_data}")
            await callback.message.edit_text(
                f"❌ Ошибка при создании VPN. Деньги возвращены на баланс.",
                reply_markup=get_main_menu(callback.from_user.id)
            )
    else:
        await callback.answer(f"Недостаточно средств! Нужно {total_cost}₽, у вас {balance:.2f}₽", show_alert=True)

# ==========================================
# ПРОБНЫЙ ПЕРИОД
# ==========================================
@dp.callback_query(F.data == "menu_trial")
async def process_trial(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    
    # Проверяем, использовал ли уже пользователь пробный период
    if user and user[6]:  # trial_used = True
        await callback.answer("❌ Вы уже использовали пробный период.", show_alert=True)
        return
        
    # Проверяем, нет ли уже активной подписки
    if user and user[2] and user[3] > int(datetime.now().timestamp()):
        await callback.answer("❌ У вас уже есть активная подписка.", show_alert=True)
        return
        
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Активировать пробный период", callback_data="trial_confirm")
    builder.button(text="⬅️ Назад", callback_data="back_to_main")
    await callback.message.edit_text(
        "🎁 **Пробный период**\n\n"
        "Мы дарим вам **24 часа** бесплатного доступа к VPN.\n"
        "1 устройство, все доступные локации.\n\n"
        "Нажмите кнопку ниже, чтобы активировать:",
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )

@dp.callback_query(F.data == "trial_confirm")
async def process_trial_confirm(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    
    if user and user[6]:
        await callback.answer("❌ Вы уже использовали пробный период.", show_alert=True)
        return
    
    unique_suffix = uuid.uuid4().hex[:6]
    client_name = f"trial_{callback.from_user.id}_{unique_suffix}"
    
    # Создаем клиента на 1 день (24 часа)
    api_data = await api_create_client(client_name, 1, 1)
    
    if isinstance(api_data, dict) and api_data.get("ok"):
        client_info = api_data.get("client", {})
        expires_at = client_info.get("expires_at")
        sub_url = client_info.get("sub_url")
        
        if not expires_at:
            expires_at = int((datetime.now() + timedelta(days=1)).timestamp())
            
        await set_user_vpn(callback.from_user.id, client_name, expires_at, 1, sub_url)
        await set_trial_used(callback.from_user.id)
        
        dt = datetime.fromtimestamp(expires_at)
        await callback.message.edit_text(
            f"🎉 **Пробный период активирован!**\n\n"
            f"Устройств: 1\n"
            f"Действует до: {dt.strftime('%d.%m.%Y %H:%M')} (МСК)\n\n"
            "Перейдите в раздел 'Мой VPN', чтобы получить ссылку.",
            reply_markup=get_main_menu(callback.from_user.id), parse_mode="Markdown"
        )
    else:
        print(f"[DEBUG] TRIAL ERROR: {api_data}")
        await callback.message.edit_text(
            f"❌ Ошибка при активации пробного периода. Попробуйте позже.",
            reply_markup=get_main_menu(callback.from_user.id)
        )

# ==========================================
# БАЛАНС И ПОПОЛНЕНИЕ (ЗВЕЗДЫ)
# ==========================================
@dp.callback_query(F.data == "menu_balance")
async def process_balance(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    balance = user[1] if user else 0
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Пополнить", callback_data="menu_topup")
    builder.button(text="⬅️ Назад", callback_data="back_to_main")
    await callback.message.edit_text(f"💳 **Ваш баланс:** {balance:.2f} ₽", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "menu_topup")
async def process_topup(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Ввести количество звезд", callback_data="topup_enter_stars")
    builder.button(text="⬅️ Назад", callback_data="back_to_main")
    builder.adjust(1)
    
    text = (
        f"💎 **Пополнение баланса**\n\n"
        f"Вы можете пополнить баланс с помощью Telegram Stars (XTR).\n"
        f"Курс: 1 Звезда = {STAR_TO_RUB} ₽\n\n"
        f"Нажмите кнопку ниже, чтобы ввести количество звезд."
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "topup_enter_stars")
async def process_enter_stars(callback: CallbackQuery):
    user_states[callback.from_user.id] = "waiting_for_stars"
    await callback.message.edit_text(
        "Введите количество звезд, на которое хотите пополнить баланс (целое число от 1 до 10000):\n\n"
        "Например: `50`",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(F.text.regexp(r"^\d+$"))
async def process_stars_input(message: types.Message):
    if user_states.get(message.from_user.id) != "waiting_for_stars":
        return
        
    stars_amount = int(message.text)
    
    if stars_amount < 1 or stars_amount > 10000:
        await message.answer("❌ Пожалуйста, введите число от 1 до 10000.")
        return
    
    user_states.pop(message.from_user.id, None)
    rub_amount = stars_amount * STAR_TO_RUB
    
    await message.answer_invoice(
        title="Пополнение баланса Minion VPN",
        description=f"Пополнение на {rub_amount:.2f} ₽ за {stars_amount} звезд",
        payload=f"topup_{stars_amount}",
        currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=stars_amount)]
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    try:
        stars = message.successful_payment.total_amount
        rub = stars * STAR_TO_RUB
        
        logging.info(f"[PAYMENT] User {message.from_user.id} paid {stars} stars. Crediting {rub} RUB.")
        
        user = await get_user(message.from_user.id)
        if not user:
            async with aiosqlite.connect(DB_PATH, timeout=20) as db:
                await db.execute("INSERT INTO users (telegram_id) VALUES (?)", (message.from_user.id,))
                await db.commit()
        
        await update_user_balance(message.from_user.id, rub)
        
        updated_user = await get_user(message.from_user.id)
        new_balance = updated_user[1] if updated_user else 0
        
        await message.answer(
            f"✅ **Баланс успешно пополнен!**\n\n"
            f"Получено звезд: {stars}\n"
            f"Зачислено на баланс: {rub:.2f} ₽\n"
            f"Текущий баланс: {new_balance:.2f} ₽",
            reply_markup=get_main_menu(message.from_user.id), parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"[PAYMENT ERROR] {e}")
        await message.answer("❌ Произошла ошибка при начислении баланса. Обратитесь в поддержку.")

# ==========================================
# МОЙ VPN
# ==========================================
@dp.callback_query(F.data == "menu_my_vpn")
async def process_my_vpn(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    if not user or not user[2]:
        text = "🔴 Подписка еще не активирована."
        builder.button(text="🛒 Купить VPN", callback_data="menu_buy")
        builder.button(text="🎁 Пробный период", callback_data="menu_trial")
    else:
        client_name, expires_at, devices, sub_url = user[2], user[3], user[4], user[5]
        now = int(datetime.now().timestamp())
        if expires_at > now:
            dt = datetime.fromtimestamp(expires_at)
            diff = dt - datetime.now()
            days = diff.days
            hours = diff.seconds // 3600
            text = (f"🟢 **Статус: Активно**\n"
                    f"🌍 Локации: Все доступные\n"
                    f"📅 Действует до: {dt.strftime('%d.%m.%Y %H:%M')} (МСК)\n"
                    f"⏳ Осталось: {days} д. {hours} ч.\n")
            builder.button(text="🔗 Ссылка на подписку", callback_data="show_sub_link")
            builder.button(text="➕ Продлить / Докупить", callback_data="menu_buy")
        else:
            text = "🔴 **Подписка истекла.**\nПожалуйста, продлите её."
            builder.button(text="🛒 Продлить", callback_data="menu_buy")
    builder.button(text="⬅️ Назад", callback_data="back_to_main")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "show_sub_link")
async def process_show_sub_link(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user or not user[5]:
        await callback.answer("Ссылка не найдена. Попробуйте продлить подписку.", show_alert=True)
        return
    
    sub_url = user[5]
    await callback.message.answer(
        f"🔗 **Ваша ссылка:**\n\n`{sub_url}`\n\nСкопируйте её и вставьте в приложение (v2rayNG, Hiddify и т.д.)",
        parse_mode="Markdown"
    )
    await callback.answer()

# ==========================================
# АДМИН ПАНЕЛЬ
# ==========================================
@dp.callback_query(F.data == "admin_panel")
async def process_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("У вас нет доступа.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Сгенерировать ключ", callback_data="admin_gen_key")
    builder.button(text="⬅️ Назад", callback_data="back_to_main")
    await callback.message.edit_text("👑 **Админ-панель**\nВыберите действие:", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_gen_key")
async def process_admin_gen_key(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="1 Месяц (1 устр)", callback_data="admin_create_1")
    builder.button(text="3 Месяца (1 устр)", callback_data="admin_create_3")
    builder.button(text="⬅️ Назад", callback_data="admin_panel")
    builder.adjust(1)
    await callback.message.edit_text("Выберите срок для генерации ключа:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("admin_create_"))
async def process_admin_create(callback: CallbackQuery):
    days = 30 if callback.data == "admin_create_1" else 90
    unique_suffix = uuid.uuid4().hex[:8]
    client_name = f"gift_{unique_suffix}"
    
    api_data = await api_create_client(client_name, days, 1)
    
    if isinstance(api_data, dict) and api_data.get("ok"):
        client_info = api_data.get("client", {})
        sub_url = client_info.get("sub_url")
        
        if sub_url:
            await callback.message.answer(f"✅ Ключ на {days} дней:\n\n`{sub_url}`", parse_mode="Markdown")
        else:
            await callback.message.answer(f"✅ Ключ создан, но API не вернуло ссылку.\nОтвет: {api_data}")
    else:
        print(f"[DEBUG] ADMIN CREATE ERROR: {api_data}")
        await callback.message.answer(f"❌ Ошибка генерации. Проверьте консоль.")

@dp.callback_query(F.data == "back_to_main")
async def process_back(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu(callback.from_user.id))

# ==========================================
# ЗАГЛУШКА ДЛЯ RENDER (ВЕБ-СЕРВЕР)
# ==========================================
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    logging.info(f"Запущен фейковый веб-сервер на порту {port} (для Render)")
    server.serve_forever()

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await init_db()
    t = Thread(target=run_dummy_server)
    t.daemon = True
    t.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
