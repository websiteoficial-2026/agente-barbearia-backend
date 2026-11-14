from fastapi import FastAPI, Request
import requests
import os
import json
import re
from datetime import datetime, timedelta
from openai import OpenAI
from google_auth import get_calendar_service
import dateparser
import database # <--- IMPORTANDO O SEU ARQUIVO DE BANCO DE DADOS

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

# --- INICIALIZAÇÃO DO BANCO DE DADOS ---
@app.on_event("startup")
def startup_event():
    database.init_database()
    print("✅ Banco de dados inicializado.")

# --- Faxineiro de JSON (Blindado) ---
def extrair_json(texto):
    if not texto: return None
    try:
        return json.loads(texto)
    except:
        pass
    match = re.search(r'\{.*\}', texto, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            pass
        try:
            texto_corrigido = match.group(0).replace("'", '"')
            return json.loads(texto_corrigido)
        except:
            pass
    print(f"--- ERRO: A IA NÃO GEROU UM JSON VÁLIDO ---")
    print(texto)
    return None

# --- Parser de Data Humano ---
def parse_data(texto_data):
    try:
        return datetime.fromisoformat(texto_data)
    except:
        return dateparser.parse(texto_data, languages=['pt'])

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

            # 1. Salva a mensagem recebida no banco de dados
            database.salvar_conversa(numero_usuario, texto_recebido, "recebida")

            # 2. Recupera o histórico do banco de dados (últimas 10 mensagens)
            historico_db = database.obter_historico(numero_usuario, limite=10)
            
            # Monta o histórico no formato que a OpenAI entende
            messages_for_api = []
            for msg_texto, msg_tipo, _ in historico_db:
                role = "user" if msg_tipo == "recebida" else "assistant"
                messages_for_api.append({"role": role, "content": msg_texto})

            agora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            
            system_prompt = f"""
            Você é a Sofia, recepcionista da Barbearia do João.
            Data/Hora atual do sistema: {agora}.
            Serviços: Corte Simples (30min, R$30), Corte+Barba (45min, R$50), Barba (20min, R$20).
            
            REGRAS (Responda APENAS o objeto JSON puro):
            1. Se você ainda não sabe o nome do cliente (verifique o histórico), pergunte o nome.
            2. Se o cliente informar o nome, retorne: 
            {{"acao": "informar_nome", "nome": "Nome", "resposta": "Sua saudação"}}
            3. Se o cliente pedir um horário, você DEVE retornar no campo "horario" a data exata no formato YYYY-MM-DDTHH:MM.
               Exemplo: Se o cliente disser "hoje às 11h", e a data atual for 2026-11-13, retorne "2026-11-13T11:00".
            {{"acao": "verificar_horario", "horario": "2026-11-13T11:00", "servico": "Corte Simples"}}
            4. Para dúvidas, retorne: {{"acao": "duvida", "resposta": "Sua resposta"}}
            """

            # Insere o system prompt no começo da lista
            messages_for_api.insert(0, {"role": "system", "content": system_prompt})

            response = client.chat.completions.create(
                model=model_name,
                messages=messages_for_api, 
                response_format={"type": "json_object"}
            )
            
            resposta_ia_raw = response.choices[0].message.content
            intencao = extrair_json(resposta_ia_raw)
            
            if not intencao:
                enviar_whatsapp(numero_usuario, "Desculpe, tive um pequeno erro técnico. Pode repetir o dia e horário?")
                return {"status": "ok"}

            # 3. Salva a resposta da IA no banco (para ela lembrar do que ela mesma disse)
            database.salvar_conversa(numero_usuario, json.dumps(intencao), "enviada")

            resposta_final = ""
            acao = intencao.get("acao")

            if acao == "informar_nome":
                # Salva o nome do cliente na tabela de clientes do seu banco
                database.registrar_cliente(numero_usuario, intencao["nome"])
                resposta_final = intencao.get("resposta", f"Prazer, {intencao['nome']}!")

            elif acao == "verificar_horario":
                data_inicio = parse_data(intencao.get("horario", ""))
                
                if not data_inicio:
                    resposta_final = "❌ Não consegui entender a data. Pode repetir? (ex: amanhã às 14h)"
                else:
                    servico = intencao.get("servico", "Corte Simples")
                    
                    # Busca o nome no banco. (Como seu database.py não tem função de buscar nome,
                    # vamos usar uma query simples ou assumir que a IA já sabe pelo contexto).
                    # Para simplificar, usaremos "Cliente" se não soubermos. 
                    # (Dica: você pode adicionar uma função obter_cliente no database.py depois).
                    nome_cliente = "Cliente" 
                    
                    if checar_disponibilidade(data_inicio):
                        duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                        try:
                            evento_id = criar_evento(nome_cliente, servico, data_inicio, duracao)
                            
                            # Salva o agendamento no banco local SQLite
                            database.salvar_agendamento(evento_id, numero_usuario, nome_cliente, servico, data_inicio, duracao)
                            
                            resposta_final = f"✅ Agendado! {servico} confirmado para {data_inicio.strftime('%d/%m às %H:%M')}."
                        except Exception as e_cal:
                            print(f"ERRO NO GOOGLE CALENDAR: {e_cal}")
                            resposta_final = "⚠️ Tive um problema ao acessar a agenda. Pode tentar novamente?"
                    else:
                        resposta_final = f"❌ O horário das {data_inicio.strftime('%H:%M')} está ocupado."

            elif acao == "reagendar":
                dt_novo = parse_data(intencao.get("horario_novo", ""))
                if not dt_novo:
                        resposta_final = "❌ Não entendi a nova data. Pode repetir?"
                else:
                    servico = intencao.get("servico", "Corte Simples")
                    duracao = 45 if "Corte+Barba" in servico else (20 if "Barba" in servico else 30)
                    try:
                        service = get_calendar_service()
                        # Busca o último evento no seu banco de dados
                        agendamentos = database.obter_agendamentos_cliente(numero_usuario)
                        if agendamentos:
                            # Pega o evento mais recente
                            evento_id_antigo = agendamentos[0][0] # Supondo que o ID esteja na posição 0
                            service.events().delete(calendarId=CALENDAR_ID, eventId=evento_id_antigo).execute()
                            database.cancelar_agendamento(evento_id_antigo)
                        
                        if checar_disponibilidade(dt_novo, duracao):
                            evento_id = criar_evento("Cliente", servico, dt_novo, duracao)
                            database.salvar_agendamento(evento_id, numero_usuario, "Cliente", servico, dt_novo, duracao)
                            resposta_final = f"✅ Reagendado! {servico} para {dt_novo.strftime('%d/%m às %H:%M')}."
                        else:
                            resposta_final = f"❌ O horário das {dt_novo.strftime('%H:%M')} está ocupado."
                    except Exception as e_cal:
                        print(f"ERRO NO GOOGLE CALENDAR: {e_cal}")
                        resposta_final = "⚠️ Tive um problema ao acessar a agenda para reagendar."
            else:
                resposta_final = intencao.get("resposta", "Como posso ajudar?")

            # Envia a resposta para o cliente e salva no banco
            enviar_whatsapp(numero_usuario, resposta_final)
            database.salvar_conversa(numero_usuario, resposta_final, "enviada")

        except Exception as e:
            print(f"Erro GLOBAL ao processar: {e}")
            
    return {"status": "ok"}
