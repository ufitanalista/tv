# TVs do call center UFit (versão online)

As TVs e o Balcão funcionam sem depender de nenhum PC ligado. O próprio GitHub busca os dados no EVO e no GoVoice
a cada 5 minutos (das 6h à meia-noite; de madrugada, a cada 30 min) e publica no GitHub Pages.

**Projeto público, dados fechados.** Tudo que é dado (vendas, clientes, operadores, vouchers, links de venda,
convênios) é publicado **criptografado** (`*.enc`). Quem abrir sem a chave vê só letras sem sentido.
A chave vai no link de cada tela, depois do `#k=`, e nunca sai do navegador.
O que fica aberto aqui é só o código.

## Links das telas
`https://ufitanalista.github.io/ufit-tvs/<página>#k=<CHAVE>`, com a página `tv1.html`, `tv2.html`, `tv-unidades.html`,
`convenios.html` ou `balcao.html`. A chave não fica escrita neste projeto: o usuário a guarda (ela também está em
`C:\Users\comercial2\.claude\ufit-tvs-chaves.json`, campo `tv`). Depois de abrir uma vez com a chave, o navegador
lembra dela e os links entre as páginas funcionam sem o `#k=`.

## Mudar uma lista (operadores, venda dada como paga, ajuste de vendedor, TV das unidades) ou o Balcão
1. Mude o arquivo de sempre na pasta `dashboard-tv` (versão do PC). Ela continua sendo a fonte de tudo.
2. Na pasta `ufit-tvs-nuvem`, rode `python publicar.py --enviar`.
   Ele adapta as páginas, criptografa as listas e o Balcão, confere que nenhum dado interno vai aberto e envia.
   As TVs recebem a mudança na coleta seguinte (até ~5 min).

## Senhas e chaves
- Senhas do EVO e do GoVoice e as duas chaves ficam no cofre do GitHub (Settings → Secrets → Actions).
  Gravar ou trocar: `python ferramentas/gravar_segredos.py` (lê os arquivos de `C:\Users\comercial2\.claude`).
- **Trocar a chave das TVs** (link vazou): troque **só o campo `tv`** de `ufit-tvs-chaves.json` por uma chave nova
  (`node ferramentas/cripto.js nova-chave`), rode `python ferramentas/gravar_segredos.py` e depois
  `python publicar.py --enviar` (o Balcão é recriptografado com a chave nova). Abra cada TV com o link novo.
  Não troque o campo `interna`: o estado guardado no site está fechado com ela.

## Acompanhar
- Execuções: https://github.com/ufitanalista/ufit-tvs/actions (o registro é público: só mostra contagens).
- Se a TV mostrar "SEM ATUALIZAÇÃO DESDE…", veja a última execução ali.
- O GitHub desliga as execuções agendadas de projeto público depois de **60 dias sem nenhuma mudança enviada**.
  Qualquer `publicar.py --enviar` com alteração conta; se ficar parado, ele avisa por e-mail e é só religar em Actions.

## Peças
- `coletor/` — o mesmo coletor do PC (`coletor_core.py`, `coletor_convenios.py`) + `rodar_nuvem.py` (um ciclo).
- `.github/workflows/tvs.yml` — o roteiro que o GitHub roda.
- `site/` — as páginas, já adaptadas por `publicar.py`; `site/cripto.js` abre os dados com a chave.
- `ferramentas/cripto.js` — criptografia (AES-256-GCM), usada aqui e no GitHub.
- `config.enc` — as listas, criptografadas. `estado-inicial.enc` — estado do PC no primeiro envio.
- O cache do GitHub Pages guarda cada endereço por 10 min e ignora `?t=`. Por isso as páginas pedem o arquivo
  escrito com letras em código (`%64ados.enc` = `dados.enc`), que o cache trata como outro endereço.
