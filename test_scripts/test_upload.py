import requests
import csv
import io

BASE_URL = "http://localhost:8000"
# BASE_URL = "https://clmf-hub-unimed-backend.onrender.com"

def create_test_csv(data):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys(), delimiter=';')
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue().encode('utf-8')

def test_upload(token, data, overwrite=False):
    csv_content = create_test_csv(data)
    files = {'file': ('test.csv', csv_content, 'text/csv')}
    headers = {'Authorization': f'Bearer {token}'}
    data_payload = {'overwrite': str(overwrite).lower()}
    
    print(f"Uploading {len(data)} rows. Overwrite={overwrite}...")
    try:
        response = requests.post(
            f"{BASE_URL}/carteirinhas/upload",
            files=files,
            headers=headers,
            data=data_payload
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

# Dummy data for testing
valid_data = [
    {
        'Carteirinha': '0064.8000.400948.00-5',
        'Paciente': 'TEST PATIENT 1',
        'IdPaciente': '1234',
        'IdPagamento': '1'
    },
     {
        'Carteirinha': '0064.4193.000001.10-2',
        'Paciente': 'TEST PATIENT 2',
        'IdPaciente': '5678',
        'IdPagamento': '2'
    }
]

if __name__ == "__main__":
    # You need a valid token here to run against local or processing
    # For now this is just a template/tool for the user or me to run if needed
    print("Test script ready.")
