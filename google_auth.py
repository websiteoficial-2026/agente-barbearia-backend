import os
import json
from google.oauth2 import service_account # <--- ESSA LINHA É A CHAVE
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    creds_json_str = os.getenv('GOOGLE_CREDENTIALS_JSON')
    
    if not creds_json_str:
        raise Exception("❌ Variável GOOGLE_CREDENTIALS_JSON não encontrada na Railway.")

    try:
        # Corrige o problema de quebra de linha na chave privada
        creds_json_str = creds_json_str.replace('\\n', '\n')
        creds_info = json.loads(creds_json_str)
        
        # ISSO AQUI É O QUE DIFERENCIA A CONTA DE SERVIÇO
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
        
        return build('calendar', 'v3', credentials=creds)
        
    except Exception as e:
        print(f"❌ Erro ao autenticar com Conta de Serviço: {e}")
        raise e
