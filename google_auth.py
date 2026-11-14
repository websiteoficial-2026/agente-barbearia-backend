import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    # Pega o JSON da variável de ambiente
    creds_json_str = os.getenv('GOOGLE_CREDENTIALS_JSON')
    
    if not creds_json_str:
        raise Exception("❌ Variável GOOGLE_CREDENTIALS_JSON não encontrada na Railway.")

    try:
        # O json.loads já sabe lidar com os "\n" da private_key
        creds_info = json.loads(creds_json_str)
        
        # Autentica usando a Conta de Serviço
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
        
        return build('calendar', 'v3', credentials=creds)
        
    except Exception as e:
        print(f"❌ Erro ao autenticar com Conta de Serviço: {e}")
        raise e
