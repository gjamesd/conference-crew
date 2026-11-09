const groupIdEl = document.getElementById("groupId");
const messagesEl = document.getElementById("messages");
const metaEl = document.getElementById("meta");
const explainEl = document.getElementById("explain");
const feedbackEl = document.getElementById("feedback");
const statusEl = document.getElementById("status");
const form = document.getElementById("composer");
const demoCue = document.getElementById("demoCue");
const demoCueText = document.getElementById("demoCueText");
const continueCrewBtn = document.getElementById("continueCrew");
const restartDemoBtn = document.getElementById("restartDemo");
const downloadJsonBtn = document.getElementById("downloadJson");
const downloadMdBtn = document.getElementById("downloadMd");
const openLinearLink = document.getElementById("openLinear");
const suggestHint = document.getElementById("suggestHint");

const CREW = new Set(["Alex", "Sam", "Jordan"]);
const BEAT_KEY = "crewMeetingBeat_v3";
const GROUP_KEY = "crewMeetingGroup_v3";
const WAIT_KEY = "crewMeetingWaiting_v3";

let lastQueryId = null;
let nextBeatIndex = 0;
let totalBeats = 0;
let waitingForYou = false;
let revealing = false;
let lastArtifact = null;

function hasMoreBeats() {
  return nextBeatIndex < (totalBeats || 0);
}

function downloadText(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function showArtifactButtons(artifact) {
  lastArtifact = artifact || null;
  const on = Boolean(artifact);
  downloadJsonBtn.hidden = !on;
  downloadMdBtn.hidden = !on;
}

function showLinearLink(linear) {
  if (!linear?.sync_issue_url) {
    openLinearLink.hidden = true;
    openLinearLink.removeAttribute("href");
    return;
  }
  openLinearLink.hidden = false;
  openLinearLink.href = linear.sync_issue_url;
  openLinearLink.textContent = `Open in Linear (${linear.sync_issue_identifier || "sync"})`;
  if (linear.ok) {
    setStatusLine(
      `Pushed to Linear · ${linear.sync_issue_identifier || "sync"} · commented ${
        (linear.commented_issues || []).length
      } workstream issue(s)`
    );
  } else if (linear.error) {
    setStatusLine(`Linear push failed: ${linear.error}`, true);
  }
}

function freshGroupId() {
  return `crew-live-${Date.now().toString(36)}`;
}

function getGroupId() {
  return groupIdEl.textContent.trim();
}

function setGroupId(id) {
  groupIdEl.textContent = id;
  sessionStorage.setItem(GROUP_KEY, id);
}

function bubbleClass(msg) {
  if (msg.role === "assistant" || msg.sender === "CrewBot") return "bot";
  if (msg.sender === "You" || msg.sender === form.sender.value) return "me";
  return "them";
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
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function renderMessages(messages) {
  messagesEl.innerHTML = "";
  for (const msg of messages || []) appendBubble(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setStatusLine(text, bad = false) {
  statusEl.classList.toggle("bad", bad);
  statusEl.textContent = text;
}

function setCue(text, { showContinue = false } = {}) {
  demoCue.hidden = false;
  demoCueText.textContent = text;
  continueCrewBtn.hidden = !showContinue;
  waitingForYou = !showContinue;
  sessionStorage.setItem(WAIT_KEY, waitingForYou ? "1" : "0");
}

function setSuggest(text) {
  if (!text) return;
  suggestHint.setAttribute("data-fill", text);
  suggestHint.textContent = `Suggested: ${text.length > 42 ? text.slice(0, 42) + "…" : text}`;
  form.body.placeholder = text;
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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
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
    const llm = data.openai_configured
      ? "OpenAI live (gpt-4o-mini + cache)"
      : "OpenAI key not set — template answers";
    const lin = data.linear_configured
      ? `Linear ${data.linear_team_key || "on"}`
      : "Linear not configured";
    setStatusLine(`Elasticsearch connected · paced demo · ${llm} · ${lin}`);
  } catch (e) {
    setStatusLine(String(e.message || e), true);
  }
}

async function revealBeat(beatIndex) {
  if (revealing) return;
  revealing = true;
  continueCrewBtn.disabled = true;
  setStatusLine(`Crew is typing (beat ${beatIndex + 1})…`);

  try {
    const res = await fetch(`/api/groups/${encodeURIComponent(getGroupId())}/demo/advance`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beat_index: beatIndex }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "demo advance failed");

    if (!data.ok) {
      setCue(data.cue || "Script finished — keep asking CrewBot.", { showContinue: false });
      continueCrewBtn.hidden = true;
      return;
    }

    for (const msg of data.messages || []) {
      appendBubble(msg);
      await sleep(450);
    }

    nextBeatIndex = data.next_beat_index ?? beatIndex + 1;
    totalBeats = data.total_beats || totalBeats;
    sessionStorage.setItem(BEAT_KEY, String(nextBeatIndex));
    setSuggest(data.suggest);
    setCue(
      `${data.cue || "Your turn."} (${beatIndex + 1}/${data.total_beats})`,
      { showContinue: hasMoreBeats() }
    );
    setStatusLine(
      hasMoreBeats()
        ? "Ask CrewBot anytime — or Continue the conversation for more meeting detail."
        : "Meeting script finished — keep asking CrewBot from this thread."
    );
  } catch (err) {
    setStatusLine(String(err.message || err), true);
  } finally {
    revealing = false;
    continueCrewBtn.disabled = false;
  }
}

async function startPacedDemo({ restart = false } = {}) {
  messagesEl.innerHTML = "";
  metaEl.textContent = "—";
  explainEl.textContent =
    "Meeting chat is indexed in Elasticsearch. Summaries and sprint JSON use chat only (no random talks).";
  feedbackEl.hidden = true;
  showArtifactButtons(null);
  setSuggest("summarize this meeting — what's most important?");

  if (restart || !sessionStorage.getItem(GROUP_KEY)) {
    setGroupId(freshGroupId());
    nextBeatIndex = 0;
    sessionStorage.setItem(BEAT_KEY, "0");
  } else {
    setGroupId(sessionStorage.getItem(GROUP_KEY));
    nextBeatIndex = Number(sessionStorage.getItem(BEAT_KEY) || "0");
  }

  demoCue.hidden = false;
  if (nextBeatIndex === 0) {
    setCue("Alex, Sam, and Jordan are posting where they are…", {
      showContinue: false,
    });
    await revealBeat(0);
  } else {
    const res = await fetch(`/api/groups/${encodeURIComponent(getGroupId())}/messages`);
    const data = await res.json();
    if (res.ok) renderMessages(data.messages);
    setCue(
      "Pick up where you left off: ask CrewBot, or Continue the conversation.",
      { showContinue: hasMoreBeats() }
    );
  }
}

async function askCrewBot(sender, body) {
  appendBubble({ sender, body, role: "user" });
  const botBubble = appendBubble({ sender: "CrewBot", body: "", role: "assistant" });
  const botBody = botBubble.querySelector(".bubble-body");
  botBubble.classList.add("streaming");
  setStatusLine("CrewBot is searching Elasticsearch…");

  const res = await fetch(`/api/groups/${encodeURIComponent(getGroupId())}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ sender, body, invoke_bot: true }),
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
          searching: "CrewBot is reading crew chat in Elasticsearch…",
          comparing: "Comparing keyword vs semantic vs hybrid…",
          planning: "Drafting a sprint-planning artifact…",
          generating: "Narrating with gpt-4o-mini…",
          cache_hit: "Cache hit — replaying saved answer (no new spend)…",
          indexed: "Message saved to Elasticsearch…",
          linear_push: "Pushing summary / plan to Linear…",
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
      } else if (evt.event === "artifact") {
        showArtifactButtons(evt.data?.artifact);
        setStatusLine("Sprint artifact ready — download JSON or markdown doc.");
      } else if (evt.event === "linear") {
        showLinearLink(evt.data?.linear);
      } else if (evt.event === "done") {
        botBubble.classList.remove("streaming");
        if (!assembled && evt.data?.bot_message?.body) {
          botBody.textContent = evt.data.bot_message.body;
        }
        if (evt.data?.meta?.artifact) showArtifactButtons(evt.data.meta.artifact);
        if (evt.data?.meta?.linear) showLinearLink(evt.data.meta.linear);
        metaEl.textContent = JSON.stringify(evt.data?.meta || {}, null, 2);
        renderExplain(evt.data?.meta?.explanation, evt.data?.query_id || evt.data?.meta?.query_id);
      }
    }
  }
  botBubble.classList.remove("streaming");
}

async function postCrewOnly(sender, body) {
  appendBubble({ sender, body, role: "user" });
  const res = await fetch(`/api/groups/${encodeURIComponent(getGroupId())}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sender, body, invoke_bot: false }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "chat failed");
  setStatusLine(`${sender} posted — CrewBot stayed quiet.`);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sender = form.sender.value;
  const body = form.body.value.trim();
  if (!body || revealing) return;
  form.body.value = "";
  form.querySelector("button").disabled = true;

  try {
    if (CREW.has(sender)) {
      await postCrewOnly(sender, body);
    } else {
      await askCrewBot(sender, body);
      setCue(
        hasMoreBeats()
          ? "Ask again anytime — or hit Continue the conversation for more meeting detail."
          : "Meeting script finished — keep asking from this live thread.",
        { showContinue: hasMoreBeats() }
      );
      await checkHealth();
    }
  } catch (err) {
    setStatusLine(String(err.message || err), true);
  } finally {
    form.querySelector("button").disabled = false;
  }
});

