"""Painel da Gestão do call center: conversas do EvoTalks por consultor + ligações do GoVoice + avisos.

Sai em gestao.json, que na nuvem vira gestao.enc (só abre com a senha da gestão).
Leitura apenas: o token do EvoTalks consegue apagar dados, por isso só as ferramentas de LEITURA são chamadas.

Consultor ↔ EvoTalks pela coluna id_evotalks de operadores.csv (usuário callcenterNN).

Situação de cada conversa ABERTA (dona = último atendente, last_fk_user), da mais grave para a mais leve:
- atencao      : cliente esperando há mais de 24 h (janela do WhatsApp fechou: só volta com modelo)
                 ou conversa parada há PARADA_DIAS dias ou mais
- semResposta  : cliente escreveu e nenhuma pessoa respondeu ainda
- retorno      : a conversa andou, o cliente falou por último e espera a nossa resposta
- followup     : nós falamos por último, o cliente já tinha respondido antes e sumiu há 1 dia ou mais
- semRetornoCliente : 1º contato nosso (disparo/abordagem) e o cliente nunca respondeu, há 1 dia ou mais
- emDia        : última mensagem há menos de 1 dia
"""
import json, os, statistics, time, urllib.request
from datetime import datetime, timedelta

import coletor_core as cc

MCP_URL = 'https://ufit.evotalks.com.br/mcp'
LEITURA = {'reports_run_query'}
PARADA_DIAS = 5
DIAS_HISTORICO = 90      # conversas abertas iniciadas nos últimos N dias
DIAS_AVISOS = 7          # janela dos avisos de "pegou conversa" e "abriu e deixou aberta"
MAX_LISTA = 80           # conversas listadas por consultor e situação (o total vem sempre)
SITUACOES = ['atencao', 'semResposta', 'retorno', 'followup', 'semRetornoCliente', 'emDia']


# ------------------------------------------------------------------ EvoTalks (MCP, só leitura)
class EvoTalks:
    def __init__(self, token):
        self.h = {'Authorization': token if token.startswith('Bearer ') else 'Bearer ' + token,
                  'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                  'User-Agent': 'ufit-gestao/1.0'}  # o Cloudflare bloqueia o User-Agent padrão do Python
        self.sessao, self.seq = None, 0
        self._rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'ufit-gestao', 'version': '1.0'}})
        self._rpc('notifications/initialized', {}, notificacao=True)

    def _rpc(self, metodo, params, notificacao=False):
        corpo = {'jsonrpc': '2.0', 'method': metodo, 'params': params}
        if not notificacao:
            self.seq += 1
            corpo['id'] = self.seq
        h = dict(self.h)
        if self.sessao:
            h['Mcp-Session-Id'] = self.sessao
        with urllib.request.urlopen(urllib.request.Request(MCP_URL, json.dumps(corpo).encode(), h), timeout=180) as r:
            self.sessao = r.headers.get('Mcp-Session-Id') or self.sessao
            txt = r.read().decode('utf-8')
        linhas = [l[5:] for l in txt.splitlines() if l.startswith('data:')]
        return json.loads(linhas[-1]) if linhas else (json.loads(txt) if txt.strip().startswith('{') else None)

    def consulta(self, q):
        r = self._rpc('tools/call', {'name': 'reports_run_query', 'arguments': {'query': q}})
        if 'error' in r:
            raise RuntimeError(r['error'])
        txt = ''.join(c.get('text', '') for c in r['result']['content'])
        if r['result'].get('isError'):
            raise RuntimeError(txt[:300])
        d = json.loads(txt)
        if d.get('truncated'):
            print('aviso: consulta do EvoTalks cortada (truncated)', flush=True)
        return [{k.split('__', 1)[1]: v for k, v in l.items()} for l in d.get('data', [])]


