
import requests
import json

URL = "http://localhost:8000/api/agendamentos/batch-status"
DATA = {
    "ids": [1], # Adjust if needed
    "status": "Confirmado",
    "capturar_guias": True
}

try:
    resp = requests.put(URL, json=DATA)
    print(f"Status: {resp.status_code}")
    print(f"Body: {resp.text}")
except Exception as e:
    print(f"Error: {e}")
