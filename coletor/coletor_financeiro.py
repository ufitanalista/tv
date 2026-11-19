"""Painel de Vendas do financeiro (site/vendas.html): vendas de planos da rede, por dia, unidade e plano.

Saída fin.json → fin.enc, que só abre com a senha do financeiro (sal "ufit-fin").
Feito em 19/11/2026 para substituir o painel que o financeiro montava à mão a partir do relatório
"Vendas Detalhadas" (tipo Contrato) do EVO.

Regras (as mesmas do painel dele, até onde a API permite):
- Entra cada item de PLANO (idMembership) de venda não removida, pela data da venda. Paga ou não.
- Fica de fora o débito recorrente (idSaleRecurrency) e a origem 8 (saleSource 8: troca/transferência de contrato
  a R$ 0, que não aparece no relatório de vendas do EVO).
- Valor líquido = saleValue do item; bruto = itemValue. Contratos sem valor (permuta, parceria, funcionário) contam.
- Conferência de out/2026 contra o painel do financeiro: valor +0,5%, contratos +2,7% (o relatório do EVO e a API não
  saem idênticos).
- Regiões: lista da Diretoria Financeira (Norte 15, Sul 12, Roosevelt sem região). Mudar em UNIDADES.

Coleta: janela de MESES meses (o atual e os anteriores). Na 1ª vez carrega no máximo CARGA_DIAS por ciclo
(para não estourar o tempo do GitHub) e publica só quando a janela inteira estiver lida. Depois, a cada
INTERVALO relê os últimos REVER_DIAS dias; uma vez por dia, de madrugada, relê o mês atual e o anterior inteiros
(venda removida ou editada depois).
"""
import time
from datetime import date, datetime, timedelta

from coletor_core import BRT, http_json

MESES = 4
CARGA_DIAS = 14
REVER_DIAS = 7
INTERVALO = 55 * 60
ORIGENS_FORA = {8}

# idBranch: (nome curto, região) — como no painel do financeiro
UNIDADES = {
    13: ('Porto Belo Jardim Dourado', 'Sul'), 25: ('Ponte do Imaruim', 'Sul'), 28: ('Florianópolis Córrego Grande', 'Sul'),
    29: ('Itapema Morretes', 'Sul'), 30: ('Palhoça Pagani', 'Sul'), 32: ('Jardim Atlântico', 'Sul'), 34: ('Tijucas Praça', 'Sul'),
    36: ('Alto São Bento', 'Sul'), 39: ('Palhoça Pedra Branca', 'Sul'), 41: ('Balneário Camboriú', 'Sul'),
    46: ('Bombinhas Bombas', 'Sul'), 51: ('São João Batista', 'Sul'),
    16: ('Blumenau', 'Norte'), 18: ('Timbó Estados', 'Norte'), 26: ('Brusque Centro', 'Norte'), 27: ('Indaial', 'Norte'),
    33: ('Gaspar', 'Norte'), 35: ('Jaraguá do Sul', 'Norte'), 37: ('São João', 'Norte'), 38: ('Rio Negro', 'Norte'),
    40: ('Canoinhas', 'Norte'), 42: ('Navegantes', 'Norte'), 43: ('Pomerode', 'Norte'), 45: ('Gaspar Santa Terezinha', 'Norte'),
    47: ('São Bento do Sul', 'Norte'), 48: ('São Francisco do Sul', 'Norte'), 49: ('Cidade Nova', 'Norte'),
    44: ('Roosevelt', 'Sem região'),
}
FORA = {0, 50, 1000}  # administração, Exclusive (sem vendas), treinamento


def inicio_janela(hoje):
    d = hoje.replace(day=1)
    for _ in range(MESES - 1):
        d = (d - timedelta(days=1)).replace(day=1)
    return d


def ler_periodo(get, ini, fim):
    out, skip = [], 0
    while True:
        p = get(f'/api/v2/sales?dateSaleStart={ini}&dateSaleEnd={fim}&take=100&skip={skip}') or []
        out += p
        if len(p) < 100:
            return out
        skip += 100
        time.sleep(0.15)


def guardar(cache, vendas, ini, fim):
    """Troca as vendas do período [ini, fim] pelas lidas agora (a que sumiu ou foi removida sai)."""
    v = cache['vendas']
    for k in [k for k, x in v.items() if ini <= x[0] <= fim]:
        del v[k]
    for s in vendas:
        if s.get('removed') or s.get('idSaleRecurrency') or s.get('saleSource') in ORIGENS_FORA:
            continue
        b = s.get('idBranch')
        if b in FORA:
            continue
        itens = [[(i.get('item') or '').strip().upper(), round(i.get('saleValue') or 0, 2), round(i.get('itemValue') or 0, 2)]
                 for i in s.get('saleItens') or [] if i.get('idMembership')]
        d = (s.get('saleDate') or '')[:10]
        if itens and ini <= d <= fim:
            v[str(s['idSale'])] = [d, b, itens]


