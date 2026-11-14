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

# --- Configuração do Banco de Dados (SQLite) ---
DB_PATH = "/data/barbearia.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ Banco de dados inicializado.")

init_db()

def get_chat_history(session_id: str, limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content FROM messages 
        WHERE session_id = ? 
        ORDER BY id DESC LIMIT ?
    ''', (session_id, limit))
    rows = cursor.fetchall()
    conn.close()
    # Inverte para a ordem correta e monta a lista para a IA
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def save_message(session_id: str, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO messages (session_id, role, content) 
        VALUES (?, ?, ?)
    ''', (session_id, role, content))
    conn.commit()
    conn.close()

# --- Faxineiro de JSON (Blindado) ---
def extrair_json(texto):
    if not texto: return None
    # Remove markdown e espaços
    texto_limpo = texto.replace('```json', '').replace('```', '').strip()
    
    try:
        return json.loads(texto_limpo)
    except:
        # Tenta achar o primeiro { e o último }
        start = texto_limpo.find('{')
        end = texto_limpo.rfind('}')
        if start != -1 and end != -1 and start < end:
            try:
                return json.loads(texto_limpo[start:end+1])
            except:
                pass
    
    # Se chegar aqui, a IA falhou miseravelmente. Vamos criar uma resposta de fallback.
    print(f"⚠️ A IA NÃO GEROU JSON VÁLIDO. Texto bruto:\n{texto}")
    return None

def parse_data(texto_data):
    try:
        return datetime.fromisoformat(texto_data)
    except:
        return dateparser.parse(texto_data, languages=['pt'])

def checar_disponibilidade(data_inicio, duracao=30):
    data_fim = data_inicio + timedelta(minutes=duracao)
    service = get_calendar_service()
    eventos = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=data_inicio.isoformat() + "-03:00",
        timeMax=data_fim.isoformat() + "-03:00",
        singleEvents=True
    ).execute()
    return not eventos.get('items')

def criar_evento(nome, servico, data_inicio, duracao):
    data_fim = data_inicio + timedelta(minutes=duracao)
    service = get_calendar_service()
    titulo_evento = f"Agendamento: {nome} - {servico}"
    evento_body = {
        'summary': titulo_evento,
        'start': {'dateTime': data_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'end': {'dateTime': data_fim.isoformat(), 'timeZone': 'America/Sao_Paulo'},
    }
    evento_criado = service.events().insert(calendarId=CALENDAR_ID, body=evento_body).execute()
    return evento_criado['id']

def enviar_whatsapp(numero_destino, texto_mensagem):
    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero_destino, "text": texto_mensagem}
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status Evolução: {response.status_code}")
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
            
            if message_data["key"].get("fromMe"): 
                return {"status": "ok"}

            texto_recebido = message_data.get("message", {}).get("conversation", "")
            if not texto_recebido:
                 texto_recebido = message_data.get("message", {}).get("extendedTextMessage", {}).get("text", "")

            print(f"Mensagem de {numero_usuario}: {texto_recebido}")

            # 1. Busca histórico ANTES de salvar a nova mensagem
            historico = get_chat_history(numero_usuario)
            
            # 2. Salva a nova mensagem no banco
            save_message(numero_usuario, "user", texto_recebido)

            agora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            
            # PROMPT BLINDADO
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João.
            Data/Hora atual: {agora}.
            Serviços: Corte Simples (30min, R$30), Corte+Barba (45min, R$50), Barba (20min, R$20).
            
            HISTÓRICO DA CONVERSA (Leia com atenção para não repetir perguntas):
            {historico}
            
            REGRAS (Responda APENAS em JSON puro, sem markdown):
            1. Se no histórico o cliente já disse o nome, NUNCA pergunte de novo.
            2. Se o cliente pedir um horário, você deve calcular a data.
               Exemplo: Se ele disse "hoje às 10h" e hoje é {agora[:10]}, retorne "YYYY-MM-DDTHH:MM".
               Use: {{"acao": "verificar_horario", "horario": "2026-11-14T10:00", "servico": "Corte+Barba", "nome": "Nome do Cliente"}}
            3. Para conversas gerais: {{"acao": "duvida", "resposta": "Sua resposta"}}
            """

            # Monta as mensagens para a API da OpenAI
            messages_for_api = [{"role": "system", "content": system_prompt}]
            # Adiciona o histórico recente
            for msg in historico:
                messages_for_api.append(msg)
            # Adiciona a mensagem atual
            messages_for_api.append({"role": "user", "content": texto_recebido})

            response = client.chat.completions.create(
                model=model_name,
                messages=messages_for_api, 
                response_format={"type": "json_object"}
            )
            
            resposta_ia_raw = response.choices[0].message.content
            intencao = extrair_json(resposta_ia_raw)
            
            # Se a IA falhou e não gerou JSON, salvamos a resposta bruta como fallback
            if not intencao:
                intencao = {"acao": "duvida", "resposta": "Desculpe, me confundi. Pode repetir?"}

            # Salva a resposta da IA no banco
            save_message(numero_usuario, "assistant", json.dumps(intencao))
            
            resposta_final = ""
            acao = intencao.get("acao")
            nome_ia = intencao.get("nome", "")

            if acao == "verificar_horario":
                data_inicio = parse_data(intencao.get("horario", ""))
                servico = intencao.get("servico", "Corte Simples")
                
                # Checa se o nome está no JSON ou se já temos no histórico
                if not nome_ia and not any("Prazer" in m["content"] or "nome é" in m["content"] for m in historico):
                     resposta_final = "Qual é o seu nome para eu confirmar o agendamento?"
                elif not data_inicio:
                    resposta_final = "❌ Não consegui entender a data. Pode repetir? (ex: hoje às 10h)"
                else:
                    if checar_disponibilidade(data_inicio):
                        duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                        try:
                            evento_id = criar_evento(nome_ia or "Cliente", servico, data_inicio, duracao)
                            resposta_final = f"✅ Agendado! {servico} confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                        except Exception as e_cal:
                            print(f"ERRO NO GOOGLE CALENDAR: {e_cal}")
                            resposta_final = "⚠️ Tive um problema ao acessar a agenda."
                    else:
                        resposta_final = f"❌ O horário das {data_inicio.strftime('%H:%M')} está ocupado."
            else:
                # Se a ação for dúvida, apenas responde o que a IA escreveu
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            enviar_whatsapp(numero_usuario, resposta_final)

        except Exception as e:
            print(f"Erro GLOBAL ao processar: {e}")
            # Em caso de erro, tenta avisar o usuário em vez de ficar mudo
            try:
                enviar_whatsapp(numero_usuario, "⚠️ Ops, tive um pequeno erro interno. Pode repetir sua última mensagem?")
            except:
                pass
            
    return {"status": "ok"}
