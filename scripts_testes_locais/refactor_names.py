import os

files_to_update = [
    r"c:\dev\Agenda_hub_MultiConv\backend\routes\carteirinhas.py",
    r"c:\dev\Agenda_hub_MultiConv\backend\routes\pei.py",
    r"c:\dev\Agenda_hub_MultiConv\frontend\src\pages\Carteirinhas.jsx",
    r"c:\dev\Agenda_hub_MultiConv\frontend\src\components\EditCarteirinhaModal.jsx",
    r"c:\dev\Agenda_hub_MultiConv\frontend\src\utils\formatters.js"
]

replacements = {
    "cod_convenio": "codigo_beneficiario",
    "Cod_convenio": "Codigo_beneficiario",
    "maskCodConvenio": "maskCodigoBeneficiario"
}

for file_path in files_to_update:
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        for old, new in replacements.items():
            content = content.replace(old, new)
            
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Updated {file_path}")
    else:
        print(f"File not found: {file_path}")
