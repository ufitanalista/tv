// Criptografia dos arquivos publicados (AES-256-GCM). Formato: 12 bytes de IV + texto cifrado + 16 bytes de tag,
// o mesmo que o navegador abre com crypto.subtle (site/cripto.js).
// A chave (32 bytes em base64url) vem de uma variável de ambiente, nunca da linha de comando.
//
//   node cripto.js cifrar      <ENV_DA_CHAVE> <entrada> <saida.enc>
//   node cripto.js decifrar    <ENV_DA_CHAVE> <entrada.enc> <saida>
//   node cripto.js empacotar   <ENV_DA_CHAVE> <pasta> <saida.enc>     (todos os arquivos da pasta num .enc)
//   node cripto.js desempacotar <ENV_DA_CHAVE> <entrada.enc> <pasta>
//   node cripto.js nova-chave                                          (mostra uma chave nova)
const crypto = require('crypto'), fs = require('fs'), path = require('path');

function chave(nomeEnv) {
  const k = Buffer.from(process.env[nomeEnv] || '', 'base64url');
  if (k.length !== 32) throw new Error('variável ' + nomeEnv + ' sem chave válida');
  return k;
}
function cifrar(k, dados) {
  const iv = crypto.randomBytes(12), c = crypto.createCipheriv('aes-256-gcm', k, iv);
  return Buffer.concat([iv, c.update(dados), c.final(), c.getAuthTag()]);
}
function decifrar(k, buf) {
  const d = crypto.createDecipheriv('aes-256-gcm', k, buf.subarray(0, 12));
  d.setAuthTag(buf.subarray(buf.length - 16));
  return Buffer.concat([d.update(buf.subarray(12, buf.length - 16)), d.final()]);
}

// Senha das TVs → chave. Tem que dar exatamente o mesmo que site/index.html (mesmo sal, voltas e limpeza da senha).
const SAL = 'ufit-tvs', VOLTAS = 300000;
function chaveDaSenha(senha) {
  const limpa = senha.toLowerCase().replace(/[^a-z0-9]/g, '');
  return crypto.pbkdf2Sync(limpa, SAL, VOLTAS, 32, 'sha256').toString('base64url');
}

const [cmd, env, a, b] = process.argv.slice(2);
if (cmd === 'nova-chave') console.log(crypto.randomBytes(32).toString('base64url'));
// "nova-senha": 4 blocos de 4 (letras e números sem os que confundem: 0/o, 1/l/i) ≈ 80 bits
else if (cmd === 'nova-senha') {
  const abc = 'abcdefghjkmnpqrstuvwxyz23456789';
  console.log(Array.from({ length: 4 }, () => Array.from({ length: 4 }, () => abc[crypto.randomInt(abc.length)]).join('')).join('-'));
}
// "chave-da-senha SENHA_ENV": a chave das TVs a partir da senha guardada na variável de ambiente
else if (cmd === 'chave-da-senha') console.log(chaveDaSenha(process.env[env] || ''));
// "node cripto.js variar estado.enc" → o mesmo nome com letras sorteadas em código (ex.: e%73tado.enc).
// O cache do GitHub Pages ignora "?t=", mas trata cada escrita como outro endereço: assim vem sempre a versão nova.
else if (cmd === 'variar') {
  const nome = env, i = crypto.randomInt(1, 2 ** Math.min(nome.length, 12));
  console.log([...nome].map((c, j) => (j < 12 && (i >> j) & 1) ? '%' + c.charCodeAt(0).toString(16).toUpperCase() : c).join(''));
}
else if (cmd === 'cifrar') fs.writeFileSync(b, cifrar(chave(env), fs.readFileSync(a)));
else if (cmd === 'decifrar') fs.writeFileSync(b, decifrar(chave(env), fs.readFileSync(a)));
else if (cmd === 'empacotar') {
  const pacote = {};
  for (const n of fs.readdirSync(a)) if (fs.statSync(path.join(a, n)).isFile()) pacote[n] = fs.readFileSync(path.join(a, n)).toString('base64');
  fs.writeFileSync(b, cifrar(chave(env), Buffer.from(JSON.stringify(pacote))));
} else if (cmd === 'desempacotar') {
  const pacote = JSON.parse(decifrar(chave(env), fs.readFileSync(a)));
  fs.mkdirSync(b, { recursive: true });
  for (const n in pacote) fs.writeFileSync(path.join(b, path.basename(n)), Buffer.from(pacote[n], 'base64'));
} else { console.error('uso: veja o começo do arquivo'); process.exit(2); }