def token_evotalks():
    t = os.environ.get('EVOTALKS_TOKEN')
    if t:
        return t.strip()
    p = os.path.join(os.path.expanduser('~'), '.claude.json')  # no PC: o mesmo token do MCP "omni"
    with open(p, encoding='utf-8') as f:
        cfg = json.load(f)
    for proj in cfg.get('projects', {}).values():
        omni = (proj.get('mcpServers') or {}).get('omni')
        if omni:
            return omni['headers']['Authorization']
    raise RuntimeError('token do EvoTalks não encontrado')


CAMPOS = ['id', 'last_fk_user', 'initiatedby', 'firstresponse_fk_user', 'close_fk_user', 'fk_queue', 'status',
          'lastrcvdmsgtime', 'lastsntmsgtime', 'begintime', 'endtime', 'clientname', 'clientnumber', 'endreason']


def baixar_conversas(et, ids):
    ids_txt = [str(i) for i in ids]
    dims = [f'Chathistory.{c}' for c in CAMPOS]
    abertas = et.consulta({'dimensions': dims, 'limit': 20000,
                           'filters': [{'member': 'Chathistory.last_fk_user', 'operator': 'equals', 'values': ids_txt},
                                       {'member': 'Chathistory.status', 'operator': 'equals', 'values': ['1']}],
                           'timeDimensions': [{'dimension': 'Chathistory.begintime', 'dateRange': f'last {DIAS_HISTORICO} days'}]})
    semana = et.consulta({'dimensions': dims, 'limit': 20000,
                          'filters': [{'or': [{'member': f'Chathistory.{c}', 'operator': 'equals', 'values': ids_txt}
                                              for c in ('last_fk_user', 'initiatedby', 'firstresponse_fk_user', 'close_fk_user')]}],
                          'timeDimensions': [{'dimension': 'Chathistory.begintime', 'dateRange': f'last {DIAS_AVISOS} days'}]})
    return abertas, semana


def situacao(c, agora):
    rc, sn = c.get('lastrcvdmsgtime') or 0, c.get('lastsntmsgtime') or 0
    # depois de 24 h sem resposta o EvoTalks grava "Atendimento bloqueado (fim da janela de 24h)" como mensagem
    # enviada: a última fala real continua sendo do cliente
    if rc and sn and 86000 < sn - rc < 86800:
        sn = rc - 1
    ultima = max(rc, sn)
    parada = (agora - ultima) / 86400 if ultima else 999
    cliente_por_ultimo = rc > sn
    if cliente_por_ultimo and (agora - rc) > 86400:
        s = 'atencao'
    elif parada >= PARADA_DIAS:
        s = 'atencao'
    elif cliente_por_ultimo:
        s = 'semResposta' if not c.get('firstresponse_fk_user') else 'retorno'
    elif parada < 1:
        s = 'emDia'
    elif rc:
        s = 'followup'
    else:
        s = 'semRetornoCliente'
    return s, parada, cliente_por_ultimo


