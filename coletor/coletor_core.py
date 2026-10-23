"""Coleta dos dados das TVs do call center (GoVoice + EVO).

Usado pelo servidor.py (PC / servidor interno). Para testar só a coleta:
    py -3.12 coletor_core.py      → coleta com as credenciais de ~/.claude e mostra um resumo

Regras:
- Operador GoVoice ↔ EVO pelo ramal, em operadores.csv (ramal; nome; nome no EVO; id no EVO).
- Venda = do COMISSIONADO (idCommissionedEmployee) e, sem comissionado, do VENDEDOR (idEmployeeSale).
  Venda pelo link sai com vendedor "SUPORTEEVO" e o operador só como comissionado.
- Só conta venda PAGA (alguma parcela "received"). Venda lançada com link de cobrança fica "open"
  até o cliente pagar; aí entra no dia e na hora do pagamento (e toca o sino nessa hora). Ignora venda removida e débito recorrente
  (idSaleRecurrency preenchido); soma todos os itens (mesma regra do Forecast).
- Tabulação → grupo (pos/ret/neg/imp) em tabulacoes.csv. Tabulação nova sem grupo cai em "imp".
- Exceção manual: ajustes_vendas.csv (id_venda; id_evo; motivo) dá a venda ao operador indicado, acima da regra.
"""
import csv, json, os, re, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

# Na nuvem (GitHub Actions) as listas (operadores.csv etc.), o estado e as credenciais vêm de pastas indicadas no ambiente.
PASTA = os.environ.get('UFIT_CONFIG') or os.path.dirname(os.path.abspath(__file__))
BRT = timezone(timedelta(hours=-3))

GRUPOS = [
    {'id': 'pos', 'nome': 'Positivas',    'icone': '✓', 'cor': '#0ca30c'},
    {'id': 'ret', 'nome': 'Retornar',     'icone': '↻', 'cor': '#fab219'},
    {'id': 'neg', 'nome': 'Negativas',    'icone': '✕', 'cor': '#d03b3b'},
    {'id': 'imp', 'nome': 'Improdutivas', 'icone': '–', 'cor': '#7a7a76'},
]


def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '') if unicodedata.category(c) != 'Mn').lower().strip()


def ler_csv(nome):
    with open(os.path.join(PASTA, nome), encoding='utf-8-sig') as f:
        return list(csv.DictReader(f, delimiter=';'))


def http_json(url, headers, corpo=None, metodo='GET'):
    for tentativa in range(4):
        try:
            r = urllib.request.Request(url, data=json.dumps(corpo).encode() if corpo else None, headers=headers, method=metodo)
            return json.load(urllib.request.urlopen(r, timeout=60))
        except urllib.error.HTTPError:
            raise
        except Exception:
            if tentativa == 3:
                raise
            time.sleep(2 + tentativa * 3)


class Guarda:
    """Onde ficam o token do GoVoice e o cache das vendas do mês.
    No Firebase é o Firestore; no PC, arquivos. Interface: ler(nome) → dict|None, gravar(nome, dict)."""
    def ler(self, nome):
        raise NotImplementedError

    def gravar(self, nome, valor):
        raise NotImplementedError


CASA = os.path.join(os.path.expanduser('~'), '.claude')  # credenciais e cache ficam fora do OneDrive


class GuardaArquivo(Guarda):
    pasta = os.environ.get('UFIT_ESTADO') or os.path.join(CASA, 'ufit-tv-cache')

    def ler(self, nome):
        p = os.path.join(self.pasta, nome + '.json')
        if not os.path.exists(p):
            return None
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def gravar(self, nome, valor):
        os.makedirs(self.pasta, exist_ok=True)
        with open(os.path.join(self.pasta, nome + '.json'), 'w', encoding='utf-8') as f:
            json.dump(valor, f, ensure_ascii=False)


def credenciais():
    cred = os.environ.get('UFIT_CREDENCIAIS') or CASA
    with open(os.path.join(cred, 'evo-api-credentials.json'), encoding='utf-8') as a, \
         open(os.path.join(cred, 'govoice-credentials.json'), encoding='utf-8') as b:
        return json.load(a), json.load(b)


