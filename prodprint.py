# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import sqlite3
import aiohttp
import ssl
import base64
import re
import time
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile  # Добавлен FSInputFile для отправки файла
from aiogram.filters import Command
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
GIGACHAT_CLIENT_ID = os.getenv('GIGACHAT_CLIENT_ID')
GIGACHAT_CLIENT_SECRET = os.getenv('GIGACHAT_CLIENT_SECRET')
GIGACHAT_SCOPE = os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS')

if not BOT_TOKEN or not GIGACHAT_CLIENT_ID or not GIGACHAT_CLIENT_SECRET:
    raise ValueError("Отсутствуют токены! Проверь .env файл.")

# Кэш для токена GigaChat
gigachat_token_cache: Dict[str, Any] = {
    "access_token": None,
    "expires_at": 0
}

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# SQLite: Подключение и создание таблицы
def init_db():
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            gender TEXT,
            age INTEGER,
            weight REAL,
            height REAL,
            activity TEXT,
            goal TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# FSM состояния
class UserData(StatesGroup):
    gender = State()
    age = State()
    weight = State()
    height = State()
    activity = State()
    goal = State()

# Клавиатуры
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="1. Заполнить физические данные здоровья")],
        [KeyboardButton(text="2. Расчет калорийности")],
        [KeyboardButton(text="3. Расчет меню питания")],
        [KeyboardButton(text="4. Печать меню")]
    ],
    resize_keyboard=True
)

# Функция расчёта BMR и TDEE
def calculate_calories(gender: str, age: int, weight: float, height: float, activity: str, goal: str) -> Dict[str, float]:
    if gender.lower() not in ["мужчина", "женщина"]:
        logger.warning(f"Некорректный gender: {gender}")
        gender = "мужчина"
    if activity.lower() not in ["низкий", "средний", "высокий"]:
        logger.warning(f"Некорректный activity: {activity}")
        activity = "средний"
    if goal.lower() not in ["поддерживать форму", "похудеть", "набрать массу"]:
        logger.warning(f"Некорректный goal: {goal}")
        goal = "поддерживать форму"
    
    if gender.lower() == "мужчина":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    
    activity_coeffs = {"низкий": 1.2, "средний": 1.55, "высокий": 1.725}
    tdee = bmr * activity_coeffs.get(activity.lower(), 1.2)
    
    if goal.lower() == "похудеть":
        daily_calories = tdee - 500
    elif goal.lower() == "набрать массу":
        daily_calories = tdee + 500
    else:
        daily_calories = tdee
    
    protein = weight * 2
    fat = (daily_calories * 0.25) / 9
    carbs = (daily_calories - (protein * 4 + fat * 9)) / 4
    
    return {
        'bmr': bmr,
        'tdee': tdee,
        'daily_calories': daily_calories,
        'protein': round(protein, 1),
        'fat': round(fat, 1),
        'carbs': round(carbs, 1)
    }