def montar_conversas(abertas, semana, ops, agora):
    """ops: {id_evotalks: nome}. Devolve (por consultor, totais, avisos do EvoTalks)."""
    nome_de = lambda i: ops.get(i) or ('consultor de unidade' if i else 'robô/ninguém')
    por = {n: {'contagem': {s: 0 for s in SITUACOES}, 'listas': {s: [] for s in SITUACOES}, 'abertas': 0,
               'esperando24h': 0, 'paradas': 0} for n in ops.values()}
    for c in abertas:
        n = ops.get(c.get('last_fk_user'))
        if not n:
            continue
        s, parada, cli = situacao(c, agora)
        p = por[n]
        p['abertas'] += 1
        p['contagem'][s] += 1
        if s == 'atencao':
            if cli:
                p['esperando24h'] += 1
            else:
                p['paradas'] += 1
        p['listas'][s].append({
            'id': c['id'], 'cliente': (c.get('clientname') or '').strip() or '—', 'telefone': c.get('clientnumber') or '',
            'fila': c.get('fk_queue'), 'inicio': (c.get('begintime') or '')[:16], 'paradoDias': round(parada, 1),
            'clienteEsperando': cli, 'motivo': ('cliente esperando há mais de 24 h' if cli else f'parada há {int(parada)} dias') if s == 'atencao' else '',
            'abriu': nome_de(c.get('initiatedby')) if c.get('initiatedby') else 'cliente',
            'primeiraResposta': nome_de(c.get('firstresponse_fk_user')) if c.get('firstresponse_fk_user') else '',
        })
    for p in por.values():
        for s in SITUACOES:
            p['listas'][s].sort(key=lambda x: -x['paradoDias'])
            p['listas'][s] = p['listas'][s][:MAX_LISTA]

    # últimos 7 dias: conversas que chegaram ao consultor depois de outra pessoa responder, e as que ele abriu
    pegou = {n: [] for n in ops.values()}
    abriu = {n: {'total': 0, 'abertas': 0} for n in ops.values()}
    for c in semana:
        dono, primeiro, ini = c.get('last_fk_user'), c.get('firstresponse_fk_user'), c.get('initiatedby')
        if dono in ops and primeiro and primeiro != dono:
            pegou[ops[dono]].append({'id': c['id'], 'cliente': (c.get('clientname') or '').strip() or '—', 'fila': c.get('fk_queue'),
                                     'deQuem': nome_de(primeiro), 'colega': primeiro in ops, 'aberta': c.get('status') == 1,
                                     'inicio': (c.get('begintime') or '')[:16]})
        if ini in ops:
            abriu[ops[ini]]['total'] += 1
            abriu[ops[ini]]['abertas'] += 1 if c.get('status') == 1 else 0
    for n in por:
        por[n]['pegouDeOutros'] = sorted(pegou[n], key=lambda x: x['inicio'], reverse=True)[:MAX_LISTA]
        por[n]['pegouDeColegas'] = sum(1 for x in pegou[n] if x['colega'])
        por[n]['pegouDeUnidades'] = sum(1 for x in pegou[n] if not x['colega'])
        por[n]['abriu7d'] = abriu[n]
    total = {s: sum(p['contagem'][s] for p in por.values()) for s in SITUACOES}
    total['abertas'] = sum(p['abertas'] for p in por.values())
    return por, total


# ------------------------------------------------------------------ GoVoice: desempenho de hoje + histórico diário
def ramal_de(ext):
    return (ext or '').split('@')[0]


def coletar_ligacoes(gv, guarda, hoje):
    perf = gv.get('/api/agents/performance', window='today')
    pausas = gv.get('/api/agents/pauses', window='today')
    pausa_por = {ramal_de(p['extension']): p.get('byReason') or {} for p in pausas.get('byOperator', [])}
    hoje_ops = {}
    for a in perf:
        r = ramal_de(a.get('extension'))
        hoje_ops[r] = {
            'atendidas': a.get('attended') or 0, 'faladoSeg': round(a.get('talk_seconds') or 0),
            'tmaSeg': round(a.get('tma_seconds') or 0), 'logadoSeg': round(a.get('logged_seconds') or 0),
            'pausaSeg': round(a.get('pause_seconds') or 0), 'tabulandoSeg': round(a.get('qualifying_seconds') or 0),
            'pausaImprodutivaSeg': round(a.get('pause_unproductive_seconds') or 0), 'ociosoSeg': round(a.get('idle_seconds') or 0),
            'ociosoMedioSeg': round(a.get('idle_avg_seconds') or 0), 'ocupacao': round(a.get('occupancy') or 0, 1),
            'manuais': a.get('manual_calls') or 0, 'primeiroLogin': a.get('first_login_at'), 'ultimoLogout': a.get('last_logout_at'),
            'pausas': {k: round(v) for k, v in pausa_por.get(r, {}).items()},
        }
    h = guarda.ler('gestao_ligacoes') or {}
    h[hoje.isoformat()] = hoje_ops
    for d in sorted(h)[:-21]:   # guarda 3 semanas
        del h[d]
    guarda.gravar('gestao_ligacoes', h)
    return hoje_ops, h


