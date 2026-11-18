const $ = (s) => document.querySelector(s);

const el = {
  login: $("#login"),
  loginForm: $("#login-form"),
  senha: $("#senha"),
  loginErro: $("#login-erro"),
  app: $("#app"),
  timeline: $("#timeline"),
  acao: $("#acao"),
  enviar: $("#btn-enviar"),
  parar: $("#btn-parar"),
  topoTurno: $("#btn-topo-turno"),
  controlesRodada: $("#controles-rodada"),
  refazer: $("#btn-refazer"),
  apagarRodada: $("#btn-apagar-rodada"),
  stats: $("#stats"),
  painelUso: $("#painel-uso"),
  ctxValor: $("#ctx-valor"),
  ctxBarra: $("#ctx-barra"),
  ctxDetalhe: $("#ctx-detalhe"),
  cotas: $("#cotas"),
  totais: $("#totais"),
  logEventos: $("#log-eventos"),
  logFalhas: $("#log-falhas"),
  avisoOrfao: $("#aviso-orfao"),
  descartar: $("#btn-descartar"),
  consolidar: $("#btn-consolidar"),
  docsBtn: $("#btn-docs"),
  docsPainel: $("#docs-painel"),
  docsAbas: $("#docs-abas"),
  docTitulo: $("#doc-titulo"),
  docsStatus: $("#docs-status"),
  visor: $("#doc-visor"),
  editor: $("#doc-editor"),
  galeria: $("#galeria"),
  editarDoc: $("#btn-editar-doc"),
  salvarDoc: $("#btn-salvar-doc"),
  fecharDocs: $("#btn-fechar-docs"),
};

let ocupado = false;

// ---------------------------------------------------------------- sessão

el.loginForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const r = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: el.senha.value }),
  });
  if (r.ok) {
    el.loginErro.hidden = true;
    await iniciar();
  } else {
    el.loginErro.textContent = "senha incorreta";
    el.loginErro.hidden = false;
  }
});

async function iniciar() {
  const r = await fetch("/api/state");
  if (r.status === 401) {
    el.login.hidden = false;
    el.app.hidden = true;
    return;
  }
  el.login.hidden = true;
  el.app.hidden = false;
  const estado = await r.json();
  aplicarEstado(estado);
  await carregarHistorico();
  el.acao.focus();
}

let PERSONAGEM = "Você";

function aplicarEstado(s) {
  if (s.character) {
    PERSONAGEM = s.character;
    el.acao.placeholder = `O que ${s.character} faz? (Enter envia · Shift+Enter quebra linha)`;
  }
  if (s.title) {
    document.querySelector("#titulo").textContent = s.title;
    document.title = s.title;
  }
  const pend = s.pending_consolidation > 0 ? ` · ${s.pending_consolidation} a consolidar` : "";
  el.stats.textContent =
    `${s.turns} turnos · janela ${s.window} · ${fmt(s.tokens_in)} in / ${fmt(s.tokens_out)} out${pend}`;
  el.avisoOrfao.hidden = !s.orphan;
  el.controlesRodada.hidden = s.turns < 2 || !!s.orphan;
  ultimoEstado = s;
  if (!el.painelUso.hidden) pintarUso(s);
}

const fmt = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));

// ------------------------------------------------------------- uso da sessão

let ultimoEstado = null;