# SSL-контекст для GigaChat
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# Получение токена GigaChat с retry
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_gigachat_access_token() -> Optional[str]:
    global gigachat_token_cache
    
    if gigachat_token_cache["access_token"] and gigachat_token_cache["expires_at"] > time.time():
        return gigachat_token_cache["access_token"]
    
    credentials = base64.b64encode(f"{GIGACHAT_CLIENT_ID}:{GIGACHAT_CLIENT_SECRET}".encode()).decode()
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    headers = {
        "Authorization": f"Basic {credentials}",
        "RqUID": "12345678-1234-1234-1234-123456789012",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = f"scope={GIGACHAT_SCOPE}"
    
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        async with session.post(url, headers=headers, data=data) as response:
            if response.status == 200:
                result = await response.json()
                gigachat_token_cache["access_token"] = result.get("access_token")
                gigachat_token_cache["expires_at"] = time.time() + result.get("expires_in", 3600) - 60
                logger.info("Токен GigaChat обновлён.")
                return gigachat_token_cache["access_token"]
            else:
                logger.error(f"Ошибка получения токена GigaChat: {response.status} - {await response.text()}")
                raise Exception(f"Failed to get access token: {response.status}")

# Функция для генерации меню с GigaChat
async def generate_menu(gender: str, age: int, weight: float, height: float, activity: str, goal: str) -> str:
    token = await get_gigachat_access_token()
    if not token:
        logger.warning("Не удалось получить токен GigaChat, переходим на локальную генерацию.")
        return "Ошибка авторизации с GigaChat. Попробуйте позже."
    
    calories_dict = calculate_calories(gender, age, weight, height, activity, goal)
    prompt = f"""
Создай меню на день для {gender}, {age} лет, вес {weight} кг, рост {height} см, активность: {activity}, цель: {goal}. Калории: {int(calories_dict['daily_calories'])}.

Оформи меню стильно и красиво для печати:
- Используй заголовки с символами (например, === МЕНЮ НА ДЕНЬ ===).
- Добавь эмодзи для блюд (🍎 для фруктов, 🥗 для салатов, 💪 для белков).
- Укажи солько грамм каждого блюда должно быть в готовом виде.
- Структурируй в виде списков: Завтрак (ккал), Обед (ккал), Ужин (ккал), Перекусы (ккал), укажи количество ккал.
- Добавь рамки или разделители для разделов, название разделов посередине листа.
- Укажи калории, белки, жиры, углеводы в конце.
- Добавь рекомендации на день.
- Добавь список продуктов к покупке на день для указанного меню.
- Сделай текст читаемым и привлекательным для TXT-файла.
"""

    
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "GigaChat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status == 200:
                result = await response.json()
                menu = result["choices"][0]["message"]["content"]
                logger.info("Меню успешно сформировано с помощью GigaChat.")
                return f"Меню сгенерировано с помощью GigaChat:\n\n{menu}"
            elif response.status == 401:
                logger.warning("Токен GigaChat истёк, сбрасываем кэш и переходим на локальную генерацию.")
                gigachat_token_cache["access_token"] = None
                raise Exception("Token expired")
            else:
                logger.error(f"Ошибка GigaChat: {response.status} - {await response.text()}")
                logger.info("Переходим на локальную генерацию меню из-за ошибки GigaChat.")
                return "Меню сгенерировано локально (ошибка GigaChat):\n\n" + await generate_local_menu(gender, age, weight, height, activity, goal)

# Функция для генерации меню локально
async def generate_local_menu(gender: str, age: int, weight: float, height: float, activity: str, goal: str) -> str:
    calories_dict = calculate_calories(gender, age, weight, height, activity, goal)
    daily_calories = int(calories_dict['daily_calories'])
    protein = calories_dict['protein']
    fat = calories_dict['fat']
    carbs = calories_dict['carbs']
    
    menu = f"""
Меню на день для {gender}, {age} лет, вес {weight} кг, рост {height} см, активность: {activity}, цель: {goal}.

Калории: {daily_calories} ккал.
Белки: {protein} г.
Жиры: {fat} г.
Углеводы: {carbs} г.

Завтрак: Овсянка с фруктами (300 ккал).
Обед: Курица с овощами (500 ккал).
Ужин: Рыба с салатом (400 ккал).
Перекусы: Орехи и йогурт (300 ккал).
    """
    logger.info("Меню успешно сформировано локально.")
    return f"Меню сгенерировано локально:\n\n{menu.strip()}"

# Обработчики
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Добро пожаловать! Я бот для расчёта калорий и меню питания.\nВыберите действие:",
        reply_markup=main_menu
    )

@dp.message(F.text == "1. Заполнить физические данные здоровья")
async def process_fill_data(message: Message, state: FSMContext):
    await message.answer("Введите пол (мужчина/женщина):")
    await state.set_state(UserData.gender)

@dp.message(UserData.gender)
async def process_gender(message: Message, state: FSMContext):
    if not re.match(r"^(мужчина|женщина)$", message.text, flags=re.IGNORECASE):
        await message.answer("Пожалуйста, введите 'мужчина' или 'женщина'.")
        return
    await state.update_data(gender=message.text.lower())
    await message.answer("Введите возраст (в годах):")
    await state.set_state(UserData.age)

@dp.message(UserData.age)
async def process_age(message: Message, state: FSMContext):
    try:
        age = int(message.text)
        if age < 10 or age > 120:
            raise ValueError
        await state.update_data(age=age)
        await message.answer("Введите вес (в кг):")
        await state.set_state(UserData.weight)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный возраст (число от 10 до 120).")

@dp.message(UserData.weight)
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text)
        if weight < 20 or weight > 300:
            raise ValueError
        await state.update_data(weight=weight)
        await message.answer("Введите рост (в см):")
        await state.set_state(UserData.height)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный вес (число от 20 до 300).")

