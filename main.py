from fastapi import FastAPI, Request
import requests
import os
import json
from datetime import datetime, timedelta
from openai import OpenAI
from google_auth import get_calendar_service
import base64

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

# --- Funções do Google Calendar ---
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

# --- Função de Envio ---
def enviar_whatsapp(numero_destino, texto_mensagem):
    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    
    # === ALTERE O PAYLOAD PARA ESTE FORMATO ===
    payload = {
        "number": numero_destino,
        "text": texto_mensagem
    }
    # ==========================================

    print(f"--- ENVIANDO PARA: {numero_destino} ---")
    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Status Evolution: {response.status_code}")
        if response.status_code != 200:
            print(f"RESPOSTA DE ERRO DA EVOLUTION: {response.text}")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

# --- Endpoint do Webhook ---
@app.post("/webhook/whatsapp")
async def receber_webhook(request: Request):
    data = await request.json()
    
    if data.get("event") == "messages.upsert":
        try:
            message_data = data["data"]
            remote_jid = message_data["key"]["remoteJid"]
            numero_usuario = remote_jid.split("@")[0]
            
            # Ignora mensagens enviadas pelo próprio bot
            if message_data["key"].get("fromMe"): 
                return {"status": "ok"}

            texto_recebido = message_data.get("message", {}).get("conversation", "")
            if not texto_recebido:
                 texto_recebido = message_data.get("message", {}).get("extendedTextMessage", {}).get("text", "")

            print(f"Mensagem de {numero_usuario}: {texto_recebido}")

            # --- LÓGICA DA SOFIA ---
            agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João. Você é educada, humana e profissional.
            Data/Hora atual: {agora}.
            Serviços: Corte Simples (30min, R$30), Corte+Barba (45min, R$50), Barba (20min, R$20).
            
            REGRAS IMPORTANTES (Responda SEMPRE em formato JSON):
            1. Se o cliente for novo, pergunte o nome de forma educada ANTES de prosseguir.
            2. Se o cliente informar o nome, retorne: 
            {{"acao": "informar_nome", "nome": "Nome", "resposta": "Sua saudação"}}
            3. Se o cliente perguntar por um horário, retorne:
            {{"acao": "verificar_horario", "horario": "AAAA-MM-DDTHH:MM", "servico": "nome do serviço", "nome": "nome do cliente se ele disse"}}
            4. Para dúvidas ou conversas, retorne:
            {{"acao": "duvida", "resposta": "Sua resposta"}}
            """

            messages_for_api = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": texto_recebido}
            ]

            response = client.chat.completions.create(
                model=model_name,
                messages=messages_for_api, 
                response_format={"type": "json_object"}
            )
            intencao = json.loads(response.choices[0].message.content)
            acao = intencao.get("acao")
            resposta_final = ""

            # Processamento básico para teste
            if acao == "informar_nome":
                resposta_final = intencao.get("resposta")
            elif acao == "verificar_horario":
                data_inicio = datetime.fromisoformat(intencao["horario"])
                servico = intencao.get("servico")
                nome = intencao.get("nome", "Cliente")
                
                if checar_disponibilidade(data_inicio):
                    duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                    criar_evento(nome, servico, data_inicio, duracao)
                    resposta_final = f"✅ Excelente! Seu agendamento de {servico} foi confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                else:
                    resposta_final = f"❌ Infelizmente, o horário das {data_inicio.strftime('%H:%M')} já está ocupado."
            else:
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            # Envia a resposta
            enviar_whatsapp(numero_usuario, resposta_final)

        except Exception as e:
            print(f"Erro ao processar: {e}")
            
    return {"status": "ok"}
