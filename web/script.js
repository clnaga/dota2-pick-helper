// ===== State =====
let state = { heroDatabase: [] };
let heroMap = {};
let selectedHeroId = null;
let attrFilter = 'all';
let searchQuery = '';

// ===== Attribute colors =====
const attrColors = { str: '#f66', agi: '#6f6', int: '#68f', uni: '#d8f' };

// ===== SSE — push-based, 1 persistent connection =====
const es = new EventSource('/api/stream');
es.onmessage = function(e) {
  try {
    const data = JSON.parse(e.data);
    if (data) {
      state = data;
      if (data.heroDatabase) {
        heroMap = {};
        for (const h of data.heroDatabase) heroMap[h.id] = h;
      }
      render();
    }
  } catch(_) {}
};
// No onerror — browser handles reconnection natively

// ===== Render =====
function render() {
  renderBans();
  renderAllies();
  renderEnemies();
  renderTurn();
  renderPicks();
  renderBanSuggestions();
  renderAllySuggestions();
  renderTestControls();
  renderHeroGrid();
  renderStatus();
}

function shortName(info) {
  if (!info) return '?';
  return info.localizedNameZh || info.localizedName;
}

function heroIconHtml(heroId, isBanned, removable) {
  const info = heroMap[heroId];
  const name = info ? (info.localizedNameZh || info.localizedName) : '#' + heroId;
  const attr = info ? (info.attribute || 'all') : 'all';
  const banClass = isBanned ? ' banned' : '';
  const remClass = removable ? ' removable' : '';
  const short = shortName(info);
  return `<div class="hero-icon${banClass}${remClass}" data-id="${heroId}" data-action="${removable ? 'remove' : ''}" style="background:${attrColors[attr] || '#555'}22; border:1px solid ${attrColors[attr] || '#555'}44">
    <span class="hero-label" style="color:${attrColors[attr] || '#888'}">${short}</span>
    <div class="tooltip">${name}${isBanned ? ' [BANNED]' : ''}</div>
  </div>`;
}

function renderBans() {
  const list = document.getElementById('bans-list');
  const bans = state.bannedHeroes || [];
  document.getElementById('ban-count').textContent = bans.length;
  list.innerHTML = bans.map(h => heroIconHtml(h.heroId, true, true)).join('');
}

function renderAllies() {
  const list = document.getElementById('allies-list');
  const allies = state.allyHeroes || [];
  document.getElementById('ally-count').textContent = allies.length + '/5';
  list.innerHTML = allies.map(h => heroIconHtml(h.heroId, false, true)).join('');
}

function renderEnemies() {
  const list = document.getElementById('enemies-list');
  const enemies = state.enemyHeroes || [];
  document.getElementById('enemy-count').textContent = enemies.length + '/5';
  list.innerHTML = enemies.map(h => heroIconHtml(h.heroId, false, true)).join('');
}

