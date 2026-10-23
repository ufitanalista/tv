"""Coleta do painel de convênios (EVO), usado pelo servidor.py (página convenios.html).

Para rodar só a coleta (a 1ª vez lê 13 meses de vendas, ~20–40 min):
    py -3.12 coletor_convenios.py

Regras:
- O convênio fica gravado no item da venda (corporatePartnershipName / coporatePartnershipId), inclusive
  nas cobranças da recorrência. Cada unidade cadastra o seu: o painel junta pelo nome e abre por unidade.
- Aluno ativo = XLSX de active-members (sem "ACADEMIA CANCELADA"). O convênio do aluno é o da ÚLTIMA venda
  de plano dele (nova ou recorrência) nos últimos 13 meses. Sem venda nesse período = sem convênio.
- Venda nova = não removida e sem idSaleRecurrency (mesma regra do Forecast). Conta pela data da venda, paga ou não.
- Convênio parado = teve venda nova nos 13 meses e nenhuma nos últimos 60 dias.
- /api/v1/partnership só devolve 2 convênios, por isso a lista sai das vendas: convênio sem venda não aparece.
"""
import io, json, os, re, time, unicodedata, zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import coletor_core
from coletor_core import BRT, http_json, nome_unidade

MESES_HISTORICO = 13
DIAS_PARADO = 60
REVER_DIAS = 3  # a cada coleta relê os últimos dias (venda removida, lançada com atraso)


def chave(nome):
    s = ''.join(c for c in unicodedata.normalize('NFD', nome or '') if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', s).strip().upper()


def celular(m):
    cs = (m or {}).get('contacts') or []
    for tipo in (2, 1):
        for c in cs:
            if c.get('idContactType') == tipo and c.get('description'):
                return c['description']
    return ''


def vendas_periodo(get, ini, fim):
    out, skip = [], 0
    while True:
        p = get(f'/api/v2/sales?dateSaleStart={ini}&dateSaleEnd={fim}&take=100&skip={skip}')
        out += p or []
        if not p or len(p) < 100:
            return out
        skip += 100
        time.sleep(0.2)


def processar(cache, vendas):
    """Atualiza a última venda de plano por aluno e as vendas com convênio."""
    ult, conv = cache['ultimo'], cache['vendasConv']
    for s in vendas:
        planos = [i for i in s.get('saleItens') or [] if i.get('idMembership')]
        if not planos or not s.get('idMember'):
            continue
        m, ids = str(s['idMember']), str(s['idSale'])
        quando = (s.get('saleDateServer') or s.get('saleDate') or '')[:19]
        if s.get('removed'):
            conv.pop(ids, None)
            if ult.get(m, [None, None])[1] == ids:
                del ult[m]
            continue
        if m not in ult or [quando, ids] >= ult[m]:
            ult[m] = [quando, ids]
        it = next((i for i in planos if i.get('coporatePartnershipId')), None)
        if not it:
            conv.pop(ids, None)
            continue
        mb = s.get('member') or {}
        conv[ids] = {
            'd': quando, 'b': s.get('idBranch'), 'm': s['idMember'], 'rec': bool(s.get('idSaleRecurrency')),
            'conv': (it.get('corporatePartnershipName') or '').strip(), 'idConv': it['coporatePartnershipId'],
            'plano': it.get('item') or '', 'valor': it.get('saleValue') or 0, 'cheio': it.get('itemValue') or 0,
            'desconto': it.get('corporateDiscount') or 0,
            'consultor': (s.get('nameCommissionedEmployee') or s.get('nameEmployeeSale') or '').strip(),
            'cliente': ' '.join(x for x in (mb.get('firstName'), mb.get('lastName')) if x).strip(),
            'cel': celular(mb),
        }


def atualizar_cache(get, guarda, hoje, avisar=print):
    cache = guarda.ler('convenios') or {}
    inicio = (hoje.replace(day=1) - timedelta(days=31 * (MESES_HISTORICO - 1))).replace(day=1)
    if not cache.get('ate'):
        cache = {'ate': None, 'ultimo': {}, 'vendasConv': {}}
        dia = inicio
    else:
        dia = max(inicio, date.fromisoformat(cache['ate']) - timedelta(days=REVER_DIAS - 1))
    while dia <= hoje:
        fim = min(hoje, dia + timedelta(days=6))
        processar(cache, vendas_periodo(get, dia.isoformat(), fim.isoformat()))
        cache['ate'] = fim.isoformat()
        if (hoje - dia).days > REVER_DIAS:  # carga inicial: grava a cada semana para não perder o feito
            guarda.gravar('convenios', cache)
            avisar(f'convênios: vendas lidas até {fim:%d/%m/%Y}')
        dia = fim + timedelta(days=1)
    # descarta o que saiu da janela
    lim = inicio.isoformat()
    cache['vendasConv'] = {k: v for k, v in cache['vendasConv'].items() if v['d'] >= lim}
    cache['ultimo'] = {k: v for k, v in cache['ultimo'].items() if v[0] >= lim}
    guarda.gravar('convenios', cache)
    return cache


def ler_ativos(cred):
    """XLSX de active-members da rede → lista de dicts (A=IdBranch … J=EndDate)."""
    import urllib.request
    r = urllib.request.Request(cred['baseUrl'] + '/api/v2/members/active-members', headers=cred['headers'])
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(r, timeout=300).read()))
    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    textos = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('x:si', ns):
            textos.append(''.join(t.text or '' for t in si.iter('{%s}t' % ns['x'])))
    linhas = []
    for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).iter('{%s}row' % ns['x']):
        lin = {}
        for c in row.findall('x:c', ns):
            col = re.match(r'[A-Z]+', c.get('r')).group()
            v = c.find('x:v', ns)
            t = c.find('.//x:t', ns)
            val = v.text if v is not None else (t.text if t is not None else '')
            if c.get('t') == 's':
                val = textos[int(val)]
            lin[col] = val
        linhas.append(lin)
    cab = linhas[0]
    return [{cab[k]: v for k, v in l.items() if k in cab} for l in linhas[1:]]


