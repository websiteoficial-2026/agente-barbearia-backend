from fastapi import FastAPI, Request, BackgroundTasks
import requests
import os
import json
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
    cursor.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('CREATE TABLE IF NOT EXISTS user_profiles (session_id TEXT PRIMARY KEY, name TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS appointments (session_id TEXT PRIMARY KEY, event_id TEXT)')
    conn.commit()
    conn.close()

def limpar_historico_antigo(dias=30):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(f"DELETE FROM messages WHERE timestamp < datetime('now', '-{dias} days', '-3 hours')")
        deletados = cursor.rowcount
        conn.commit()
        if deletados > 0:
            print(f"🧹 Limpeza de rotina: {deletados} mensagens antigas apagadas.")
    except Exception as e:
        print(f"❌ Erro ao limpar o banco: {e}")
    finally:
        conn.close()

init_db()

@app.on_event("startup")
async def startup_event():
    print("🚀 Iniciando o servidor...")
    limpar_historico_antigo(30)

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

def save_user_name(session_id: str, name: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO user_profiles (session_id, name) VALUES (?, ?)', (session_id, name))
    conn.commit()
    conn.close()

def get_user_name(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM user_profiles WHERE session_id = ?', (session_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

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
    titulo_evento = f"Agendamento: {nome} - {servico}"
    evento_body = {
        'summary': titulo_evento,
        'start': {'dateTime': data_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'end': {'dateTime': data_fim.isoformat(), 'timeZone': 'America/Sao_Paulo'},
    }
    evento_criado = service.events().insert(calendarId=CALENDAR_ID, body=evento_body).execute()
    return evento_criado['id']

def deletar_evento(event_id):
    try:
        service = get_calendar_service()
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return True
    except Exception as e:
        print(f"Erro ao deletar evento pelo ID: {e}")
        return False

def deletar_eventos_por_nome(nome_cliente):
    if not nome_cliente: return False
    try:
        service = get_calendar_service()
        agora = datetime.now()
        timeMin = (agora - timedelta(days=1)).isoformat() + "-03:00"
        timeMax = (agora + timedelta(days=30)).isoformat() + "-03:00"
        eventos = service.events().list(calendarId=CALENDAR_ID, timeMin=timeMin, timeMax=timeMax, singleEvents=True).execute().get('items', [])
        
        deletou = False
        for evento in eventos:
            if nome_cliente.lower() in evento.get('summary', '').lower():
                print(f"🗑️ Deletando evento antigo (fallback nome): {evento['summary']}")
                service.events().delete(calendarId=CALENDAR_ID, eventId=evento['id']).execute()
                deletou = True
        return deletou
    except Exception as e:
        print(f"Erro ao deletar por nome: {e}")
        return False

# --- FUNÇÃO DE ENVIO ---
def enviar_whatsapp(numero_destino, texto_mensagem):
    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero_destino, "text": texto_mensagem}
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status Evolução: {response.status_code} | Resposta da API: {response.text}")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

async def enviar_com_delay(numero_destino, texto_mensagem):
    tempo_calculado = len(texto_mensagem) * 0.08
    tempo_de_espera = min(max(tempo_calculado, 3.0), 20.0)
    print(f"⏳ Sofia está digitando... (aguardando {tempo_de_espera:.1f}s)")
    await asyncio.sleep(tempo_de_espera)
    enviar_whatsapp(numero_destino, texto_mensagem)

# --- ENDPOINT DE MANUTENÇÃO ---
@app.get("/manutencao/limpar-banco")
async def limpar_banco_endpoint(senha: str = ""):
    if senha != os.getenv("EVOLUTION_API_KEY"):
        return {"status": "erro", "mensagem": "Acesso negado."}
    try:
        limpar_historico_antigo(30)
        return {"status": "sucesso", "mensagem": "Banco de dados limpo."}
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

# --- WEBHOOK PRINCIPAL ---
@app.post("/webhook/whatsapp")
@app.post("/webhook/whatsapp/")
@app.post("/webhook/whatsapp/messages-upsert")
async def receber_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    
    if data.get("event") == "messages.upsert":
        try:
            message_data = data["data"]
            
            jid_envio = message_data["key"]["remoteJid"]
            jid_alt = message_data["key"].get("remoteJidAlt")
            numero_usuario = jid_alt.split("@")[0] if jid_alt else jid_envio.split("@")[0]
            
            if message_data["key"].get("fromMe"): return {"status": "ok"}

            texto_recebido = message_data.get("message", {}).get("conversation", "")
            if not texto_recebido:
                 texto_recebido = message_data.get("message", {}).get("extendedTextMessage", {}).get("text", "")

            print(f"\n--- Nova Mensagem de {numero_usuario}: {texto_recebido} ---")

            historico = get_chat_history(numero_usuario)
            save_message(numero_usuario, "user", texto_recebido)

            agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nome_atual = get_user_name(numero_usuario) or "Não identificado"
            
            # --- SUPER PROMPT: COBRINDO TODOS OS CENÁRIOS DA BARBEARIA ---
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João. Você é educada, humana e profissional.
            Data/Hora atual: {agora}.
            Nome do cliente no sistema: {nome_atual}
            
            INFORMAÇÕES DA BARBEARIA:
            - Serviços: Corte Masculino (R$30), Corte Feminino (R$40), Corte+Barba (R$50), Barba (R$20).
            - Funcionamento: Segunda a Sábado, das 09:00 às 19:00. Fechado aos Domingos.
            - Pagamento: Aceitamos Cartão de Crédito, Débito e Pix.
            
            REGRAS IMPORTANTES (Responda SEMPRE em formato JSON):
            
            1. SAUDAÇÃO / NOME:
               - Se o nome for "Não identificado", pergunte o nome educadamente.
               - Se o cliente informar o nome, retorne: 
                 {{"acao": "informar_nome", "nome": "Nome do Cliente", "resposta": "Sua saudação usando o histórico para continuar o assunto"}}
            
            2. CHECAR HORÁRIO (SEM AGENDAR):
               - Se o cliente perguntar por um horário (ex: "tem 14h?"), use a ação "checar_horario". 
               - Se ele não citou o serviço, pergunte qual serviço ele quer antes de checar.
                 {{"acao": "checar_horario", "horario": "AAAA-MM-DDTHH:MM", "servico": "Nome do Serviço", "nome": "Nome do Cliente"}}
            
            3. CONFIRMAR AGENDAMENTO (SÓ USAR APÓS O CLIENTE DIZER SIM):
               - Se você já ofereceu um horário e o cliente respondeu "Sim", "Pode", "Confirmo" ou similar, use:
                 {{"acao": "confirmar_agendamento", "horario": "AAAA-MM-DDTHH:MM", "servico": "Nome do Serviço", "nome": "Nome do Cliente"}}
            
            4. REAGENDAR (MUDAR UM HORÁRIO JÁ EXISTENTE):
               - Se o cliente já tem um horário e pedir para mudar:
                 {{"acao": "reagendar", "horario_novo": "AAAA-MM-DDTHH:MM", "servico": "Nome do Serviço", "nome": "Nome do Cliente"}}
            
            5. CANCELAR:
               - Se o cliente pedir para desmarcar ou cancelar o horário dele:
                 {{"acao": "cancelar", "nome": "Nome do Cliente"}}
            
            6. DÚVIDAS OU CONVERSAS:
               - Para perguntas sobre preços, horários de funcionamento, pagamento ou conversa fiada:
                 {{"acao": "duvida", "resposta": "Sua resposta humanizada aqui"}}
            
            ATENÇÃO CRÍTICA: 
            - O formato AAAA-MM-DDTHH:MM é de uso INTERNO. NUNCA peça nem mostre esse formato ao cliente. 
            - Se o cliente disser "amanhã às 14h", "sexta de manhã", converta para a data correta usando a Data/Hora atual acima.
            - Analise o histórico da conversa. Se o cliente já disse o serviço ou o nome antes, inclua esses dados no JSON atual.
            """

            messages_for_api = [{"role": "system", "content": system_prompt}]
            for msg in historico: messages_for_api.append(msg)
            messages_for_api.append({"role": "user", "content": texto_recebido})

            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages_for_api, 
                    response_format={"type": "json_object"}
                )
                resposta_ia_raw = response.choices[0].message.content
            except Exception as e:
                print(f"Erro na API da OpenAI: {e}")
                background_tasks.add_task(enviar_com_delay, jid_envio, "Tive um problema de conexão. Pode repetir?")
                return {"status": "ok"}

            intencao = extrair_json(resposta_ia_raw)
            print(f"Intenção da IA: {intencao}")
            
            if not intencao or not isinstance(intencao, dict):
                print(f"⚠️ FALHA NO JSON DA IA: {resposta_ia_raw}")
                intencao = {"acao": "duvida", "resposta": "Desculpe, me confundi. Pode repetir?"}

            save_message(numero_usuario, "assistant", json.dumps(intencao))
            resposta_final = ""
            acao = intencao.get("acao")
            
            # Atualiza o nome no banco se a IA extraiu ele
            nome_ia = intencao.get("nome")
            if nome_ia and isinstance(nome_ia, str) and len(nome_ia) > 2:
                save_user_name(numero_usuario, nome_ia)
                nome_atual = nome_ia

            # --- LÓGICA 1: INFORMAR NOME ---
            if acao == "informar_nome":
                resposta_final = intencao.get("resposta", f"Prazer em te conhecer, {nome_atual}! Como posso ajudar?")

            # --- LÓGICA 2: CHECAR HORÁRIO (APENAS CHECA, NÃO AGENDA) ---
            elif acao == "checar_horario":
                data_inicio = parse_data(intencao.get("horario"))
                servico = intencao.get("servico") or "Corte Simples"
                
                if not data_inicio:
                    resposta_final = "❌ Não consegui entender a data. Pode repetir? (ex: hoje às 10h)"
                else:
                    if checar_disponibilidade(data_inicio):
                        if not get_user_name(numero_usuario):
                            resposta_final = f"✅ Temos horário às {data_inicio.strftime('%H:%M')}. Qual o seu nome para eu já deixar reservado?"
                        else:
                            resposta_final = f"✅ Temos horário disponível para {servico} às {data_inicio.strftime('%d/%m às %H:%M')}. Posso confirmar o seu agendamento?"
                    else:
                        resposta_final = f"❌ Infelizmente, o horário das {data_inicio.strftime('%H:%M')} já está ocupado. Gostaria de tentar outro horário?"

            # --- LÓGICA 3: CONFIRMAR AGENDAMENTO (CRIA O EVENTO) ---
            elif acao == "confirmar_agendamento":
                data_inicio = parse_data(intencao.get("horario"))
                servico = intencao.get("servico") or "Corte Simples"
                
                if not data_inicio:
                    resposta_final = "❌ Não consegui entender a data do agendamento. Pode repetir?"
                elif not get_user_name(numero_usuario):
                    resposta_final = "Só preciso do seu nome para finalizar o agendamento."
                else:
                    if checar_disponibilidade(data_inicio): # Checa de novo por segurança
                        duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                        try:
                            evento_id = criar_evento(get_user_name(numero_usuario), servico, data_inicio, duracao)
                            save_event_id(numero_usuario, evento_id)
                            resposta_final = f"✅ Excelente! Seu agendamento de {servico} foi confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                        except Exception as e_cal:
                            print(f"❌ ERRO NO GOOGLE CALENDAR: {e_cal}")
                            resposta_final = "⚠️ Problema técnico na agenda."
                    else:
                        resposta_final = f"❌ Poxa, alguém acabou de pegar o horário das {data_inicio.strftime('%H:%M')}. Qual outro horário fica bom?"

            # --- LÓGICA 4: REAGENDAR ---
            elif acao == "reagendar":
                if not get_user_name(numero_usuario):
                    resposta_final = "Só um instante, qual é o seu nome para eu localizar seu agendamento?"
                else:
                    dt_novo = parse_data(intencao.get("horario_novo"))
                    servico = intencao.get("servico") or "Corte Simples"
                    
                    if not dt_novo:
                        resposta_final = "❌ Não consegui entender a nova data. Pode repetir?"
                    else:
                        duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                        
                        old_event_id = get_event_id(numero_usuario)
                        deletou = False
                        
                        if old_event_id:
                            if deletar_evento(old_event_id):
                                deletou = True
                        
                        if not deletou:
                            print("⚠️ ID não encontrado no banco ou falhou. Tentando deletar pelo nome...")
                            deletou = deletar_eventos_por_nome(get_user_name(numero_usuario))

                        if not deletou:
                            try:
                                novo_evento_id = criar_evento(get_user_name(numero_usuario), servico, dt_novo, duracao)
                                save_event_id(numero_usuario, novo_evento_id)
                                resposta_final = f"⚠️ Não achei o agendamento antigo para apagar, mas criei o novo! {servico} para {dt_novo.strftime('%d/%m às %H:%M')}."
                            except:
                                resposta_final = "⚠️ Tive um problema técnico na agenda."
                        else:
                            if checar_disponibilidade(dt_novo, duracao):
                                try:
                                    novo_evento_id = criar_evento(get_user_name(numero_usuario), servico, dt_novo, duracao)
                                    save_event_id(numero_usuario, novo_evento_id)
                                    resposta_final = f"✅ Excelente! Seu agendamento de {servico} foi reagendado para {dt_novo.strftime('%d/%m às %H:%M')}."
                                except Exception as e_cal:
                                    print(f"❌ ERRO NO GOOGLE CALENDAR: {e_cal}")
                                    resposta_final = "⚠️ Problema técnico na agenda."
                            else:
                                resposta_final = f"❌ Infelizmente, o horário das {dt_novo.strftime('%H:%M')} está ocupado. O antigo foi cancelado, qual outro horário fica bom?"

            # --- LÓGICA 5: CANCELAR ---
            elif acao == "cancelar":
                if not get_user_name(numero_usuario):
                    resposta_final = "Só um instante, qual é o seu nome para eu localizar seu agendamento?"
                else:
                    old_event_id = get_event_id(numero_usuario)
                    deletou = False
                    
                    if old_event_id:
                        if deletar_evento(old_event_id):
                            deletou = True
                    
                    if not deletou:
                        deletou = deletar_eventos_por_nome(get_user_name(numero_usuario))

                    if deletou:
                        resposta_final = "✅ Seu agendamento foi cancelado com sucesso. Se precisar remarcar, é só me avisar!"
                    else:
                        resposta_final = "❌ Não encontrei nenhum agendamento ativo no seu nome para cancelar."

            # --- LÓGICA 6: DÚVIDAS GERAIS ---
            else:
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            background_tasks.add_task(enviar_com_delay, jid_envio, resposta_final)

        except Exception as e:
            print(f"Erro GLOBAL ao processar: {e}")
            try: 
                fallback = jid_envio if 'jid_envio' in locals() else numero_usuario
                background_tasks.add_task(enviar_com_delay, fallback, "⚠️ Ops, tive um problema interno. Pode repetir?")
            except: pass
            
    return {"status": "ok"}
