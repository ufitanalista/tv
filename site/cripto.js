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

  var m = location.hash.match(/k=([A-Za-z0-9_-]{43})/), k = m && m[1];
  try {
    if (k) localStorage.setItem('ufit-chave', k);
    else k = localStorage.getItem('ufit-chave');
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

  function ler(arquivo) {
    return fetch(variar(arquivo), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        return obterChave().then(function (ck) {
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

  function iniciar() {
    if (!k) { irParaEntrada(); return; }
    if (!window.crypto || !crypto.subtle) { aviso('Este navegador não abre os dados protegidos. Use outro navegador ou atualize a TV.'); return; }
    ler(ARQUIVO).then(function (d) { window[VAR] = d; rodarApp(); })
      .catch(function (e) {
        if (/OperationError/.test(e && e.name)) { irParaEntrada(); return; }
        aviso('Carregando os dados…');
        setTimeout(iniciar, 30000);
      });
  }

  // TVs: tv-comum.js chama esta função para buscar os dados de novo (a cada 20 s)
  window.TV_RECARREGAR = function (aoChegar) {
    ler(ARQUIVO).then(function (d) { window[VAR] = d; aoChegar(); }).catch(function () {});
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
