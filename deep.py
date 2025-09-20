# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import aiohttp
import json
import ssl
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram import Router
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# Создаем кастомный SSL контекст для разработки
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# Временное хранилище данных пользователей
user_data_storage = {}

# Состояния для сбора данных
class UserData(StatesGroup):
    gender = State()
    age = State()
    weight = State()
    height = State()
    activity = State()
    goal = State()

# Главное меню
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="1. Заполнить физические данные здоровья")],
        [KeyboardButton(text="2. Расчет калорийности")],
        [KeyboardButton(text="3. Расчет меню питания")]
    ],
    resize_keyboard=True
)

@router.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Привет! Я твой нутрициолог-ассистент. Выбери опцию:", reply_markup=main_menu)

@router.message(F.text == "1. Заполнить физические данные здоровья")
async def fill_data(message: types.Message, state: FSMContext):
    await message.answer("Укажите Ваш пол (мужчина/женщина):")
    await state.set_state(UserData.gender)

@router.message(UserData.gender)
async def process_gender(message: types.Message, state: FSMContext):
    gender = message.text.lower()
    if gender not in ["мужчина", "женщина"]:
        await message.answer("Пожалуйста, укажите 'мужчина' или 'женщина'.")
        return
    
    await state.update_data(gender=gender)
    await message.answer("Укажите Ваш возраст:")
    await state.set_state(UserData.age)

@router.message(UserData.age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text)
        if age <= 0 or age > 120:
            await message.answer("Пожалуйста, введите корректный возраст.")
            return
    except ValueError:
        await message.answer("Пожалуйста, введите число для возраста.")
        return
    
    await state.update_data(age=age)
    await message.answer("Укажите Ваш вес (кг):")
    await state.set_state(UserData.weight)

@router.message(UserData.weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text)
        if weight <= 0 or weight > 300:
            await message.answer("Пожалуйста, введите корректный вес.")
            return
    except ValueError:
        await message.answer("Пожалуйста, введите число для веса.")
        return
    
    await state.update_data(weight=weight)
    await message.answer("Укажите Ваш рост (см):")
    await state.set_state(UserData.height)

@router.message(UserData.height)
async def process_height(message: types.Message, state: FSMContext):
    try:
        height = float(message.text)
        if height <= 0 or height > 250:
            await message.answer("Пожалуйста, введите корректный рост.")
            return
    except ValueError:
        await message.answer("Пожалуйста, введите число для роста.")
        return
    
    await state.update_data(height=height)
    await message.answer("Укажите уровень физической активности (низкий/средний/высокий):")
    await state.set_state(UserData.activity)

@router.message(UserData.activity)
async def process_activity(message: types.Message, state: FSMContext):
    activity = message.text.lower()
    if activity not in ["низкий", "средний", "высокий"]:
        await message.answer("Пожалуйста, выберите из предложенных вариантов: низкий/средний/высокий.")
        return
    
    await state.update_data(activity=activity)
    await message.answer("Выберите цель (поддерживать форму/похудеть/набрать массу):")
    await state.set_state(UserData.goal)

@router.message(UserData.goal)
async def process_goal(message: types.Message, state: FSMContext):
    goal = message.text.lower()
    if goal not in ["поддерживать форму", "похудеть", "набрать массу"]:
        await message.answer("Пожалуйста, выберите из предложенных вариантов: поддерживать форму/похудеть/набрать массу.")
        return
    
    # Сохраняем данные во временное хранилище
    user_id = message.from_user.id
    data = await state.get_data()
    data["goal"] = goal
    user_data_storage[user_id] = data
    
    await message.answer("Данные сохранены! Вернитесь в меню.", reply_markup=main_menu)
    await state.clear()

@router.message(F.text == "2. Расчет калорийности")
async def calculate_calories(message: types.Message):
    user_id = message.from_user.id
    data = user_data_storage.get(user_id)
    
    if not data:
        await message.answer("Сначала заполните данные в опции 1. Или используйте /calculate с примером.")
        return
    
    gender = data.get("gender", "мужчина")
    age = data.get("age", 30)
    weight = data.get("weight", 70)
    height = data.get("height", 175)
    activity = data.get("activity", "средний")
    goal = data.get("goal", "поддерживать форму")
    
    # Расчет BMR (Mifflin-St Jeor Equation)
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
    
    # Расчет БЖУ
    protein = weight * 2  # 2г белка на кг веса
    fat = (daily_calories * 0.25) / 9  # 25% от калорий, 9 ккал/г
    carbs = (daily_calories - (protein * 4 + fat * 9)) / 4  # остальное углеводы
    
    response = (
        f"🍽️ Ваша суточная норма:\n\n"
        f"• Калории: {int(daily_calories)} ккал\n"
        f"• Белки: {protein:.1f} г\n"
        f"• Жиры: {fat:.1f} г\n"
        f"• Углеводы: {carbs:.1f} г\n\n"
        f"Для точного меню используйте опцию '3. Расчет меню питания'"
    )
    
    await message.answer(response, reply_markup=main_menu)

@router.message(Command("calculate"))
async def calc_calories(message: types.Message):
    # Пример расчета без данных (для тестирования)
    gender = "мужчина"
    age = 30
    weight = 70
    height = 175
    activity = "средний"
    goal = "поддерживать форму"
    
    if gender == "мужчина":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    
    activity_coeffs = {"низкий": 1.2, "средний": 1.55, "высокий": 1.725}
    tdee = bmr * activity_coeffs.get(activity, 1.2)
    
    if goal == "похудеть":
        daily_calories = tdee - 500
    elif goal == "набрать массу":
        daily_calories = tdee + 500
    else:
        daily_calories = tdee
    
    await message.answer(f"Примерная суточная норма: {int(daily_calories)} ккал.")

