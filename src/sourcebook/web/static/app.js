// Sourcebook UI: ask a question, then render the SSE stream (thinking, tools, tokens, citations).

const $ = (sel, root = document) => root.querySelector(sel);

const els = {
  form: $("#ask-form"),
  input: $("#question"),
  send: $("#send-btn"),
  hero: $("#hero"),
  area: $("#answer-area"),
  echo: $("#question-echo"),
  status: $("#answer-status"),
  meta: $("#answer-meta"),
  body: $("#answer-body"),
  sources: $("#sources"),
  sourceList: $("#source-list"),
  trace: $("#trace"),
  history: $("#history"),
};

let source = null; // the active EventSource
let state = null;

const escapeHtml = (s) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ---- answer text: paragraphs, lists, **bold**, and [^n] citation markers ----------------

function renderInline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\s*\[\^(\d+)\]/g, (_m, n) => `<button type="button" class="cite" data-cite="${n}">${n}</button>`);
}

function renderAnswer(raw, streaming) {
  const blocks = raw.trim().split(/\n{2,}/);
  const html = blocks
    .map((block) => {
      const lines = block.split("\n");
      if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
        const ordered = /^\s*\d+\./.test(lines[0]);
        const items = lines.map((l) => `<li>${renderInline(l.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>`).join("");
        return ordered ? `<ol>${items}</ol>` : `<ul>${items}</ul>`;
      }
      return `<p>${renderInline(lines.join(" "))}</p>`;
    })
    .join("");
  els.body.innerHTML = html || (streaming ? "<p></p>" : "");
  if (streaming) {
    const last = els.body.lastElementChild || els.body;
    last.insertAdjacentHTML("beforeend", '<span class="caret"></span>');
  }
}

// ---- trace (thinking + tool steps) ---------------------------------------------------

const TOOL_LABEL = { search_documents: "Searched documents", calculate: "Calculated" };

function traceThinking(text) {
  // Not every model or effort level sends a thinking summary; never open an empty step.
  if (!state.thinkingItem && !text.trim()) return;
  let item = state.thinkingItem;
  if (!item) {
    item = document.createElement("li");
    item.className = "step step-thinking is-running";
    item.innerHTML = `<span class="step-icon">✦</span><div class="step-main"><div class="step-title">Thinking</div><p class="step-detail thinking-text"></p></div>`;
    els.trace.append(item);
    state.thinkingItem = item;
  }
  $(".thinking-text", item).textContent += text;
}

function endThinking() {
  if (state.thinkingItem) {
    if ($(".thinking-text", state.thinkingItem).textContent.trim()) {
      state.thinkingItem.classList.remove("is-running");
    } else {
      state.thinkingItem.remove();
    }
    state.thinkingItem = null;
  }
}

function traceToolCall({ id, name, input }) {
  endThinking();
  const item = document.createElement("li");
  item.className = `step step-tool is-running`;
  item.dataset.id = id;
  const arg = input.query ?? input.expression ?? JSON.stringify(input);
  item.innerHTML = `
    <span class="step-icon">${name === "calculate" ? "∑" : "⌕"}</span>
    <div class="step-main">
      <div class="step-title">${escapeHtml(TOOL_LABEL[name] || name)}</div>
      <code class="step-arg">${escapeHtml(String(arg))}</code>
      <div class="step-detail step-summary"><span class="spinner"></span>Running…</div>
    </div>`;
  els.trace.append(item);
}

function traceToolResult({ id, summary, is_error, hits }) {
  const item = els.trace.querySelector(`[data-id="${CSS.escape(id)}"]`);
  if (!item) return;
  item.classList.remove("is-running");
  item.classList.toggle("is-error", is_error);
  const hitsHtml = hits
    .map(
      (h) => `<li><button type="button" class="hit" data-doc="${h.document_id}">
        <span class="hit-title">${escapeHtml(h.title)}</span>
        <span class="hit-score">${h.score.toFixed(2)}</span></button></li>`
    )
    .join("");
  $(".step-summary", item).innerHTML = escapeHtml(summary) + (hitsHtml ? `<ul class="hits">${hitsHtml}</ul>` : "");
}

// ---- sources ------------------------------------------------------------------------

function addSource(c) {
  if (state.citations.has(c.index)) return;
  state.citations.set(c.index, c);
  els.sources.hidden = false;
  const li = document.createElement("li");
  li.className = "source";
  li.id = `source-${c.index}`;
  li.innerHTML = `
    <button type="button" class="source-btn" data-doc="${c.document_id ?? ""}" data-cite="${c.index}">
      <span class="source-num">${c.index}</span>
      <span class="source-body">
        <span class="source-title">${escapeHtml(c.title)}</span>
        <q class="source-quote">${escapeHtml(c.cited_text)}</q>
      </span>
    </button>`;
  els.sourceList.append(li);
}

// ---- lifecycle ------------------------------------------------------------------------

function setStatus(kind, label) {
  els.status.className = `badge badge-${kind}`;
  els.status.innerHTML = (kind === "running" ? '<span class="spinner"></span>' : "") + escapeHtml(label);
}

function reset(questionText) {
  if (source) source.close();
  state = { raw: "", citations: new Map(), thinkingItem: null };
  els.hero.classList.add("is-compact");
  els.area.hidden = false;
  els.echo.textContent = questionText;
  els.body.innerHTML = "";
  els.trace.innerHTML = "";
  els.sourceList.innerHTML = "";
  els.sources.hidden = true;
  els.meta.textContent = "";
  setStatus("running", "Thinking");
  renderAnswer("", true);
}

function finish() {
  els.send.disabled = false;
  if (source) source.close();
  source = null;
}

