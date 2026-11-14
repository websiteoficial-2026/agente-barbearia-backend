import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    creds = None
    token_path = 'token.json'
    credentials_path = 'credentials.json'

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    elif os.getenv('GOOGLE_TOKEN_JSON'):
        try:
            # Tenta ler o token. Se estiver mal formatado, avisa.
            token_str = os.getenv('GOOGLE_TOKEN_JSON')
            token_info = json.loads(token_str)
            creds = Credentials.from_authorized_user_info(token_info, SCOPES)
        except json.JSONDecodeError as e:
            print(f"❌ ERRO FATAL: O GOOGLE_TOKEN_JSON está mal formatado! Erro: {e}")
            raise Exception("Token do Google inválido")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if os.path.exists(credentials_path):
                flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                raise Exception("Credenciais do Google não encontradas.")
        
        if not os.getenv('GOOGLE_TOKEN_JSON'):
            with open(token_path, 'w') as token:
                token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)
