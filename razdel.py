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
from datetime import datetime
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile, BotCommand, BotCommandScopeDefault
from aiogram.filters import Command
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

# Добавлен для парсинга HTML
from bs4 import BeautifulSoup

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
        logging.FileHandler('bot.log', encoding='utf-8'),
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

# Клавиатуры (добавлена кнопка для списка продуктов)
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="1. Заполнить физические данные здоровья")],
        [KeyboardButton(text="2. Расчет калорийности")],
        [KeyboardButton(text="3. Расчет меню питания")],
        [KeyboardButton(text="4. Печать меню")],
        [KeyboardButton(text="5. Список продуктов для покупки")]
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

# Функция для конвертации HTML в читаемый текст
def html_to_text(html_content: str) -> str:
    try:
        # Убедитесь, что контент в правильной кодировке
        if isinstance(html_content, bytes):
            html_content = html_content.decode('utf-8', errors='ignore')
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Удаляем скрипты и стили
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Обрабатываем таблицы для лучшего форматирования
        for table in soup.find_all('table'):
            # Добавляем переносы строк вокруг таблиц
            table.insert_before(soup.new_string('\n\n'))
            table.insert_after(soup.new_string('\n\n'))
            
            # Обрабатываем строки таблицы
            for i, row in enumerate(table.find_all('tr')):
                if i == 0:  # Заголовок таблицы
                    row.insert_before(soup.new_string('\n'))
                cells = row.find_all(['th', 'td'])
                row_text = ' | '.join(cell.get_text(strip=True) for cell in cells)
                row.string = row_text
                row.insert_after(soup.new_string('\n'))
        
        # Обрабатываем списки
        for ul in soup.find_all('ul'):
            ul.insert_before(soup.new_string('\n'))
            for li in ul.find_all('li'):
                li.string = '• ' + li.get_text(strip=True)
                li.insert_after(soup.new_string('\n'))
            ul.insert_after(soup.new_string('\n'))
        
        # Обрабатываем заголовки
        for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            tag.insert_before(soup.new_string('\n\n'))
            tag.insert_after(soup.new_string('\n\n'))
        
        # Обрабатываем параграфы
        for p in soup.find_all('p'):
            p.insert_before(soup.new_string('\n'))
            p.insert_after(soup.new_string('\n'))
        
        # Получаем текст с сохранением структуры
        text = soup.get_text()
        
        # Заменяем множественные переносы на двойные для лучшей читаемости
        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        # Убираем лишние пробелы, но сохраняем структуру
        text = re.sub(r'[ \t]+', ' ', text)
        
        return text.strip()
    except Exception as e:
        logger.error(f"Ошибка конвертации HTML в текст: {e}")
        return html_content  # Возвращаем оригинал в случае ошибки