const ROTULO_COTA = {
  five_hour: "Limite de 5 horas",
  seven_day: "Semanal · todos os modelos",
  seven_day_opus: "Semanal · Opus",
  seven_day_sonnet: "Semanal · Sonnet",
  overage: "Excedente",
};
// Da janela mais curta para a mais longa: é a ordem em que elas apertam.
const ORDEM_COTA = ["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "overage"];

function faltaPara(iso) {
  if (!iso) return "";
  const ms = new Date(iso) - new Date();
  if (!(ms > 0)) return "renovado";
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return h ? `renova em ${h} h ${m} min` : `renova em ${m} min`;
}

function pintarUso(s) {
  const uso = s.uso || {};
  const ctx = uso.contexto;

  // Duas medidas diferentes, que eu já confundi uma vez: o PROMPT é o que vai
  // numa chamada e ocupa a janela; o CONSUMO é a soma de todas as chamadas do
  // laço agêntico e passa tranquilamente de 100% da janela — mostrar aquilo
  // como "ocupação" produzia 172%, que não quer dizer nada.
  const prompt = uso.prompt;
  if (prompt && ctx && ctx.janela) {
    const pct = Math.round((prompt.tokens / ctx.janela) * 100);
    el.ctxValor.textContent = `${fmt(prompt.tokens)} / ${fmt(ctx.janela)} (${pct}%)`;
    el.ctxBarra.style.width = `${Math.min(100, pct)}%`;
    el.ctxBarra.className = "preenche" + (pct >= 80 ? " alerta" : "");
    const partes = [`consumo do turno ${fmt(ctx.usado)}`, `cache ${fmt(ctx.cache_lido)}`, `saída ${fmt(ctx.saida)}`];
    if (ctx.custo_usd) partes.push(`US$ ${ctx.custo_usd.toFixed(2)}`);
    if (ctx.modelo) partes.push(ctx.modelo);
    el.ctxDetalhe.textContent = partes.join(" · ");
  } else {
    el.ctxValor.textContent = "—";
    el.ctxBarra.style.width = "0%";
    el.ctxDetalhe.textContent = "jogue um turno para medir";
  }

  el.cotas.innerHTML = "";
  const cota = uso.cota || {};
  const tipos = ORDEM_COTA.filter((k) => cota[k]).concat(
    Object.keys(cota).filter((k) => !ORDEM_COTA.includes(k))
  );
  if (!tipos.length) {
    const p = document.createElement("p");
    p.className = "uso-nota";
    p.textContent = "o limite de uso aparece durante um turno, quando o SDK informa";
    el.cotas.append(p);
  }
  for (const tipo of tipos) {
    const c = cota[tipo];
    const pct = c.utilization == null ? null : Math.round(c.utilization * 100);
    const linha = document.createElement("div");
    linha.className = "cota-linha";
    const nome = document.createElement("span");
    nome.className = "cota-nome";
    nome.textContent = ROTULO_COTA[tipo] || tipo;
    const reset = document.createElement("span");
    reset.className = "cota-reset";
    reset.textContent = faltaPara(c.resets_at);
    const valor = document.createElement("span");
    valor.className = "cota-pct" + (pct >= 80 ? " alerta" : "");
    valor.textContent = pct == null ? "—" : `${pct}%`;
    const barra = document.createElement("div");
    barra.className = "barra fina";
    const dentro = document.createElement("div");
    dentro.className = "preenche" + (pct >= 80 ? " alerta" : "");
    dentro.style.width = `${Math.min(100, pct || 0)}%`;
    barra.append(dentro);
    linha.append(nome, reset, valor, barra);
    el.cotas.append(linha);
  }

  const ultimo = uso.ultimo_turno || {};
  const linhas = [
    ["turnos", s.turns],
    ["janela de histórico", `${s.window} turnos`],
    ["tokens acumulados", `${fmt(s.tokens_in)} in · ${fmt(s.tokens_out)} out`],
    ["último turno", ultimo.tokens_in ? `${fmt(ultimo.tokens_in)} in · ${fmt(ultimo.tokens_out)} out` : "—"],
    ["a consolidar", s.pending_consolidation],
    ["modelo", s.model],
  ];
  el.totais.innerHTML = "";
  for (const [rot, val] of linhas) {
    const d = document.createElement("div");
    d.className = "total";
    d.innerHTML = "";
    const a = document.createElement("span");
    a.className = "rot";
    a.textContent = rot;
    const b = document.createElement("span");
    b.textContent = val ?? "—";
    d.append(a, b);
    el.totais.append(d);
  }
}

// O registro fica em disco, no volume da campanha, e é a única memória do que o
// modelo devolveu. Três incidentes seguidos precisaram de um print de tela do
// jogador porque isto não existia.
async function pintarLog() {
  const falhas = el.logFalhas.checked ? "&falhas=true" : "";
  const r = await fetch(`/api/log?limite=40${falhas}`);
  if (!r.ok) return;
  const { eventos } = await r.json();
  el.logEventos.innerHTML = "";
  if (!eventos.length) {
    const p = document.createElement("p");
    p.className = "uso-nota";
    p.textContent = falhas ? "nenhuma falha registrada" : "nada registrado ainda";
    el.logEventos.append(p);
    return;
  }
  for (const e of eventos) {
    const linha = document.createElement("div");
    const ruim = e.erro || e.is_error;
    linha.className = "log-linha" + (ruim ? " ruim" : "");

    const hora = document.createElement("span");
    hora.className = "log-hora";
    hora.textContent = new Date(e.em).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

    const rot = document.createElement("span");
    rot.className = "log-rot";
    rot.textContent = (e.fluxo === "consolidacao" ? "cânone" : `turno ${e.idx ?? ""}`).trim();

    const desc = document.createElement("span");
    desc.className = "log-desc";
    desc.textContent = descreverEvento(e);

    linha.append(hora, rot, desc);
    el.logEventos.append(linha);
  }
}

function descreverEvento(e) {
  if (e.tipo === "inicio") return "começou";
  if (e.tipo === "ferramenta") return `${e.nome} (${e.em_s}s)`;
  if (e.tipo === "cota") return `cota ${e.limite}: ${e.status}`;
  if (e.tipo === "erro" || e.tipo === "excecao") return e.erro || "falhou";
  if (e.tipo === "fim") {
    if (e.is_error) return `FALHOU — ${e.terminal_reason || e.stop_reason || "sem motivo"} (${e.duracao_s}s)`;
    const partes = [e.terminal_reason || "ok", `${e.duracao_s}s`];
    if (e.tokens_in) partes.push(`${fmt(e.tokens_in)} in`);
    if (e.custo_usd) partes.push(`US$ ${e.custo_usd.toFixed(2)}`);
    return partes.join(" · ");
  }
  return e.tipo;
}

el.logFalhas.addEventListener("change", pintarLog);

el.stats.addEventListener("click", async () => {
  const abrindo = el.painelUso.hidden;
  el.painelUso.hidden = !abrindo;
  el.stats.classList.toggle("aberto", abrindo);
  if (!abrindo) return;
  const r = await fetch("/api/state");
  if (r.ok) ultimoEstado = await r.json();
  if (ultimoEstado) pintarUso(ultimoEstado);
  pintarLog();
});

// ---------------------------------------------------------------- histórico

async function carregarHistorico() {
  const r = await fetch("/api/history?limit=40");
  const dados = await r.json();
  el.timeline.innerHTML = "";
  for (const t of dados.turns) addTurno(t.role, t.content);
  rolar();
}

function addTurno(role, texto) {
  const div = document.createElement("div");
  div.className = `turno ${role}`;
  const quem = document.createElement("div");
  quem.className = "quem";
  quem.textContent = role === "user" ? PERSONAGEM : "Mestre";
  const corpo = document.createElement("div");
  corpo.className = "texto";
  // O mestre escreve em markdown — negrito nos resultados de rolagem, itálico
  // nas perícias. O turno do jogador fica literal: é o texto dele, e formatar
  // o que ele digitou só arriscaria mexer no que ele quis dizer.
  // Um corpo vazio é o placeholder do stream: fica literal até a resposta fechar.
  if (role === "assistant" && texto) renderizarNarracao(corpo, texto);
  else corpo.textContent = texto;
  div.append(quem, corpo);
  el.timeline.append(div);
  return corpo;
}

// Guarda o texto cru no próprio nó: durante o stream mostramos texto puro (mais
// barato e o cursor pisca no lugar certo) e só convertemos para markdown quando
// a resposta fecha.
function renderizarNarracao(corpo, texto) {
  corpo.dataset.cru = texto;
  corpo.innerHTML = renderMarkdown(texto);
  corpo.classList.add("md");
  ativarCopiaDeCodigo(corpo);
}

// ---------------------------------------------------------- parar o turno

// Enquanto o mestre escreve, o botão de enviar vira "parar". É o mesmo lugar da
// tela, então não há o que procurar no momento em que se percebe o engano.
function mostrarParar(ligado) {
  el.enviar.hidden = ligado;
  el.parar.hidden = !ligado;
  el.enviar.disabled = false;
  el.parar.disabled = false;
}

// Cancelar não é o mesmo que apagar depois: aqui a resposta é interrompida no
// meio e o texto volta para a caixa, sem esperar o mestre terminar de escrever
// uma cena que já se sabe errada.
el.parar.addEventListener("click", async () => {
  el.parar.disabled = true;
  el.parar.textContent = "parando…";
  try {
    const r = await fetch("/api/cancelar-turno", { method: "POST" });
    const dados = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert(dados.detail || "não foi possível cancelar");
      return;
    }
    if (dados.ok === false && dados.motivo === "concluido") {
      alert("A resposta terminou de chegar antes do cancelamento. Use 'apagar' se quiser removê-la.");
      return;
    }
    // Tira o turno do mestre e o seu da tela, e devolve o texto para a caixa.
    el.timeline.lastElementChild?.remove();
    if (dados.removidos > 1) el.timeline.lastElementChild?.remove();
    el.acao.value = dados.texto || "";
    ajustarAltura();
    el.acao.focus();
  } finally {
    el.parar.textContent = "parar";
    el.parar.disabled = false;
  }
});

