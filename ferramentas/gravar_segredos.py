"""Grava no cofre do GitHub (Secrets do projeto tv) as credenciais e as chaves, sem mostrar nada na tela.
Rodar pelo próprio usuário (uma vez, ou de novo para trocar uma senha ou chave):
    python ferramentas/gravar_segredos.py
Lê de C:\\Users\\<usuário>\\.claude: evo-api-credentials.json, govoice-credentials.json e ufit-tvs-chaves.json.
"""
import json, os, subprocess

CASA = os.path.join(os.path.expanduser('~'), '.claude')
GH = os.path.join(os.path.expanduser('~'), '.local', 'gh', 'bin', 'gh.exe')
PROJETO = 'ufitanalista/tv'


def ler(n):
    with open(os.path.join(CASA, n), encoding='utf-8') as f:
        return f.read()


def token_evotalks():
    with open(os.path.join(os.path.expanduser('~'), '.claude.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    for proj in cfg.get('projects', {}).values():
        omni = (proj.get('mcpServers') or {}).get('omni')
        if omni:
            return omni['headers']['Authorization']
    raise SystemExit('token do EvoTalks (MCP omni) não encontrado em ~/.claude.json')


chaves = json.loads(ler('ufit-tvs-chaves.json'))
segredos = {
    'EVO_CRED': ler('evo-api-credentials.json'),
    'GOVOICE_CRED': ler('govoice-credentials.json'),
    'CHAVE_TV': chaves['tv'],
    'CHAVE_INTERNA': chaves['interna'],
    'CHAVE_DP': chaves['dp'],  # aba Período da TV 2 (senha do DP)
    'CHAVE_CONV': chaves['conv'],  # Painel de convênios (senha própria)
    'CHAVE_GESTAO': chaves['gestao'],  # Painel da Gestão (senha própria)
    'CHAVE_FIN': chaves['fin'],  # Painel de Vendas do financeiro (senha própria)
    'EVOTALKS_TOKEN': token_evotalks(),  # o mesmo do MCP "omni"; o coletor só faz consultas de leitura
}
for nome, valor in segredos.items():
    subprocess.run([GH, 'secret', 'set', nome, '-R', PROJETO], input=valor.encode('utf-8'), check=True)
subprocess.run([GH, 'secret', 'list', '-R', PROJETO], check=True)