# Функция для генерации меню с GigaChat
async def generate_menu(gender: str, age: int, weight: float, height: float, activity: str, goal: str) -> str:
    token = await get_gigachat_access_token()
    if not token:
        logger.warning("Не удалось получить токен GigaChat, переходим на локальную генерацию.")
        return "Ошибка авторизации с GigaChat. Попробуйте позже."
    
    calories_dict = calculate_calories(gender, age, weight, height, activity, goal)
    
    # Текущая дата и день недели
    now = datetime.now()
    try:
        import locale
        locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
    except:
        pass
    day_of_week = now.strftime('%A')
    date = now.strftime('%d.%m.%Y')
    
    prompt = f"""
        Создай меню на день для {gender}, {age} лет, вес {weight} кг, рост {height} см, активность: {activity}, цель: {goal}. Жирным шрифтом 14 pt: Сегодня {day_of_week}, {date}. Жирным шрифтом 14 pt: Калории: {int(calories_dict['daily_calories'])}.

        Сгенерируй в формате HTML для печати: шрифт 12 pt, умести на одном листе A4 (портрет, margins 1cm, без лишних слов), текст меню должен начинаться с даты. 

        Для каждого приема пищи создай аккуратную таблицу с выровненными столбцами и фиксированной шириной:

        <table width="100%" style="border-collapse: collapse; margin-bottom: 15px;">
        <tr style="background-color: #f2f2f2;">
            <th style="border: 1px solid black; padding: 8px; text-align: left; width: 35%;">Блюдо</th>
            <th style="border: 1px solid black; padding: 8px; text-align: center; width: 15%;">Вес</th>
            <th style="border: 1px solid black; padding: 8px; text-align: center; width: 20%;">Калорийность</th>
            <th style="border: 1px solid black; padding: 8px; text-align: left; width: 30%;">КБЖУ</th>
        </tr>
        <tr>
            <td style="border: 1px solid black; padding: 6px;">🍳 Овсяная каша с ягодами</td>
            <td style="border: 1px solid black; padding: 6px; text-align: center;">150г</td>
            <td style="border: 1px solid black; padding: 6px; text-align: center;">300 ккал</td>
            <td style="border: 1px solid black; padding: 6px;">Белки:12г, Жиры:5г, Углеводы:50г</td>
        </tr>
        </table>

        Требования к таблице:
        - Все столбцы должны быть идеально выровнены
        - Используй фиксированную ширину столбцов как в примере
        - Выравнивание: Блюдо и КБЖУ - по левому краю, Вес и Калорийность - по центру
        - Границы у всех ячеек: 1px solid black
        - Отступы: padding: 6px для данных, 8px для заголовков
        - Заголовки таблицы с серым фоном: background-color: #f2f2f2

        Для каждого приема пищи:
        1. Сначала заголовок приема пищи (например: "🍳 ЗАВТРАК")
        2. Затем таблица с блюдами
        3. Пустая строка после таблицы

        Добавь:
        - Суп если нужно
        - Эмодзи для блюд подходящие по смыслу(🍎 фрукты, 🥗 салаты, 💪 белки, 🥩 мясо, 🐟 рыба, 🥦 овощи)
        - напитки для каждого приема пищи
        - Вес в граммах в готовом виде
        - Калорийность в ккал
        - КБЖУ в формате "Белки:Xг, Жиры:Xг, Углеводы:Xг"
        - Общий КБЖУ за день
        - Список продуктов для покупки в <ul class="shopping-list"> с количествами (например, "Овсянка 150г" без дефисов)
        - Короткие рекомендации на день
        Не нужно:
        Больше никакой информации, без лишнего!
        Пример итогового меню не нужен. 
        Без пояснений!
        Не нужно писать как использовать этот код!
        Только факты, без лишних слов и примеров!
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
                logger.info("Меню успешно сформировано с помощью.")
                return f"<html><head><meta charset=\"UTF-8\"><style>body {{font-family: Arial; font-size: 12pt; margin: 1cm;}} table {{width: 100%; border-collapse: collapse;}} th, td {{border: 1px solid black; padding: 8px; text-align: left;}} th {{background-color: #f2f2f2;}}</style></head><body>{menu}</body></html>"
            elif response.status == 401:
                logger.warning("Токен GigaChat истёк, сбрасываем кэш и переходим на локальную генерацию.")
                gigachat_token_cache["access_token"] = None
                raise Exception("Token expired")
            else:
                logger.error(f"Ошибка GigaChat: {response.status} - {await response.text()}")
                logger.info("Переходим на локальную генерацию меню из-за ошибки GigaChat.")
                return await generate_local_menu(gender, age, weight, height, activity, goal)

# Функция для генерации меню локально
async def generate_local_menu(gender: str, age: int, weight: float, height: float, activity: str, goal: str) -> str:
    calories_dict = calculate_calories(gender, age, weight, height, activity, goal)
    daily_calories = int(calories_dict['daily_calories'])
    protein = calories_dict['protein']
    fat = calories_dict['fat']
    carbs = calories_dict['carbs']
    
    # Текущая дата и день недели
    now = datetime.now()
    try:
        import locale
        locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
    except:
        pass
    day_of_week = now.strftime('%A')
    date = now.strftime('%d.%m.%Y')
    
    menu = f"""
    <h2>Меню на {date} ({day_of_week})</h2>
    
    <table border="1" width="100%">
        <tr><th style="background-color: #f2f2f2; text-align: left;">Приём пищи</th><th style="background-color: #f2f2f2; text-align: left;">Блюдо</th><th style="background-color: #f2f2f2; text-align: left;">Граммы</th><th style="background-color: #f2f2f2; text-align: left;">Ккал</th></tr>
        <tr><td style="text-align: left;">🍳 Завтрак</td><td style="text-align: left;">Овсянка с фруктами</td><td style="text-align: left;">150г</td><td style="text-align: left;">300</td></tr>
        <tr><td style="text-align: left;">🍲 Обед</td><td style="text-align: left;">Курица с овощами</td><td style="text-align: left;">200г</td><td style="text-align: left;">500</td></tr>
        <tr><td style="text-align: left;">🍽️ Ужин</td><td style="text-align: left;">Рыба с салатом</td><td style="text-align: left;">150г</td><td style="text-align: left;">400</td></tr>
        <tr><td style="text-align: left;">🥨 Перекусы</td><td style="text-align: left;">Орехи и йогурт</td><td style="text-align: left;">100г</td><td style="text-align: left;">300</td></tr>
    </table>
    
    <h3>📊 Общий КБЖУ:</h3>
    <p>Калории: {daily_calories} ккал</p>
    <p>Белки: {protein} г</p>
    <p>Жиры: {fat} г</p>
    <p>Углеводы: {carbs} г</p>
    
    <h3>🛒 Список продуктов для покупки:</h3>
    <ul class="shopping-list">
        <li>Овсянка - 150г</li>
        <li>Фрукты (яблоки, бананы) - 200г</li>
        <li>Куриное филе - 250г</li>
        <li>Овощи (морковь, брокколи) - 300г</li>
        <li>Рыба (лосось) - 200г</li>
        <li>Салат (листовой) - 150г</li>
        <li>Орехи (миндаль) - 100г</li>
        <li>Йогурт греческий - 200г</li>
        <li>Масло оливковое - 50мл</li>
        <li>Специи - по вкусу</li>
    </ul>
    
    <p>💡 Рекомендация: Пейте достаточное количество воды в течение дня!</p>
    """
    logger.info("Меню успешно сформировано локально.")
    return f"<html><head><meta charset=\"UTF-8\"><style>body {{font-family: Arial; font-size: 12pt; margin: 1cm;}} table {{width: 100%; border-collapse: collapse;}} th, td {{border: 1px solid black; padding: 8px; text-align: left;}} th {{background-color: #f2f2f2;}}</style></head><body>{menu}</body></html>"

# Функция для генерации списка продуктов отдельно
def generate_shopping_list(menu_html: str) -> list:
    try:
        soup = BeautifulSoup(menu_html, 'html.parser')
        # Ищем <ul> с классом shopping-list
        ul = soup.find('ul', class_='shopping-list')
        if ul:
            products = []
            for li in ul.find_all('li'):
                text = li.get_text(strip=True)
                # Убираем дефисы из текста
                text = text.replace('-', '').strip()
                
                # Разделяем на продукт и количество
                match = re.search(r'(.+?)\s*(\d+\.?\d*\s*[гкгмлшт]+\.?)$', text)
                if match:
                    product = match.group(1).strip()
                    amount = match.group(2).strip()
                    products.append({'product': product, 'amount': amount})
                else:
                    # Если не нашли количество, проверяем наличие "шт" в тексте
                    if 'шт' in text.lower():
                        parts = re.split(r'шт', text, flags=re.IGNORECASE)
                        if len(parts) >= 2:
                            product = parts[0].strip()
                            amount = parts[1].strip()
                            if amount.isdigit():
                                amount = f"{amount} шт"
                            else:
                                amount = "шт"
                            products.append({'product': product, 'amount': amount})
                        else:
                            products.append({'product': text, 'amount': 'шт'})
                    else:
                        products.append({'product': text, 'amount': 'Не указано'})
            return products if products else []
        else:
            logger.warning("Не найден список продуктов в HTML")
            return []
    except Exception as e:
        logger.error(f"Ошибка парсинга HTML для списка продуктов: {e}")
        return []

# Глобальные переменные для хранения данных
user_menus = {}

# Обработчики сообщений
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Добро пожаловать! Я бот для расчёта калорий и меню питания.\nВыберите действие:",
        reply_markup=main_menu
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = """
🤖 <b>Помощь по использованию бота</b>

