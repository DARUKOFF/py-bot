import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
import asyncpg
from config import BOT_TOKEN, DATABASE_URL, OPERATORS_CHAT_ID

# Логирование
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Подключение к базе данных
# async def create_db_pool():
#     return await asyncpg.create_pool(DATABASE_URL)

db_pool = None

class RequestForm(StatesGroup):
    waiting_for_request_type = State()
    waiting_for_name = State()
    waiting_for_phone = State()

@dp.message(Command(commands=['start']))
async def send_welcome(message: types.Message):
    start_buttons = [
        InlineKeyboardButton(text="Создать заявку", callback_data="create_request"),
        InlineKeyboardButton(text="О нас", callback_data="about_us")
    ]
    start_kb = InlineKeyboardMarkup(inline_keyboard=[start_buttons])
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=start_kb)

@dp.callback_query(F.data.in_("about_us"))
async def about_us(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer("Информация о разработчике: ... (тут ваш текст)")

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

@dp.message(RequestForm.waiting_for_request_type)
async def ask_for_name(message: types.Message, state: FSMContext):
    await state.update_data(request_type=message.text)
    await message.answer("Введите ваше ФИО:")
    await state.set_state(RequestForm.waiting_for_name)

@dp.message(RequestForm.waiting_for_name)
async def ask_for_phone(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите ваш номер телефона:")
    await state.set_state(RequestForm.waiting_for_phone)

@dp.message(RequestForm.waiting_for_phone)
async def save_request(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    request_type = user_data['request_type']
    name = user_data['name']
    phone = message.text
    user_id = message.from_user.id

    async with db_pool.acquire() as connection:
        record = await connection.fetchrow(
            "INSERT INTO requests (user_id, name, phone, request_type) VALUES ($1, $2, $3, $4) RETURNING id",
            user_id, name, phone, request_type
        )
        request_id = record['id']
    
    operator_message = await bot.send_message(
        OPERATORS_CHAT_ID, 
        f"Новая заявка от пользователя {name}.\nТип заявки: {request_type}\nТелефон: {phone}\n\nОтветьте на это сообщение, чтобы ответ был переслан пользователю."
    )

    async with db_pool.acquire() as connection:
        await connection.execute(
            "UPDATE requests SET operator_message_id=$1 WHERE id=$2",
            operator_message.message_id, request_id
        )
    
    await message.answer("Спасибо! Ваша заявка сохранена и отправлена операторам.")
    await state.clear()

@dp.message(lambda message: message.chat.id == OPERATORS_CHAT_ID)
async def forward_operator_reply(message: types.Message):
    async with db_pool.acquire() as connection:
        request = await connection.fetchrow(
            "SELECT user_id FROM requests WHERE operator_message_id=$1",
            message.reply_to_message.message_id
        )

    if request:
        user_id = request['user_id']
        await bot.send_message(user_id, f"Ответ от оператора:\n{message.text}")

async def on_startup():
    global db_pool
    # db_pool = await create_db_pool()

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())