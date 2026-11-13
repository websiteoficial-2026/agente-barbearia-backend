import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Escopo de acesso ao Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    creds = None
    token_path = 'token.json'
    credentials_path = 'credentials.json'

    # Tenta carregar o token (do arquivo físico ou das variáveis de ambiente da Railway)
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    elif os.getenv('GOOGLE_TOKEN_JSON'):
        # Se estiver rodando na nuvem, lê o JSON salvo nas variáveis de ambiente
        token_info = json.loads(os.getenv('GOOGLE_TOKEN_JSON'))
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)

    # Se não houver credenciais válidas, tenta renovar ou pedir login
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Este bloco tentará abrir o navegador (só funciona na sua máquina local, não na Railway)
            if os.path.exists(credentials_path):
                flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                raise Exception("Credenciais do Google não encontradas. Configure o token.json ou a variável GOOGLE_TOKEN_JSON.")

        # Salva o token para a próxima execução (se estiver local)
        if not os.getenv('GOOGLE_TOKEN_JSON'):
            with open(token_path, 'w') as token:
                token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)