# ------------------------------------------------------------------ avisos
def mediana(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else 0


def mmss(s):
    s = int(s or 0)
    return f'{s // 3600}h{(s % 3600) // 60:02d}' if s >= 3600 else f'{s // 60} min'


def avisos_ligacoes(hoje_ops, nomes, hist, hoje):
    """Compara cada um com a mediana da equipe HOJE (só quem ficou logado 1 h ou mais). Assim, um dia fraco para todos não vira alarme."""
    out = []
    ativos = {r: o for r, o in hoje_ops.items() if r in nomes and o['logadoSeg'] >= 3600}
    if len(ativos) >= 2:
        m_at = mediana([o['atendidas'] for o in ativos.values()])
        m_fal = mediana([o['faladoSeg'] for o in ativos.values()])
        m_tab = mediana([o['tabulandoSeg'] / o['atendidas'] for o in ativos.values() if o['atendidas']])
        for r, o in ativos.items():
            n = nomes[r]
            if m_at >= 10 and o['atendidas'] < 0.6 * m_at:
                out.append(aviso('alto' if o['atendidas'] < 0.4 * m_at else 'medio', n, 'Poucas ligações atendidas hoje',
                                 f"{o['atendidas']} atendidas; a média da equipe hoje é {round(m_at)}.", 'ligacoes'))
            if m_fal >= 1800 and o['faladoSeg'] < 0.6 * m_fal:
                out.append(aviso('medio', n, 'Pouco tempo em conversa hoje', f"{mmss(o['faladoSeg'])} falando; a média da equipe é {mmss(m_fal)}.", 'ligacoes'))
            if o['atendidas'] >= 10:
                tab = o['tabulandoSeg'] / o['atendidas']
                if tab > max(45, 2 * m_tab):
                    out.append(aviso('medio', n, 'Tabulação demorada', f"{round(tab)} s por ligação para tabular; a média da equipe é {round(m_tab)} s.", 'ligacoes'))
    # pausa longa (fora almoço e qualificação): se metade ou mais da equipe tem a mesma, é jeito de trabalhar → 1 aviso "Equipe"
    longas = {}
    for r, o in hoje_ops.items():
        if r in nomes:
            for k, v in o['pausas'].items():
                if 'almo' not in cc.norm(k) and 'qualific' not in cc.norm(k) and v >= 3600:
                    longas.setdefault(k, {})[nomes[r]] = v
    logados = sum(1 for r, o in hoje_ops.items() if r in nomes and o['logadoSeg'] >= 3600) or 1
    for k, quem in longas.items():
        if len(quem) >= 3 and len(quem) >= logados / 2:
            out.append(aviso('medio', 'Equipe', f'Pausa "{k}" longa em {len(quem)} pessoas',
                             'Parece jeito de trabalhar, não caso isolado: ' + ', '.join(f'{n} {mmss(v)}' for n, v in sorted(quem.items(), key=lambda x: -x[1]))
                             + f'. Vale combinar o que se faz nessa pausa (ex.: WhatsApp) e quanto tempo pode.', 'ligacoes'))
        else:
            for n, v in quem.items():
                out.append(aviso('alto' if v >= 7200 else 'medio', n, f'Pausa "{k}" longa', f'{mmss(v)} em pausa "{k}" hoje.', 'ligacoes'))
    for r, o in hoje_ops.items():
        if r not in nomes:
            continue
        n = nomes[r]
        almoco = sum(v for k, v in o['pausas'].items() if 'almo' in cc.norm(k))
        if almoco > 80 * 60:
            out.append(aviso('medio', n, 'Almoço passou do tempo', f'{mmss(almoco)} em pausa de almoço.', 'ligacoes'))
        if o['logadoSeg'] >= 5400 and o['atendidas'] == 0:
            out.append(aviso('alto', n, 'Logado sem atender', f"{mmss(o['logadoSeg'])} logado e nenhuma ligação atendida.", 'ligacoes'))
    # últimos 7 dias úteis com dados (sem hoje): atendidas por dia x equipe
    dias = [d for d in sorted(hist) if d < hoje.isoformat()][-7:]
    if len(dias) >= 3:
        media = {}
        for r in nomes:
            vals = [hist[d][r]['atendidas'] for d in dias if r in hist[d] and hist[d][r]['logadoSeg'] >= 3600]
            if len(vals) >= 2:
                media[r] = sum(vals) / len(vals)
        m = mediana(list(media.values()))
        for r, v in media.items():
            if m >= 10 and v < 0.6 * m:
                out.append(aviso('medio', nomes[r], 'Abaixo da equipe na semana',
                                 f'{round(v)} atendidas por dia nos últimos {len(dias)} dias; a média da equipe é {round(m)}.', 'ligacoes'))
    return out


def avisos_conversas(por):
    out = []
    # "abre e não fecha": comparado com a equipe (disparo sem resposta deixa quase tudo aberto para todos)
    pct = {n: p['abriu7d']['abertas'] / p['abriu7d']['total'] for n, p in por.items() if p['abriu7d']['total'] >= 10}
    m_pct = mediana(list(pct.values()))
    for n, p in por.items():
        if p['pegouDeColegas'] >= 3:
            out.append(aviso('alto', n, 'Ficou com conversas de colegas',
                             f"{p['pegouDeColegas']} conversas dos últimos {DIAS_AVISOS} dias que outro consultor do call center respondeu primeiro "
                             f"estão agora com {n}. Pode ser transferência combinada; confira na lista.", 'conversas'))
        a = p['abriu7d']
        if n in pct and pct[n] >= m_pct + 0.25:
            out.append(aviso('medio', n, 'Deixa aberto mais que a equipe',
                             f"Abriu {a['total']} conversas nos últimos {DIAS_AVISOS} dias e {round(100 * pct[n])}% continuam abertas; na equipe são {round(100 * m_pct)}%.", 'conversas'))
        if p['esperando24h'] >= 5:
            out.append(aviso('alto', n, 'Clientes esperando há mais de 24 h',
                             f"{p['esperando24h']} clientes falaram por último e não tiveram resposta em 24 h (a janela do WhatsApp fechou: só volta com modelo).", 'conversas'))
        if p['paradas'] >= 20:
            out.append(aviso('medio', n, f'Muitas conversas paradas ({PARADA_DIAS}+ dias)',
                             f"{p['paradas']} conversas abertas sem movimento há {PARADA_DIAS} dias ou mais. Encerrar o que não tem mais chance libera a fila.", 'conversas'))
        if p['contagem']['semResposta'] >= 3:
            out.append(aviso('medio', n, 'Cliente sem nenhuma resposta',
                             f"{p['contagem']['semResposta']} clientes escreveram e ninguém respondeu ainda.", 'conversas'))
    return out


NIVEL = {'alto': 0, 'medio': 1, 'info': 2}


def aviso(nivel, operador, titulo, detalhe, origem):
    return {'nivel': nivel, 'operador': operador, 'titulo': titulo, 'detalhe': detalhe, 'origem': origem}


def para_consultor(g):
    """O que cada consultor vê no Balcão sobre o próprio atendimento (sem nota, sem aviso, sem comparação com colegas):
    ligações de hoje e as conversas dele que estão esperando ação, com a lista dos clientes."""
    out = {}
    lig = (g.get('ligacoes') or {}).get('hoje') or {}
    conv = (g.get('conversas') or {}).get('porConsultor') or {}
    for n in g.get('operadores', []):
        l, c = lig.get(n), conv.get(n)
        item = {}
        if l:
            item['ligacoes'] = {k: l[k] for k in ('atendidas', 'faladoSeg', 'tmaSeg', 'logadoSeg')}
        if c:
            ct = c['contagem']
            item['conversas'] = {'abertas': c['abertas'], 'contagem': ct}
            pend = []
            for s, rot in (('semResposta', 'Sem nenhuma resposta'), ('retorno', 'Esperando a sua resposta'), ('atencao', 'Esperando há mais de 24 h'), ('followup', 'Fazer follow-up')):
                for x in c['listas'].get(s, []):
                    if s == 'atencao' and not x['clienteEsperando']:
                        continue
                    pend.append({'situacao': rot, 'cliente': x['cliente'], 'telefone': x['telefone'], 'paradoDias': x['paradoDias']})
            item['conversas']['pendentes'] = pend[:40]
        out[n] = item
    return out


# ------------------------------------------------------------------ junta tudo
def coletar(gv, guarda, discador=None):
    agora_dt = datetime.now(cc.BRT)
    agora, hoje = agora_dt.timestamp(), agora_dt.date()
    ops_csv = cc.ler_csv('operadores.csv')
    nomes_ramal = {o['ramal'].strip(): o['nome_govoice'].strip() for o in ops_csv}
    ops_et = {int(o['id_evotalks']): o['nome_govoice'].strip() for o in ops_csv if (o.get('id_evotalks') or '').strip().isdigit()}
    out = {'atualizadoEm': agora_dt.isoformat(timespec='seconds'), 'operadores': [nomes_ramal[r] for r in nomes_ramal],
           'regras': {'paradaDias': PARADA_DIAS, 'diasAvisos': DIAS_AVISOS, 'diasHistorico': DIAS_HISTORICO, 'maxLista': MAX_LISTA},
           'erros': []}
    avisos = []

    try:
        et = EvoTalks(token_evotalks())
        abertas, semana = baixar_conversas(et, list(ops_et))
        por, total = montar_conversas(abertas, semana, ops_et, agora)
        out['conversas'] = {'porConsultor': por, 'total': total}
        avisos += avisos_conversas(por)
        print('gestão: EvoTalks ok —', total['abertas'], 'conversas abertas', flush=True)
    except Exception as e:
        out['erros'].append('EvoTalks indisponível: ' + type(e).__name__ + ' ' + str(e)[:150])
        print('gestão: EvoTalks indisponível:', type(e).__name__, str(e)[:200], flush=True)

    try:
        hoje_ops, hist = coletar_ligacoes(gv, guarda, hoje)
        dias = sorted(hist)[-8:]
        out['ligacoes'] = {
            'hoje': {nomes_ramal[r]: dict(o, ramal=r) for r, o in hoje_ops.items() if r in nomes_ramal},
            'historico': {d: {nomes_ramal[r]: {k: o[k] for k in ('atendidas', 'faladoSeg', 'logadoSeg', 'pausaImprodutivaSeg', 'tabulandoSeg')}
                              for r, o in hist[d].items() if r in nomes_ramal} for d in dias},
        }
        if discador:  # ligações no discador por dia (todas as tentativas), do mesmo cache da comissão
            out['ligacoes']['discador'] = {d: {nomes_ramal.get(r, r): n for r, n in v.items()} for d, v in sorted(discador.items())[-14:]}
        avisos += avisos_ligacoes(hoje_ops, nomes_ramal, hist, hoje)
    except Exception as e:
        out['erros'].append('GoVoice indisponível: ' + type(e).__name__ + ' ' + str(e)[:150])
        print('gestão: GoVoice indisponível:', type(e).__name__, str(e)[:200], flush=True)

    avisos.sort(key=lambda a: (NIVEL[a['nivel']], a['operador'], a['titulo']))
    out['avisos'] = avisos
    return out


if __name__ == '__main__':  # teste no PC: python coletor_gestao.py saida.json
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    cred_evo, cred_gv = cc.credenciais()
    d = coletar(cc.GoVoice(cred_gv, cc.GuardaArquivo()), cc.GuardaArquivo())
    with open(sys.argv[1] if len(sys.argv) > 1 else 'gestao.json', 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
    print(len(d['avisos']), 'avisos;', d['erros'])