<b>Основные команды:</b>
/start - Запустить бота и показать главное меню
/help - Показать эту справку

<b>Функции бота:</b>
1️⃣ Заполнить физические данные здоровья
2️⃣ Расчет калорийности
3️⃣ Расчет меню питания
4️⃣ Печать меню в формате HTML
5️⃣ Список продуктов для покупки

<b>Как использовать:</b>
1. Сначала заполните свои данные через пункт 1
2. Затем можете рассчитать калории или меню
3. Для печати используйте пункты 4 и 5
    """
    await message.answer(help_text, parse_mode="HTML")

@dp.message(F.text == "1. Заполнить физические данные здоровья")
async def process_fill_data(message: Message, state: FSMContext):
    # Создаем клавиатуру для выбора пола
    gender_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Мужчина"), KeyboardButton(text="Женщина")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Выберите ваш пол:", reply_markup=gender_keyboard)
    await state.set_state(UserData.gender)

@dp.message(UserData.gender)
async def process_gender(message: Message, state: FSMContext):
    await state.update_data(gender=message.text.lower())
    await message.answer("Введите возраст (в годах):", reply_markup=main_menu)
    await state.set_state(UserData.age)

@dp.message(UserData.age)
async def process_age(message: Message, state: FSMContext):
    try:
        age = int(message.text)
        if age < 10 or age > 120:
            raise ValueError
        await state.update_data(age=age)
        await message.answer("Введите вес (в кг):", reply_markup=main_menu)
        await state.set_state(UserData.weight)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный возраст (число от 10 до 120).", reply_markup=main_menu)

@dp.message(UserData.weight)
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text)
        if weight < 20 or weight > 300:
            raise ValueError
        await state.update_data(weight=weight)
        await message.answer("Введите рост (в см):", reply_markup=main_menu)
        await state.set_state(UserData.height)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный вес (число от 20 до 300).", reply_markup=main_menu)

@dp.message(UserData.height)
async def process_height(message: Message, state: FSMContext):
    try:
        height = float(message.text)
        if height < 50 or height > 250:
            raise ValueError
        await state.update_data(height=height)
        
        # Клавиатура для выбора активности
        activity_keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Низкий"), KeyboardButton(text="Средний"), KeyboardButton(text="Высокий")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await message.answer("Выберите уровень активности:", reply_markup=activity_keyboard)
        await state.set_state(UserData.activity)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный рост (число от 50 до 250).", reply_markup=main_menu)

@dp.message(UserData.activity)
async def process_activity(message: Message, state: FSMContext):
    await state.update_data(activity=message.text.lower())
    
    # Клавиатура для выбора цели
    goal_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Поддерживать форму")],
            [KeyboardButton(text="Похудеть")],
            [KeyboardButton(text="Набрать массу")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.answer("Выберите вашу цель:", reply_markup=goal_keyboard)
    await state.set_state(UserData.goal)

@dp.message(UserData.goal)
async def process_goal(message: Message, state: FSMContext):
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
    
    try:
        menu_html = await generate_menu(data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'])
    except Exception as e:
        logger.warning(f"Ошибка при генерации меню: {e}. Используем локальное.")
        menu_html = await generate_local_menu(data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'])
    
    # Конвертируем HTML в читаемый текст для отображения в боте
    menu_text = html_to_text(menu_html)
    
    # Проверяем длину и отправляем как файл, если слишком длинное
    if len(menu_text) > 4000:
        file_path = f"temp_menu_{user_id}.html"
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(menu_html)
            document = FSInputFile(file_path)
            await message.answer_document(
                document,
                caption="Меню слишком длинное для сообщения. Скачайте файл для просмотра (шрифт 12 pt, A4). Откройте в браузере!"
            )
            logger.info(f"Меню отправлено как файл для пользователя {user_id} (длина текста: {len(menu_text)} символов).")
        except Exception as e:
            logger.error(f"Ошибка отправки файла: {e}")
            await message.answer(f"Ошибка при отправке меню: {e}. Попробуйте позже.")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        await message.answer(menu_text)
        logger.info(f"Меню отправлено как текст для пользователя {user_id} (длина: {len(menu_text)} символов).")

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
    
    # Сохраняем меню в HTML-файл с явным указанием кодировки
    file_path = f"menu_{user_id}.html"
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(menu_content)
        
        document = FSInputFile(file_path)
        await message.answer_document(document, caption="Меню для печати (шрифт 12 pt, A4). Откройте в браузере и распечатайте!")
        logger.info(f"Меню отправлено как файл для пользователя {user_id}.")
    except Exception as e:
        logger.error(f"Ошибка создания/отправки файла: {e}")
        await message.answer(f"Ошибка при отправке файла: {e}. Попробуйте позже.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@dp.message(F.text == "5. Список продуктов для покупки")
async def process_print_shopping_list(message: Message):
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
        logger.warning(f"Ошибка при генерации меню для списка: {e}. Используем локальное.")
        menu_content = await generate_local_menu(data['gender'], data['age'], data['weight'], data['height'], data['activity'], data['goal'])
    
    shopping_list = generate_shopping_list(menu_content)
    
    if not shopping_list:
        await message.answer("Список продуктов не найден в сгенерированном меню. Попробуйте сгенерировать меню заново.")
        return
    
    # Формируем HTML-таблицу с явным указанием кодировки UTF-8
    table_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; font-size: 12pt; margin: 1cm; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid black; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; font-weight: bold; }
        h2 { color: #333; }
    </style>
</head>
<body>
    <h2>Список продуктов для покупки</h2>
    <table>
        <tr><th>№</th><th>Продукт</th><th>Количество</th></tr>
"""
    
    for i, item in enumerate(shopping_list, 1):
        product_name = item['product'].replace('-', '').strip()
        table_html += f"<tr><td>{i}</td><td>{product_name}</td><td>{item['amount']}</td></tr>"
    
    table_html += "</table></body></html>"
    
    # Сохраняем с явным указанием кодировки UTF-8
    file_path = f"shopping_list_{user_id}.html"
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(table_html)
        
        document = FSInputFile(file_path)
        await message.answer_document(
            document, 
            caption="Список продуктов для покупки в таблице (шрифт 12 pt, A4)."
        )
        logger.info(f"Список продуктов отправлен как файл для пользователя {user_id}.")
        
    except Exception as e:
        logger.error(f"Ошибка создания/отправки файла: {e}")
        # Альтернативно отправляем как текстовое сообщение
        text_response = "Список продуктов для покупки:\n\n"
        for i, item in enumerate(shopping_list, 1):
            text_response += f"{i}. {item['product']} - {item['amount']}\n"
        
        await message.answer(text_response)
        
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# Настройка команд бота
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="/start", description="Запустить бота и показать меню"),
        BotCommand(command="/help", description="Помощь по использованию бота")
    ]
    await bot.set_my_commands(commands, BotCommandScopeDefault())

# Запуск бота
async def main():
    await set_bot_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())