const state = {
  selectedPlayerId: null,
  tags: [],
  players: [],
  repeats: [],
};
const SELF_PLAYER_ID = "126600075";

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
    },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${text}`);
  }
  return response.json();
}

async function loadDashboard() {
  const [alerts, summary, repeats, players, tags] = await Promise.all([
    api("/alerts/current"),
    api("/summary"),
    api(`/players/repeats?limit=20&min_encounters=2&exclude_player_id=${encodeURIComponent(SELF_PLAYER_ID)}`),
    api(`/players/recent?limit=20&exclude_player_id=${encodeURIComponent(SELF_PLAYER_ID)}`),
    api("/tags/presets"),
  ]);

  state.tags = tags.items || [];
  state.players = players.items || [];
  state.repeats = repeats.items || [];

  renderAlerts(alerts);
  renderSummary(summary);
  renderRepeats(state.repeats);
  renderPlayers(state.players);
  renderTagOptions();

  if (!state.selectedPlayerId) {
    const firstCandidate = state.repeats[0]?.player_id || state.players[0]?.steam_id;
    if (firstCandidate) {
      await selectPlayer(firstCandidate);
    }
  }
}

function renderAlerts(data) {
  const headline = document.querySelector("#alert-headline");
  const updatedAt = document.querySelector("#alert-updated-at");
  const list = document.querySelector("#alert-list");
  list.innerHTML = "";
  headline.textContent = data.headline || "暂无提醒";
  headline.classList.toggle("empty", !data.headline);
  updatedAt.textContent = data.updated_at ? `更新于 ${formatDate(data.updated_at)}` : "";

  for (const item of data.items || []) {
    const node = document.querySelector("#alert-item-template").content.firstElementChild.cloneNode(true);
    node.dataset.playerId = item.player_id;
    node.querySelector(".card-title").textContent = item.player_name || item.player_id;
    node.querySelector(".card-summary").textContent = item.summary_text || "暂无摘要";
    node.querySelector(".badge").textContent = item.tag || `${item.encounter_count}次`;
    node.addEventListener("click", () => selectPlayer(item.player_id));
    list.appendChild(node);
  }
}

function renderSummary(data) {
  const container = document.querySelector("#summary-cards");
  const cards = [
    ["玩家数", data.total_players],
    ["相遇记录", data.total_encounters],
    ["事件数", data.total_events],
    ["最近命中", (data.recent_hits || []).length],
  ];
  container.innerHTML = "";
  for (const [label, value] of cards) {
    const item = document.createElement("article");
    item.className = "stat";
    item.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${value ?? 0}</div>`;
    container.appendChild(item);
  }
}

function renderRepeats(items) {
  const list = document.querySelector("#repeats-list");
  list.innerHTML = "";
  for (const item of items) {
    const node = document.querySelector("#repeat-item-template").content.firstElementChild.cloneNode(true);
    node.dataset.playerId = item.player_id;
    node.querySelector(".card-title").textContent = item.latest_name || item.player_id;
    node.querySelector(".card-summary").textContent =
      `交手 ${item.encounter_count} 次，${item.teammate_count} 次队友 / ${item.opponent_count} 次对手`;
    node.querySelector(".badge").textContent = `${item.win_count}W-${item.lose_count}L`;
    node.addEventListener("click", () => selectPlayer(item.player_id));
    list.appendChild(node);
  }
}

function renderPlayers(items) {
  const list = document.querySelector("#players-list");
  list.innerHTML = "";
  for (const item of items) {
    const node = document.querySelector("#player-item-template").content.firstElementChild.cloneNode(true);
    node.dataset.playerId = item.steam_id;
    node.querySelector(".card-title").textContent = item.latest_name || item.steam_id;
    node.querySelector(".card-summary").textContent =
      item.note || `最近出现：${formatDate(item.last_seen_at)}`;
    node.querySelector(".badge").textContent = item.tag || "未标记";
    node.addEventListener("click", () => selectPlayer(item.steam_id));
    list.appendChild(node);
  }
}