# ------------------------------------------------------------------ GoVoice
class GoVoice:
    def __init__(self, cred, guarda):
        self.c, self.guarda = cred, guarda
        t = guarda.ler('govoice_token') or {}
        self.token = t.get('token') if t.get('expira', 0) > time.time() else None

    def login(self):
        h = {'Content-Type': 'application/json', 'User-Agent': 'ufit-tv/1.0'}
        r = http_json(self.c['baseUrl'] + '/api/auth/login', h, {'username': self.c['username'], 'password': self.c['password']}, 'POST')
        self.token = r['token']
        # o token vale 7 dias; renova com folga
        self.guarda.gravar('govoice_token', {'token': self.token, 'expira': time.time() + 6 * 86400})

    def get(self, caminho, **params):
        if not self.token:
            self.login()
        url = self.c['baseUrl'] + caminho + ('?' + urllib.parse.urlencode(params) if params else '')
        h = {'Authorization': 'Bearer ' + self.token, 'User-Agent': 'ufit-tv/1.0'}
        try:
            return http_json(url, h)
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            self.login()
            h['Authorization'] = 'Bearer ' + self.token
            return http_json(url, h)


def status_tv(s):
    """Status do GoVoice → status da TV."""
    s = norm(s)
    if not s or 'offline' in s or 'deslog' in s:
        return 'offline'
    if 'paus' in s:
        return 'pausa'
    if any(k in s for k in ('fal', 'convers', 'talk', 'busy', 'ocup', 'chaman', 'ring', 'qualific', 'tabul')):
        return 'em_ligacao'
    return 'livre'


def contagem(rel):
    return {q['name'].strip(): q['count'] for q in (rel or {}).get('by_qualification', [])}


def coletar_govoice(gv, ops_csv):
    agentes = gv.get('/api/agents')                      # id interno ↔ ramal (traz senhas SIP: não guardar)
    id_por_ramal = {a['endpoint_id']: a['id'] for a in agentes}
    visao = gv.get('/api/agents/overview')               # status ao vivo + números do dia
    cdr = gv.get('/api/cdr/report', window='today')['window']['metrics']
    campanhas = gv.get('/api/campaigns')
    membros = gv.get('/api/queues/members')
    filas = [q['name'] for q in gv.get('/api/queues')]
    listas = gv.get('/api/qualification-lists')

    grupo_csv = {norm(l['tabulacao']): l['grupo'].strip() for l in ler_csv('tabulacoes.csv')}
    nomes_tab = []
    for l in listas:
        for q in l.get('qualifications', []):
            n = q['name'].strip()
            if n not in nomes_tab and not q.get('deleted_at'):
                nomes_tab.append(n)
    tabulacoes = [{'nome': n, 'grupo': grupo_csv.get(norm(n), 'imp'), 'semGrupo': norm(n) not in grupo_csv} for n in nomes_tab]

    por_ramal = {o['ramal'].strip(): o for o in ops_csv}
    operadores = []
    for a in visao:
        ramal = a['extension']
        o = por_ramal.get(ramal, {})
        # ramal fora de operadores.csv só aparece se estiver logado ou já tiver atendido hoje
        if not o and status_tv(a.get('status')) == 'offline' and not a.get('atendHoje'):
            continue
        idg = id_por_ramal.get(ramal)
        operadores.append({
            'ramal': ramal,
            'govoice': o.get('nome_govoice') or a['name'],
            'idEvo': int(o['id_evo']) if (o.get('id_evo') or '').strip() else None,
            'evo': o.get('nome_evo', ''),
            'equipes': a.get('teams') or [],
            'equipe': ' · '.join(a.get('teams') or []),
            'status': status_tv(a.get('status')),
            'statusGoVoice': a.get('status'),
            'statusDesde': a.get('status_since'),
            'pausa': a.get('pause_reason'),
            'atendidas': a.get('atendHoje') or 0,
            'tmaSeg': round(a.get('tma') or 0),
            'tabulacoes': contagem(gv.get('/api/qualifications/report', window='today', agent=idg)) if idg else {},
        })

    ramais_tv = {o['ramal'] for o in operadores}
    equipes = []
    for f in filas:
        ramais = sorted({m['interface'].split('/')[-1] for m in membros if m['queue_name'] == f} & ramais_tv)
        equipes.append({
            'nome': f,
            'campanhas': [{'nome': c['name'], 'status': c['status']} for c in campanhas if c.get('queue_name') == f and not c.get('deleted_at')],
            'membros': ramais,
            'tab': contagem(gv.get('/api/qualifications/report', window='today', queue=f)),
            'tabPorMembro': {r: contagem(gv.get('/api/qualifications/report', window='today', queue=f, agent=id_por_ramal[r]))
                             for r in ramais if r in id_por_ramal},
        })

    return {
        'grupos': GRUPOS,
        'tabulacoes': tabulacoes,
        'tabTotal': contagem(gv.get('/api/qualifications/report', window='today')),
        'totais': {'discadas': cdr.get('total', 0), 'atendidas': cdr.get('attendedByAgent', 0), 'atendidasRede': cdr.get('answered', 0)},
        'equipes': equipes,
        'operadores': operadores,
    }


