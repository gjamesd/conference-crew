const groupId = document.getElementById("groupId").textContent.trim();
const messagesEl = document.getElementById("messages");
const metaEl = document.getElementById("meta");
const explainEl = document.getElementById("explain");
const feedbackEl = document.getElementById("feedback");
const statusEl = document.getElementById("status");
const form = document.getElementById("composer");

let lastQueryId = null;

function bubbleClass(msg) {
  if (msg.role === "assistant" || msg.sender === "CrewBot") return "bot";
  if (msg.sender === "You" || msg.sender === form.sender.value) return "me";
  return "them";
}

function renderMessages(messages) {
  messagesEl.innerHTML = "";
  for (const msg of messages || []) {
    appendBubble(msg);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendBubble(msg) {
  const div = document.createElement("div");
  div.className = `bubble ${bubbleClass(msg)}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = msg.sender || msg.role || "unknown";
  div.appendChild(who);
  const body = document.createElement("div");
  body.className = "bubble-body";
  body.textContent = msg.body || "";
  div.appendChild(body);
  messagesEl.appendChild(div);
  return div;
}

function setStatusLine(text, bad = false) {
  statusEl.classList.toggle("bad", bad);
  statusEl.textContent = text;
}

function renderExplain(exp, queryId) {
  if (queryId) {
    lastQueryId = queryId;
    feedbackEl.hidden = false;
  }
  if (!exp) {
    explainEl.textContent = "Waiting for Elasticsearch retrieval…";
    return;
  }
  const parts = [];
  if (exp.mode || exp.methods) {
    parts.push(
      `<div class="row"><span class="pill">${exp.mode || "compare"}</span>${
        typeof exp.methods === "string" ? exp.methods : exp.narrative || ""
      }</div>`
    );
  }
  if (exp.narrative) parts.push(`<div class="row">${exp.narrative}</div>`);
  if (exp.plan_mode) {
    parts.push(
      `<div class="row"><span class="pill">${exp.plan_mode}</span> conflicts skipped: ${
        exp.conflicts_resolved || 0
      }</div>`
    );
  }
  if (exp.only_semantic?.length) {
    parts.push(`<div class="row"><b>Semantic-only:</b> ${exp.only_semantic.join(", ")}</div>`);
  }
  if (exp.hybrid_rescues?.length) {
    parts.push(`<div class="row"><b>Hybrid rescues:</b> ${exp.hybrid_rescues.join(", ")}</div>`);
  }
  const talks = exp.top_talks || exp.plan_items || [];
  if (talks.length) {
    parts.push("<div class='row'><b>Top evidence</b></div>");
    for (const t of talks.slice(0, 5)) {
      parts.push(
        `<div class="row">· ${t.talk_id || ""} ${t.title || ""} ` +
          `<span class="pill">${t.score ?? t.overlap_score ?? ""}</span></div>`
      );
    }
  }
  explainEl.innerHTML = parts.join("") || "Explanation present but empty.";
}

function renderTalkCards(talks, afterEl) {
  const existing = afterEl.parentElement?.querySelector(".talk-cards");
  if (existing) existing.remove();
  if (!talks?.length) return;
  const wrap = document.createElement("div");
  wrap.className = "talk-cards";
  for (const t of talks.slice(0, 4)) {
    const card = document.createElement("div");
    card.className = "talk-card";
    const score =
      t.score != null && !Number.isNaN(Number(t.score))
        ? ` · ${Number(t.score).toFixed(2)}`
        : "";
    card.innerHTML = `<strong>${t.title || t.talk_id || "Talk"}</strong>
      <span>${t.room || "?"} · ${t.start || "?"}</span>
      <span class="pill">${t.track || ""}${score}</span>`;
    wrap.appendChild(card);
  }
  afterEl.insertAdjacentElement("afterend", wrap);
}

async function loadThread() {
  const res = await fetch(`/api/groups/${encodeURIComponent(groupId)}/messages`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "failed to load messages");
  renderMessages(data.messages);
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (!data.elasticsearch_configured) {
      setStatusLine("Add ELASTICSEARCH_URL (+ API key) to .env, then run scripts/ingest.py", true);
      return;
    }
    if (!data.elasticsearch_ping) {
      setStatusLine(`Elasticsearch ping failed: ${data.elasticsearch_error || "unknown"}`, true);
      return;
    }
    const llm = data.openai_configured ? "OpenAI live (gpt-4o-mini + cache)" : "OpenAI key not set — template answers";
    setStatusLine(`Elasticsearch connected · streaming ready · ${llm}`);
  } catch (e) {
    setStatusLine(String(e.message || e), true);
  }
}

function parseSSEChunk(buffer) {
  const events = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() || "";
  for (const part of parts) {
    let event = "message";
    const dataLines = [];
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    try {
      events.push({ event, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      /* ignore partial */
    }
  }
  return { events, rest };
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sender = form.sender.value;
  const body = form.body.value.trim();
  if (!body) return;
  form.body.value = "";
  form.querySelector("button").disabled = true;

  appendBubble({ sender, body, role: "user" });
  const botBubble = appendBubble({ sender: "CrewBot", body: "", role: "assistant" });
  const botBody = botBubble.querySelector(".bubble-body");
  botBubble.classList.add("streaming");
  setStatusLine("CrewBot is searching Elasticsearch…");

  try {
    const res = await fetch(`/api/groups/${encodeURIComponent(groupId)}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ sender, body }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : `stream failed (${res.status})`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let assembled = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSSEChunk(buffer);
      buffer = parsed.rest;
      for (const evt of parsed.events) {
        if (evt.event === "status") {
          const phase = evt.data?.phase || "working";
          const labels = {
            searching: "CrewBot is searching Elasticsearch…",
            comparing: "Comparing keyword vs semantic vs hybrid…",
            planning: "Building a conflict-aware crew itinerary…",
            generating: "Narrating with gpt-4o-mini…",
            cache_hit: "Cache hit — replaying saved answer (no new spend)…",
            indexed: "Message saved to Elasticsearch…",
          };
          setStatusLine(labels[phase] || phase);
        } else if (evt.event === "explanation") {
          renderExplain(evt.data, null);
        } else if (evt.event === "talks") {
          renderTalkCards(evt.data?.talks || [], botBubble);
        } else if (evt.event === "token") {
          assembled += evt.data?.text || "";
          botBody.textContent = assembled;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        } else if (evt.event === "done") {
          botBubble.classList.remove("streaming");
          if (evt.data?.thread) renderMessages(evt.data.thread);
          metaEl.textContent = JSON.stringify(evt.data?.meta || {}, null, 2);
          renderExplain(evt.data?.meta?.explanation, evt.data?.query_id || evt.data?.meta?.query_id);
          await checkHealth();
        }
      }
    }
  } catch (err) {
    botBody.textContent = String(err.message || err);
    setStatusLine(String(err.message || err), true);
  } finally {
    botBubble.classList.remove("streaming");
    form.querySelector("button").disabled = false;
  }
});

document.querySelectorAll(".hints [data-fill]").forEach((btn) => {
  btn.addEventListener("click", () => {
    form.body.value = btn.getAttribute("data-fill");
    form.body.focus();
  });
});

feedbackEl.querySelectorAll("[data-rating]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    if (!lastQueryId) return;
    const rating = btn.getAttribute("data-rating");
    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_id: lastQueryId, rating, group_id: groupId }),
    });
    if (res.ok) setStatusLine(`Feedback saved (${rating}) for ${lastQueryId}`);
  });
});

checkHealth();
loadThread().catch((e) => setStatusLine(String(e.message || e), true));