function formatMeta(done) {
  const parts = [done.provider === "fake" ? "Demo model" : done.provider];
  const u = done.usage;
  if (u && u.input_tokens + u.output_tokens > 0) {
    parts.push(`${(u.input_tokens + u.output_tokens).toLocaleString()} tokens`);
    if (u.cache_read_input_tokens) parts.push(`${u.cache_read_input_tokens.toLocaleString()} cached`);
  }
  parts.push(`${done.citations.length} citation${done.citations.length === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function stream(url) {
  source = new EventSource(url);
  // Connection failures also fire "error", but without data; those go to onerror below.
  const on = (name, fn) =>
    source.addEventListener(name, (e) => {
      if (typeof e.data === "string") fn(JSON.parse(e.data));
    });

  on("thinking", (d) => traceThinking(d.text));
  on("tool_call", (d) => {
    traceToolCall(d);
    setStatus("running", "Using tools");
  });
  on("tool_result", traceToolResult);
  on("token", (d) => {
    endThinking();
    setStatus("running", "Writing");
    state.raw += d.text;
    renderAnswer(state.raw, true);
  });
  on("citation", (d) => {
    addSource(d);
    state.raw += `[^${d.index}]`;
    renderAnswer(state.raw, true);
  });
  on("done", (d) => {
    endThinking();
    renderAnswer(d.text, false);
    d.citations.forEach(addSource);
    setStatus("done", "Answered");
    if (!els.trace.children.length) {
      els.trace.innerHTML = `<li class="step step-note">The model answered directly: no thinking summary or tool calls for this question.</li>`;
    }
    els.meta.textContent = formatMeta(d);
    finish();
  });
  on("error", (d) => {
    endThinking();
    setStatus("error", "Failed");
    els.body.innerHTML = `<p class="error-text">${escapeHtml(d.message)}</p>`;
    finish();
  });
  // Network-level failure (not our "error" event, which carries data).
  source.onerror = (e) => {
    if (typeof e.data === "string") return;
    if (source && source.readyState !== EventSource.CLOSED) {
      setStatus("error", "Connection lost");
      finish();
    }
  };
}

function addHistory(q) {
  $(".empty-history", els.history)?.remove();
  const li = document.createElement("li");
  li.innerHTML = `<button type="button" class="history-item" data-question="">
    <span class="dot dot-pending"></span><span class="history-text"></span></button>`;
  const btn = $("button", li);
  btn.dataset.question = JSON.stringify(q);
  $(".history-text", li).textContent = q.question;
  els.history.prepend(li);
}

async function ask(text) {
  const question = text.trim();
  if (!question) return;
  els.send.disabled = true;
  reset(question);
  try {
    const res = await fetch("/api/questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const q = await res.json();
    addHistory(q);
    stream(q.stream_url);
  } catch (err) {
    setStatus("error", "Failed");
    els.body.innerHTML = `<p class="error-text">${escapeHtml(String(err.message || err))}</p>`;
    els.send.disabled = false;
  }
}

// ---- document viewer ---------------------------------------------------------------

async function openDocument(id, highlight) {
  if (!id) return;
  const dialog = $("#doc-viewer");
  const res = await fetch(`/api/documents/${id}`);
  if (!res.ok) return;
  const doc = await res.json();
  $("#doc-title").textContent = doc.title;
  let html = escapeHtml(doc.content)
    .replace(/^#{1,6}\s+(.+)$/gm, "<h4>$1</h4>")
    .replace(/\n{2,}/g, "</p><p>");
  html = `<p>${html}</p>`;
  if (highlight) {
    const needle = escapeHtml(highlight);
    html = html.replace(needle, `<mark id="hl">${needle}</mark>`);
  }
  $("#doc-content").innerHTML = html;
  dialog.showModal();
  $("#hl", dialog)?.scrollIntoView({ block: "center" });
}

// ---- wiring -------------------------------------------------------------------------

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  ask(els.input.value);
});

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

els.input.addEventListener("input", () => {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 180)}px`;
});

document.addEventListener("click", (e) => {
  const target = e.target.closest("button, [data-close]");
  if (!target) return;

  if (target.dataset.example) {
    els.input.value = target.dataset.example;
    ask(target.dataset.example);
  } else if (target.dataset.open) {
    $(`#${target.dataset.open}`).showModal();
  } else if (target.hasAttribute("data-close")) {
    target.closest("dialog").close();
  } else if (target.classList.contains("cite")) {
    const el = $(`#source-${target.dataset.cite}`);
    el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    el?.classList.remove("flash");
    void el?.offsetWidth;
    el?.classList.add("flash");
  } else if (target.classList.contains("source-btn")) {
    const c = state?.citations.get(Number(target.dataset.cite));
    openDocument(target.dataset.doc, c?.cited_text);
  } else if (target.dataset.doc) {
    openDocument(target.dataset.doc);
  } else if (target.classList.contains("history-item")) {
    const q = JSON.parse(target.dataset.question);
    els.input.value = "";
    reset(q.question);
    stream(q.stream_url);
  }
});

// htmx skips swapping 4xx responses by default; the library partial carries its own alert.
document.body.addEventListener("htmx:beforeSwap", (e) => {
  if (e.detail.target.id === "library" && e.detail.xhr.status === 413) {
    e.detail.shouldSwap = true;
    e.detail.isError = false;
  }
});

// Close the add-document dialog once the library re-renders without an error.
document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.detail.target.id !== "library") return;
  const dialog = $("#add-doc");
  if (dialog.open && !$("#library .alert")) {
    $("#add-doc-form").reset();
    dialog.close();
  }
});

// Click on the backdrop closes dialogs.
document.querySelectorAll("dialog").forEach((d) =>
  d.addEventListener("click", (e) => {
    if (e.target === d) d.close();
  })
);