# ------------------------------------------------------------------ EVO
def vendas_do_dia(get, dia, ids):
    out, skip = [], 0
    while True:
        p = get(f'/api/v2/sales?dateSaleStart={dia}&dateSaleEnd={dia}&take=100&skip={skip}&showReceivables=true')
        if not p:
            return out
        out += resumir(p, ids)
        skip += len(p)
        time.sleep(0.25)


def ajustes():
    try:
        return {int(l['id_venda']): int(l['id_evo']) for l in ler_csv('ajustes_vendas.csv') if (l.get('id_venda') or '').strip()}
    except FileNotFoundError:
        return {}


def ganhas():
    """Vendas dadas como pagas à mão (cliente mandou comprovante, boleto ainda não compensou): id_venda → quando."""
    try:
        return {int(l['id_venda']): l['quando'].strip() for l in ler_csv('vendas_ganhas.csv') if (l.get('id_venda') or '').strip()}
    except FileNotFoundError:
        return {}


def nome_cliente(s):
    """Primeiro e último nome do cliente, para a TV (sem documento nem telefone)."""
    m = s.get('member') or s.get('prospect') or {}
    partes = ((m.get('name') or ' '.join(filter(None, [m.get('firstName'), m.get('lastName')]))) if isinstance(m, dict) else '').split()
    return ' '.join(p.capitalize() for p in (partes[:1] + partes[-1:] if len(partes) > 1 else partes))


def resumir(vendas, ids):
    r, aj = [], ajustes()
    for s in vendas:
        quem = aj.get(s.get('idSale')) or s.get('idCommissionedEmployee') or s.get('idEmployeeSale')
        if s.get('removed') or s.get('idSaleRecurrency') or quem not in ids:
            continue
        itens = s.get('saleItens') or []
        recs = s.get('receivables') or []
        pagas = [x for x in recs if (x.get('status') or {}).get('id') == 2 and not x.get('cancellationDate')]
        pago = bool(pagas) or not recs  # sem parcela nenhuma = nada a pagar
        venda = (s.get('saleDateServer') or s.get('saleDate') or '')[:19]
        pago_em = min(((x.get('updateDate') or '')[:19] for x in pagas), default='') or venda
        plano = next((i.get('item') for i in itens if i.get('idMembership')), None) or (itens[0].get('item') if itens else 'Venda')
        r.append({
            'id': s['idSale'], 'idEmployee': quem, 'idBranch': s.get('idBranch'),
            'valor': round(sum(i.get('saleValue') or 0 for i in itens), 2), 'plano': plano,
            'cliente': nome_cliente(s), 'venda': venda + '-03:00',
            # link de cobrança que passou do vencimento sem pagamento (status 4 no EVO)
            'vencida': not pagas and any((x.get('status') or {}).get('id') == 4 for x in recs),
            # datas do EVO vêm em horário de Brasília, sem fuso → acrescenta -03:00
            'pago': pago, 'quando': (max(pago_em, venda) if pago else venda) + '-03:00',
        })
    return r