function renderTagOptions() {
  const select = document.querySelector("#tag-select");
  const currentValue = select.value;
  select.innerHTML = `<option value="">未设置</option>`;
  for (const item of state.tags) {
    const option = document.createElement("option");
    option.value = item.tag;
    option.textContent = `${item.tag} · P${item.priority}`;
    select.appendChild(option);
  }
  select.value = currentValue;
}

async function selectPlayer(playerId) {
  state.selectedPlayerId = playerId;
  highlightSelected(playerId);
  const data = await api(`/players/${encodeURIComponent(playerId)}/history-summary?limit=8`);
  renderDetail(data);
}

function highlightSelected(playerId) {
  for (const node of document.querySelectorAll("[data-player-id]")) {
    node.classList.toggle("active", node.dataset.playerId === playerId);
  }
}

function renderDetail(data) {
  const player = data.player;
  const summary = data.summary;
  document.querySelector("#detail-title").textContent = player.latest_name || player.steam_id;
  document.querySelector("#detail-meta").textContent = player.steam_id;
  document.querySelector("#tag-select").value = player.tag || "";
  document.querySelector("#note-input").value = player.note || "";
  document.querySelector("#detail-summary").classList.remove("empty");
  document.querySelector("#detail-summary").textContent =
    `累计 ${summary.encounter_count} 次，${summary.teammate_count} 次队友 / ${summary.opponent_count} 次对手，` +
    `${summary.win_count} 胜 ${summary.lose_count} 负。最近一次：${formatRelation(summary.last_same_team)}，` +
    `${summary.last_player_hero_name || heroFallback(summary.last_player_hero_id)}，` +
    `${formatResult(summary.last_result)}。`;

  const history = document.querySelector("#detail-history");
  history.innerHTML = "";
  for (const item of data.recent_encounters || []) {
    const card = document.createElement("article");
    card.className = "card";
    const playerHero = heroFallback(item.player_hero_id, item.player_hero_name);
    const myHero = heroFallback(item.my_hero_id, item.my_hero_name);
    card.innerHTML = `
      <div class="card-top">
        <div>
          <h3 class="card-title">${item.match_id || "unknown match"}</h3>
          <p class="card-summary">${formatDate(item.played_at)} · ${formatRelation(item.same_team)} · 对方 ${playerHero} · 我方 ${myHero} · ${formatResult(item.result)}</p>
        </div>
        <span class="badge muted">${item.source || "unknown"}</span>
      </div>
    `;
    history.appendChild(card);
  }
}

async function savePlayerMeta(event) {
  event.preventDefault();
  if (!state.selectedPlayerId) return;
  const tag = document.querySelector("#tag-select").value || null;
  const note = document.querySelector("#note-input").value.trim() || null;
  await api(`/players/${encodeURIComponent(state.selectedPlayerId)}`, {
    method: "PATCH",
    body: JSON.stringify({ tag, note }),
  });
  await loadDashboard();
  await selectPlayer(state.selectedPlayerId);
}

function formatDate(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatRelation(value) {
  if (value === 1) return "队友";
  if (value === 0) return "对手";
  return "关系未知";
}

function formatResult(value) {
  if (value === "win") return "你赢了";
  if (value === "lose") return "你输了";
  return value || "结果未知";
}

function heroFallback(heroId, heroName) {
  if (heroName) return heroName;
  return "未知英雄";
}

document.querySelector("#refresh-all").addEventListener("click", () => loadDashboard());
document.querySelector("#reload-detail").addEventListener("click", () => {
  if (state.selectedPlayerId) {
    selectPlayer(state.selectedPlayerId);
  }
});
document.querySelector("#player-form").addEventListener("submit", savePlayerMeta);

loadDashboard().catch((error) => {
  console.error(error);
  document.querySelector("#alert-headline").textContent = `加载失败: ${error.message}`;
});
