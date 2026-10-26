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
    with open(os.path.join(saida, 'dados.json'), 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False)
    print('TVs ok —', coletor_core.resumo(dados), flush=True)

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