async def generate_with_deepseek(prompt: str) -> str:
    """Функция для запроса к DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        return "Ошибка: API ключ не настроен"
    
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1500,
        "temperature": 0.7,
        "stream": False
    }
    
    try:
        # Используем кастомный SSL контекст для обхода ошибки сертификата
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(url, headers=headers, json=data, timeout=60) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content']
                else:
                    error_text = await response.text()
                    logging.error(f"DeepSeek API error: {response.status} - {error_text}")
                    return f"Ошибка API: {response.status}"
                    
    except asyncio.TimeoutError:
        logging.error("DeepSeek API timeout")
        return "Таймаут запроса к AI"
    except Exception as e:
        logging.error(f"DeepSeek error: {e}")
        return f"Ошибка: {str(e)}"

async def generate_fallback_menu(user_data: dict) -> str:
    """Резервное меню на случай ошибок API"""
    goal = user_data.get("goal", "поддерживать форму")
    
    menus = {
        "похудеть": """
🥗 Меню для похудения (≈1200-1400 ккал):

🍳 ЗАВТРАК (300 ккал):
• Овсянка на воде 50г + ягоды 100г
• Белки: 12г, Жиры: 5г, Углеводы: 45г

🍲 ОБЕД (400 ккал):
• Куриная грудка 150г + гречка 100г + овощной салат
• Б: 35г, Ж: 8г, У: 45г

🥗 УЖИН (350 ккал):
• Рыба на пару 150г + тушеные овощи 200г
• Б: 25г, Ж: 10г, У: 20г

🍎 ПЕРЕКУСЫ (150-250 ккал):
• Яблоко + греческий йогурт 100г
• Горсть орехов (20г)
""",
        "набрать массу": """
💪 Меню для набора массы (≈2500-3000 ккал):

🍳 ЗАВТРАК (600 ккал):
• Омлет из 3 яиц + сыр 50г + цельнозерновой хлеб
• Б: 35г, Ж: 25г, У: 60г

🍲 ОБЕД (700 ккал):
• Говядина 200г + рис 150г + овощи на гриле
• Б: 45г, Ж: 20г, У: 80г

🍗 УЖИН (500 ккал):
• Творог 200г + банан + орехи 30г
• Б: 35г, Ж: 15г, У: 40г

🥛 ПЕРЕКУСЫ (700 ккал):
• Протеиновый коктейль (молоко + протеин)
• Бутерброд с арахисовой пастой
"""
    }
    
    return menus.get(goal, """
⚖️ Сбалансированное меню (≈1800-2000 ккал):

🍳 Завтрак: Творог 150г с фруктами (350 ккал)
🍲 Обед: Курица 150г с рисом и овощами (500 ккал) 
🥗 Ужин: Рыба 150г с салатом (400 ккал)
🍎 Перекусы: Орехи, йогурт, фрукты (550 ккал)
""")

@router.message(F.text == "3. Расчет меню питания")
async def generate_menu(message: types.Message):
    user_id = message.from_user.id
    data = user_data_storage.get(user_id)
    
    if not data:
        await message.answer("Сначала заполните данные в опции 1.")
        return
    
    # Показываем что бот думает
    await message.answer("🍽️ Генерирую персонализированное меню...")
    
    gender = data.get("gender", "мужчина")
    age = data.get("age", 30)
    weight = data.get("weight", 70)
    height = data.get("height", 175)
    activity = data.get("activity", "средний")
    goal = data.get("goal", "поддерживать форму")
    
    # Расчет калорий
    if gender == "мужчина":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    
    activity_coeffs = {"низкий": 1.2, "средний": 1.55, "высокий": 1.725}
    tdee = bmr * activity_coeffs.get(activity, 1.2)
    
    if goal == "похудеть":
        daily_calories = tdee - 500
    elif goal == "набрать массу":
        daily_calories = tdee + 500
    else:
        daily_calories = tdee
    
    # Промпт для DeepSeek
    prompt = f"""
Создай подробное персонализированное меню питания на 1 день для {gender}, {age} лет, вес {weight} кг, рост {height} см, уровень активности: {activity}, цель: {goal}. 

Общая калорийность: около {int(daily_calories)} ккал.

Включи:
1. Завтрак
2. Обед  
3. Ужин
4. 2 перекуса

Для каждого приема пищи укажи:
- Конкретные блюда и продукты
- Примерный вес порций в граммах
- Калорийность приема пищи
- БЖУ (белки, жиры, углеводы в граммах)
- Краткое описание приготовления

Сделай меню сбалансированным, полезным и практичным для приготовления в домашних условиях. Учитывай российские продукты и привычки питания.
"""
    
    # Генерация меню через DeepSeek
    menu_text = await generate_with_deepseek(prompt)
    
    # Если AI не ответил, используем резервное меню
    if menu_text.startswith("Ошибка") or menu_text.startswith("Таймаут"):
        logging.warning(f"AI не ответил, используем резервное меню. Ошибка: {menu_text}")
        menu_text = await generate_fallback_menu(data)
        response = f"⚠️ Используем шаблонное меню:\n\n{menu_text}"
    else:
        response = f"🍽️ Ваше персонализированное меню:\n\n{menu_text}"
    
    # Разбиваем длинное сообщение на части (ограничение Telegram)
    if len(response) > 4000:
        parts = [response[i:i+4000] for i in range(0, len(response), 4000)]
        for part in parts:
            await message.answer(part)
    else:
        await message.answer(response, reply_markup=main_menu)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())