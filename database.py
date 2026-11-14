import sqlite3
from datetime import datetime

def init_database():
    """Inicializa o banco de dados com as tabelas necessárias"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()

    # Tabela de conversas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telefone TEXT NOT NULL,
            mensagem TEXT NOT NULL,
            tipo TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Tabela de clientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telefone TEXT UNIQUE NOT NULL,
            nome TEXT,
            ultima_interacao DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Tabela de agendamentos (cache local do Google Calendar)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agendamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_event_id TEXT UNIQUE,
            cliente_telefone TEXT NOT NULL,
            cliente_nome TEXT,
            servico TEXT NOT NULL,
            data_hora DATETIME NOT NULL,
            duracao_minutos INTEGER DEFAULT 30,
            status TEXT DEFAULT 'confirmado',
            observacoes TEXT,
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_telefone) REFERENCES clientes(telefone)
        )
    ''')

    # Tabela de serviços
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS servicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            duracao_minutos INTEGER NOT NULL,
            preco REAL,
            ativo INTEGER DEFAULT 1
        )
    ''')

    # Inserir serviços padrão
    servicos_padrao = [
        ('Corte Simples', 30, 30.00),
        ('Corte + Barba', 45, 50.00),
        ('Barba', 20, 25.00),
        ('Corte Degrade', 40, 40.00),
        ('Sobrancelha', 10, 15.00)
    ]

    cursor.executemany('''
        INSERT OR IGNORE INTO servicos (nome, duracao_minutos, preco)
        VALUES (?, ?, ?)
    ''', servicos_padrao)

    conn.commit()
    conn.close()
    print("✅ Database inicializado com sucesso!")

def salvar_conversa(telefone, mensagem, tipo):
    """Salva uma mensagem no histórico"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO conversas (telefone, mensagem, tipo) VALUES (?, ?, ?)',
        (telefone, mensagem, tipo)
    )
    conn.commit()
    conn.close()

def obter_historico(telefone, limite=10):
    """Retorna o histórico de conversas de um telefone"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT mensagem, tipo, timestamp
           FROM conversas
           WHERE telefone = ?
           ORDER BY timestamp DESC
           LIMIT ?''',
        (telefone, limite)
    )
    historico = cursor.fetchall()
    conn.close()
    return list(reversed(historico))

def registrar_cliente(telefone, nome=None):
    """Registra ou atualiza informações do cliente"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO clientes (telefone, nome, ultima_interacao)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(telefone) DO UPDATE SET
           nome = COALESCE(?, nome),
           ultima_interacao = CURRENT_TIMESTAMP''',
        (telefone, nome, nome)
    )
    conn.commit()
    conn.close()

def obter_servicos():
    """Retorna lista de serviços ativos"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()
    cursor.execute('SELECT nome, duracao_minutos, preco FROM servicos WHERE ativo = 1')
    servicos = cursor.fetchall()
    conn.close()
    return servicos

def salvar_agendamento(google_event_id, cliente_telefone, cliente_nome, servico,
                       data_hora, duracao_minutos, observacoes=None):
    """Salva um agendamento no banco local"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO agendamentos
           (google_event_id, cliente_telefone, cliente_nome, servico, data_hora,
            duracao_minutos, observacoes)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (google_event_id, cliente_telefone, cliente_nome, servico, data_hora,
         duracao_minutos, observacoes)
    )
    conn.commit()
    conn.close()

def obter_agendamentos_cliente(telefone):
    """Retorna agendamentos futuros de um cliente"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT servico, data_hora, status, observacoes
           FROM agendamentos
           WHERE cliente_telefone = ? AND data_hora >= datetime('now')
           ORDER BY data_hora''',
        (telefone,)
    )
    agendamentos = cursor.fetchall()
    conn.close()
    return agendamentos

def cancelar_agendamento(google_event_id):
    """Marca um agendamento como cancelado"""
    conn = sqlite3.connect('/data/barbearia.db')
    cursor = conn.cursor()
    cursor.execute(
        '''UPDATE agendamentos
           SET status = 'cancelado', atualizado_em = CURRENT_TIMESTAMP
           WHERE google_event_id = ?''',
        (google_event_id,)
    )
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_database()
