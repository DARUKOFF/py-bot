import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram import F
import asyncpg
from config import BOT_TOKEN, DATABASE_URL, OPERATORS_CHAT_ID

# logging
logging.basicConfig(level=logging.INFO)

# init bot
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# DB connect
db_pool = None

async def create_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

# FSM
class RequestForm(StatesGroup):
    waiting_for_request_type = State()
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_message = State()

# /start command
@dp.message(Command(commands=['start']))
async def send_welcome(message: types.Message):
    start_buttons = [
        InlineKeyboardButton(text="Создать заявку", callback_data="create_request"),
        InlineKeyboardButton(text="О нас", callback_data="about_us")
    ]
    start_kb = InlineKeyboardMarkup(inline_keyboard=[start_buttons])
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=start_kb)

# button answer
@dp.callback_query(F.data.in_("about_us"))
async def about_us(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer("Информация о разработчике: ... (тут ваш текст)")

# create request 
@dp.callback_query(F.data.in_("create_request"))
async def create_request(callback_query: types.CallbackQuery, state: FSMContext):
    request_buttons = [
        KeyboardButton(text="по документам"),
        KeyboardButton(text="по номеру"),
        KeyboardButton(text="по заявке")
    ]
    request_kb = ReplyKeyboardMarkup(keyboard=[request_buttons], resize_keyboard=True)
    await callback_query.answer()
    await callback_query.message.answer("Выберите тип заявки:", reply_markup=request_kb)
    await state.set_state(RequestForm.waiting_for_request_type)

# start to create new request
@dp.message(RequestForm.waiting_for_request_type)
async def ask_for_name(message: types.Message, state: FSMContext):
    request_type = message.text
    if request_type not in ["по документам", "по номеру", "по заявке"]:
        await message.answer("Пожалуйста, выберите один из предложенных типов заявок.")
        return

    await state.update_data(request_type=request_type)
    
    user_id = message.from_user.id
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            user_record = await connection.fetchrow(
                "SELECT fio, phone FROM users_info WHERE user_id = $1",
                user_id
            )
            
            if user_record:
                fio = user_record['fio']
                phone = user_record['phone']
                if fio and phone:
                    await state.update_data(fio=fio, phone=phone)
                    await message.answer(f"Привет, {fio}! Тел: {phone}. Напиши, пожалуйста, запрос.")
                    await message.answer("Опишите вашу проблему:")
                    await state.set_state(RequestForm.waiting_for_message)
                    return

    await message.answer("Введите ваше ФИО:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(RequestForm.waiting_for_name)

# check name
@dp.message(RequestForm.waiting_for_name)
async def check_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip()
    user_id = message.from_user.id
    
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            user_record = await connection.fetchrow(
                "SELECT user_id FROM users_info WHERE fio = $1",
                fio
            )
            
            if user_record:
                if user_record['user_id'] is not None and user_record['user_id'] != user_id:
                    await message.answer("Это ФИО уже используется другим пользователем. Пожалуйста, используйте другое ФИО.")
                    await state.clear()
                    return
                elif user_record['user_id'] is None:
                    await connection.execute(
                        "UPDATE users_info SET user_id = $1 WHERE fio = $2",
                        user_id, fio
                    )
            else:
                await message.answer("ФИО не найдено в базе данных. Пожалуйста, введите корректное ФИО:")
                return
    
    await state.update_data(fio=fio)
    await message.answer("Введите ваш номер телефона:")
    await state.set_state(RequestForm.waiting_for_phone)

# save phone and proceed to message
@dp.message(RequestForm.waiting_for_phone)
async def ask_for_message(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    user_data = await state.get_data()
    fio = user_data.get('fio')
    
    if not fio:
        await message.answer("ФИО не найдено в данных. Пожалуйста, начните процесс создания заявки заново.")
        await state.clear()
        return

    user_id = message.from_user.id
    
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO users_info (user_id, fio, phone) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO UPDATE SET phone = EXCLUDED.phone",
                user_id, fio, phone
            )
    
    await state.update_data(phone=phone)
    await message.answer("Опишите вашу проблему:")
    await state.set_state(RequestForm.waiting_for_message)

# save request to db and notify operators
@dp.message(RequestForm.waiting_for_message)
async def save_request(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    request_type = user_data['request_type']
    fio = user_data['fio']
    phone = user_data['phone']
    problem_description = message.text.strip()
    user_id = message.from_user.id
    
    if request_type == "по документам":
        table_name = "requests_documents"
    elif request_type == "по номеру":
        table_name = "requests_number"
    elif request_type == "по заявке":
        table_name = "requests_request"
    else:
        await message.answer("Неизвестный тип заявки. Попробуйте снова.")
        await state.clear()
        return
    
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            record = await connection.fetchrow(
                f"""
                INSERT INTO {table_name} (user_id, fio, phone, message, time_submitted)
                VALUES ($1, $2, $3, $4, NOW())
                RETURNING id
                """,
                user_id, fio, phone, problem_description
            )
            request_id = record['id']
    
    operator_message = await bot.send_message(
        OPERATORS_CHAT_ID, 
        f"Новая заявка от пользователя {fio}.\nТип заявки: {request_type}\nТелефон: {phone}\nОписание проблемы: {problem_description}\n\nОтветьте на это сообщение, чтобы ответ был переслан пользователю."
    )
    
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                f"UPDATE {table_name} SET operator_message_id = $1 WHERE id = $2",
                operator_message.message_id, request_id
            )
    
    await message.answer("Спасибо! Ваша заявка сохранена и отправлена операторам.")
    await state.clear()

# operator answer to request
@dp.message(F.chat.id == OPERATORS_CHAT_ID)
async def forward_operator_reply(message: types.Message):
    logging.info(f"Received a message in the operators' chat: {message.text}")

    if not message.reply_to_message:
        logging.info("Message is not a reply to any other message.")
        return

    bot_user = await bot.get_me()
    if message.reply_to_message.from_user.id != bot_user.id:
        logging.info("Message is not a reply to the bot's message.")
        return

    async with db_pool.acquire() as connection:
        tables = ["requests_documents", "requests_number", "requests_request"]
        user_id = None
        request_id = None
        table_found = None

        for table in tables:
            request = await connection.fetchrow(
                f"SELECT user_id, id FROM {table} WHERE operator_message_id = $1",
                message.reply_to_message.message_id
            )
            if request:
                user_id = request['user_id']
                request_id = request['id']
                table_found = table
                break

    if user_id and table_found:
        await bot.send_message(user_id, f"Ответ от оператора:\n{message.text}")
        logging.info(f"Forwarded operator's message to user {user_id}")

        async with db_pool.acquire() as connection:
            await connection.execute(
                f"UPDATE {table_found} SET time_answered = NOW() WHERE id = $1",
                request_id
            )
    else:
        logging.warning(f"No corresponding user found for the operator's reply message ID {message.reply_to_message.message_id}")


# start work
async def on_startup():
    await create_db_pool()
    logging.info("Bot has started.")

async def on_shutdown():
    await db_pool.close()
    logging.info("Database pool closed.")

async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()

if __name__ == '__main__':
    asyncio.run(main())


    