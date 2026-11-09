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
    const div = document.createElement("div");
    div.className = `bubble ${bubbleClass(msg)}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = msg.sender || msg.role || "unknown";
    div.appendChild(who);
    div.appendChild(document.createTextNode(msg.body || ""));
    messagesEl.appendChild(div);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderExplain(meta) {
  const exp = meta?.explanation || meta?.compare?.explanation || meta?.plan?.explanation;
  lastQueryId = meta?.query_id || meta?.plan?.query_id || meta?.ask?.query_id || null;
  feedbackEl.hidden = !lastQueryId;

  if (!exp) {
    explainEl.textContent = "No explanation payload on this turn.";
    return;
  }

  const parts = [];
  if (exp.mode || exp.methods) {
    parts.push(`<div class="row"><span class="pill">${exp.mode || "compare"}</span>${exp.methods || exp.narrative || ""}</div>`);
  }
  if (exp.narrative) parts.push(`<div class="row">${exp.narrative}</div>`);
  if (exp.plan_mode) {
    parts.push(`<div class="row"><span class="pill">${exp.plan_mode}</span> conflicts skipped: ${exp.conflicts_resolved || 0}</div>`);
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
  if (exp.interest_map && Object.keys(exp.interest_map).length) {
    parts.push("<div class='row'><b>Crew interests used</b></div>");
    for (const [person, interest] of Object.entries(exp.interest_map)) {
      parts.push(`<div class="row">· <b>${person}</b>: ${String(interest).slice(0, 90)}…</div>`);
    }
  }
  explainEl.innerHTML = parts.join("") || "Explanation present but empty.";
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
      statusEl.classList.add("bad");
      statusEl.textContent = "Add ELASTICSEARCH_URL (+ API key) to .env, then run scripts/ingest.py";
      return;
    }
    if (data.elasticsearch_ping) {
      statusEl.classList.remove("bad");
      statusEl.textContent = "Elasticsearch connected · compare / plan / explain ready";
    } else {
      statusEl.classList.add("bad");
      statusEl.textContent = `Elasticsearch configured but ping failed: ${data.elasticsearch_error || "unknown"}`;
    }
  } catch (e) {
    statusEl.classList.add("bad");
    statusEl.textContent = String(e.message || e);
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sender = form.sender.value;
  const body = form.body.value.trim();
  if (!body) return;
  form.body.value = "";
  const current = Array.from(messagesEl.children).map((node) => ({
    sender: node.querySelector(".who")?.textContent,
    body: node.childNodes[1]?.textContent || "",
    role: node.classList.contains("bot") ? "assistant" : "user",
  }));
  renderMessages([...current, { sender, body, role: "user" }]);

  try {
    const res = await fetch(`/api/groups/${encodeURIComponent(groupId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender, body }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    renderMessages(data.thread);
    metaEl.textContent = JSON.stringify(data.meta || {}, null, 2);
    renderExplain(data.meta || {});
  } catch (err) {
    statusEl.classList.add("bad");
    statusEl.textContent = String(err.message || err);
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
    if (res.ok) {
      statusEl.classList.remove("bad");
      statusEl.textContent = `Feedback saved (${rating}) for ${lastQueryId}`;
    }
  });
});

checkHealth();
loadThread().catch((e) => {
  statusEl.classList.add("bad");
  statusEl.textContent = String(e.message || e);
});
