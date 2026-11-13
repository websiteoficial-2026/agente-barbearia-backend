from fastapi import FastAPI, Request
import requests
import os
import json
from datetime import datetime, timedelta
from openai import OpenAI
from google_auth import get_calendar_service

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

# === MEMÓRIA DO CHAT ===
user_memories = {}      
user_names = {}         
user_last_events = {}   

# --- Função FAXINEIRO DE JSON SUPER RESISTENTE ---
def extrair_json(texto):
    # Limpa blocos de código markdown que a IA às vezes envia
    texto_limpo = texto.replace('```json', '').replace('```', '').strip()
    try:
        return json.loads(texto_limpo)
    except json.JSONDecodeError:
        # Tenta achar a primeira chave '{' e a última '}'
        start = texto_limpo.find('{')
        end = texto_limpo.rfind('}')
        if start != -1 and end != -1 and start < end:
            try:
                return json.loads(texto_limpo[start:end+1])
            except:
                pass
                
    print(f"--- TEXTO BRUTO DA IA QUE QUEBROU O SISTEMA ---")
    print(texto)
    print(f"-----------------------------------------------")
    return None

# --- Funções Auxiliares ---
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

            if numero_usuario not in user_memories:
                user_memories[numero_usuario] = []
                user_names[numero_usuario] = None

            agora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            nome_atual = user_names.get(numero_usuario) or "Não identificado"
            
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João.
            Data/Hora atual do sistema: {agora}.
            Nome do cliente: {nome_atual}
            Serviços: Corte Simples (30min, R$30), Corte+Barba (45min, R$50), Barba (20min, R$20).
            
            REGRAS (Responda APENAS o objeto JSON, sem crases, sem texto antes ou depois):
            1. Se o nome for "Não identificado", pergunte o nome.
            2. Se o cliente informar o nome, retorne: 
            {{"acao": "informar_nome", "nome": "Nome", "resposta": "Sua saudação"}}
            3. Se o cliente perguntar por um horário, você deve calcular a data exata baseada na "Data/Hora atual" e retornar:
            {{"acao": "verificar_horario", "horario": "YYYY-MM-DDTHH:MM", "servico": "nome"}}
            4. Se o cliente pedir para mudar um agendamento, retorne:
            {{"acao": "reagendar", "horario_novo": "YYYY-MM-DDTHH:MM", "servico": "nome"}}
            5. Para dúvidas ou conversas, retorne:
            {{"acao": "duvida", "resposta": "Sua resposta"}}
            """

            messages_for_api = [{"role": "system", "content": system_prompt}]
            for msg in user_memories[numero_usuario]:
                messages_for_api.append(msg)
            
            user_memories[numero_usuario].append({"role": "user", "content": texto_recebido})
            messages_for_api.append({"role": "user", "content": texto_recebido})

            # Chamada da IA
            response = client.chat.completions.create(
                model=model_name,
                messages=messages_for_api, 
                response_format={"type": "json_object"}
            )
            
            resposta_ia_raw = response.choices[0].message.content
            intencao = extrair_json(resposta_ia_raw)
            
            if not intencao:
                enviar_whatsapp(numero_usuario, "Desculpe, me confundi com os dados. Pode me dizer o dia e horário novamente?")
                return {"status": "ok"}

            user_memories[numero_usuario].append({"role": "assistant", "content": json.dumps(intencao)})
            resposta_final = ""
            acao = intencao.get("acao")

            if acao == "informar_nome":
                user_names[numero_usuario] = intencao["nome"]
                resposta_final = intencao.get("resposta", f"Prazer, {intencao['nome']}!")

            elif acao == "verificar_horario":
                try:
                    # Converte a data que a IA mandou
                    data_inicio = datetime.fromisoformat(intencao["horario"])
                    servico = intencao.get("servico", "Corte Simples")
                    
                    if checar_disponibilidade(data_inicio):
                        if not user_names.get(numero_usuario):
                            resposta_final = f"✅ Temos horário às {data_inicio.strftime('%H:%M')}. Qual o seu nome?"
                        else:
                            duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                            
                            # Tenta criar no calendário
                            try:
                                evento_id = criar_evento(user_names[numero_usuario], servico, data_inicio, duracao)
                                user_last_events[numero_usuario] = evento_id 
                                resposta_final = f"✅ Agendado! {servico} confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                            except Exception as e_cal:
                                print(f"ERRO NO GOOGLE CALENDAR: {e_cal}")
                                resposta_final = "⚠️ Tive um problema ao acessar a agenda. Pode tentar novamente?"
                    else:
                        resposta_final = f"❌ O horário das {data_inicio.strftime('%H:%M')} está ocupado."
                        
                except Exception as e_data:
                    print(f"ERRO NA DATA DA IA: {e_data} | Data recebida: {intencao.get('horario')}")
                    resposta_final = "❌ Não consegui entender a data. Pode repetir? (ex: amanhã às 14h)"

            elif acao == "reagendar":
                if not user_names.get(numero_usuario):
                    resposta_final = "Qual é o seu nome para eu localizar o agendamento?"
                else:
                    try:
                        dt_novo = datetime.fromisoformat(intencao["horario_novo"])
                        servico = intencao.get("servico", "Corte Simples")
                        duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                        
                        try:
                            service = get_calendar_service()
                            if user_last_events.get(numero_usuario):
                                service.events().delete(calendarId=CALENDAR_ID, eventId=user_last_events[numero_usuario]).execute()
                            
                            if checar_disponibilidade(dt_novo, duracao):
                                evento_id = criar_evento(user_names[numero_usuario], servico, dt_novo, duracao)
                                user_last_events[numero_usuario] = evento_id
                                resposta_final = f"✅ Reagendado! {servico} para {dt_novo.strftime('%d/%m às %H:%M')}."
                            else:
                                resposta_final = f"❌ O horário das {dt_novo.strftime('%H:%M')} está ocupado."
                        except Exception as e_cal:
                            print(f"ERRO NO GOOGLE CALENDAR: {e_cal}")
                            resposta_final = "⚠️ Tive um problema ao acessar a agenda para reagendar."
                            
                    except Exception as e_data:
                        resposta_final = "❌ Não entendi a nova data. Pode repetir?"
            else:
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            enviar_whatsapp(numero_usuario, resposta_final)

        except Exception as e:
            print(f"Erro GLOBAL ao processar: {e}")
            
    return {"status": "ok"}