// ------------------------------------------------- voltar ao início do turno

// Uma resposta do mestre passa de dez mil caracteres. Depois de rolar até o fim
// para acompanhar a escrita, voltar ao começo dela na mão é trabalhoso.
function irAoInicioDoUltimoTurno() {
  const turnos = el.timeline.querySelectorAll(".turno.assistant");
  const alvo = turnos[turnos.length - 1];
  if (!alvo) return;
  el.timeline.scrollTo({
    top: alvo.offsetTop - el.timeline.offsetTop - 12,
    behavior: "smooth",
  });
}

el.topoTurno.addEventListener("click", irAoInicioDoUltimoTurno);

// O botão só aparece quando há de fato o que subir: se o último turno cabe
// inteiro na tela, ele não serve para nada e vira ruído.
function avaliarBotaoTopo() {
  const turnos = el.timeline.querySelectorAll(".turno.assistant");
  const alvo = turnos[turnos.length - 1];
  if (!alvo) {
    el.topoTurno.hidden = true;
    return;
  }
  const inicioNaTela = alvo.getBoundingClientRect().top;
  const limite = el.timeline.getBoundingClientRect().top;
  el.topoTurno.hidden = inicioNaTela >= limite - 4;
}

el.timeline.addEventListener("scroll", avaliarBotaoTopo, { passive: true });