continueCrewBtn.addEventListener("click", async () => {
  if (revealing) return;
  await revealBeat(nextBeatIndex);
});

downloadJsonBtn.addEventListener("click", () => {
  if (!lastArtifact) return;
  const { document_markdown: _md, ...jsonBody } = lastArtifact;
  downloadText(
    "sprint-plan.json",
    JSON.stringify(jsonBody, null, 2),
    "application/json"
  );
});

downloadMdBtn.addEventListener("click", () => {
  if (!lastArtifact) return;
  const md =
    lastArtifact.document_markdown ||
    `# Sprint plan\n\n\`\`\`json\n${JSON.stringify(lastArtifact, null, 2)}\n\`\`\`\n`;
  downloadText("sprint-plan.md", md, "text/markdown");
});

restartDemoBtn.addEventListener("click", async () => {
  sessionStorage.removeItem(GROUP_KEY);
  sessionStorage.removeItem(BEAT_KEY);
  sessionStorage.removeItem(WAIT_KEY);
  await startPacedDemo({ restart: true });
});

document.querySelectorAll(".hints [data-fill]").forEach((btn) => {
  btn.addEventListener("click", () => {
    form.body.value = btn.getAttribute("data-fill");
    form.sender.value = "You";
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
      body: JSON.stringify({ query_id: lastQueryId, rating, group_id: getGroupId() }),
    });
    if (res.ok) setStatusLine(`Feedback saved (${rating}) for ${lastQueryId}`);
  });
});

(async function boot() {
  await checkHealth();
  try {
    const meta = await fetch("/api/demo/script").then((r) => r.json());
    totalBeats = meta.total_beats || 0;
  } catch {
    totalBeats = 4;
  }
  await startPacedDemo();
})();
