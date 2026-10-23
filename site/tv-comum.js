// Funções comuns às duas TVs: ajuste de tela, relógio, rodízio de telas e recarga dos dados.

// Se a página abriu com o servidor fora do ar (sem dados.js), recarrega sozinha em 30 s
// em vez de ficar parada só com o título.
if (!window.DADOS && !window.TV_RECARREGAR) setTimeout(function () { location.reload(); }, 30000);
var TV = (function () {
  function ajustar() {
    var p = document.getElementById('palco');
    var e = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    p.style.transform = 'translate(-50%, -50%) scale(' + e + ')';
  }
  window.addEventListener('resize', ajustar);

  var DIAS = ['domingo', 'segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado'];
  var MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  function relogio() {
    var d = new Date();
    document.getElementById('relogio').textContent =
      String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    document.getElementById('data').innerHTML =
      DIAS[d.getDay()] + '<br>' + d.getDate() + ' ' + MESES[d.getMonth()] + ' ' + d.getFullYear();
  }

  function reais(v, centavos) {
    return 'R$ ' + v.toLocaleString('pt-BR', { minimumFractionDigits: centavos ? 2 : 0, maximumFractionDigits: centavos ? 2 : 0 });
  }
  function tempo(s) {
    var m = Math.floor(s / 60), r = s % 60;
    if (m >= 60) return Math.floor(m / 60) + 'h' + String(m % 60).padStart(2, '0');
    return String(m).padStart(2, '0') + ':' + String(r).padStart(2, '0');
  }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // Rodízio: mostra cada tela por "segundos" e chama aoTrocar(indice) antes de exibir
  function rodizio(segundos, aoTrocar) {
    // "#tela=3" no endereço começa pela 3ª tela (útil para conferir uma tela específica)
    var m = location.hash.match(/tela=(\d+)/);
    var atual = m ? parseInt(m[1], 10) - 2 : -1, inicio = 0;
    function proxima() {
      var telas = document.querySelectorAll('.tela');
      if (!telas.length) return;
      atual = (atual + 1) % telas.length;
      if (aoTrocar) aoTrocar(atual);
      telas = document.querySelectorAll('.tela');
      telas.forEach(function (t, i) { t.classList.toggle('ativa', i === atual); });
      var pts = document.getElementById('pontos');
      pts.innerHTML = Array.prototype.map.call(telas, function (t, i) { return '<span class="' + (i === atual ? 'ativo' : '') + '"></span>'; }).join('');
      document.getElementById('nome-tela').textContent = telas[atual].dataset.nome || '';
      inicio = Date.now();
    }
    setInterval(function () {
      var f = (Date.now() - inicio) / (segundos * 1000);
      document.getElementById('barra').style.width = Math.min(100, f * 100) + '%';
      if (f >= 1) proxima();
    }, 200);
    proxima();
    return { atual: function () { return atual; } };
  }

  // Recarrega dados.js (que o programa do PC reescreve) a cada "segundos"
  function recarregar(segundos, aoChegar) {
    setInterval(function () {
      if (window.DADOS && window.DADOS.exemplo) return; // exemplo: não recarrega
      if (window.TV_RECARREGAR) { window.TV_RECARREGAR(aoChegar); return; } // versão online
      var s = document.createElement('script');
      s.src = 'dados.js?t=' + Date.now();
      s.onload = function () { s.remove(); aoChegar(); };
      s.onerror = function () { s.remove(); };
      document.body.appendChild(s);
    }, segundos * 1000);
  }

  // selo vermelho: dados de exemplo, GoVoice fora do ar ou dados parados há mais de 5 min
  function conferirSelo() {
    var d = window.DADOS, selo = document.getElementById('selo'), txt = '';
    if (!d) return;
    if (d.exemplo) txt = 'DADOS DE EXEMPLO';
    else if (d.atualizadoEm && Date.now() - new Date(d.atualizadoEm) > (window.TV_RECARREGAR ? 20 : 5) * 60000) {
      var h = new Date(d.atualizadoEm);
      txt = 'SEM ATUALIZAÇÃO DESDE ' + String(h.getHours()).padStart(2, '0') + ':' + String(h.getMinutes()).padStart(2, '0');
    } else if (d.govoice === false) txt = 'GOVOICE SEM CONEXÃO';
    selo.textContent = txt; selo.style.display = txt ? '' : 'none';
  }
  // anti-sono: vídeo minúsculo, preto e mudo em repetição (NoSleep.js), para a TV não dormir.
  // Vídeo mudo pode tocar sozinho; se a TV exigir, liga no primeiro OK do controle.
  function acordar() {
    if (!window.NoSleep) return;
    var ns = new NoSleep(), v = ns.noSleepVideo;
    if (v) { // deixa o vídeo dentro da página (algumas TVs ignoram vídeo fora da tela), 2 px e quase transparente
      v.muted = true; v.setAttribute('muted', '');
      v.style.cssText = 'position:fixed;right:0;bottom:0;width:2px;height:2px;opacity:0.01;pointer-events:none';
      document.body.appendChild(v);
    }
    function liga() { if (!ns.isEnabled) { try { var p = ns.enable(); if (p && p.catch) p.catch(function () {}); } catch (e) {} } }
    liga();
    ['keydown', 'click', 'touchstart'].forEach(function (ev) { document.addEventListener(ev, liga); });
    setInterval(liga, 60000);
  }
  function iniciar() {
    if (!window.DADOS) throw new Error('sem dados');
    acordar();
    ajustar(); relogio(); setInterval(relogio, 1000);
    conferirSelo(); setInterval(conferirSelo, 10000);
  }

  return { iniciar: iniciar, reais: reais, tempo: tempo, esc: esc, rodizio: rodizio, recarregar: recarregar };
})();