const rolar = () => {
  el.timeline.scrollTo({ top: el.timeline.scrollHeight, behavior: "smooth" });
  avaliarBotaoTopo();
};

// ---------------------------------------------------------------- jogar

// Espelha o teto do servidor. A checagem aqui é o que evita perder o texto:
// barrada no servidor, a ação voltaria como erro; barrada aqui, ela continua
// na caixa para o jogador cortar o que colou por engano.
const LIMITE_ACAO = 8000;

async function enviar() {
  const texto = el.acao.value.trim();
  if (!texto || ocupado) return;
  if (texto.length > LIMITE_ACAO) {
    alert(
      `Essa ação tem ${texto.length.toLocaleString("pt-BR")} caracteres, ` +
      `acima do limite de ${LIMITE_ACAO.toLocaleString("pt-BR")}.

` +
      "Quase sempre isso é a página inteira selecionada e colada por engano. " +
      "O texto continua na caixa — confira antes de enviar."
    );
    return;
  }

  ocupado = true;
  mostrarParar(true);
  el.acao.value = "";
  ajustarAltura();
  addTurno("user", texto);

  const corpo = addTurno("assistant", "");
  corpo.classList.add("cursor");
  rolar();

  try {
    await consumirSSE("/api/turn", { text: texto }, (ev) => {
      if (ev.type === "text") {
        corpo.textContent += ev.text;
        corpo.dataset.cru = corpo.textContent;
        rolar();
      } else if (ev.type === "tool") {
        const nota = document.createElement("div");
        nota.className = "ferramenta";
        nota.textContent = `— ${ev.label}…`;
        corpo.parentElement.insertBefore(nota, corpo);
      } else if (ev.type === "ratelimit") {
        mostrarCota(ev);
      } else if (ev.type === "error" || (ev.type === "done" && ev.is_error)) {
        corpo.classList.remove("cursor");
        if (ev.message) mostrarFalha(corpo.parentElement, ev.message, ev.retryable);
      }
    });
  } finally {
    corpo.classList.remove("cursor");
    if (corpo.dataset.cru) renderizarNarracao(corpo, corpo.dataset.cru);
    ocupado = false;
    mostrarParar(false);
    const r = await fetch("/api/state");
    if (r.ok) aplicarEstado(await r.json());
    el.acao.focus();
  }
  consolidarSePreciso();
}

