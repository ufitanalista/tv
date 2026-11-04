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
        valor = sum(i.get('saleValue') or 0 for i in itens)
        # boleto (paymentType 5): conta só o 1º boleto, não o plano inteiro (pedido de 04/11/2026).
        # Ex.: anual R$ 958,80 em 12 boletos de 79,90 → vale 79,90. Usa `ammount` (sem descontar a tarifa do banco).
        boletos = sorted((x for x in recs if (x.get('paymentType') or {}).get('id') == 5 and not x.get('cancellationDate')
                          and (x.get('status') or {}).get('id') != 3),  # 3 = cancelado
                         key=lambda x: x.get('dueDate') or '')
        if boletos:
            valor = valor - sum(x.get('ammount') or 0 for x in boletos) + (boletos[0].get('ammount') or 0)
        r.append({
            'id': s['idSale'], 'idEmployee': quem, 'idBranch': s.get('idBranch'),
            'valor': round(valor, 2), 'plano': plano,
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
    c['vendas'], m2 = reconferir(get, c['vendas'], ids)
    m3 = recalcular_boletos(get, c, ids)
    if mudou or m2 or m3:
        guarda.gravar('vendas_mes', c)
    return c['vendas']


VALOR_BOLETO = 1  # versão da regra "boleto vale só o 1º boleto": quando muda, as vendas guardadas são recalculadas


def recalcular_boletos(get, c, ids):
    """Uma vez só: busca de novo no EVO as vendas guardadas com plano de BOLETO, para valer a regra do 1º boleto
    também nos dias já fechados (sem rebuscar o mês inteiro). Devolve True se mexeu no cache."""
    if c.get('valorBoleto') == VALOR_BOLETO:
        return False
    vendas = list(c['vendas'])
    for k, v in enumerate(vendas):
        if 'BOLETO' not in (v.get('plano') or '').upper():
            continue
        try:
            novo = resumir([get(f"/api/v2/sales/{v['id']}?showReceivables=true")], ids)
        except Exception as e:  # tenta de novo no próximo ciclo
            print('venda', v['id'], 'sem resposta do EVO (boleto):', e)
            return False
        if novo:  # mantém pago/quando já guardados; só troca o valor
            vendas[k] = dict(v, valor=novo[0]['valor'])
    c['vendas'], c['valorBoleto'] = vendas, VALOR_BOLETO
    return True


def reconferir(get, vendas, ids, desde=''):
    """Vendas de dias fechados ainda sem pagamento: confere de novo se o cliente já pagou ou se a venda foi excluída.
    `desde` (AAAA-MM-DD): só as lançadas a partir desse dia. Devolve (vendas, mudou)."""
    vendas, mudou, excluidas = list(vendas), False, set()
    for k, v in enumerate(vendas):
        if not v.get('pago') and v.get('venda', '')[:10] >= desde:
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
                vendas[k], mudou = novo[0], True
    if excluidas:
        print('vendas excluídas no EVO, saem do painel:', sorted(excluidas))
        vendas, mudou = [v for v in vendas if v['id'] not in excluidas], True
    return vendas, mudou


# Histórico para o filtro de período da TV 2 (DP calcula a comissão): vendas dos meses anteriores,
# desde HISTORICO_DESDE. Dia fechado é buscado uma vez; na 1ª carga vai aos poucos (DIAS_POR_CICLO por coleta).
HISTORICO_DESDE = '2026-09-01'
DIAS_POR_CICLO = 15


def vendas_historico(get, guarda, ids, hoje):
    """Vendas de HISTORICO_DESDE até o fim do mês passado (o mês atual vem de vendas_do_mes).
    Guarda os operadores que já passaram pela lista: quem sai continua no histórico.
    Devolve (vendas, dias que ainda faltam buscar)."""
    regra = 'comissionado-pago-cliente'
    c = guarda.ler('vendas_hist') or {}
    if c.get('regra') != regra or c.get('desde') != HISTORICO_DESDE:
        c = {'regra': regra, 'desde': HISTORICO_DESDE, 'ids': [], 'dias': [], 'vendas': []}
    mudou = False
    if set(ids) - set(c['ids']):  # operador novo: busca os dias de novo para trazer as vendas dele
        c['ids'], c['dias'], mudou = sorted(set(c['ids']) | set(ids)), [], True
    todos = set(c['ids'])
    fim = hoje.replace(day=1)
    dia = datetime.strptime(HISTORICO_DESDE, '%Y-%m-%d').date()
    faltam, feitos = 0, 0
    while dia < fim:
        if dia.isoformat() not in c['dias']:
            if feitos < DIAS_POR_CICLO:
                por_id = {v['id']: v for v in c['vendas']}
                por_id.update({v['id']: v for v in vendas_do_dia(get, dia.isoformat(), todos)})
                c['vendas'] = list(por_id.values())
                c['dias'].append(dia.isoformat())
                feitos, mudou = feitos + 1, True
            else:
                faltam += 1
        dia += timedelta(days=1)
    # venda do fim do mês paga depois: reconfere as dos últimos 60 dias
    c['vendas'], m2 = reconferir(get, c['vendas'], todos, (hoje - timedelta(days=60)).isoformat())
    m3 = recalcular_boletos(get, c, todos)
    if mudou or m2 or m3:
        guarda.gravar('vendas_hist', c)
    return c['vendas'], faltam


# Dias trabalhados (comissão): ligações de cada ramal por dia no discador. Dia fechado é buscado uma vez.
DISCADOR_DESDE = '2026-10-01'


def dias_discador(gv, guarda, hoje):
    """{dia: {ramal: nº de ligações}} de DISCADOR_DESDE até hoje (hoje é buscado de novo a cada coleta)."""
    c = guarda.ler('dias_gv') or {}
    if c.get('desde') != DISCADOR_DESDE:
        c = {'desde': DISCADOR_DESDE, 'dias': {}}
    ramais = sorted({a['endpoint_id'] for a in gv.get('/api/agents') if a.get('endpoint_id')})
    dia, feitos, mudou = datetime.strptime(DISCADOR_DESDE, '%Y-%m-%d').date(), 0, False
    while dia <= hoje:
        d = dia.isoformat()
        if (d not in c['dias'] and feitos < DIAS_POR_CICLO) or dia == hoje:
            cont = {}
            for r in ramais:
                n = gv.get('/api/cdr', start_date=d, end_date=d, extension=r, limit=1)['meta']['item_count']
                if n:
                    cont[r] = n
            if dia == hoje:
                hoje_cont = cont
            else:
                c['dias'][d], feitos, mudou = cont, feitos + 1, True
        dia += timedelta(days=1)
    if mudou:
        guarda.gravar('dias_gv', c)
    return dict(c['dias'], **{hoje.isoformat(): hoje_cont})


def dados_dp(vendas, discador, ops_csv):
    """O que só o DP vê (arquivo com senha própria): vendas pagas, dias no discador e as listas de ajuste."""
    def csv_ou_vazio(nome):
        try:
            return [{k: (v or '').strip() for k, v in l.items()} for l in ler_csv(nome)]
        except OSError:
            return []
    return {
        'historicoVendas': vendas,
        'discador': discador,
        'ramais': {o['ramal'].strip(): o['nome_govoice'].strip() for o in ops_csv},
        'ramaisAntes': csv_ou_vazio('ramais_historico.csv'),   # ramal;nome;ate → até essa data o ramal era dessa pessoa
        'diasAjuste': csv_ou_vazio('dias_trabalhados.csv'),    # mes;nome;dias;vendas_desde;motivo (decisão do DP/gestão)
    }


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

    gv = GoVoice(cred_gv, guarda)
    try:
        g = coletar_govoice(gv, ops_csv)
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
    try:
        hist, hist_faltam = vendas_historico(get, guarda, ids, hoje)
    except Exception as e:  # não derruba as TVs
        print('histórico de vendas indisponível:', type(e).__name__, str(e)[:200])
        hist, hist_faltam = (guarda.ler('vendas_hist') or {}).get('vendas', []), None
    aj = ajustes()  # vale também para as vendas que já estavam no cache
    gn = ganhas()
    for v in dia + mes + hist:
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
    for o in ops_csv:  # quem não está logado hoje (ou saiu da lista) também aparece com nome no histórico
        if (o.get('id_evo') or '').strip().isdigit():
            nome_por_id.setdefault(int(o['id_evo']), (o.get('nome_govoice') or o.get('nome_evo') or '').strip())

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
    # filtro de período da TV 2 (comissão): todas as vendas pagas desde HISTORICO_DESDE, pela data do pagamento.
    # Vai em dados['_dp'], que NÃO entra no arquivo das TVs: na nuvem vira dp.enc, aberto só com a senha do DP.
    todas = {v['id']: v for v in hist + mes + dia}  # o mais recente vale (dia > mês > histórico)
    historico = [dict(item(v), idEmployee=v['idEmployee'], operador=item(v)['operador'] or f"Consultor {v['idEmployee']}")
                 for v in sorted((v for v in todas.values() if v['pago']), key=lambda v: v['quando'], reverse=True)]
    try:
        discador = dias_discador(gv, guarda, hoje)
    except Exception as e:  # não derruba as TVs
        print('dias do discador indisponíveis:', type(e).__name__, str(e)[:200])
        discador = (guarda.ler('dias_gv') or {}).get('dias', {})
    dados['_dp'] = dict(dados_dp(historico, discador, ops_csv), historicoDesde=HISTORICO_DESDE,
                        historicoFaltamDias=hist_faltam, atualizadoEm=dados['atualizadoEm'])
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
