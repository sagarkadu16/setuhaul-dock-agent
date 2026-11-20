// SetuHaul driver chat page: email -> phone link (first time) -> chat.
// All text is inserted with textContent, never innerHTML, so messages can't inject markup.

const $ = (id) => document.getElementById(id);
let currentId = null;
let sending = false;

async function api(path, options = {}) {
  const res = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.");
    err.status = res.status;
    throw err;
  }
  return data;
}

// name: "login" | "link" | "chat". Login and link share one view with the demo drivers panel.
function show(name) {
  $("view-auth").hidden = name === "chat";
  $("login-form").hidden = name !== "login";
  $("link-form").hidden = name !== "link";
  $("view-chat").hidden = name !== "chat";
  $("toggle-list").hidden = name !== "chat";
  $("who").hidden = name === "login";
}

// Demo drivers: clicking a number puts it in the phone field (used at the link step).
for (const btn of document.querySelectorAll(".phone-pick")) {
  btn.addEventListener("click", () => {
    $("phone").value = btn.dataset.phone;
    $("link-error").textContent = "";
    if (!$("link-form").hidden) $("phone").focus();
  });
}

function setUser(user) {
  $("driver-name").textContent = user.driver_name || user.email;
}

// ---------- start ----------

async function start() {
  try {
    const { user } = await api("/api/me");
    setUser(user);
    user.linked ? await openChat() : show("link");
  } catch {
    show("login");
    $("email").focus();
  }
}

// ---------- login / link ----------

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    const { user } = await api("/api/login", { method: "POST", body: { email: $("email").value } });
    setUser(user);
    if (user.linked) {
      await openChat();
    } else {
      show("link");
      $("phone").focus();
    }
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("link-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("link-error").textContent = "";
  try {
    const { user } = await api("/api/link", { method: "POST", body: { phone: $("phone").value } });
    setUser(user);
    await openChat();
  } catch (err) {
    $("link-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  currentId = null;
  $("email").value = "";
  show("login");
});

// ---------- conversations ----------

async function openChat() {
  show("chat");
  const list = await loadList();
  if (list.length) await openConversation(list[0].id);
  else await newConversation();
}

function formatWhen(iso) {
  const d = new Date(iso);
  return d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

async function loadList() {
  const { conversations } = await api("/api/conversations");
  const ul = $("conversation-list");
  ul.replaceChildren();
  for (const c of conversations) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.dataset.id = c.id;
    btn.className = c.id === currentId ? "active" : "";
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = formatWhen(c.opened_at);
    const preview = document.createElement("span");
    preview.className = "preview";
    preview.textContent = c.preview || "New chat";
    btn.append(when, preview);
    btn.addEventListener("click", () => openConversation(c.id));
    li.append(btn);
    ul.append(li);
  }
  return conversations;
}

async function newConversation() {
  const { conversation } = await api("/api/conversations", { method: "POST" });
  await loadList();
  await openConversation(conversation.id);
}

async function openConversation(id) {
  currentId = id;
  $("sidebar").classList.remove("open");
  for (const b of $("conversation-list").querySelectorAll("button")) {
    b.classList.toggle("active", b.dataset.id === id);
  }
  const { messages } = await api(`/api/conversations/${encodeURIComponent(id)}`);
  const box = $("messages");
  box.replaceChildren();
  if (!messages.length) showEmpty();
  for (const m of messages) addBubble(m.sender, m.text);
  setComposer(true);
  $("text").focus();
}

function showEmpty() {
  const div = document.createElement("div");
  div.className = "empty";
  div.id = "empty";
  const strong = document.createElement("strong");
  strong.textContent = "Running late?";
  const p = document.createElement("p");
  p.textContent = 'Tell me what happened and when you will reach. For example: "Stuck in traffic, reaching 11:20".';
  div.append(strong, p);
  $("messages").append(div);
}

const SENDER_LABEL = { AGENT: "Assistant", OPERATIONS: "Operations", WAREHOUSE: "Warehouse", SYSTEM: "System" };

function addBubble(sender, text, extraClass = "") {
  $("empty")?.remove();
  const div = document.createElement("div");
  const known = sender === "DRIVER" || sender === "AGENT";
  div.className = `bubble ${known ? sender : "other"} ${extraClass}`.trim();
  if (sender !== "DRIVER") {
    const label = document.createElement("span");
    label.className = "sender";
    label.textContent = SENDER_LABEL[sender] || sender;
    div.append(label);
  }
  div.append(document.createTextNode(text));
  $("messages").append(div);
  div.scrollIntoView({ block: "end" });
  return div;
}

// ---------- sending ----------

function setComposer(enabled) {
  $("text").disabled = !enabled;
  $("send").disabled = !enabled;
}

$("send-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (sending || !currentId) return;
  const text = $("text").value;
  if (!text.trim()) return;

  sending = true;
  setComposer(false);
  $("send-error").textContent = "";
  const mine = addBubble("DRIVER", text.trim());
  const typing = addBubble("AGENT", "typing…", "typing");
  try {
    const { agent_message } = await api(`/api/conversations/${encodeURIComponent(currentId)}/messages`, {
      method: "POST",
      body: { text },
    });
    typing.remove();
    addBubble("AGENT", agent_message.text);
    $("text").value = "";
    autoGrow();
    loadList();
  } catch (err) {
    typing.remove();
    mine.remove();   // on any error the message wasn't stored, so don't show it as sent
    $("send-error").textContent = err.message;
  } finally {
    sending = false;
    setComposer(true);
    $("text").focus();
  }
});

// Enter sends; Shift+Enter adds a new line.
$("text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("send-form").requestSubmit();
  }
});

function autoGrow() {
  const t = $("text");
  t.style.height = "auto";
  t.style.height = `${t.scrollHeight}px`;
}
$("text").addEventListener("input", autoGrow);

$("new-chat").addEventListener("click", newConversation);
$("toggle-list").addEventListener("click", () => $("sidebar").classList.toggle("open"));

start();