// ------------------------------------------------------- desfazer a rodada

// Os dois botões chamam o mesmo endpoint: apagar a última rodada e receber de
// volta o texto do jogador. A diferença é o destino desse texto — "refazer"
// reenvia na hora, "apagar" só devolve para a caixa de ação.
async function desfazerRodada(reenviar) {
  if (ocupado) return;
  const aviso = reenviar
    ? "Descartar a resposta do mestre e jogar a mesma ação de novo?"
    : "Apagar a última rodada? O seu texto volta para a caixa de ação; a narração do mestre é perdida.";
  if (!confirm(aviso)) return;

  ocupado = true;
  el.refazer.disabled = el.apagarRodada.disabled = el.enviar.disabled = true;
  try {
    const r = await fetch("/api/desfazer-rodada", { method: "POST" });
    if (!r.ok) {
      const erro = await r.json().catch(() => ({}));
      alert(erro.detail || "não foi possível desfazer");
      return;
    }
    const { texto } = await r.json();
    // Tira os dois últimos blocos da tela sem recarregar o histórico inteiro.
    el.timeline.lastElementChild?.remove();
    el.timeline.lastElementChild?.remove();
    el.acao.value = texto;
    ajustarAltura();
    const s = await fetch("/api/state");
    if (s.ok) aplicarEstado(await s.json());
  } finally {
    ocupado = false;
    el.refazer.disabled = el.apagarRodada.disabled = el.enviar.disabled = false;
  }
  if (reenviar) enviar();
  else el.acao.focus();
}

el.refazer.addEventListener("click", () => desfazerRodada(true));
el.apagarRodada.addEventListener("click", () => desfazerRodada(false));

// Falha não vira turno da campanha: mostramos o motivo e oferecemos repetir
// a mesma ação, que continua salva no servidor.
function mostrarFalha(container, mensagem, retryable) {
  const box = document.createElement("div");
  box.className = "falha";
  const txt = document.createElement("div");
  txt.textContent = mensagem;
  box.append(txt);
  if (retryable) {
    const btn = document.createElement("button");
    btn.className = "ghost";
    btn.textContent = "tentar de novo";
    btn.addEventListener("click", () => { box.remove(); repetir(container); });
    box.append(btn);
  }
  container.append(box);
  rolar();
}

async function repetir(container) {
  if (ocupado) return;
  ocupado = true;
  el.enviar.disabled = true;
  const corpo = container.querySelector(".texto");
  corpo.textContent = "";
  corpo.classList.remove("md");
  delete corpo.dataset.cru;
  corpo.classList.add("cursor");
  try {
    await consumirSSE("/api/turn", { retry: true }, (ev) => {
      if (ev.type === "text") {
        corpo.textContent += ev.text;
        corpo.dataset.cru = corpo.textContent;
        rolar();
      }
      else if (ev.type === "ratelimit") mostrarCota(ev);
      else if (ev.type === "error" || (ev.type === "done" && ev.is_error)) {
        corpo.classList.remove("cursor");
        if (ev.message) mostrarFalha(container, ev.message, ev.retryable);
      }
    });
  } finally {
    corpo.classList.remove("cursor");
    if (corpo.dataset.cru) renderizarNarracao(corpo, corpo.dataset.cru);
    ocupado = false;
    el.enviar.disabled = false;
  }
}

