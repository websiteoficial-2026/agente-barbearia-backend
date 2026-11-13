from fastapi import FastAPI, Request
import requests
import os
import json
import re # <-- Adicionado para limpar o JSON
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

# --- Função FAXINEIRO DE JSON (Limpa a resposta da IA) ---
def extrair_json(texto):
    try:
        # Tenta ler direto
        return json.loads(texto)
    except json.JSONDecodeError:
        # Se falhar, tenta achar o JSON no meio do texto usando Regex
        match = re.search(r'\{.*\}', texto, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except:
                pass
    return None # Se não achar, retorna nada

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
        if response.status_code not in [200, 201]:
            print(f"RESPOSTA DE ERRO DA EVOLUÇÃO: {response.text}")
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

            agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nome_atual = user_names.get(numero_usuario) or "Não identificado"
            
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João. Você é educada, humana e profissional.
            Data/Hora atual: {agora}.
            Nome do cliente: {nome_atual}
            Serviços: Corte Simples (30min, R$30), Corte+Barba (45min, R$50), Barba (20min, R$20).
            
            REGRAS IMPORTANTES (Responda SEMPRE em formato JSON puro, sem blocos de código markdown):
            1. Se o nome for "Não identificado", pergunte o nome de forma educada ANTES de prosseguir.
            2. Se o cliente informar o nome, retorne: 
            {{"acao": "informar_nome", "nome": "Nome", "resposta": "Sua saudação usando o histórico para continuar o assunto"}}
            3. Se o cliente perguntar por um horário (ex: "tem 14h?"), retorne:
            {{"acao": "verificar_horario", "horario": "AAAA-MM-DDTHH:MM", "servico": "nome do serviço se ele citou, senão deixe nulo"}}
            4. Se o cliente pedir para mudar um agendamento feito, retorne:
            {{"acao": "reagendar", "horario_novo": "AAAA-MM-DDTHH:MM", "servico": "nome"}}
            5. Para dúvidas ou conversas, retorne:
            {{"acao": "duvida", "resposta": "Sua resposta"}}
            6. O formato AAAA-MM-DDTHH:MM é de uso INTERNO. NUNCA peça nem mencione esse formato ao cliente.
            7. Se o cliente já escolheu o serviço mas ainda não disse dia e horário, use "acao": "duvida" e pergunte de forma natural.
            """

            messages_for_api = [{"role": "system", "content": system_prompt}]
            for msg in user_memories[numero_usuario]:
                messages_for_api.append(msg)
            
            user_memories[numero_usuario].append({"role": "user", "content": texto_recebido})
            messages_for_api.append({"role": "user", "content": texto_recebido})

            # Removemos o response_format para o Llama não engasgar
            response = client.chat.completions.create(
                model=model_name,
                messages=messages_for_api
            )
            
            resposta_ia_raw = response.choices[0].message.content
            
            # Usa o faxineiro para pegar o JSON
            intencao = extrair_json(resposta_ia_raw)
            
            if not intencao:
                print(f"ERRO CRÍTICO: IA não retornou JSON válido. Resposta bruta: {resposta_ia_raw}")
                enviar_whatsapp(numero_usuario, "Desculpe, tive um problema interno. Pode repetir?")
                return {"status": "ok"}

            # Salva no histórico apenas o JSON limpo para não poluir a memória
            user_memories[numero_usuario].append({"role": "assistant", "content": json.dumps(intencao)})

            resposta_final = ""
            acao = intencao.get("acao")

            if acao == "informar_nome":
                user_names[numero_usuario] = intencao["nome"]
                resposta_final = intencao.get("resposta", f"Prazer em te conhecer, {intencao['nome']}! Como posso ajudar?")

            elif acao == "verificar_horario":
                data_inicio = datetime.fromisoformat(intencao["horario"])
                servico = intencao.get("servico")
                
                if checar_disponibilidade(data_inicio):
                    if servico:
                        if not user_names.get(numero_usuario):
                            resposta_final = f"✅ Sim, temos horário às {data_inicio.strftime('%H:%M')}. Só me diga seu nome para eu confirmar o agendamento."
                        else:
                            duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                            evento_id = criar_evento(user_names[numero_usuario], servico, data_inicio, duracao)
                            user_last_events[numero_usuario] = evento_id 
                            resposta_final = f"✅ Excelente! Seu agendamento de {servico} foi confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                    else:
                        resposta_final = f"✅ Sim, temos horário às {data_inicio.strftime('%H:%M')} do dia {data_inicio.strftime('%d/%m')}. Qual serviço deseja?"
                else:
                    resposta_final = f"❌ Infelizmente, o horário das {data_inicio.strftime('%H:%M')} já está ocupado. Gostaria de tentar outro horário?"

            elif acao == "reagendar":
                if not user_names.get(numero_usuario):
                    resposta_final = "Só um instante, qual é o seu nome para eu localizar seu agendamento?"
                else:
                    dt_novo = datetime.fromisoformat(intencao["horario_novo"])
                    servico = intencao["servico"]
                    duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)

                    service = get_calendar_service()
                    deletou = False
                    
                    if user_last_events.get(numero_usuario):
                        try:
                            service.events().delete(calendarId=CALENDAR_ID, eventId=user_last_events[numero_usuario]).execute()
                            user_last_events[numero_usuario] = None
                            deletou = True
                        except Exception:
                            pass

                    if not deletou:
                        resposta_final = "Não localizei seu agendamento anterior. Vamos fazer um novo?"
                    else:
                        if checar_disponibilidade(dt_novo, duracao):
                            evento_id = criar_evento(user_names[numero_usuario], servico, dt_novo, duracao)
                            user_last_events[numero_usuario] = evento_id
                            resposta_final = f"✅ Excelente! Seu agendamento de {servico} foi reagendado para {dt_novo.strftime('%d/%m às %H:%M')}."
                        else:
                            resposta_final = f"❌ Infelizmente, o horário das {dt_novo.strftime('%H:%M')} está ocupado. O antigo foi cancelado, qual outro horário fica bom?"
            else:
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            enviar_whatsapp(numero_usuario, resposta_final)

        except Exception as e:
            print(f"Erro ao processar: {e}")
            
    return {"status": "ok"}
