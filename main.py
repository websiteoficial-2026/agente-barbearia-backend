from fastapi import FastAPI, Request, HTTPException
import requests
import os

app = FastAPI()

# Configure estas variáveis de ambiente na Railway posteriormente
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME")

def enviar_mensagem_whatsapp(numero_destino, texto_mensagem):
    """Função para enviar a resposta de volta ao usuário."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero_destino, "textMessage": {"text": texto_mensagem}}
    
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

@app.post("/webhook/whatsapp")
async def receber_webhook(request: Request):
    """Endpoint que a Evolution API vai chamar."""
    data = await request.json()
    
    # Verifica se é uma mensagem recebida (ignora confirmações de leitura, etc.)
    if data.get("event") == "messages.upsert":
        try:
            # Extrai os dados da mensagem
            message_data = data["data"]
            remote_jid = message_data["key"]["remoteJid"] # Número do usuário
            numero_usuario = remote_jid.split("@")[0]
            
            # Ignora mensagens enviadas pelo próprio bot para evitar loops
            if message_data["key"].get("fromMe"): 
                return {"status": "ok"}

            # Extrai o texto da mensagem (pode variar conforme o tipo)
            texto_recebido = message_data.get("message", {}).get("conversation", "")
            if not texto_recebido:
                 texto_recebido = message_data.get("message", {}).get("extendedTextMessage", {}).get("text", "")

            print(f"Mensagem de {numero_usuario}: {texto_recebido}")

            # --- AQUI ENTRA A LÓGICA DO SEU AGENTE ---
            # Exemplo simples de resposta automática:
            resposta = f"Olá! Você disse: '{texto_recebido}'. Como posso ajudar com seu agendamento?"
            
            # Envia a resposta
            enviar_mensagem_whatsapp(numero_usuario, resposta)

        except Exception as e:
            print(f"Erro ao processar webhook: {e}")
            # Retorna 200 mesmo em erro para a Evolution API não ficar reenviando
            return {"status": "error", "message": str(e)}
            
    return {"status": "ok"}
