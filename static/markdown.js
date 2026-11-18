// Render de Markdown do necessário para os documentos da campanha:
// cabeçalhos, ênfase, código, citações, listas, tabelas e regras.
//
// Trabalha por BLOCOS, não por linha. Os arquivos têm parágrafos quebrados em
// várias linhas, e tanto os prompts de imagem entre crases quanto alguns
// negritos atravessam a quebra — processar linha a linha deixa `**` e crases
// órfãs no texto.
//
// Escapa HTML antes de qualquer coisa: o conteúdo é editável pelo usuário e
// não deve virar markup ativo.

function mdEscape(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function mdInline(s) {
  return mdEscape(s)
    .replace(/`([^`]+)`/gs, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/gs, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

function renderMarkdown(md) {
  const linhas = md.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;

  const ehTabela = (l) => /^\|(.+)\|\s*$/.test(l.trim());
  const ehLista = (l) => /^\s*[-*]\s+/.test(l);
  const ehCitacao = (l) => /^>\s?/.test(l.trim());
  const ehTitulo = (l) => /^#{1,6}\s+/.test(l);
  const ehRegra = (l) => /^-{3,}$/.test(l.trim());
  const vazia = (l) => !l.trim();

  while (i < linhas.length) {
    const l = linhas[i];

    // Bloco de código cercado: os prompts de imagem em JSON vivem aqui, e
    // precisam sair literais, sem o inline mexer em aspas e chaves.
    if (/^```/.test(l.trim())) {
      const lingua = l.trim().slice(3).trim();
      const corpo = [];
      i++;
      while (i < linhas.length && !/^```/.test(linhas[i].trim())) {
        corpo.push(linhas[i]);
        i++;
      }
      i++; // fecha
      out.push(
        `<pre class="bloco-codigo"${lingua ? ` data-lang="${mdEscape(lingua)}"` : ""}>` +
        `<code>${mdEscape(corpo.join("\n"))}</code></pre>`
      );
      continue;
    }

    if (vazia(l)) { i++; continue; }

    if (ehRegra(l)) { out.push("<hr>"); i++; continue; }

    if (ehTitulo(l)) {
      const m = l.match(/^(#{1,6})\s+(.*)$/);
      out.push(`<h${m[1].length}>${mdInline(m[2])}</h${m[1].length}>`);
      i++;
      continue;
    }

    // Tabela: consome as linhas seguidas, ignorando a de separação.
    if (ehTabela(l)) {
      const linhasTab = [];
      while (i < linhas.length && ehTabela(linhas[i])) { linhasTab.push(linhas[i].trim()); i++; }
      const corpo = [];
      let primeira = true;
      for (const lt of linhasTab) {
        const celulas = lt.slice(1, -1).split("|").map((c) => c.trim());
        if (celulas.every((c) => /^:?-{2,}:?$/.test(c))) continue;
        const tag = primeira ? "th" : "td";
        primeira = false;
        corpo.push("<tr>" + celulas.map((c) => `<${tag}>${mdInline(c)}</${tag}>`).join("") + "</tr>");
      }
      out.push("<table><tbody>" + corpo.join("") + "</tbody></table>");
      continue;
    }

    // Citação: junta as linhas do bloco antes de formatar, para uma crase ou
    // um negrito aberto numa linha fechar na seguinte.
    if (ehCitacao(l)) {
      const partes = [];
      while (i < linhas.length && ehCitacao(linhas[i])) {
        partes.push(linhas[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      out.push("<blockquote>" + mdInline(partes.join(" ")) + "</blockquote>");
      continue;
    }

    // Lista: cada item pode continuar nas linhas seguintes indentadas.
    if (ehLista(l)) {
      const itens = [];
      while (i < linhas.length && (ehLista(linhas[i]) || (itens.length && /^\s{2,}\S/.test(linhas[i])))) {
        if (ehLista(linhas[i])) itens.push(linhas[i].replace(/^\s*[-*]\s+/, ""));
        else itens[itens.length - 1] += " " + linhas[i].trim();
        i++;
      }
      out.push("<ul>" + itens.map((t) => `<li>${mdInline(t)}</li>`).join("") + "</ul>");
      continue;
    }

    // Parágrafo: consome até a próxima linha em branco ou estrutural.
    const paragrafo = [];
    while (
      i < linhas.length && !vazia(linhas[i]) && !ehTitulo(linhas[i]) &&
      !ehCitacao(linhas[i]) && !ehLista(linhas[i]) && !ehTabela(linhas[i]) && !ehRegra(linhas[i])
    ) {
      paragrafo.push(linhas[i].trim());
      i++;
    }
    out.push("<p>" + mdInline(paragrafo.join(" ")) + "</p>");
  }

  return out.join("\n");
}

// Os prompts de imagem viram blocos longos de <code>; um clique para copiar
// poupa selecionar oito linhas com o mouse.
function ativarCopiaDeCodigo(container) {
  for (const code of container.querySelectorAll("code")) {
    // Blocos cercados sempre copiáveis; inline só quando for longo o bastante
    // para valer o clique (um `termo` curto não precisa disso).
    const cercado = code.parentElement?.classList.contains("bloco-codigo");
    if (!cercado && code.textContent.length < 60) continue;
    (cercado ? code.parentElement : code).classList.add("copiavel");
    (cercado ? code.parentElement : code).title = "clique para copiar";
    (cercado ? code.parentElement : code).addEventListener("click", () => {
      const alvo = cercado ? code.parentElement : code;
      navigator.clipboard.writeText(code.textContent).then(() => {
        const original = alvo.style.background;
        alvo.style.background = "rgba(157,254,242,.26)";
        setTimeout(() => { alvo.style.background = original; }, 700);
      });
    });
  }
}
