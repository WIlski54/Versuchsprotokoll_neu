const socket = io();
const root = document.querySelector("[data-page='detail']");
const studentId = root.dataset.schuelerId;
let detail = JSON.parse(document.getElementById("detail-data").textContent);

socket.emit("lehrer_join");
socket.emit("watch_schueler", { schueler_id: studentId });
socket.on("detail_snapshot", (data) => {
  detail = data;
  renderDetail();
});
socket.on("antwort_live", (entry) => {
  detail.answers.unshift(entry);
  renderAnswers();
});
socket.on("chat_live", (entry) => {
  detail.chats.push({ rolle: entry.role, inhalt: entry.content, tokens: entry.tokens || 0, created_at: entry.created_at || "" });
  renderChats();
});

function escapeHtml(text) {
  return String(text || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}

function renderAnswers() {
  const list = document.getElementById("answerLog");
  if (!detail.answers.length) {
    list.innerHTML = '<div class="log-row">Noch keine gespeicherten Antworten.</div>';
    return;
  }
  list.innerHTML = detail.answers.map((answer) => `
    <article class="log-row">
      <strong>${escapeHtml(answer.aufgabe)}</strong>
      <p>${escapeHtml(answer.inhalt)}</p>
      <small>${escapeHtml(answer.created_at)}</small>
    </article>
  `).join("");
}

function renderChats() {
  const list = document.getElementById("chatLogTeacher");
  if (!detail.chats.length) {
    list.innerHTML = '<div class="log-row">Noch kein KI-Chat.</div>';
    return;
  }
  list.innerHTML = detail.chats.map((chat) => `
    <article class="log-row">
      <strong>${escapeHtml(chat.rolle)} ${chat.tokens ? `| ${chat.tokens} Tokens` : ""}</strong>
      <p>${escapeHtml(chat.inhalt)}</p>
      <small>${escapeHtml(chat.created_at)}</small>
    </article>
  `).join("");
}

function renderRequests() {
  const list = document.getElementById("requestLog");
  if (!detail.requests.length) {
    list.innerHTML = '<div class="request-row">Keine KI-Anfragen.</div>';
    return;
  }
  list.innerHTML = detail.requests.map((req) => `
    <article class="request-row">
      <span class="pill">${escapeHtml(req.status)}</span>
      <p>${escapeHtml(req.frage)}</p>
      <small>${escapeHtml(req.created_at)}</small>
    </article>
  `).join("");
}

function renderDetail() {
  renderAnswers();
  renderChats();
  renderRequests();
}

renderDetail();
