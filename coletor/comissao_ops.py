"""Comissão de cada consultor para o Balcão ("Minha comissão"), pela regra das faixas do ranking do CEO.

Regra (definida pelo comercial em 18/11/2026): a faixa vale POR DIA, pelas vendas pagas naquele dia.
    menos de 3 vendas → sem comissão · M1 3 a 4 = R$ 2 por venda · M2 5 a 6 = R$ 5 · M3 7 a 9 = R$ 7 · SM 10 ou mais = R$ 10
    A faixa do dia vale para TODAS as vendas daquele dia.
Venda paga = mesma regra das TVs (comissionado, data do PAGAMENTO, boleto vale o 1º boleto).
Projeção do mês = comissão até agora + (comissão por dia trabalhado no mês) × dias úteis que faltam (seg a sáb). É estimativa.

Cada consultor tem a própria senha. O arquivo dele sai criptografado com a chave dessa senha
(PBKDF2, sal "ufit-op") e com nome "op-<16 primeiros hex do SHA-256 da chave>.enc": quem não tem a senha
não sabe nem qual arquivo é de quem.
"""
import base64, calendar, hashlib, json, os
from datetime import date, datetime, timedelta

FAIXAS = [  # (sigla, nome, mínimo de vendas no dia, R$ por venda), da menor para a maior
    ('M1', 'Bronze', 3, 2),
    ('M2', 'Prata', 5, 5),
    ('M3', 'Ouro', 7, 7),
    ('SM', 'Super Meta', 10, 10),
]
REGRA_TEXTO = ('A faixa vale por dia, pelas vendas pagas no dia: M1 (3 a 4) = R$ 2 por venda · M2 (5 a 6) = R$ 5 · '
               'M3 (7 a 9) = R$ 7 · SM (10 ou mais) = R$ 10 · abaixo de 3 vendas no dia não comissiona. '
               'A faixa do dia vale para todas as vendas daquele dia.')
MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']


def faixa(qtd):
    atual = None
    for f in FAIXAS:
        if qtd >= f[2]:
            atual = f
    return atual


def proxima(qtd):
    for f in FAIXAS:
        if qtd < f[2]:
            return f
    return None


def nome_arquivo(chave_b64url):
    raw = base64.urlsafe_b64decode(chave_b64url + '=' * (-len(chave_b64url) % 4))
    return 'op-' + hashlib.sha256(raw).hexdigest()[:16]


def _dias_uteis(de, ate):
    """Dias de segunda a sábado entre de e ate (inclusive)."""
    n, d = 0, de
    while d <= ate:
        n += d.weekday() < 6
        d += timedelta(days=1)
    return n


def _dias_trabalhados(dp, nome, mes):
    """Dias do mês com ligação no ramal da pessoa (o mesmo critério da comissão do DP)."""
    ramais_dele = {r for r, n in (dp.get('ramais') or {}).items() if n == nome}
    antes = [(a['ramal'], a['nome'], a['ate']) for a in dp.get('ramaisAntes') or []]
    dias = set()
    for dia, cont in (dp.get('discador') or {}).items():
        if not dia.startswith(mes):
            continue
        for r, n in cont.items():
            if not n:
                continue
            dono = next((nm for rr, nm, ate in antes if rr == r and dia <= ate), None)
            if dono == nome or (dono is None and r in ramais_dele):
                dias.add(dia)
    return dias


def resumo_dia(qtd):
    f = faixa(qtd)
    return {'vendas': qtd, 'sigla': f[0] if f else '', 'porVenda': f[3] if f else 0, 'comissao': qtd * f[3] if f else 0}