def vendas_do_mes(get, guarda, ids, hoje):
    """Dias fechados do mês: busca uma vez e guarda (não mudam). Refaz se mudar o mês ou a lista de operadores."""
    chave = {'mes': hoje.strftime('%Y-%m'), 'ids': sorted(ids), 'regra': 'comissionado-pago-cliente'}
    c = guarda.ler('vendas_mes') or {}
    if any(c.get(k) != chave[k] for k in chave):
        c = dict(chave, dias=[], vendas=[])
    mudou, dia = False, hoje.replace(day=1)
    while dia < hoje:
        if dia.isoformat() not in c['dias']:
            c['vendas'] += vendas_do_dia(get, dia.isoformat(), ids)
            c['dias'].append(dia.isoformat())
            mudou = True
        dia += timedelta(days=1)
    # vendas de dias fechados ainda sem pagamento: confere de novo se o cliente já pagou ou se a venda foi excluída
    excluidas = set()
    for k, v in enumerate(c['vendas']):
        if not v.get('pago'):
            try:
                s = get(f"/api/v2/sales/{v['id']}?showReceivables=true")
            except urllib.error.HTTPError as e:
                # venda apagada no EVO: responde 400 "Sale not found"
                if e.code in (400, 404) and 'not found' in e.read().decode('utf-8', 'replace').lower():
                    excluidas.add(v['id'])
                else:
                    print('venda', v['id'], 'sem resposta do EVO:', e)
                continue
            except Exception as e:  # EVO às vezes devolve 500 numa venda: tenta no próximo ciclo
                print('venda', v['id'], 'sem resposta do EVO:', e)
                continue
            if s.get('removed') or s.get('removalDate'):
                excluidas.add(v['id'])
                continue
            novo = resumir([s], ids)
            if novo and novo[0] != v:
                c['vendas'][k], mudou = novo[0], True
    if excluidas:
        print('vendas excluídas no EVO, saem do painel:', sorted(excluidas))
        c['vendas'], mudou = [v for v in c['vendas'] if v['id'] not in excluidas], True
    if mudou:
        guarda.gravar('vendas_mes', c)
    return c['vendas']


def nome_unidade(n):
    n = (n or '').replace('U FIT ACADEMIA', '').strip(' -')
    return n.title() if n.isupper() else n


# ------------------------------------------------------------------ TV das unidades (planos, preços, campanha)
_unidades = {'em': 0, 'dados': None}


