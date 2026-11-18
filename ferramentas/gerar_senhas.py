"""Cria as senhas que ainda não existem (nunca troca uma que já existe):
- senha da GESTÃO  → campos senha_gestao / gestao em ~/.claude/ufit-tvs-chaves.json (sal "ufit-gestao")
- senha de cada CONSULTOR (Balcão → Minha comissão) → ~/.claude/ufit-operadores-senhas.json  {nome: {senha, chave}} (sal "ufit-op")
  Consultores = nome_govoice de dashboard-tv/operadores.csv.

Trocar a senha de UM consultor (vazou, ou a pessoa saiu): python ferramentas/gerar_senhas.py --trocar Felipe
Depois de criar ou trocar: python publicar.py --enviar (a chave nova vai junto, criptografada, para o robô).
Nada é mostrado na tela além dos nomes.
"""
import csv, json, os, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
CASA = os.path.join(os.path.expanduser('~'), '.claude')
CHAVES = os.path.join(CASA, 'ufit-tvs-chaves.json')
OPS = os.path.join(CASA, 'ufit-operadores-senhas.json')
OPERADORES = os.path.join(AQUI, '..', '..', 'dashboard-tv', 'operadores.csv')


def node(*args, env=None):
    return subprocess.run(['node', os.path.join(AQUI, 'cripto.js'), *args], capture_output=True, text=True, check=True,
                          env=dict(os.environ, **(env or {}))).stdout.strip()


def nova(sal):
    s = node('nova-senha')
    return s, node('chave-da-senha', 'SENHA', env={'SENHA': s, 'SAL': sal})


def main():
    trocar = sys.argv[sys.argv.index('--trocar') + 1] if '--trocar' in sys.argv else None
    with open(CHAVES, encoding='utf-8') as f:
        ch = json.load(f)
    if not ch.get('gestao'):
        ch['senha_gestao'], ch['gestao'] = nova('ufit-gestao')
        with open(CHAVES, 'w', encoding='utf-8') as f:
            json.dump(ch, f, indent=1)
        print('senha da gestão criada')
    ops = {}
    if os.path.exists(OPS):
        with open(OPS, encoding='utf-8') as f:
            ops = json.load(f)
    with open(OPERADORES, encoding='utf-8-sig') as f:
        nomes = [l['nome_govoice'].strip() for l in csv.DictReader(f, delimiter=';')]
    for n in nomes:
        if n not in ops or n == trocar:
            senha, chave = nova('ufit-op')
            ops[n] = {'senha': senha, 'chave': chave}
            print('senha', 'trocada' if n == trocar else 'criada', 'para', n)
    with open(OPS, 'w', encoding='utf-8') as f:
        json.dump(ops, f, indent=1, ensure_ascii=False)
    print('consultores com senha:', ', '.join(ops))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
