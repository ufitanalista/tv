"""Prepara e envia a versão online das TVs e do Balcão (GitHub Pages, projeto público).

Fonte de tudo continua sendo a pasta dashboard-tv (versão do PC):
- páginas de dashboard-tv/site → site/ (adaptadas para ler os dados criptografados);
- dados do Balcão (dentro do balcao.html) → site/balcao.enc;
- listas (operadores.csv, ajustes, vendas dadas como pagas, TV das unidades…) → config.enc.
Nada sai daqui sem criptografia: antes de enviar, confere se algum arquivo aberto tem dado interno.

Uso (na pasta ufit-tvs-nuvem):
    python publicar.py            → prepara e mostra o que mudou (não envia)
    python publicar.py --enviar   → prepara, confere e envia para o GitHub
    python publicar.py --estado   → também manda o estado atual do PC (vendas do mês, convênios), só na 1ª vez

Chaves: C:\\Users\\<usuário>\\.claude\\ufit-tvs-chaves.json (fora do OneDrive e fora do projeto).
"""
import json, os, re, shutil, subprocess, sys, tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
ORIGEM = os.path.join(AQUI, '..', 'dashboard-tv')
CASA = os.path.join(os.path.expanduser('~'), '.claude')
CHAVES = os.path.join(CASA, 'ufit-tvs-chaves.json')
LISTAS = ['operadores.csv', 'tabulacoes.csv', 'ajustes_vendas.csv', 'vendas_ganhas.csv', 'tv-unidades.json', 'tv-aulas.json']
ESTADO = ['vendas_mes.json', 'convenios.json', 'govoice_token.json']
PAGINAS_TV = {'tv1.html': ('dados.enc', 'DADOS'), 'tv2.html': ('dados.enc', 'DADOS'),
              'tv-unidades.html': ('dados.enc', 'DADOS'), 'convenios.html': ('conv.enc', 'CONV')}
COPIAR = ['tv.css', 'nosleep.min.js', 'logo_ufit.png']


def chaves():
    if not os.path.exists(CHAVES):
        nova = lambda: subprocess.run(['node', os.path.join(AQUI, 'ferramentas', 'cripto.js'), 'nova-chave'],
                                      capture_output=True, text=True, check=True).stdout.strip()
        with open(CHAVES, 'w', encoding='utf-8') as f:
            json.dump({'tv': nova(), 'interna': nova()}, f)
        print('chaves novas criadas em', CHAVES)
    with open(CHAVES, encoding='utf-8') as f:
        return json.load(f)


def cripto(k, *args):
    env = dict(os.environ, CHAVE=k)
    subprocess.run(['node', os.path.join(AQUI, 'ferramentas', 'cripto.js'), args[0], 'CHAVE', *args[1:]], env=env, check=True)


def ler(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def gravar(p, txt):
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(txt)


def trocar(txt, de, para, nome):
    if txt.count(de) != 1:
        sys.exit(f'ERRO: em {nome} esperava achar 1 vez: {de[:60]!r} (achou {txt.count(de)}). A página mudou; ajuste o publicar.py.')
    return txt.replace(de, para)


def montar_site(k):
    site, orig = os.path.join(AQUI, 'site'), os.path.join(ORIGEM, 'site')
    for n in os.listdir(site):
        if n not in ('cripto.js', 'index.html'):  # estes dois são só da versão online
            os.remove(os.path.join(site, n))
    for n in COPIAR:
        shutil.copy(os.path.join(orig, n), site)

    js = ler(os.path.join(orig, 'tv-comum.js'))
    js = trocar(js, 'if (!window.DADOS) setTimeout', 'if (!window.DADOS && !window.TV_RECARREGAR) setTimeout', 'tv-comum.js')
    # online os dados chegam a cada ~5 min: só avisa "sem atualização" depois de 20 min
    js = trocar(js, '> 5 * 60000', '> (window.TV_RECARREGAR ? 20 : 5) * 60000', 'tv-comum.js')
    gravar(os.path.join(site, 'tv-comum.js'), js)

    for nome, (arq, var) in PAGINAS_TV.items():
        h = ler(os.path.join(orig, nome))
        src = 'convenios-dados.js' if arq == 'conv.enc' else 'dados.js'
        h = trocar(h, f'<script src="{src}"></script>', f'<script src="cripto.js" data-arquivo="{arq}" data-var="{var}"></script>', nome)
        h = trocar(h, '\n<script>\n', '\n<script type="text/plain" class="app">\n', nome)
        gravar(os.path.join(site, nome), h)

    h = ler(os.path.join(orig, 'balcao.html'))
    m = re.search(r'\n<script>\nconst DADOS = (\{.*?\});\n', h)
    if not m:
        sys.exit('ERRO: não achei os dados do Balcão (const DADOS) no balcao.html')
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, 'balcao.json')
        gravar(p, json.dumps(json.loads(m.group(1)), ensure_ascii=False))
        cripto(k['tv'], 'cifrar', p, os.path.join(site, 'balcao.enc'))
    h = h.replace(m.group(0), '\n<script src="cripto.js" data-arquivo="balcao.enc" data-var="BALCAO"></script>\n'
                              '<script type="text/plain" class="app">\nconst DADOS = window.BALCAO;\n')
    gravar(os.path.join(site, 'balcao.html'), h)
    gravar(os.path.join(site, '.nojekyll'), '')