def coletar_unidades(get):
    """Preços dos planos e vouchers da campanha nas unidades do call center (tv-unidades.json).
    Muda pouco: busca no EVO a cada 30 min e, se o EVO falhar, mantém a última leitura."""
    if _unidades['dados'] and time.time() - _unidades['em'] < 1800:
        return _unidades['dados']
    with open(os.path.join(PASTA, 'tv-unidades.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    with open(os.path.join(PASTA, 'tv-aulas.json'), encoding='utf-8') as f:
        aulas = json.load(f)
    codigos = cfg['campanha']['vouchers']
    for u in cfg['unidades']:
        planos, skip = [], 0
        while True:
            d = get(f"/api/v3/membership?idBranch={u['idBranch']}&take=50&skip={skip}")
            lista = d.get('list') or d.get('lista') or []
            planos += [m for m in lista if not m.get('inactive')]
            if len(lista) < 50:
                break
            skip += 50
            time.sleep(0.25)
        vouchers = {v.get('nameVoucher', '').upper(): v for v in get(f"/api/v1/voucher?idBranch={u['idBranch']}&valid=true") or []}
        u['planos'] = []
        for p in cfg['planos']:
            m = next((m for m in planos if re.search(p['busca'], (m.get('nameMembership') or '').strip().upper())), None)
            if not m:
                u['planos'].append({'chave': p['chave'], 'faltando': True})
                continue
            item = {'chave': p['chave'], 'valor': m.get('value'), 'parcelas': m.get('maxAmountInstallments') or m.get('duration') or 1,
                    'duracao': m.get('duration') or 0, 'matricula': (m.get('additionalService') or {}).get('value')}
            if (m.get('valuePromotionalPeriod') or 0) > 0 and (m.get('monthsPromotionalPeriod') or 0) > 0:
                item['promo'] = {'valor': m['valuePromotionalPeriod'], 'meses': m['monthsPromotionalPeriod']}
            v = vouchers.get((codigos.get(p['chave']) or '').upper())
            desc = (v or {}).get('monthyDiscount') or {}
            if v and m['idMembership'] in (v.get('idMemberships') or []) and desc.get('typeDiscountMembership') == 'Value':
                item['voucher'] = {'codigo': v['nameVoucher'], 'desconto': desc.get('value'), 'expira': (v.get('expirationDate') or '')[:10]}
            u['planos'].append(item)
        u['aulas'] = aulas.get(u['slug'], {}).get('grade', [])
        time.sleep(0.25)
    dados = {k: cfg[k] for k in ('campanha', 'planos', 'linhas', 'tipos', 'unidades')}
    _unidades.update(em=time.time(), dados=dados)
    return dados


# ------------------------------------------------------------------ junta tudo
def coletar(cred_evo, cred_gv, guarda):
    agora = datetime.now(BRT)
    hoje = agora.date()
    ops_csv = ler_csv('operadores.csv')

    try:
        g = coletar_govoice(GoVoice(cred_gv, guarda), ops_csv)
        g['govoice'] = True
    except Exception as e:
        print('GoVoice indisponível:', e)
        g = {'govoice': False, 'erroGoVoice': str(e)[:200], 'grupos': GRUPOS, 'tabulacoes': [], 'tabTotal': {}, 'equipes': [],
             'totais': {'discadas': 0, 'atendidas': 0, 'atendidasRede': 0}, 'operadores': [
            {'ramal': o['ramal'], 'govoice': o['nome_govoice'], 'evo': o['nome_evo'], 'equipe': '', 'equipes': [],
             'idEvo': int(o['id_evo']) if o['id_evo'].strip() else None, 'atendidas': 0, 'tmaSeg': 0, 'tabulacoes': {},
             'status': 'offline'} for o in ops_csv]}

    get = lambda caminho: http_json(cred_evo['baseUrl'] + caminho, cred_evo['headers'])
    ids = {o['idEvo'] for o in g['operadores'] if o.get('idEvo')}
    unidades = {b['idBranch']: nome_unidade(b.get('name')) for b in get('/api/v1/configuration')}
    mes = vendas_do_mes(get, guarda, ids, hoje)
    dia = vendas_do_dia(get, hoje.isoformat(), ids)
    aj = ajustes()  # vale também para as vendas que já estavam no cache
    gn = ganhas()
    for v in dia + mes:
        v['idEmployee'] = aj.get(v['id'], v['idEmployee'])
        if not v['pago'] and v['id'] in gn:  # quando o EVO der o pagamento, vale a data do EVO
            v.update(pago=True, vencida=False, quando=gn[v['id']])

    # conta no dia em que foi PAGA (venda de ontem paga hoje entra hoje)
    pagas = [v for v in dia + mes if v['pago'] and v['quando'][:7] == hoje.isoformat()[:7]]
    nome_por_id = {}
    for o in g['operadores']:
        i = o.get('idEvo')
        vm = [v for v in pagas if v['idEmployee'] == i] if i else []
        vh = [v for v in vm if v['quando'][:10] == hoje.isoformat()]
        o['aguardandoPagamento'] = sum(1 for v in dia + mes if not v['pago'] and v['idEmployee'] == i) if i else 0
        o['cadastradoNoEvo'] = i is not None
        o['vendasHoje'] = {'qtd': len(vh), 'valor': round(sum(v['valor'] for v in vh), 2)}
        o['vendasMes'] = {'qtd': len(vm), 'valor': round(sum(v['valor'] for v in vm), 2)}
        if i:
            nome_por_id[i] = o['govoice']

    def item(v):
        return {'id': v['id'], 'operador': nome_por_id.get(v['idEmployee'], ''), 'plano': v['plano'], 'cliente': v.get('cliente', ''),
                'unidade': unidades.get(v['idBranch'], ''), 'valor': v['valor'], 'quando': v['quando'],
                'venda': v.get('venda', v['quando']), 'vencida': v.get('vencida', False)}
    recentes = sorted(pagas, key=lambda v: v['quando'], reverse=True)
    pendentes = sorted((v for v in {v['id']: v for v in mes + dia}.values() if not v['pago']), key=lambda v: v['quando'], reverse=True)
    dados = dict(g)
    dados.update({
        'exemplo': False,
        'atualizadoEm': agora.isoformat(timespec='seconds'),
        'metaDia': None, 'metaMes': None,
        'ultimasVendas': [item(v) for v in recentes[:6]],
        'vendasRecentes': [item(v) for v in recentes[:20]],   # aba "Últimas vendas"
        'vendasPendentes': [item(v) for v in pendentes],      # aba "Vendas pendentes"
    })
    try:
        dados['tvUnidades'] = coletar_unidades(get)
    except Exception as e:  # não derruba as outras TVs
        print('TV das unidades sem dados do EVO:', e)
        dados['tvUnidades'] = _unidades['dados']
    return dados


def resumo(d):
    on = sum(1 for o in d['operadores'] if o.get('status') != 'offline')
    return (f"GoVoice {'sim' if d['govoice'] else 'NÃO'}, {on} logados; vendas hoje "
            f"{sum(o['vendasHoje']['qtd'] for o in d['operadores'])}, mês {sum(o['vendasMes']['qtd'] for o in d['operadores'])}")


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print(resumo(coletar(*credenciais(), GuardaArquivo())))