// O SDK informa o limite de uso por janela. Quando o SDK avisa, mostramos antes de
// virar erro no meio de uma cena.
function mostrarCota(ev) {
  if (ev.utilization == null) return;
  if (ultimoEstado) {
    ultimoEstado.uso = ultimoEstado.uso || { cota: {} };
    ultimoEstado.uso.cota[ev.limit_type || "desconhecido"] = ev;
    if (!el.painelUso.hidden) pintarUso(ultimoEstado);
  }
  const pct = Math.round(ev.utilization * 100);
  const reset = ev.resets_at ? ` · reseta ${new Date(ev.resets_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}` : "";
  let tag = document.querySelector("#cota");
  if (!tag) {
    tag = document.createElement("span");
    tag.id = "cota";
    el.stats.after(tag);
  }
  tag.className = pct >= 80 ? "cota alerta" : "cota";
  tag.textContent = `cota ${pct}%${reset}`;
}

async function consumirSSE(url, payload, onEvent) {
  const resposta = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!resposta.ok || !resposta.body) {
    onEvent({ type: "error", message: `falha HTTP ${resposta.status}` });
    return;
  }
  const leitor = resposta.body.getReader();
  const dec = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await leitor.read();
    if (done) break;
    buffer += dec.decode(value, { stream: true });
    const partes = buffer.split("\n\n");
    buffer = partes.pop();
    for (const parte of partes) {
      const linha = parte.split("\n").find((l) => l.startsWith("data: "));
      if (!linha) continue;
      try {
        onEvent(JSON.parse(linha.slice(6)));
      } catch {
        /* evento malformado: ignora e segue */
      }
    }
  }
}

el.enviar.addEventListener("click", enviar);
// Enter envia; Shift+Enter e Ctrl+Enter quebram linha. O isComposing evita
// enviar no meio de um acento ou de um teclado com IME, em que o Enter só
// confirma o caractere que está sendo composto.
// A caixa cresce com o texto em vez de rolar: uma ação de RPG costuma passar
// de três linhas, e reler o que se escreveu dentro de uma janelinha é ruim.
// O teto em 40vh evita que a caixa engula a narração.
function ajustarAltura() {
  el.acao.style.height = "auto";
  const teto = Math.round(window.innerHeight * 0.4);
  const alvo = Math.min(el.acao.scrollHeight, teto);
  el.acao.style.height = alvo + "px";
  el.acao.style.overflowY = el.acao.scrollHeight > teto ? "auto" : "hidden";
}

el.acao.addEventListener("input", ajustarAltura);
window.addEventListener("resize", ajustarAltura);

el.acao.addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" || ev.isComposing) return;
  if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
    ev.preventDefault();
    const i = el.acao.selectionStart;
    const j = el.acao.selectionEnd;
    el.acao.value = el.acao.value.slice(0, i) + "\n" + el.acao.value.slice(j);
    el.acao.selectionStart = el.acao.selectionEnd = i + 1;
    return;
  }
  ev.preventDefault();
  enviar();
});

// ---------------------------------------------------------------- cânone

async function consolidar(automatica) {
  if (ocupado) return;
  ocupado = true;
  el.consolidar.disabled = true;
  const corpo = addTurno("assistant", "");
  corpo.classList.add("cursor");
  corpo.parentElement.classList.add("consolidacao");
  corpo.parentElement.querySelector(".quem").textContent =
    automatica ? "Consolidando o cânone (automático)" : "Consolidando cânone";
  rolar();
  try {
    await consumirSSE("/api/consolidate", {}, (ev) => {
      if (ev.type === "text") { corpo.textContent += ev.text; rolar(); }
      else if (ev.type === "tool") { corpo.textContent += `\n— ${ev.label}…\n`; }
      else if (ev.type === "error") { corpo.textContent += `\n${ev.message}`; }
    });
  } finally {
    corpo.classList.remove("cursor");
    ocupado = false;
    el.consolidar.disabled = false;
    const r = await fetch("/api/state");
    if (r.ok) aplicarEstado(await r.json());
  }
}

el.consolidar.addEventListener("click", () => consolidar(false));

