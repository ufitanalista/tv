"""Troca a senha do Painel da Gestão.

    python ferramentas/trocar_senha_gestao.py               → sorteia uma senha nova
    python ferramentas/trocar_senha_gestao.py MinhaSenha123 → usa a senha escolhida (8 letras/números ou mais)

Faz tudo: calcula a chave nova, grava em ~/.claude/ufit-tvs-chaves.json, manda para o cofre do GitHub
(gravar_segredos.py) e republica as fichas com a chave nova (publicar.py --enviar).
Na coleta seguinte (até ~5 min) o painel passa a abrir só com a senha nova; quem estava com a antiga
guardada vê a tela de senha de novo.
"""
import json, os, re, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
CHAVES = os.path.join(os.path.expanduser('~'), '.claude', 'ufit-tvs-chaves.json')


def node(*args, env=None):
    return subprocess.run(['node', os.path.join(AQUI, 'cripto.js'), *args], capture_output=True, text=True, check=True,
                          env=dict(os.environ, **(env or {}))).stdout.strip()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    senha = sys.argv[1] if len(sys.argv) > 1 else node('nova-senha')
    if len(re.sub(r'[^a-z0-9]', '', senha.lower())) < 8:
        sys.exit('A senha precisa de pelo menos 8 letras ou números.')
    with open(CHAVES, encoding='utf-8') as f:
        ch = json.load(f)
    ch['senha_gestao'] = senha
    ch['gestao'] = node('chave-da-senha', 'SENHA', env={'SENHA': senha, 'SAL': 'ufit-gestao'})
    with open(CHAVES, 'w', encoding='utf-8') as f:
        json.dump(ch, f, indent=1)
    print('senha guardada em', CHAVES)
    subprocess.run([sys.executable, os.path.join(AQUI, 'gravar_segredos.py')], check=True, stdout=subprocess.DEVNULL)
    print('cofre do GitHub atualizado')
    subprocess.run([sys.executable, os.path.join(RAIZ, 'publicar.py'), '--enviar'], cwd=RAIZ, check=True, stdout=subprocess.DEVNULL)
    print('fichas republicadas. Nova senha da gestão:', senha)


if __name__ == '__main__':
    main()