def empacotar(k, nomes, pasta_origem, saida):
    with tempfile.TemporaryDirectory() as t:
        for n in nomes:
            p = os.path.join(pasta_origem, n)
            if os.path.exists(p):
                shutil.copy(p, t)
            else:
                print('  (não existe, fica de fora:', n + ')')
        cripto(k, 'empacotar', t, os.path.join(AQUI, saida))


def proibidos():
    """Textos que nunca podem aparecer em arquivo aberto: nomes completos dos operadores, vouchers, tokens das páginas de venda,
    credenciais e as chaves."""
    out = set()
    import csv
    with open(os.path.join(ORIGEM, 'operadores.csv'), encoding='utf-8-sig') as f:
        out |= {l['nome_evo'].strip() for l in csv.DictReader(f, delimiter=';') if len(l.get('nome_evo', '').strip()) > 8}
    m = re.search(r'\nconst DADOS = (\{.*?\});\n', ler(os.path.join(ORIGEM, 'site', 'balcao.html')))
    b = json.loads(m.group(1))
    for u in b['unidades']:
        out |= {t for t in (u.get('paginas') or {}).values() if t and len(t) > 10}
        for v in u.get('vouchers') or []:
            out |= {r.get('codigo') for r in (v.get('regras') or {}).values() if r and len(r.get('codigo') or '') > 5}
    for n in ('evo-api-credentials.json', 'govoice-credentials.json'):
        c = json.loads(ler(os.path.join(CASA, n)))
        out |= {str(v) for v in list(c.values()) + list((c.get('headers') or {}).values()) if isinstance(v, str) and len(v) > 12 and 'http' not in v}
    out |= set(chaves().values())
    return {x for x in out if x}


def conferir():
    arquivos = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=AQUI,
                              capture_output=True, text=True, check=True).stdout.split('\n')
    ruins = proibidos()
    erro = False
    for a in filter(None, arquivos):
        if a.endswith('.enc') or a.endswith('.png'):
            continue
        txt = open(os.path.join(AQUI, a), encoding='utf-8', errors='replace').read()
        achou = [x for x in ruins if x in txt]
        if achou:
            print(f'BLOQUEADO: {a} tem dado interno aberto ({len(achou)} trecho(s))')
            erro = True
    if erro:
        sys.exit('Nada foi enviado.')
    print('conferência ok:', len([a for a in arquivos if a]), 'arquivos, nenhum dado interno aberto')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    k = chaves()
    montar_site(k)
    empacotar(k['interna'], LISTAS, ORIGEM, 'config.enc')
    if '--estado' in sys.argv:
        empacotar(k['interna'], ESTADO, os.path.join(CASA, 'ufit-tv-cache'), 'estado-inicial.enc')
    conferir()
    subprocess.run(['git', 'add', '-A'], cwd=AQUI, check=True)
    subprocess.run(['git', 'status', '--short'], cwd=AQUI)
    if '--enviar' in sys.argv:
        if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=AQUI).returncode == 0:
            print('nada mudou')
            return
        subprocess.run(['git', 'commit', '-q', '-m', 'Atualiza páginas e listas'], cwd=AQUI, check=True)
        subprocess.run(['git', 'push', '-q'], cwd=AQUI, check=True)
        print('enviado; o GitHub publica em 1–3 minutos')


if __name__ == '__main__':
    main()
