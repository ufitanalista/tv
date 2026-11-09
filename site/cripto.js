// Versão online (GitHub Pages): os dados ficam publicados criptografados (AES-256-GCM).
// A chave vem no endereço, depois do # (ex.: tv2.html#k=XXXX), e nunca sai do navegador.
// Na 1ª vez fica guardada neste navegador, para os links entre as páginas funcionarem sem o #k=.
//
// Uso na página: <script src="cripto.js" data-arquivo="dados.enc" data-var="DADOS"></script>
// e o código principal da página em <script type="text/plain" class="app">…</script>,
// que só roda depois que os dados chegam.
(function () {
  var eu = document.currentScript;
  var ARQUIVO = eu.getAttribute('data-arquivo'), VAR = eu.getAttribute('data-var');

  // data-sal="…": a página tem senha própria (Convênios). Pede a senha aqui mesmo e guarda a chave separada da das TVs.
  var SAL = eu.getAttribute('data-sal'), GUARDA = SAL ? 'ufit-chave-' + SAL : 'ufit-chave';

  var m = SAL ? null : location.hash.match(/k=([A-Za-z0-9_-]{43})/), k = m && m[1];
  try {
    if (k) localStorage.setItem(GUARDA, k);
    else k = localStorage.getItem(GUARDA);
  } catch (e) {}

  var chave = null;
  function obterChave() {
    if (chave) return chave;
    var b = atob(k.replace(/-/g, '+').replace(/_/g, '/') + '=');
    var raw = new Uint8Array(b.length);
    for (var i = 0; i < b.length; i++) raw[i] = b.charCodeAt(i);
    chave = crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
    return chave;
  }

  // O cache do GitHub Pages guarda cada endereço por 10 min e ignora "?t=". Mas "%64ados.enc" é o mesmo arquivo
  // que "dados.enc" com outro endereço no cache: a cada 20 s a página usa outra combinação de letras em código,
  // e assim sempre recebe a versão recente (dentro da mesma janela de 20 s as TVs dividem a mesma cópia).
  function variar(nome) {
    var n = Math.min(nome.length, 12), i = Math.floor(Date.now() / 20000) % ((1 << n) - 1) + 1, out = '';
    for (var j = 0; j < nome.length; j++) {
      out += (j < n && (i >> j) & 1) ? '%' + nome.charCodeAt(j).toString(16).toUpperCase() : nome[j];
    }
    return out;
  }

  function ler(arquivo, outraChave) {
    return fetch(variar(arquivo), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        return (outraChave || obterChave()).then(function (ck) {
          return crypto.subtle.decrypt({ name: 'AES-GCM', iv: new Uint8Array(buf, 0, 12) }, ck, new Uint8Array(buf, 12));
        });
      })
      .then(function (txt) { return JSON.parse(new TextDecoder().decode(txt)); });
  }

  function aviso(txt) {
    var d = document.getElementById('ufit-aviso');
    if (!d) {
      d = document.createElement('div'); d.id = 'ufit-aviso';
      d.style.cssText = 'position:fixed;inset:0;z-index:99;background:#0b0a09;color:#f0efec;font:700 34px Segoe UI,Arial,sans-serif;' +
        'display:flex;align-items:center;justify-content:center;text-align:center;padding:40px';
      document.body.appendChild(d);
    }
    d.textContent = txt;
  }

  function rodarApp() {
    var ja = document.getElementById('ufit-aviso'); if (ja) ja.remove();
    document.querySelectorAll('script[type="text/plain"].app').forEach(function (s) {
      var n = document.createElement('script'); n.text = s.text; document.body.appendChild(n);
    });
  }

  // sem senha guardada (ou senha trocada): volta para a entrada (index.html), que pede a senha
  function irParaEntrada() {
    try { localStorage.removeItem('ufit-chave'); } catch (e) {}
    location.href = './';
  }

  // ---------- Senha própria da página (só com data-sal) ----------
  // PBKDF2 igual ferramentas/cripto.js → chave-da-senha com SAL=<data-sal>.
  function chaveDaSenha(senha) {
    var enc = new TextEncoder(), limpa = senha.toLowerCase().replace(/[^a-z0-9]/g, '');
    return crypto.subtle.importKey('raw', enc.encode(limpa), 'PBKDF2', false, ['deriveBits'])
      .then(function (b) { return crypto.subtle.deriveBits({ name: 'PBKDF2', salt: enc.encode(SAL), iterations: 300000, hash: 'SHA-256' }, b, 256); });
  }
  function esquecer() {
    k = null; chave = null;
    try { localStorage.removeItem(GUARDA); } catch (e) {}
  }
  function botaoEsquecer() {
    var b = document.createElement('button');
    b.type = 'button'; b.textContent = 'Esquecer a senha';
    b.style.cssText = 'position:fixed;right:10px;bottom:10px;z-index:50;padding:6px 10px;border:1px solid #555;border-radius:6px;' +
      'background:#1b1917;color:#c9c6c0;font:500 12px Segoe UI,Arial,sans-serif;cursor:pointer';
    b.addEventListener('click', function () { esquecer(); location.reload(); });
    document.body.appendChild(b);
  }
  function pedirSenha(msg) {
    var d = document.createElement('form'); d.id = 'ufit-senha';
    d.style.cssText = 'position:fixed;inset:0;z-index:9999;background:#0b0a09;color:#f0efec;font:500 18px Segoe UI,Arial,sans-serif;' +
      'display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:16px;text-align:center';
    var t = document.createElement('h1'); t.textContent = 'Painel de convênios'; t.style.cssText = 'margin:0;font-size:32px';
    var p = document.createElement('p'); p.textContent = msg || 'Digite a senha de acesso'; p.style.margin = '0';
    var i = document.createElement('input'); i.type = 'password'; i.autocomplete = 'off'; i.setAttribute('aria-label', 'Senha de acesso');
    i.style.cssText = 'width:min(340px,100%);padding:12px;font-size:20px;border-radius:8px;border:1px solid #666;background:#1b1917;color:#f0efec;text-align:center';
    var b = document.createElement('button'); b.textContent = 'Entrar';
    b.style.cssText = 'padding:12px 36px;font-size:18px;font-weight:700;border:0;border-radius:8px;background:#f0efec;color:#0b0a09;cursor:pointer';
    d.appendChild(t); d.appendChild(p); d.appendChild(i); d.appendChild(b);
    document.body.appendChild(d);
    i.focus();
    d.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (i.value.replace(/[^a-z0-9]/gi, '').length < 8) { p.textContent = 'Digite a senha completa.'; return; }
      b.disabled = true; p.textContent = 'Abrindo…';
      var bits;
      chaveDaSenha(i.value).then(function (r) {
        bits = r;
        return ler(ARQUIVO, crypto.subtle.importKey('raw', r, 'AES-GCM', false, ['decrypt']));
      }).then(function (dados) {
        k = btoa(String.fromCharCode.apply(null, new Uint8Array(bits))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
        chave = null;
        try { localStorage.setItem(GUARDA, k); } catch (e) {}
        window[VAR] = dados;
        d.remove(); rodarApp(); botaoEsquecer();
      }).catch(function (e) {
        b.disabled = false; i.value = ''; i.focus();
        p.textContent = /OperationError/.test(e && e.name) ? 'Senha errada. Confira e tente de novo.' : 'Não consegui abrir os dados. Confira a internet e tente de novo.';
      });
    });
  }

  function iniciar() {
    if (!k && !SAL) { irParaEntrada(); return; }
    if (!window.crypto || !crypto.subtle) { aviso('Este navegador não abre os dados protegidos. Use outro navegador ou atualize a TV.'); return; }
    if (!k) { pedirSenha(''); return; }
    ler(ARQUIVO).then(function (d) { window[VAR] = d; rodarApp(); if (SAL) botaoEsquecer(); })
      .catch(function (e) {
        if (/OperationError/.test(e && e.name)) {
          if (SAL) { esquecer(); pedirSenha('A senha mudou. Digite a senha nova.'); return; }
          irParaEntrada(); return;
        }
        aviso('Carregando os dados…');
        setTimeout(iniciar, 30000);
      });
  }

  // TVs: tv-comum.js chama esta função para buscar os dados de novo (a cada 20 s)
  window.TV_RECARREGAR = function (aoChegar) {
    ler(ARQUIVO).then(function (d) { window[VAR] = d; aoChegar(); }).catch(function () {});
  };

  // Aba Período da TV 2: abre outro arquivo (dp.enc) com outra chave (a do DP, que vem da senha do DP).
  // chaveBits = ArrayBuffer de 32 bytes. Devolve uma promessa com os dados; senha errada → erro OperationError.
  window.UFIT_LER_COM = function (arquivo, chaveBits) {
    return ler(arquivo, crypto.subtle.importKey('raw', chaveBits, 'AES-GCM', false, ['decrypt']));
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
