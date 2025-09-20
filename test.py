import requests
import certifi

# Определение переменных
url = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
headers = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Accept': 'application/json',
    'RqUID': 'unique-request-id'  # Замените на уникальный ID
}
data = {
    'scope': 'GIGACHAT_API_PERS',
    'grant_type': 'client_credentials'
}
auth = ('YOUR_CLIENT_ID', 'YOUR_CLIENT_SECRET')  # Замените на свои ключи

# Запрос с проверкой SSL через certifi
try:
    response = requests.post(url, headers=headers, data=data, auth=auth, verify=certifi.where())
    if response.status_code == 200:
        token = response.json().get('access_token')
        print(f"✅ Токен получен: {token}")
    else:
        print(f"❌ Ошибка: {response.status_code} - {response.text}")
except requests.exceptions.SSLError as e:
    print(f"SSL Ошибка: {e}")
except Exception as e:
    print(f"Другая ошибка: {e}")