// Passado o limiar existe campanha que saiu da janela sem estar no cânone — e o
// mestre nem sabe que ela existe para ir buscar no histórico. Roda depois do
// turno, nunca antes: a narração já está na tela e salva, e a espera cai num
// momento em que o jogador está lendo em vez de esperando.
async function consolidarSePreciso() {
  const s = ultimoEstado;
  if (!s || !s.auto_consolidate_at) return;
  if (s.pending_consolidation < s.auto_consolidate_at) return;
  await consolidar(true);
}

// ---------------------------------------------------------------- documentos

let docAtual = null;
let editando = false;

el.docsBtn.addEventListener("click", async () => {
  const r = await fetch("/api/docs");
  if (!r.ok) return;
  const { docs } = await r.json();
  el.docsAbas.innerHTML = "";

  // Agrupa para a lista não virar nove itens soltos sem hierarquia.
  const grupos = [
    ["campanha", docs.filter((d) => ["CANON.md", "MESA.md"].includes(d.path))],
    ["personagens", docs.filter((d) => d.path.startsWith("ficha-"))],
    ["apoio", docs.filter((d) => d.path.startsWith("referencia/"))],
  ];

  for (const [titulo, itens] of grupos) {
    if (!itens.length) continue;
    const cab = document.createElement("div");
    cab.className = "grupo";
    cab.textContent = titulo;
    el.docsAbas.append(cab);
    for (const d of itens) {
      const b = document.createElement("button");
      b.className = "aba";
      b.textContent = d.label;
      b.title = `${d.path} · ${(d.size / 1024).toFixed(1)} KB`;
      b.addEventListener("click", () => abrirDoc(d));
      el.docsAbas.append(b);
    }
  }
  const cabImg = document.createElement("div");
  cabImg.className = "grupo";
  cabImg.textContent = "imagens";
  el.docsAbas.append(cabImg);
  const bImg = document.createElement("button");
  bImg.className = "aba";
  bImg.textContent = "Retratos";
  bImg.title = "imagens-âncora das personagens";
  bImg.addEventListener("click", () => abrirGaleria(bImg));
  el.docsAbas.append(bImg);

  el.docsPainel.hidden = false;
  if (docs.length) abrirDoc(docs.find((d) => d.path === "CANON.md") || docs[0]);
});

async function abrirDoc(d) {
  const r = await fetch(`/api/doc?path=${encodeURIComponent(d.path)}`);
  if (!r.ok) return;
  const { content } = await r.json();
  docAtual = { ...d, content };
  el.galeria.hidden = true;
  el.visor.hidden = false;
  sairDaEdicao();
  mostrarDoc(content);
  el.docTitulo.textContent = d.label;
  el.docsStatus.textContent = d.readonly ? "somente leitura" : "";
  el.editarDoc.hidden = !!d.readonly;
  [...el.docsAbas.children].forEach((b) => b.classList.toggle("ativa", b.textContent === d.label));
  el.visor.scrollTop = 0;
}

function sairDaEdicao() {
  editando = false;
  el.editor.hidden = true;
  el.visor.hidden = false;
  el.salvarDoc.hidden = true;
  el.editarDoc.textContent = "editar";
}

function mostrarDoc(texto) {
  el.visor.innerHTML = renderMarkdown(texto);
  ativarCopiaDeCodigo(el.visor);
}

el.editarDoc.addEventListener("click", () => {
  if (!docAtual || docAtual.readonly) return;
  if (editando) { sairDaEdicao(); mostrarDoc(docAtual.content); return; }
  editando = true;
  el.editor.value = docAtual.content;
  el.editor.hidden = false;
  el.visor.hidden = true;
  el.salvarDoc.hidden = false;
  el.editarDoc.textContent = "cancelar";
  el.editor.focus();
});

el.salvarDoc.addEventListener("click", async () => {
  if (!docAtual) return;
  el.salvarDoc.disabled = true;
  el.docsStatus.textContent = "salvando...";
  const r = await fetch("/api/doc", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: docAtual.path, content: el.editor.value }),
  });
  el.salvarDoc.disabled = false;
  if (r.ok) {
    docAtual.content = el.editor.value;
    sairDaEdicao();
    mostrarDoc(docAtual.content);
    el.docsStatus.textContent = "salvo";
    setTimeout(() => { el.docsStatus.textContent = ""; }, 2500);
  } else {
    el.docsStatus.textContent = "falhou ao salvar";
  }
});

