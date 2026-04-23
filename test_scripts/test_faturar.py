
import requests
import json

URL = "http://localhost:8000/api/agendamentos/faturar"
DATA = {
    "agendamento_ids": [1]
}

try:
    resp = requests.post(URL, json=DATA)
    print(f"Status: {resp.status_code}")
    print(f"Body: {resp.text}")
except Exception as e:
    print(f"Error: {e}")