def reler(get, cache, ini, fim):
    """Lê [ini, fim] em blocos de 5 dias."""
    d = ini
    while d <= fim:
        f = min(fim, d + timedelta(days=4))
        guardar(cache, ler_periodo(get, d.isoformat(), f.isoformat()), d.isoformat(), f.isoformat())
        d = f + timedelta(days=1)


def atualizar(get, guarda, agora, avisar=print):
    """Devolve o cache atualizado, ou None quando não é hora (o painel no ar continua valendo)."""
    hoje = agora.date()
    ini = inicio_janela(hoje)
    c = guarda.ler('financeiro') or {}
    if c.get('desde') != ini.isoformat() and c.get('carga'):
        # virou o mês: descarta o mês que saiu da janela
        c['vendas'] = {k: x for k, x in c['vendas'].items() if x[0] >= ini.isoformat()}
        c['desde'] = ini.isoformat()
    if not c.get('carga'):
        c = {'desde': ini.isoformat(), 'carga': ini.isoformat(), 'completo': False, 'vendas': {}, 'em': 0, 'cheia': ''}

    if not c['completo']:
        a = date.fromisoformat(c['carga'])
        f = min(hoje, a + timedelta(days=CARGA_DIAS - 1))
        reler(get, c, a, f)
        c['carga'] = (f + timedelta(days=1)).isoformat()
        c['completo'] = f >= hoje
        c['em'] = time.time()
        guarda.gravar('financeiro', c)
        avisar(f'financeiro: carga inicial lida até {f:%d/%m/%Y}' + ('' if c['completo'] else ' (continua no próximo ciclo)'))
        return c if c['completo'] else None

    if time.time() - c.get('em', 0) < INTERVALO:
        return None
    if 3 <= agora.hour < 6 and c.get('cheia') != hoje.isoformat():
        reler(get, c, (hoje.replace(day=1) - timedelta(days=1)).replace(day=1), hoje)
        c['cheia'] = hoje.isoformat()
    else:
        reler(get, c, max(ini, hoje - timedelta(days=REVER_DIAS - 1)), hoje)
    c['em'] = time.time()
    guarda.gravar('financeiro', c)
    return c


def montar(c, agora):
    """Agrega por dia × unidade × plano: [dia, idBranch, índice do plano, contratos, líquido, bruto]."""
    planos, idx, agg = [], {}, {}
    for d, b, itens in c['vendas'].values():
        for nome, liq, bruto in itens:
            if nome not in idx:
                idx[nome] = len(planos)
                planos.append(nome)
            k = (d, b, idx[nome])
            a = agg.setdefault(k, [0, 0.0, 0.0])
            a[0] += 1
            a[1] += liq
            a[2] += bruto
    unid = dict(UNIDADES)
    for _, b, _ in c['vendas'].values():
        if b not in unid:
            unid[b] = (f'Unidade {b}', 'Sem região')
    return {
        'atualizadoEm': agora.isoformat(timespec='seconds'),
        'hoje': agora.date().isoformat(),
        'desde': c['desde'],
        'unidades': {str(b): {'nome': n, 'regiao': r} for b, (n, r) in unid.items()},
        'planos': planos,
        'linhas': [[d, b, p, n, round(l, 2), round(br, 2)] for (d, b, p), (n, l, br) in sorted(agg.items())],
    }


def coletar(cred_evo, guarda, avisar=print):
    """fin.json pronto, ou None quando não houve coleta nova neste ciclo."""
    agora = datetime.now(BRT)
    get = lambda caminho: http_json(cred_evo['baseUrl'] + caminho, cred_evo['headers'])
    c = atualizar(get, guarda, agora, avisar)
    return montar(c, agora) if c else None


def resumo(d):
    n = sum(l[3] for l in d['linhas'])
    return f"{n} contratos de {d['desde']} a {d['hoje']}, {len(d['planos'])} planos, {len(d['linhas'])} linhas"


if __name__ == '__main__':
    import json, os, sys
    import coletor_core
    sys.stdout.reconfigure(encoding='utf-8')
    g = coletor_core.GuardaArquivo()
    while True:
        d = coletar(coletor_core.credenciais()[0], g)
        if d or (g.ler('financeiro') or {}).get('completo'):
            break
    d = d or montar(g.ler('financeiro'), datetime.now(BRT))
    print(resumo(d))
    with open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(g.pasta, 'fin.json'), 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
