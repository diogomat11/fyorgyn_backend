with open('error.log', 'rb') as f:
    text = f.read().decode('utf-8', errors='ignore')

with open('clean_err.txt', 'w', encoding='utf-8') as f:
    f.write(text)
