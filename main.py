from fastapi import FastAPI, Request, BackgroundTasks
import requests
import os
import json
import re
import sqlite3
import asyncio
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
    # Tabela de mensagens
    cursor.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    # Tabela NOVA para guardar o ID do evento no Google
    cursor.execute('CREATE TABLE IF NOT EXISTS appointments (session_id TEXT PRIMARY KEY, event_id TEXT)')
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

# --- Funções para Gerenciar Eventos no Banco ---
def save_event_id(session_id: str, event_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO appointments (session_id, event_id) VALUES (?, ?)', (session_id, event_id))
    conn.commit()
    conn.close()

def get_event_id(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT event_id FROM appointments WHERE session_id = ?', (session_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

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

def parse_data(texto_data):
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

def deletar_evento(event_id):
    try:
        service = get_calendar_service()
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return True
    except Exception as e:
        print(f"Erro ao deletar evento antigo: {e}")
        return False

def enviar_whatsapp(numero_destino, texto_mensagem):
    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero_destino, "text": texto_mensagem}
    try:
        requests.post(url, json=payload, headers=headers)
        print(f"Status Evolução: 201 | Enviado para {numero_destino}")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

async def enviar_com_delay(numero_destino, texto_mensagem):
    tempo_calculado = len(texto_mensagem) * 0.05
    tempo_de_espera = min(max(tempo_calculado, 2.0), 8.0)
    print(f"⏳ Sofia está digitando... (aguardando {tempo_de_espera:.1f}s)")
    await asyncio.sleep(tempo_de_espera)
    enviar_whatsapp(numero_destino, texto_mensagem)

@app.post("/webhook/whatsapp")
async def receber_webhook(request: Request, background_tasks: BackgroundTasks):
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
            
            # PROMPT ENSINANDO A IA A DIFERENCIAR AGENDAR DE REAGENDAR
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João.
            Data/Hora atual: {agora}.
            Serviços: Corte Simples (30min, R$30), Corte+Barba (45min, R$50), Barba (20min, R$20).
            
            REGRAS (Responda APENAS JSON):
            1. Se o cliente quiser marcar um horário novo: {{"acao": "agendar", "nome": "Nome", "servico": "Corte Simples", "horario": "YYYY-MM-DDTHH:MM"}}
            2. Se o cliente já tem um horário e pedir para MUDAR ou REMARCAR: {{"acao": "reagendar", "nome": "Nome", "servico": "Corte Simples", "horario_novo": "YYYY-MM-DDTHH:MM"}}
            3. Se for só conversa: {{"acao": "duvida", "resposta": "Sua resposta"}}
            
            CRÍTICO: Só preencha o campo "nome" se o cliente disser explicitamente. Use o histórico para saber o nome e o serviço se ele já falou antes.
            """

            messages_for_api = [{"role": "system", "content": system_prompt}]
            for msg in historico: messages_for_api.append(msg)
            messages_for_api.append({"role": "user", "content": texto_recebido})

            try:
                response = client.chat.completions.create(model=model_name, messages=messages_for_api)
                resposta_ia_raw = response.choices[0].message.content
            except Exception as e:
                print(f"Erro na API da OpenAI: {e}")
                background_tasks.add_task(enviar_com_delay, numero_usuario, "Tive um problema de conexão. Pode repetir?")
                return {"status": "ok"}

            intencao = extrair_json(resposta_ia_raw)
            
            if not intencao or not isinstance(intencao, dict):
                intencao = {"acao": "duvida", "resposta": "Desculpe, me confundi. Pode repetir?"}

            save_message(numero_usuario, "assistant", json.dumps(intencao))
            
            resposta_final = ""
            acao = intencao.get("acao")
            servico = intencao.get("servico", "Corte Simples")
            nome_ia = intencao.get("nome")
            
            # --- LÓGICA DE AGENDAR (NOVO) ---
            if acao == "agendar":
                data_inicio = parse_data(intencao.get("horario"))
                
                if not data_inicio:
                    resposta_final = "❌ Não entendi a data. Pode repetir?"
                elif not nome_ia:
                    resposta_final = "✅ Temos horário! Qual o seu nome para confirmar?"
                else:
                    try:
                        if checar_disponibilidade(data_inicio):
                            duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                            evento_id = criar_evento(nome_ia, servico, data_inicio, duracao)
                            save_event_id(numero_usuario, evento_id) # Salva o ID no banco!
                            resposta_final = f"✅ Agendado! {servico} confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                        else:
                            resposta_final = f"❌ O horário das {data_inicio.strftime('%H:%M')} está ocupado."
                    except Exception as e_cal:
                        print(f"❌ ERRO NO GOOGLE CALENDAR: {e_cal}")
                        resposta_final = "⚠️ Problema técnico na agenda."

            # --- LÓGICA DE REAGENDAR (NOVO - APAGA O ANTIGO E CRIA O NOVO) ---
            elif acao == "reagendar":
                data_nova = parse_data(intencao.get("horario_novo"))
                evento_antigo_id = get_event_id(numero_usuario)
                
                if not data_nova:
                    resposta_final = "❌ Não entendi a nova data. Pode repetir?"
                elif not evento_antigo_id:
                    resposta_final = "Não encontrei um agendamento anterior no seu nome. Vamos criar um novo?"
                else:
                    try:
                        # 1. Deleta o antigo
                        if deletar_evento(evento_antigo_id):
                            # 2. Checa se o novo está livre
                            if checar_disponibilidade(data_nova):
                                duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                                novo_evento_id = criar_evento(nome_ia or "Cliente", servico, data_nova, duracao)
                                save_event_id(numero_usuario, novo_evento_id) # Atualiza o ID
                                resposta_final = f"✅ Reagendado com sucesso! {servico} alterado para {data_nova.strftime('%d/%m às %H:%M')}."
                            else:
                                resposta_final = f"❌ O horário das {data_nova.strftime('%H:%M')} está ocupado. O antigo foi cancelado. Qual outro horário prefere?"
                        else:
                            resposta_final = "⚠️ Tive um problema ao cancelar o agendamento antigo."
                    except Exception as e_cal:
                        print(f"❌ ERRO NO GOOGLE CALENDAR: {e_cal}")
                        resposta_final = "⚠️ Problema técnico na agenda."
            
            # --- CONVERSA GERAL ---
            else:
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            background_tasks.add_task(enviar_com_delay, numero_usuario, resposta_final)

        except Exception as e:
            print(f"Erro GLOBAL ao processar: {e}")
            try: background_tasks.add_task(enviar_com_delay, numero_usuario, "⚠️ Ops, tive um problema interno.")
            except: pass
            
    return {"status": "ok"}
