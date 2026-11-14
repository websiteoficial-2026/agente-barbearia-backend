from fastapi import FastAPI, Request
import requests
import os
import json
import re
import sqlite3
from datetime import datetime, timedelta
from openai import OpenAI
from google_auth import get_calendar_service
import dateparser

app = FastAPI()

# --- Variáveis de Ambiente ---
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME")

api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_BASE_URL")
model_name = os.getenv("OPENAI_MODEL", "llama3-70b-8192")
client = OpenAI(api_key=api_key, base_url=base_url)

CALENDAR_ID = 'dominio3004@gmail.com'

# --- Banco de Dados ---
DB_PATH = "/data/barbearia.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    conn.close()

init_db()

def get_chat_history(session_id: str, limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?', (session_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_message(session_id: str, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)', (session_id, role, content))
    conn.commit()
    conn.close()

# --- Faxineiro JSON ---
def extrair_json(texto):
    if not texto: return None
    texto_limpo = texto.replace('```json', '').replace('```', '').strip()
    try: return json.loads(texto_limpo)
    except: pass
    start = texto_limpo.find('{')
    end = texto_limpo.rfind('}')
    if start != -1 and end != -1 and start < end:
        try: return json.loads(texto_limpo[start:end+1])
        except: pass
    return None

# --- TRAVA DE SEGURANÇA PARA DATA (CORRIGIDA AQUI) ---
def parse_data(texto_data):
    # Se a IA mandar None, número ou algo que não seja texto, retorna None e evita o erro
    if not texto_data or not isinstance(texto_data, str):
        return None
    try: 
        return datetime.fromisoformat(texto_data)
    except: 
        return dateparser.parse(texto_data, languages=['pt'])

def checar_disponibilidade(data_inicio, duracao=30):
    data_fim = data_inicio + timedelta(minutes=duracao)
    service = get_calendar_service()
    eventos = service.events().list(calendarId=CALENDAR_ID, timeMin=data_inicio.isoformat() + "-03:00", timeMax=data_fim.isoformat() + "-03:00", singleEvents=True).execute()
    return not eventos.get('items')

def criar_evento(nome, servico, data_inicio, duracao):
    data_fim = data_inicio + timedelta(minutes=duracao)
    service = get_calendar_service()
    evento_body = {'summary': f"Agendamento: {nome} - {servico}", 'start': {'dateTime': data_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'}, 'end': {'dateTime': data_fim.isoformat(), 'timeZone': 'America/Sao_Paulo'}}
    return service.events().insert(calendarId=CALENDAR_ID, body=evento_body).execute()['id']

def enviar_whatsapp(numero_destino, texto_mensagem):
    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero_destino, "text": texto_mensagem}
    try:
        requests.post(url, json=payload, headers=headers)
        print(f"Status Evolução: 201")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

@app.post("/webhook/whatsapp")
async def receber_webhook(request: Request):
    data = await request.json()
    
    if data.get("event") == "messages.upsert":
        try:
            message_data = data["data"]
            remote_jid = message_data["key"]["remoteJid"]
            numero_usuario = remote_jid.split("@")[0]
            
            if message_data["key"].get("fromMe"): return {"status": "ok"}

            texto_recebido = message_data.get("message", {}).get("conversation", "")
            if not texto_recebido:
                 texto_recebido = message_data.get("message", {}).get("extendedTextMessage", {}).get("text", "")

            print(f"Mensagem de {numero_usuario}: {texto_recebido}")

            historico = get_chat_history(numero_usuario)
            save_message(numero_usuario, "user", texto_recebido)

            agora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João.
            Data/Hora atual: {agora}.
            Serviços: Corte Simples (30min, R$30), Corte+Barba (45min, R$50), Barba (20min, R$20).
            
            REGRAS:
            1. Se o cliente pedir um horário, extraia a data, o serviço e o nome.
            2. Retorne SEMPRE um objeto JSON válido, sem markdown:
            {{
                "acao": "agendar",
                "nome": "Nome do cliente se ele falou, senão null",
                "servico": "Corte Simples, Corte+Barba ou Barba",
                "horario": "YYYY-MM-DDTHH:MM"
            }}
            3. Se o cliente estiver apenas conversando, retorne:
            {{"acao": "duvida", "resposta": "Sua resposta humanizada aqui"}}
            
            IMPORTANTE: Use o histórico abaixo para saber o nome do cliente.
            """

            messages_for_api = [{"role": "system", "content": system_prompt}]
            for msg in historico: messages_for_api.append(msg)
            messages_for_api.append({"role": "user", "content": texto_recebido})

            try:
                response = client.chat.completions.create(model=model_name, messages=messages_for_api)
                resposta_ia_raw = response.choices[0].message.content
            except Exception as e:
                print(f"Erro na API da OpenAI: {e}")
                enviar_whatsapp(numero_usuario, "Tive um problema de conexão. Pode repetir?")
                return {"status": "ok"}

            intencao = extrair_json(resposta_ia_raw)
            
            # TRAVA DE SEGURANÇA EXTRA (Garante que a IA mandou um dicionário)
            if not intencao or not isinstance(intencao, dict):
                print(f"⚠️ FALHA NO JSON DA IA: {resposta_ia_raw}")
                intencao = {"acao": "duvida", "resposta": "Desculpe, me confundi. Pode repetir o dia e horário?"}

            save_message(numero_usuario, "assistant", json.dumps(intencao))
            
            resposta_final = ""
            acao = intencao.get("acao")

            if acao == "agendar":
                # Passa o valor direto para a função parse_data (ela sabe lidar se vier vazio)
                data_inicio = parse_data(intencao.get("horario"))
                servico = intencao.get("servico", "Corte Simples")
                nome_ia = intencao.get("nome")
                
                # Se a IA não mandou o nome, tenta pescar no histórico
                if not nome_ia:
                    for msg in reversed(historico):
                        if msg["role"] == "user" and len(msg["content"].split()) == 1 and msg["content"].istitle():
                            nome_ia = msg["content"]
                            break
                
                if not data_inicio:
                    resposta_final = "❌ Não consegui entender a data. Pode repetir? (ex: hoje às 10h)"
                elif not nome_ia:
                    resposta_final = "✅ Temos horário! Qual é o seu nome para eu confirmar o agendamento?"
                else:
                    # ISOLANDO O ERRO DO GOOGLE CALENDAR AQUI
                    try:
                        if checar_disponibilidade(data_inicio):
                            duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                            criar_evento(nome_ia, servico, data_inicio, duracao)
                            resposta_final = f"✅ Agendado! {servico} confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                        else:
                            resposta_final = f"❌ O horário das {data_inicio.strftime('%H:%M')} está ocupado."
                    except Exception as e_cal:
                        print(f"❌ ERRO NO GOOGLE CALENDAR: {e_cal}")
                        resposta_final = "⚠️ Estou com um problema técnico na agenda. Pode tentar novamente em instantes?"
            else:
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            enviar_whatsapp(numero_usuario, resposta_final)

        except Exception as e:
            print(f"Erro GLOBAL ao processar: {e}")
            try: enviar_whatsapp(numero_usuario, "⚠️ Ops, tive um problema interno. Pode repetir?")
            except: pass
            
    return {"status": "ok"}
