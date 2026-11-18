"""Um ciclo de coleta na nuvem (GitHub Actions), no lugar do servidor.py do PC.

O workflow abre as listas e o estado (criptografados) nas pastas UFIT_CONFIG e UFIT_ESTADO, grava as
credenciais em UFIT_CREDENCIAIS e roda:  python rodar_nuvem.py <pasta_saida>
Saída: <pasta_saida>/dados.json (TVs) e, de hora em hora, <pasta_saida>/conv.json (convênios).
O log é público: só contagens, nunca nomes de clientes.
"""
import json, os, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coletor_core  # noqa: E402
import coletor_convenios  # noqa: E402
import coletor_gestao  # noqa: E402
import comissao_ops  # noqa: E402

INTERVALO_CONVENIOS = 55 * 60


def repetir(dados):
    """repetir_vendas.csv (id_venda;ate): até a hora `ate`, a venda volta ao topo como "repete-<id>" e a TV 2 comemora de novo.
    Não soma no ranking (só entra em ultimasVendas)."""
    try:
        linhas = coletor_core.ler_csv('repetir_vendas.csv')
    except OSError:
        return
    agora = datetime.now().astimezone()
    por_id = {str(v['id']): v for v in dados.get('vendasRecentes', [])}
    novas = []
    for l in linhas:
        i, ate = (l.get('id_venda') or '').strip(), (l.get('ate') or '').strip()
        if i in por_id and ate and datetime.fromisoformat(ate) > agora:
            novas.append(dict(por_id[i], id='repete-' + i))
    if novas:
        novas.sort(key=lambda v: v['quando'], reverse=True)  # a TV comemora da mais antiga para a mais nova
        dados['ultimasVendas'] = novas + dados['ultimasVendas']
        print('repetindo', len(novas), 'venda(s)', flush=True)


def main(saida):
    os.makedirs(saida, exist_ok=True)
    guarda = coletor_core.GuardaArquivo()
    cred_evo, cred_gv = coletor_core.credenciais()

    # TV das unidades: preços e vouchers mudam pouco; reaproveita a leitura dos últimos 30 min
    u = guarda.ler('unidades') or {}
    if u.get('dados'):
        coletor_core._unidades.update(em=u.get('em', 0), dados=u['dados'])
    dados = coletor_core.coletar(cred_evo, cred_gv, guarda)
    guarda.gravar('unidades', coletor_core._unidades)
    repetir(dados)
    # vendas para comissão (aba Período da TV 2): arquivo à parte, criptografado com a chave do DP
    dp = dados.pop('_dp', None)
    if dp:
        with open(os.path.join(saida, 'dp.json'), 'w', encoding='utf-8') as f:
            json.dump(dp, f, ensure_ascii=False)
        print('DP ok —', len(dp['historicoVendas']), 'vendas,', len(dp['discador']), 'dias de discador', flush=True)
    with open(os.path.join(saida, 'dados.json'), 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False)
    print('TVs ok —', coletor_core.resumo(dados), flush=True)
    convenios(saida, guarda, cred_evo)
    gestao_e_comissao(saida, guarda, cred_gv, dados, dp)


def gestao_e_comissao(saida, guarda, cred_gv, dados, dp):
    """Painel da Gestão (gestao.json → gestao.enc, senha da gestão) e "Minha comissão" de cada consultor
    (ops/op-xxxx.json → .enc, cada um com a senha do consultor). Erro aqui nunca derruba as TVs."""
    hoje = datetime.now(coletor_core.BRT).date()
    os.makedirs(saida, exist_ok=True)
    try:
        g = coletor_gestao.coletar(coletor_core.GoVoice(cred_gv, guarda), guarda, (dp or {}).get('discador'))
        g['vendas'] = {}
        if dp:
            for nome in g['operadores']:
                c = comissao_ops.minha_comissao(dp, nome, hoje, dados['atualizadoEm'])
                mes = c['periodos'][0]
                g['vendas'][nome] = {'hoje': c['hoje'], 'mes': {k: mes.get(k) for k in ('vendas', 'comissao', 'diasTrabalhados', 'diasNaFaixa', 'projecao')}}
        with open(os.path.join(saida, 'gestao.json'), 'w', encoding='utf-8') as f:
            json.dump(g, f, ensure_ascii=False)
        print('gestão ok —', len(g['avisos']), 'avisos', ('; ' + ' | '.join(g['erros'])) if g['erros'] else '', flush=True)
    except Exception as e:
        print('ERRO na gestão:', type(e).__name__, str(e)[:200], flush=True)
    try:
        p = os.path.join(coletor_core.PASTA, 'chaves_operadores.json')
        if dp and os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                chaves = json.load(f)
            n = comissao_ops.gravar_todos(dp, chaves, os.path.join(saida, 'ops'), hoje, dados['atualizadoEm'])
            print('minha comissão ok —', n, 'consultores', flush=True)
    except Exception as e:
        print('ERRO na comissão dos consultores:', type(e).__name__, str(e)[:200], flush=True)


def convenios(saida, guarda, cred_evo):
    c = guarda.ler('conv_em') or {}
    if time.time() - c.get('em', 0) >= INTERVALO_CONVENIOS:
        try:
            conv = coletor_convenios.coletar(cred_evo, guarda, lambda m: print(m, flush=True))
            with open(os.path.join(saida, 'conv.json'), 'w', encoding='utf-8') as f:
                json.dump(conv, f, ensure_ascii=False)
            guarda.gravar('conv_em', {'em': time.time()})
            print('convênios ok —', coletor_convenios.resumo(conv), flush=True)
        except Exception as e:  # não derruba as TVs
            print('ERRO nos convênios:', type(e).__name__, str(e)[:200], flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main(sys.argv[1])
