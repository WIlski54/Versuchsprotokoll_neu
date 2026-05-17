const socket = io();
const root = document.querySelector("[data-page='dashboard']");
const actionToken = root.dataset.actionToken;
let snapshot = JSON.parse(document.getElementById("snapshot-data").textContent);

socket.emit("lehrer_join");
socket.on("dashboard_snapshot", (data) => {
  snapshot = data;
  renderDashboard();
});
socket.on("neuer_schueler", () => refreshSnapshot());
socket.on("fortschritt_update", () => refreshSnapshot());
socket.on("neue_anfrage", () => refreshSnapshot());
socket.on("ki_request_update", () => refreshSnapshot());
socket.on("token_update", (usage) => {
  snapshot.usage = usage;
  renderDashboard();
});
socket.on("classroom_reset", () => refreshSnapshot());

async function refreshSnapshot() {
  const response = await fetch("/api/teacher/snapshot");
  if (response.ok) {
    snapshot = await response.json();
    renderDashboard();
  }
}

function escapeHtml(text) {
  return String(text || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}

function renderDashboard() {
  document.getElementById("studentCount").textContent = snapshot.students.length;
  document.getElementById("tokenCount").textContent = `${snapshot.usage.today}/${snapshot.usage.limit}`;
  document.getElementById("pendingCount").textContent = snapshot.requests.filter((req) => req.status === "pending").length;
  renderRequests();
  renderStudents();
}

function renderRequests() {
  const list = document.getElementById("requestList");
  const requests = snapshot.requests;
  if (!requests.length) {
    list.innerHTML = '<div class="request-row">Keine KI-Anfragen.</div>';
    return;
  }
  list.innerHTML = requests.map((req) => `
    <article class="request-row">
      <strong>${escapeHtml(req.pseudonym)} (${escapeHtml(req.kurs)})</strong>
      <span class="pill">${escapeHtml(req.status)}</span>
      <p>${escapeHtml(req.frage)}</p>
      ${req.status === "pending" ? `
        <div class="request-actions">
          <button class="btn primary" type="button" data-approve="${req.id}">Freigeben</button>
          <button class="btn danger" type="button" data-deny="${req.id}">Ablehnen</button>
        </div>
      ` : ""}
    </article>
  `).join("");
}

function renderStudents() {
  const list = document.getElementById("studentList");
  if (!snapshot.students.length) {
    list.innerHTML = '<div class="student-row">Noch keine Schüler angemeldet.</div>';
    return;
  }
  list.innerHTML = snapshot.students.map((student) => `
    <article class="student-row">
      <div>
        <strong>${escapeHtml(student.pseudonym)}</strong>
        <p>${escapeHtml(student.kurs)} | Fortschritt: ${student.done_count}/${snapshot.task_count}</p>
      </div>
      <div class="row-actions">
        <a class="btn outline" href="/schueler/${student.id}">Details</a>
        <a class="btn accent" href="/schueler/${student.id}/export.pdf">PDF</a>
      </div>
    </article>
  `).join("");
}

async function decide(requestId, decision) {
  await fetch(`/api/teacher/ki/${requestId}/${decision}`, {
    method: "POST",
    headers: { "X-Lehrer-Token": actionToken }
  });
  refreshSnapshot();
}

document.addEventListener("click", (event) => {
  const approve = event.target.closest("[data-approve]");
  const deny = event.target.closest("[data-deny]");
  if (approve) decide(approve.dataset.approve, "approve");
  if (deny) decide(deny.dataset.deny, "deny");
});

document.getElementById("resetClassroom").addEventListener("click", async () => {
  const ok = confirm("Alle Schülerdaten, Antworten, KI-Anfragen und Chats dieser Sitzung löschen?");
  if (!ok) return;
  await fetch("/api/teacher/reset", {
    method: "POST",
    headers: { "X-Lehrer-Token": actionToken }
  });
  refreshSnapshot();
});

renderDashboard();
