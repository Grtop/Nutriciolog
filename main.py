import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Command
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
import openai
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Состояния для сбора данных
class UserData(StatesGroup):
    gender = State()
    age = State()
    weight = State()
    height = State()
    activity = State()
    goal = State()

# Главное меню
main_menu = ReplyKeyboardMarkup(resize_keyboard=True)
main_menu.add(KeyboardButton("1. Заполнить физические данные здоровья"))
main_menu.add(KeyboardButton("2. Расчет калорийности"))
main_menu.add(KeyboardButton("3. Расчет меню питания"))

@dp.message_handler(Command("start"))
async def start(message: types.Message):
    await message.answer("Привет! Я твой нутрициолог-ассистент. Выбери опцию:", reply_markup=main_menu)

@dp.message_handler(lambda message: message.text == "1. Заполнить физические данные здоровья")
async def fill_data(message: types.Message):
    await message.answer("Укажите Ваш пол (мужчина/женщина):")
    await UserData.gender.set()

@dp.message_handler(state=UserData.gender)
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text.lower())
    await message.answer("Укажите Ваш возраст:")
    await UserData.age.set()

@dp.message_handler(state=UserData.age)
async def process_age(message: types.Message, state: FSMContext):
    await state.update_data(age=int(message.text))
    await message.answer("Укажите Ваш вес (кг):")
    await UserData.weight.set()

@dp.message_handler(state=UserData.weight)
async def process_weight(message: types.Message, state: FSMContext):
    await state.update_data(weight=float(message.text))
    await message.answer("Укажите Ваш рост (см):")
    await UserData.height.set()

@dp.message_handler(state=UserData.height)
async def process_height(message: types.Message, state: FSMContext):
    await state.update_data(height=float(message.text))
    await message.answer("Укажите уровень физической активности (низкий/средний/высокий):")
    await UserData.activity.set()

@dp.message_handler(state=UserData.activity)
async def process_activity(message: types.Message, state: FSMContext):
    await state.update_data(activity=message.text.lower())
    await message.answer("Выберете цель (поддерживать форму/похудеть/набрать массу):")
    await UserData.goal.set()

@dp.message_handler(state=UserData.goal)
async def process_goal(message: types.Message, state: FSMContext):
    await state.update_data(goal=message.text.lower())
    await message.answer("Данные сохранены! Вернитесь в меню.", reply_markup=main_menu)
    await state.finish()

@dp.message_handler(lambda message: message.text == "2. Расчет калорийности")
async def calculate_calories(message: types.Message):
    # Получить данные пользователя (предполагаем, они сохранены в state или БД; для простоты - запросим заново или из state)
    # В реальности сохраняй в БД (например, sqlite3 или FSM с хранением).
    await message.answer("Для расчета введите данные заново или используйте сохраненные. Пример: /calculate")

@dp.message_handler(Command("calculate"))
async def calc_calories(message: types.Message):
    # Пример: Предположим данные: мужчина, 30 лет, 70 кг, 175 см, средний, поддерживать
    gender = "мужчина"
    age = 30
    weight = 70
    height = 175
    activity = "средний"
    goal = "поддерживать форму"
    
    # Расчет BMR
    if gender == "мужчина":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    
    # Коэффициент активности
    activity_coeffs = {"низкий": 1.2, "средний": 1.55, "высокий": 1.725}
    tdee = bmr * activity_coeffs.get(activity, 1.2)
    
    # Коррекция по цели
    if goal == "похудеть":
        daily_calories = tdee - 500
    elif goal == "набрать массу":
        daily_calories = tdee + 500
    else:
        daily_calories = tdee
    
    await message.answer(f"Ваша суточная норма: {int(daily_calories)} ккал.")

@dp.message_handler(lambda message: message.text == "3. Расчет меню питания")
async def generate_menu(message: types.Message):
    # Собрать данные (из примера или state)
    data = {"gender": "мужчина", "age": 30, "weight": 70, "height": 175, "activity": "средний", "goal": "поддерживать"