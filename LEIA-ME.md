# TVs do call center UFit (versão online)

As TVs e o Balcão funcionam sem depender de nenhum PC ligado. O próprio GitHub busca os dados no EVO e no GoVoice
a cada 5 minutos (das 6h à meia-noite; de madrugada, a cada 30 min) e publica no GitHub Pages.

**Projeto público, dados fechados.** Tudo que é dado (vendas, clientes, operadores, vouchers, links de venda,
convênios) é publicado **criptografado** (`*.enc`). Quem abrir sem a chave vê só letras sem sentido.
A chave sai de uma senha digitada uma vez em cada TV, e nunca sai do navegador.
O que fica aberto aqui é só o código.

## Como abrir numa TV
1. Endereço: **`ufitanalista.github.io/tv`**
2. 1ª vez: digitar a **senha de acesso** (16 letras/números; maiúscula, minúscula e tracinho tanto faz) → Entrar.
   A senha não fica escrita neste projeto: o usuário a guarda (também está em
   `C:\Users\comercial2\.claude\ufit-tvs-chaves.json`, campo `senha_tv`).
3. Menu: TV 1, TV 2, TV das unidades, Balcão, Convênios. Das próximas vezes abre sozinho a última tela em 10 s
   (qualquer botão do controle cancela e deixa escolher). `ufitanalista.github.io/tv/#menu` abre o menu sem contagem.
- A senha vira a chave por PBKDF2 (300 mil voltas, sal `ufit-tvs`), igual em `site/index.html` e `ferramentas/cripto.js`.

## Aba Período da TV 2 (DP: vendas e comissão) — senha própria
- Botão **📅 Período** na TV 2 (ou `tv2.html#periodo`). Pede a **senha do DP**, diferente da senha das TVs
  (campo `senha_dp` de `C:\Users\comercial2\.claude\ufit-tvs-chaves.json`; a chave dela é o campo `dp`, no cofre do GitHub como `CHAVE_DP`).
- As vendas (com nome de cliente) e os dias no discador vão em `dp.enc`, fechado com a chave do DP: quem só tem a senha das TVs não abre.
  A senha vira a chave por PBKDF2 com o sal `ufit-dp` (`SENHA=<senha> SAL=ufit-dp node ferramentas/cripto.js chave-da-senha SENHA`).
- Comissão (regra de 28/10/2026): média = vendas pagas ÷ dias trabalhados; 2 a 4 = R$ 2 por venda, 5 a 6 = R$ 5, 7+ = R$ 7; abaixo de 2, nada.
  Dias trabalhados = dias com ligações no discador, por pessoa. Listas na pasta `dashboard-tv`:
  `ramais_historico.csv` (ramal;nome;ate — quem usava o ramal antes do dono atual) e
  `dias_trabalhados.csv` (mes;nome;dias;vendas_desde;motivo — dias definidos pela gestão; vale quando o período é o mês inteiro).
- Trocar a senha do DP: gere uma (`node ferramentas/cripto.js nova-senha`), calcule a chave com `SAL=ufit-dp`, troque `senha_dp` e `dp`
  no json, rode `python ferramentas/gravar_segredos.py` e `python publicar.py --enviar`.

## Mudar uma lista (operadores, venda dada como paga, ajuste de vendedor, TV das unidades) ou o Balcão
1. Mude o arquivo de sempre na pasta `dashboard-tv` (versão do PC). Ela continua sendo a fonte de tudo.
2. Na pasta `ufit-tvs-nuvem`, rode `python publicar.py --enviar`.
   Ele adapta as páginas, criptografa as listas e o Balcão, confere que nenhum dado interno vai aberto e envia.
   As TVs recebem a mudança na coleta seguinte (até ~5 min).

## Senhas e chaves
- Senhas do EVO e do GoVoice e as duas chaves ficam no cofre do GitHub (Settings → Secrets → Actions).
  Gravar ou trocar: `python ferramentas/gravar_segredos.py` (lê os arquivos de `C:\Users\comercial2\.claude`).
- **Trocar a senha das TVs** (vazou): gere uma nova (`node ferramentas/cripto.js nova-senha`), calcule a chave
  (`SENHA=<nova> node ferramentas/cripto.js chave-da-senha SENHA`) e troque **só** os campos `senha_tv` e `tv` de
  `ufit-tvs-chaves.json`. Depois `python ferramentas/gravar_segredos.py` e `python publicar.py --enviar`.
  Cada TV volta para a tela de senha sozinha e pede a nova.
  Não troque o campo `interna`: o estado guardado no site está fechado com ela.

## Acompanhar
- Execuções: https://github.com/ufitanalista/tv/actions (o registro é público: só mostra contagens).
- Se a TV mostrar "SEM ATUALIZAÇÃO DESDE…", veja a última execução ali.
- Conferência automática: depois de cada deploy, o passo "Conferir se o site atualizou" compara o `dados.enc` no ar com o recém-publicado.
  Se em 3 min continuar velho, cria o commit vazio "Destrava o Pages (conferência automática)" (no máximo 1 a cada 20 min) e a coleta seguinte publica de novo.
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