def data_br(s):
    s = (s or '')[:10]
    if re.match(r'\d{4}-\d{2}-\d{2}', s):
        return s
    m = re.match(r'(\d{2})/(\d{2})/(\d{4})', s)
    if m:
        return f'{m[3]}-{m[2]}-{m[1]}'
    try:  # número de série do Excel
        return (date(1899, 12, 30) + timedelta(days=float(s))).isoformat()
    except ValueError:
        return s


def montar(cache, ativos, unidades, hoje):
    vc = cache['vendasConv']
    nomes = defaultdict(Counter)
    for v in vc.values():
        nomes[chave(v['conv'])][v['conv']] += 1
    nome = {k: c.most_common(1)[0][0] for k, c in nomes.items()}
    uni = lambda b: unidades.get(b) or f'Unidade {b}'

    ativos = [a for a in ativos if 'CANCELADA' not in (a.get('BranchName') or '').upper()]
    alunos = []
    for a in ativos:
        u = cache['ultimo'].get(str(a.get('IdMember') or '').split('.')[0])
        v = vc.get(u[1]) if u else None
        if not v:
            continue
        alunos.append({
            'id': v['m'], 'nome': v['cliente'] or a.get('MemberName', ''), 'cel': v['cel'],
            'conv': chave(v['conv']), 'unidade': uni(int(float(a['IdBranch']))) if a.get('IdBranch') else uni(v['b']),
            'plano': a.get('ContractDescription') or v['plano'], 'valor': v['valor'], 'desconto': v['desconto'],
            'inicio': data_br(a.get('StartDate')), 'fim': data_br(a.get('EndDate')), 'ultimaVenda': v['d'][:10],
        })

    novas = [v for v in vc.values() if not v['rec']]
    lim_parado = (hoje - timedelta(days=DIAS_PARADO)).isoformat()
    convs = {}
    for k in nome:
        conv_novas = [v for v in novas if chave(v['conv']) == k]
        ult = max((v['d'] for v in conv_novas), default=None)
        convs[k] = {
            'nome': nome[k],
            'unidades': sorted({uni(v['b']) for v in vc.values() if chave(v['conv']) == k}),
            'ultimaVendaNova': ult[:10] if ult else None,
            'parado': bool(ult) and ult < lim_parado,
        }
    return {
        'atualizadoEm': datetime.now(BRT).isoformat(timespec='seconds'),
        'historicoDesde': min((v['d'] for v in vc.values()), default='')[:10],
        'totalAtivos': len(ativos),
        'diasParado': DIAS_PARADO,
        'convenios': convs,
        'alunos': alunos,
        'vendas': [{'d': v['d'][:10], 'conv': chave(v['conv']), 'unidade': uni(v['b']), 'consultor': v['consultor'],
                    'cliente': v['cliente'], 'plano': v['plano'], 'valor': v['valor'], 'desconto': v['desconto']}
                   for v in sorted(novas, key=lambda v: v['d'], reverse=True)],
    }


def coletar(cred_evo, guarda, avisar=print):
    hoje = datetime.now(BRT).date()
    get = lambda caminho: http_json(cred_evo['baseUrl'] + caminho, cred_evo['headers'])
    unidades = {b['idBranch']: nome_unidade(b.get('name')) for b in get('/api/v1/configuration')}
    cache = atualizar_cache(get, guarda, hoje, avisar)
    return montar(cache, ler_ativos(cred_evo), unidades, hoje)


def resumo(d):
    return (f"{len(d['convenios'])} convênios, {len(d['alunos'])} alunos ativos com convênio de {d['totalAtivos']}, "
            f"{len(d['vendas'])} vendas novas desde {d['historicoDesde']}")


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    d = coletar(coletor_core.credenciais()[0], coletor_core.GuardaArquivo())
    print(resumo(d))
    with open(os.path.join(coletor_core.GuardaArquivo.pasta, 'convenios-dados.js'), 'w', encoding='utf-8') as f:
        f.write('window.CONV = ' + json.dumps(d, ensure_ascii=False) + ';\n')
