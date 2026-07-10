import os
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import logging
import unicodedata

logger = logging.getLogger(__name__)

# Caminho Padrão do tesseract no Windows
tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
if os.path.exists(tesseract_path):
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

def normalize_string(s):
    if not s:
        return ""
    # Remove acentos e converte para maiúsculo
    return "".join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    ).upper()

def check_and_fix_rotation(pdf_path, output_path, log_callback=None):
    if log_callback is None:
        log_callback = logger.info
        
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        log_callback(f"Erro ao abrir {pdf_path}: {e}")
        return False

    changed = False

    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # 1. PASSO: Garantir Orientação Paisagem (Landscape)
        # Se a largura for menor que altura, a guia está em pé (Retrato). SADT é sempre deitada.
        if page.rect.width < page.rect.height:
             log_callback(f"[{os.path.basename(pdf_path)}] Página em Retrato. Rotacionando para Paisagem (Girar 90).")
             page.set_rotation((page.rotation + 90) % 360)
             changed = True

        # 2. PASSO: Verificação de "Cabeça para Baixo" (Upside Down)
        # Geramos a imagem com a orientação atualizada para validar a posição do cabeçalho.
        try:
             pix = page.get_pixmap(dpi=150)
             img = Image.open(io.BytesIO(pix.tobytes("jpeg")))
             
             # OCR completo para pegar coordenadas das palavras
             ocr_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
             
             keywords = ["GUIA", "SERVICO", "SADT", "TERAPIA"]
             top_hits = 0
             bottom_hits = 0
             height = img.height
             half_height = height / 2.0
             
             matched_words_info = []
             
             for i in range(len(ocr_data['text'])):
                 text_val = ocr_data['text'][i]
                 if not text_val or not isinstance(text_val, str):
                     continue
                 
                 word_normalized = normalize_string(text_val)
                 
                 # Verifica se alguma keyword está contida na palavra normalizada
                 matched_k = None
                 for k in keywords:
                     if k in word_normalized:
                         matched_k = k
                         break
                 
                 if matched_k:
                     y_pos = ocr_data['top'][i]
                     if y_pos < half_height:
                         top_hits += 1
                         matched_words_info.append(f"'{text_val}' no topo (y={y_pos})")
                     else:
                         bottom_hits += 1
                         matched_words_info.append(f"'{text_val}' na base (y={y_pos})")
             
             total_hits = top_hits + bottom_hits
             if matched_words_info:
                 log_callback(f"[{os.path.basename(pdf_path)}] OCR encontrou {total_hits} palavras-chave: {', '.join(matched_words_info)}")
             
             # Decisão baseada na concentração de palavras-chave (requer pelo menos 2 acertos)
             if total_hits >= 2:
                 if bottom_hits > top_hits:
                     log_callback(f"[{os.path.basename(pdf_path)}] Guia detectada de cabeça para baixo (Top={top_hits}, Bottom={bottom_hits}). Corrigindo 180.")
                     page.set_rotation((page.rotation + 180) % 360)
                     changed = True
                 else:
                     log_callback(f"[{os.path.basename(pdf_path)}] Orientação validada com sucesso via OCR (Top={top_hits}, Bottom={bottom_hits}).")
             else:
                 log_callback(f"[{os.path.basename(pdf_path)}] Poucas palavras-chave encontradas ({total_hits} < 2). Tentando OSD como fallback.")
                 # Caso não ache palavras-chave suficientes (OCR ruim), usa o OSD do Tesseract como última tentativa
                 try:
                     osd = pytesseract.image_to_osd(img)
                     rotate_needed = 0
                     for line in osd.split('\n'):
                         if 'Rotate:' in line:
                             rotate_needed = int(line.split(':')[1].strip())
                             break
                     if rotate_needed != 0:
                         log_callback(f"[{os.path.basename(pdf_path)}] OSD sugeriu rotação adicional de {rotate_needed} graus.")
                         page.set_rotation((page.rotation + rotate_needed) % 360)
                         changed = True
                 except Exception as osd_err:
                     log_callback(f"[{os.path.basename(pdf_path)}] Erro no OSD do Tesseract: {osd_err}")

        except Exception as e:
             log_callback(f"[{os.path.basename(pdf_path)}] Erro na verificação OCR: {e}")

    if changed:
        try:
            temp_out = output_path + ".rotated.temp"
            doc.save(temp_out)
            doc.close()
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rename(temp_out, output_path)
            log_callback(f"Rotacao Corrigida com Sucesso. Salvo em: {output_path}")
            return True
        except Exception as e:
            log_callback(f"Erro ao salvar PDF: {e}")
            try:
                doc.close()
            except:
                pass
            return False
    else:
        doc.close()
        log_callback(f"Orientacao correta mantida para: {os.path.basename(pdf_path)}")
        return False

