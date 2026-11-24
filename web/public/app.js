const WEB_VERSION = "1.0.0";

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  log(`${options.method || "GET"} ${path} → ${res.status} · ${res.headers.get("X-Served-By") || "?"}`);
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

function log(line) {
  const li = document.createElement("li");
  li.textContent = line;
  $("log").prepend(li);
  while ($("log").children.length > 8) $("log").lastChild.remove();
}

async function loadHealth() {
  try {
    const h = await api("/health");
    $("api-version").textContent = h.version;
    $("api-status").textContent = h.status;
  } catch {
    $("api-status").textContent = "sin conexión";
  }
}

async function loadVisits() {
  $("visits").textContent = (await api("/visits")).visits;
}

async function loadSession() {
  const { session } = await api("/session");
  $("session-views").textContent = session.views;
  $("session-id").textContent = session.session_id.slice(0, 12);
  $("session-created").textContent = session.created_by;
}

async function loadItems() {
  const { items } = await api("/items");
  $("items").replaceChildren(
    ...items.map((item) => {
      const li = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = item.description ? `${item.name} — ${item.description}` : item.name;
      const del = document.createElement("button");
      del.textContent = "✕";
      del.setAttribute("aria-label", `Borrar ${item.name}`);
      del.onclick = async () => {
        await api(`/items/${item.id}`, { method: "DELETE" });
        loadItems();
      };
      li.append(text, del);
      return li;
    }),
  );
}

$("visit-btn").onclick = async () => {
  $("visits").textContent = (await api("/visits", { method: "POST" })).visits;
};

$("item-form").onsubmit = async (event) => {
  event.preventDefault();
  await api("/items", {
    method: "POST",
    body: JSON.stringify({ name: $("item-name").value, description: $("item-desc").value }),
  });
  event.target.reset();
  loadItems();
};

$("web-version").textContent = WEB_VERSION;
loadHealth();
loadVisits();
loadSession();
loadItems();
