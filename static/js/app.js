const socket = io();
const TASK_COUNT = document.querySelectorAll(".task-card").length;
const savedTasks = new Set();
let kiApproved = false;
let uploadedImage = null;
let uploadedImageName = "";

socket.emit("schueler_join", {});

socket.on("ki_entscheidung", (data) => {
  if (data.status === "approved" || data.entscheid === "approved") {
    kiApproved = true;
    document.getElementById("sendKi").disabled = false;
    document.getElementById("kiStatus").textContent = "Freigegeben";
    addChatMessage("Die Lehrkraft hat die KI freigegeben. Du kannst deine Frage jetzt senden.", "assistant");
  } else {
    kiApproved = false;
    document.getElementById("sendKi").disabled = true;
    document.getElementById("kiStatus").textContent = "Abgelehnt";
    addChatMessage("Die Lehrkraft hat diese Anfrage abgelehnt.", "error");
  }
});

socket.on("ki_gesperrt", (data) => {
  document.getElementById("sendKi").disabled = true;
  addChatMessage(data.reason || "Die KI ist gesperrt.", "error");
});

socket.on("classroom_reset", () => {
  window.location.href = "/login";
});

async function saveTask(button) {
  const card = button.closest(".task-card");
  const task = card.dataset.task;
  const active = card.querySelector("[data-answer]");
  const state = card.querySelector(".save-state");
  const content = active.value.trim();
  if (!content) {
    state.textContent = "Bitte zuerst ausfüllen.";
    state.style.color = "#dc2626";
    return;
  }
  const payload = { task, content };
  if (task === "setup" && uploadedImage) {
    payload.image = uploadedImage;
    payload.image_name = uploadedImageName;
  }
  const response = await fetch("/api/save-answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) {
    state.textContent = data.error || "Speichern fehlgeschlagen.";
    state.style.color = "#dc2626";
    return;
  }
  savedTasks.add(task);
  state.textContent = "Gespeichert";
  state.style.color = "#16a34a";
  updateProgress();
}

function updateProgress() {
  const done = savedTasks.size;
  document.getElementById("progressText").textContent = `${done}/${TASK_COUNT} Felder gespeichert`;
  document.getElementById("progressBar").style.width = `${Math.round((done / TASK_COUNT) * 100)}%`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}

function renderSafeMarkdown(text) {
  return escapeHtml(text)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

function addChatMessage(text, type) {
  const log = document.getElementById("chatLog");
  const msg = document.createElement("div");
  msg.className = `msg ${type}`;
  msg.innerHTML = renderSafeMarkdown(text);
  log.appendChild(msg);
  log.scrollTop = log.scrollHeight;
  if (window.MathJax?.typesetPromise) {
    window.MathJax.typesetPromise([msg]);
  }
}

async function requestKiApproval() {
  const input = document.getElementById("chatInput");
  const question = input.value.trim();
  if (!question) {
    input.focus();
    return;
  }
  const response = await fetch("/api/ki/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question })
  });
  const data = await response.json();
  if (!response.ok) {
    addChatMessage(data.error || "Freigabe konnte nicht angefragt werden.", "error");
    return;
  }
  document.getElementById("kiStatus").textContent = "Wartet auf Freigabe";
  addChatMessage("Freigabe wurde angefragt. Lass deine Frage im Eingabefeld stehen.", "assistant");
}

async function sendKiMessage() {
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) {
    input.focus();
    return;
  }
  if (!kiApproved) {
    addChatMessage("Bitte zuerst eine Freigabe anfragen.", "error");
    return;
  }
  addChatMessage(message, "user");
  input.value = "";
  document.getElementById("sendKi").disabled = true;
  const response = await fetch("/api/ki/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message })
  });
  const data = await response.json();
  document.getElementById("sendKi").disabled = false;
  if (!response.ok) {
    addChatMessage(data.error || "KI-Antwort fehlgeschlagen.", "error");
    return;
  }
  addChatMessage(data.answer, "assistant");
}

document.getElementById("requestKi").addEventListener("click", requestKiApproval);
document.getElementById("sendKi").addEventListener("click", sendKiMessage);

const setupImage = document.getElementById("setupImage");
if (setupImage) {
  setupImage.addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file || !["image/png", "image/jpeg"].includes(file.type)) return;
    if (file.size > 10 * 1024 * 1024) {
      alert("Das Bild ist zu groß. Maximal erlaubt sind 10 MB.");
      return;
    }
    uploadedImageName = file.name;
    const reader = new FileReader();
    reader.onload = (loadEvent) => {
      uploadedImage = loadEvent.target.result;
      const preview = document.getElementById("setupPreview");
      preview.src = uploadedImage;
      preview.hidden = false;
    };
    reader.readAsDataURL(file);
  });
}
