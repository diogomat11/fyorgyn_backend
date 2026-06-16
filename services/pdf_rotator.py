import os
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

# Caminho Padrão do tesseract no Windows
tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
if os.path.exists(tesseract_path):
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

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
        # Se a largura for menor que altura, a guia está em pé. SADT é sempre deitada.
        if page.rect.width < page.rect.height:
             log_callback(f"[{os.path.basename(pdf_path)}] Página em Retrato. Rotacionando para Paisagem (Girar 90).")
             page.set_rotation((page.rotation + 90) % 360)
             changed = True

        # 2. PASSO: Verificação de "Cabeça para Baixo" (Upside Down)
        # Vamos gerar a imagem na orientação atual para checar as palavras-chave no topo.
        try:
             pix = page.get_pixmap(dpi=150)
             img = Image.open(io.BytesIO(pix.tobytes("jpeg")))
             
             # OCR completo para pegar coordenadas das palavras
             ocr_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
             
             keywords = ["IPASGO", "SADT", "GUIA", "SERVICO", "ATENCAO"]
             top_hits = 0
             bottom_hits = 0
             height = img.height
             
             for i in range(len(ocr_data['text'])):
                 word = str(ocr_data['text'][i]).upper()
                 if any(k in word for k in keywords):
                     y_pos = ocr_data['top'][i]
                     if y_pos < (height * 0.4): # Parte de cima
                         top_hits += 1
                     elif y_pos > (height * 0.6): # Parte de baixo
                         bottom_hits += 1
             
             # Decisão baseada na concentração de palavras-chave
             if bottom_hits > top_hits:
                 log_callback(f"[{os.path.basename(pdf_path)}] Guia detectada de cabeça para baixo (Top={top_hits}, Bottom={bottom_hits}). Corrigindo 180.")
                 page.set_rotation((page.rotation + 180) % 360)
                 changed = True
             elif top_hits > 0:
                 log_callback(f"[{os.path.basename(pdf_path)}] Orientação validada com sucesso via OCR (Top Hits: {top_hits}).")
             else:
                 # Caso não ache palavras-chave (OCR ruim), use o OSD do Tesseract como última tentativa
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