function renderTurn() {
  const el = document.getElementById('turn-indicator');
  const act = state.draftActivity;
  if (act && act.active) {
    const team = act.actingTeam === (state.teamId || 0) ? 'Your team' : (act.actingTeam === 2 ? 'Radiant' : 'Dire');
    const action = act.action === 'pick' ? 'picking' : 'banning';
    el.textContent = '\u25b6 ' + team + ' is ' + action;
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

function renderPicks() {
  const list = document.getElementById('picks-list');
  const picks = state.suggestions || [];
  if (!picks.length) {
    list.innerHTML = '<div class="suggestion-row" style="color:#666;font-size:12px;">添加敌方查看克制英雄</div>';
    return;
  }
  list.innerHTML = picks.map(s => suggestionRow(s, false)).join('');
}

function renderBanSuggestions() {
  const list = document.getElementById('bans-suggestion-list');
  const bans = state.banSuggestions || [];
  if (!bans.length) {
    list.innerHTML = '<div class="suggestion-row" style="color:#666;font-size:12px;">添加友方查看禁用建议</div>';
    return;
  }
  list.innerHTML = bans.map(s => suggestionRow(s, true)).join('');
}

function renderAllySuggestions() {
  const list = document.getElementById('allies-suggestion-list');
  const allies = state.allySuggestions || [];
  if (!allies.length) {
    list.innerHTML = '<div class="suggestion-row" style="color:#666;font-size:12px;">添加友方查看搭配英雄</div>';
    return;
  }
  list.innerHTML = allies.map(s => suggestionRow(s, false, true)).join('');
}

function formatHeroDisplayName(info, heroId) {
  if (!info) return '#' + heroId;
  const zh = info.localizedNameZh;
  const en = info.localizedName;
  if (zh && en) return zh + '（' + en + '）';
  return zh || en || '#' + heroId;
}

function suggestionRow(sug, isBan, isAlly) {
  const info = heroMap[sug.heroId];
  const name = formatHeroDisplayName(info, sug.heroId);
  const attr = info ? (info.attribute || 'all') : 'all';
  const wr = (sug.winRate || 50).toFixed(0);
  const sc = (sug.score || 0).toFixed(1);
  const scColor = isBan ? '#f66' : isAlly ? '#3c8' : (sug.score >= 0 ? '#3c3' : '#e44');
  return '<div class="suggestion-row">' +
    '<div class="hero-dot" style="background:' + (attrColors[attr] || '#555') + '"></div>' +
    '<span class="name-cell ' + attr + '">' + name + '</span>' +
    '<span class="stat-cell"><span class="wr-cell">' + wr + '%</span>' +
    '<span class="score-cell ' + (sug.score >= 0 ? 'pos' : 'neg') + '" style="color:' + scColor + '">' + (sc >= 0 ? '+' : '') + sc + '</span></span>' +
  '</div>';
}

function renderStatus() {
  const el = document.getElementById('status-text');
  const phase = state.phase || 'none';
  const time = state.matchTime || 0;
  el.textContent = 'Phase: ' + phase + ' | ' + Math.floor(time / 60) + ':' + (time % 60).toString().padStart(2, '0');
}

// ===== Manual hero controls (always available) =====
function renderTestControls() {
  document.getElementById('test-controls').style.display = 'block';
  document.querySelectorAll('.remove-hint').forEach(h => h.style.display = 'inline');
}

function renderHeroGrid() {
  const grid = document.getElementById('hero-grid');
  const heroes = state.heroDatabase || [];
  const taken = new Set();
  for (const h of (state.bannedHeroes || [])) taken.add(h.heroId);
  for (const h of (state.allyHeroes || [])) taken.add(h.heroId);
  for (const h of (state.enemyHeroes || [])) taken.add(h.heroId);

  const filtered = heroes.filter(h => {
    if (taken.has(h.id)) return false;
    if (attrFilter !== 'all' && h.attribute !== attrFilter) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const cn = (h.localizedNameZh || '').toLowerCase();
      const en = (h.localizedName || '').toLowerCase();
      if (!cn.includes(q) && !en.includes(q)) return false;
    }
    return true;
  });

  grid.innerHTML = filtered.map(h => {
    const name = h.localizedNameZh || h.localizedName;
    const sel = selectedHeroId === h.id ? ' selected' : '';
    return '<div class="hero-card ' + h.attribute + sel + '" data-hero="' + h.id + '">' + name + '</div>';
  }).join('');
}

function selectHero(id) {
  selectedHeroId = (selectedHeroId === id) ? null : id;
  renderHeroGrid();
}

async function addSelectedHero(type) {
  if (!selectedHeroId) return;
  const actions = { enemy: 'enemy_add', ally: 'ally_add', ban: 'ban' };
  const action = actions[type];
  if (!action) return;
  fetch('/api/edit/' + action + '?id=' + selectedHeroId);
  selectedHeroId = null;
  // SSE pushes the update — no need to poll
}

async function removeItem(heroId) {
  const inEnemy = (state.enemyHeroes || []).some(h => h.heroId === heroId);
  const inAlly = (state.allyHeroes || []).some(h => h.heroId === heroId);
  const inBan = (state.bannedHeroes || []).some(h => h.heroId === heroId);
  
  if (inBan) fetch('/api/edit/unban?id=' + heroId);
  else if (inEnemy) fetch('/api/edit/enemy_remove?id=' + heroId);
  else if (inAlly) fetch('/api/edit/ally_remove?id=' + heroId);
}

async function clearTestData() {
  fetch('/api/edit/clear');
}

function setAttrFilter(attr) {
  attrFilter = attr;
  document.querySelectorAll('.attr-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector('.attr-btn[data-attr="' + attr + '"]');
  if (btn) btn.classList.add('active');
  renderHeroGrid();
}

// Click delegation for hero grid
document.addEventListener('click', function(e) {
  const card = e.target.closest('.hero-card[data-hero]');
  if (card) {
    selectHero(parseInt(card.dataset.hero));
  }
  const icon = e.target.closest('.hero-icon[data-action="remove"]');
  if (icon) {
    removeItem(parseInt(icon.dataset.id));
  }
});

function filterHeroes() {
  searchQuery = document.getElementById('hero-search').value;
  renderHeroGrid();
}


