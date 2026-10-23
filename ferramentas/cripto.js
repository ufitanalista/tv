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

const [cmd, env, a, b] = process.argv.slice(2);
if (cmd === 'nova-chave') console.log(crypto.randomBytes(32).toString('base64url'));
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