@dp.message(UserData.height)
async def process_height(message: Message, state: FSMContext):
    try:
        height = float(message.text)
        if height < 50 or height > 250:
            raise ValueError
        await state.update_data(height=height)
        await message.answer("Введите уровень активности (низкий/средний/высокий):")
        await state.set_state(UserData.activity)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный рост (число от 50 до 250).")

@dp.message(UserData.activity)
async def process_activity(message: Message, state: FSMContext):
    if not re.match(r"^(низкий|средний|высокий)$", message.text, flags=re.IGNORECASE):
        await message.answer("Пожалуйста, введите 'низкий', 'средний' или 'высокий'.")
        return
    await state.update_data(activity=message.text.lower())
    await message.answer("Введите цель (поддерживать форму/похудеть/набрать массу):")
    await state.set_state(UserData.goal)

@dp.message(UserData.goal)
async def process_goal(message: Message, state: FSMContext):
    if not re.match(r"^(поддерживать форму|похудеть|набрать массу)$", message.text, flags=re.IGNORECASE):
        await message.answer("Пожалуйста, введите 'поддерживать форму', 'похудеть' или 'набрать массу'.")
        return
    data = await state.update_data(goal=message.text.lower())
    user_id = message.from_user.id
    
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, gender, age, weight, height, activity, goal)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal']))
    conn.commit()
    conn.close()
    
    await message.answer("Данные сохранены! Теперь вы можете рассчитать калории или меню.", reply_markup=main_menu)
    await state.clear()

@dp.message(F.text == "2. Расчет калорийности")
async def process_calculate_calories(message: Message):
    user_id = message.from_user.id
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT gender, age, weight, height, activity, goal FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        await message.answer("Сначала заполните данные! Выберите '1. Заполнить физические данные здоровья'.")
        return
    
    data = dict(zip(['gender', 'age', 'weight', 'height', 'activity', 'goal'], row))
    calories_dict = calculate_calories(data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'])
    
    response = f"""
Расчёт калорий для {data['gender']}, {data['age']} лет, вес {data['weight']} кг, рост {data['height']} см, активность: {data['activity']}, цель: {data['goal']}.

BMR: {calories_dict['bmr']:.1f} ккал.
TDEE: {calories_dict['tdee']:.1f} ккал.
Ежедневные калории: {calories_dict['daily_calories']:.1f} ккал.
Белки: {calories_dict['protein']} г.
Жиры: {calories_dict['fat']} г.
Углеводы: {calories_dict['carbs']} г.
    """
    await message.answer(response.strip())

@dp.message(F.text == "3. Расчет меню питания")
async def process_generate_menu(message: Message):
    user_id = message.from_user.id
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT gender, age, weight, height, activity, goal FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        await message.answer("Сначала заполните данные! Выберите '1. Заполнить физические данные здоровья'.")
        return
    
    data = dict(zip(['gender', 'age', 'weight', 'height', 'activity', 'goal'], row))
    menu = await generate_menu(data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'])
    await message.answer(menu)

@dp.message(F.text == "4. Печать меню")
async def process_print_menu(message: Message):
    user_id = message.from_user.id
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT gender, age, weight, height, activity, goal FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        await message.answer("Сначала заполните данные! Выберите '1. Заполнить физические данные здоровья'.")
        return
    
    data = dict(zip(['gender', 'age', 'weight', 'height', 'activity', 'goal'], row))
    
    try:
        menu_content = await generate_menu(data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'])
    except Exception as e:
        logger.warning(f"Ошибка при генерации меню для печати: {e}. Используем локальное.")
        menu_content = await generate_local_menu(data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'])
    
    # Сохраняем меню в файл и отправляем как документ
    file_path = f"menu_{user_id}.txt"
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(menu_content.replace('Меню сгенерировано с помощью GigaChat:', '').replace('Меню сгенерировано локально:', '').strip())
    
    try:
        document = FSInputFile(file_path)
        await message.answer_document(document, caption="Меню для печати. Скачайте и распечатайте!")
        logger.info(f"Меню отправлено как файл для пользователя {user_id}.")
    except Exception as e:
        logger.error(f"Ошибка отправки файла: {e}")
        await message.answer(f"Ошибка при отправке файла: {e}. Попробуйте позже.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)  # Удаляем временный файл

# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())