// Ctrl+Enter salva sem tirar a mão do teclado, igual ao composer.
el.editor.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) el.salvarDoc.click();
});

// ---------------------------------------------------------------- retratos

// As imagens-âncora de cada personagem. Ficam no volume da campanha, entram no
// backup, e são o que mantém a aparência estável entre gerações.
async function abrirGaleria(botao) {
  const r = await fetch("/api/retratos");
  if (!r.ok) return;
  const { galerias } = await r.json();

  docAtual = null;
  editando = false;
  el.visor.hidden = true;
  el.editor.hidden = true;
  el.salvarDoc.hidden = true;
  el.editarDoc.hidden = true;
  el.galeria.hidden = false;
  el.docTitulo.textContent = "Retratos";
  const total = galerias.reduce((n, g) => n + g.imagens.length, 0);
  el.docsStatus.textContent = total ? `${total} imagens` : "nenhuma imagem ainda";
  [...el.docsAbas.querySelectorAll(".aba")].forEach((b) => b.classList.toggle("ativa", b === botao));

  el.galeria.innerHTML = "";
  for (const g of galerias) {
    const sec = document.createElement("section");
    sec.className = "galeria-grupo";

    const cab = document.createElement("div");
    cab.className = "galeria-cab";
    const h = document.createElement("h3");
    h.textContent = g.label;
    const cont = document.createElement("span");
    cont.className = "galeria-contagem";
    cont.textContent = g.imagens.length ? `${g.imagens.length}` : "";
    const add = document.createElement("label");
    add.className = "ghost botao-upload";
    add.textContent = "+ imagem";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".png,.jpg,.jpeg,.webp";
    input.multiple = true;
    input.hidden = true;
    input.addEventListener("change", () => enviarImagens(g.slug, input.files, botao));
    add.append(input);
    cab.append(h, cont, add);
    sec.append(cab);

    const grid = document.createElement("div");
    grid.className = "grid-imagens";
    if (!g.imagens.length) {
      const vazio = document.createElement("p");
      vazio.className = "galeria-vazio";
      vazio.textContent = "sem imagens";
      grid.append(vazio);
    }
    for (const img of g.imagens) {
      const fig = document.createElement("figure");
      const el_img = document.createElement("img");
      el_img.src = img.url;
      el_img.loading = "lazy";
      el_img.alt = img.nome;
      el_img.addEventListener("click", () => window.open(img.url, "_blank"));
      const rem = document.createElement("button");
      rem.className = "remover";
      rem.textContent = "×";
      rem.title = "remover";
      rem.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm(`Remover ${img.nome}?`)) return;
        await fetch(`/api/retrato?slug=${g.slug}&nome=${encodeURIComponent(img.nome)}`, { method: "DELETE" });
        abrirGaleria(botao);
      });
      fig.append(el_img, rem);
      grid.append(fig);
    }
    sec.append(grid);
    el.galeria.append(sec);
  }
}

async function enviarImagens(slug, arquivos, botao) {
  if (!arquivos || !arquivos.length) return;
  el.docsStatus.textContent = `enviando ${arquivos.length}...`;
  let falhas = 0;
  for (const arq of arquivos) {
    const fd = new FormData();
    fd.append("slug", slug);
    fd.append("arquivo", arq);
    const r = await fetch("/api/retrato", { method: "POST", body: fd });
    if (!r.ok) {
      falhas++;
      const erro = await r.json().catch(() => ({}));
      el.docsStatus.textContent = erro.detail || "falha no envio";
    }
  }
  await abrirGaleria(botao);
  if (falhas) el.docsStatus.textContent = `${falhas} falharam`;
}

el.fecharDocs.addEventListener("click", () => { el.docsPainel.hidden = true; });

el.descartar.addEventListener("click", async () => {
  await fetch("/api/discard-partial", { method: "POST" });
  await iniciar();
});

iniciar();