def periodo(dp, nome, vendas, mes, hoje):
    a, m = map(int, mes.split('-'))
    fim = date(a, m, calendar.monthrange(a, m)[1])
    fechado = fim < hoje
    do_mes = [v for v in vendas if v['quando'][:7] == mes]
    por_dia = {}
    for v in do_mes:
        por_dia.setdefault(v['quando'][:10], []).append(v)
    trabalhados = _dias_trabalhados(dp, nome, mes) | set(por_dia)
    dias = []
    for d in sorted(trabalhados):
        r = resumo_dia(len(por_dia.get(d, [])))
        dias.append(dict(r, dia=d, valor=round(sum(v.get('valor') or 0 for v in por_dia.get(d, [])), 2)))
    comissao = sum(d['comissao'] for d in dias)
    na_faixa = {f[0]: sum(1 for d in dias if d['sigla'] == f[0]) for f in FAIXAS}
    p = {'rotulo': MESES[m - 1].capitalize() + ('' if fechado else ' (até hoje)'), 'mes': mes, 'fechado': fechado,
         'vendas': len(do_mes), 'comissao': comissao, 'diasTrabalhados': len(dias), 'diasNaFaixa': na_faixa,
         'dias': dias,
         'listaVendas': [{k: v.get(k) for k in ('quando', 'cliente', 'plano', 'unidade', 'valor')}
                         for v in sorted(do_mes, key=lambda x: x['quando'], reverse=True)]}
    # meta do mês = meta do dia × dias úteis do mês (seg a sáb)
    ini = date(a, m, 1)
    uteis = _dias_uteis(ini, fim)
    passados = _dias_uteis(ini, min(hoje, fim))
    p['valorVendido'] = round(sum(v.get('valor') or 0 for v in do_mes), 2)
    p['metaMes'] = {'diasUteis': uteis, 'diasUteisPassados': passados,
                    'faixas': [{'sigla': s, 'nome': n, 'meta': mi * uteis, 'ateHoje': mi * passados} for s, n, mi, pv in FAIXAS]}
    if not fechado:
        faltam = _dias_uteis(hoje + timedelta(days=1), fim)
        por_dia_trab = comissao / len(dias) if dias else 0
        p['projecao'] = {'valor': round(comissao + por_dia_trab * faltam, 2), 'diasUteisFaltam': faltam,
                         'comissaoPorDia': round(por_dia_trab, 2),
                         'texto': f'Estimativa: mantendo a média de {_brl(por_dia_trab)} por dia trabalhado nos {faltam} dias úteis que faltam (seg a sáb).'}
    return p


def _brl(v):
    return 'R$ ' + f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def minha_comissao(dp, nome, hoje=None, agora=None, atendimento=None):
    hoje = hoje or date.today()
    vendas = [v for v in dp.get('historicoVendas') or [] if v.get('operador') == nome]
    hoje_txt = hoje.isoformat()
    qtd = sum(1 for v in vendas if v['quando'][:10] == hoje_txt)
    r = resumo_dia(qtd)
    prox = proxima(qtd)
    r['proxima'] = None if not prox else {'sigla': prox[0], 'nome': prox[1], 'minimo': prox[2], 'porVenda': prox[3],
                                          'faltam': prox[2] - qtd, 'comissaoSeChegar': prox[2] * prox[3]}
    mes = hoje_txt[:7]
    pas = (hoje.replace(day=1) - timedelta(days=1)).isoformat()[:7]
    return {'operador': nome, 'atualizadoEm': agora or datetime.now().astimezone().isoformat(timespec='seconds'),
            'regra': {'texto': REGRA_TEXTO, 'faixas': [{'sigla': s, 'nome': n, 'minimo': mi, 'porVenda': pv} for s, n, mi, pv in FAIXAS]},
            'hoje': dict(r, dia=hoje_txt),
            'periodos': [periodo(dp, nome, vendas, mes, hoje), periodo(dp, nome, vendas, pas, hoje)],
            'atendimento': atendimento or {}}


def gravar_todos(dp, chaves, pasta, hoje=None, agora=None, atendimento=None):
    """chaves = {nome do consultor: chave base64url}. Grava <pasta>/<arquivo>.json e <arquivo>.key (a chave, para o passo que criptografa)."""
    os.makedirs(pasta, exist_ok=True)
    feitos = 0
    for nome, chave in chaves.items():
        arq = nome_arquivo(chave)
        with open(os.path.join(pasta, arq + '.json'), 'w', encoding='utf-8') as f:
            json.dump(minha_comissao(dp, nome, hoje, agora, (atendimento or {}).get(nome)), f, ensure_ascii=False)
        with open(os.path.join(pasta, arq + '.key'), 'w', encoding='utf-8') as f:
            f.write(chave)
        feitos += 1
    return feitos


if __name__ == '__main__':  # teste: python comissao_ops.py dp.json Felipe
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    with open(sys.argv[1], encoding='utf-8') as f:
        dp = json.load(f)
    d = minha_comissao(dp, sys.argv[2])
    print('hoje', d['hoje'])
    for p in d['periodos']:
        print(p['rotulo'], p['vendas'], 'vendas', _brl(p['comissao']), p['diasNaFaixa'], p.get('projecao', {}).get('valor'))
        for x in p['dias']:
            print('  ', x)
