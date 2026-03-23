const gamesContainer = document.getElementById("games");
const statusEl = document.getElementById("status");
const countInput = document.getElementById("count");
const btnRefresh = document.getElementById("btn-refresh");
const btnUpdate = document.getElementById("btn-update");

const checkedGames = new Set();
const UNCHECKED_KEY = "uncheckedGames";

function loadUnchecked() {
  try {
    return new Set(JSON.parse(localStorage.getItem(UNCHECKED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveUnchecked(unchecked) {
  localStorage.setItem(UNCHECKED_KEY, JSON.stringify([...unchecked]));
}

function showStatus(msg, type = "loading") {
  statusEl.textContent = msg;
  statusEl.className = `status ${type}`;
  statusEl.hidden = false;
}

function hideStatus() {
  statusEl.hidden = true;
}

function setButtons(disabled) {
  btnRefresh.disabled = disabled;
  btnUpdate.disabled = disabled;
}

function renderGames(games) {
  gamesContainer.innerHTML = "";
  for (const game of games) {
    const card = document.createElement("div");
    card.className = "game-card";
    if (checkedGames.has(game.app_id)) {
      card.classList.add("checked");
    }

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "game-check";
    checkbox.checked = checkedGames.has(game.app_id);
    checkbox.addEventListener("change", () => {
      const unchecked = loadUnchecked();
      if (checkbox.checked) {
        checkedGames.add(game.app_id);
        card.classList.add("checked");
        unchecked.delete(game.app_id);
      } else {
        checkedGames.delete(game.app_id);
        card.classList.remove("checked");
        unchecked.add(game.app_id);
      }
      saveUnchecked(unchecked);
    });

    const img = document.createElement("img");
    if (game.thumbnail) {
      img.src = `file:///${game.thumbnail.replace(/\\/g, "/")}`;
    }
    img.alt = game.name;

    const name = document.createElement("div");
    name.className = "game-name";
    name.textContent = game.name;
    name.title = game.name;

    const id = document.createElement("div");
    id.className = "game-id";
    id.textContent = `App ID: ${game.app_id}`;

    card.appendChild(checkbox);
    card.appendChild(img);
    card.appendChild(name);
    card.appendChild(id);
    gamesContainer.appendChild(card);
  }
}

async function loadGames() {
  const count = parseInt(countInput.value) || 10;
  setButtons(true);
  showStatus("Loading games...");
  try {
    const games = await window.api.getGames(count);
    if (games.error) throw new Error(games.error);
    const unchecked = loadUnchecked();
    checkedGames.clear();
    for (const game of games) {
      if (!unchecked.has(game.app_id)) {
        checkedGames.add(game.app_id);
      }
    }
    renderGames(games);
    hideStatus();
  } catch (e) {
    showStatus(e.message, "error");
  } finally {
    setButtons(false);
  }
}

btnRefresh.addEventListener("click", loadGames);

btnUpdate.addEventListener("click", async () => {
  const appIds = [...checkedGames];
  if (appIds.length === 0) {
    showStatus("No games selected.", "error");
    return;
  }
  setButtons(true);
  showStatus("Updating Apollo config...");
  try {
    const result = await window.api.updateConfig(appIds);
    if (result.error) throw new Error(result.error);
    showStatus(`Apollo config updated with ${result.count} games.`, "success");
  } catch (e) {
    showStatus(e.message, "error");
  } finally {
    setButtons(false);
  }
});

loadGames();
