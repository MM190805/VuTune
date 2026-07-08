/* ======================================================
   VuTune Dashboard — app.js
   Live polling, controls, room management
   ====================================================== */

const POLL_MS = 2500;
let lastStatus = null;

// ── Utility ──────────────────────────────────────────────────────────

function toast(msg, duration = 2800) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), duration);
}

function feedback(elId, msg, type = 'ok') {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = msg;
  el.className = `feedback ${type}`;
  setTimeout(() => { el.textContent = ''; el.className = 'feedback'; }, 4000);
}

function fmtDuration(secs) {
  if (!secs) return '';
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

async function api(path, method = 'GET', body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  return res.json();
}

// ── Status polling ────────────────────────────────────────────────────

async function poll() {
  try {
    const s = await api('/api/status');
    renderStatus(s);
    lastStatus = s;
  } catch (e) {
    setOffline();
  }
}

function setOffline() {
  document.getElementById('statusDot').className = 'status-dot offline';
  document.getElementById('statusText').textContent = 'Offline';
}

function renderStatus(s) {
  // Nav status
  const dot = document.getElementById('statusDot');
  const txt = document.getElementById('statusText');
  const userEl = document.getElementById('botUser');

  if (s.logged_in) {
    dot.className = 'status-dot online';
    txt.textContent = 'Online';
    userEl.textContent = `@${s.bot_username}`;
  } else {
    dot.className = 'status-dot offline';
    txt.textContent = 'Not logged in';
    userEl.textContent = '';
  }

  // Player
  renderPlayer(s);
  renderQueue(s.queue || []);
  renderRooms(s.rooms || []);
}

// ── Player ────────────────────────────────────────────────────────────

function renderPlayer(s) {
  const titleEl    = document.getElementById('songTitle');
  const artistEl   = document.getElementById('songArtist');
  const albumImg   = document.getElementById('albumImg');
  const albumPh    = document.getElementById('albumPlaceholder');
  const spinRing   = document.getElementById('spinRing');
  const visualizer = document.getElementById('visualizer');

  if (s.is_playing && s.current_song) {
    const song = s.current_song;
    titleEl.textContent  = song.title || 'Unknown';
    artistEl.textContent = song.uploader || '—';

    if (song.thumbnail) {
      albumImg.src = song.thumbnail;
      albumImg.style.display = 'block';
      albumPh.style.display  = 'none';
    } else {
      albumImg.style.display = 'none';
      albumPh.style.display  = 'flex';
    }

    spinRing.classList.add('active');
    visualizer.classList.add('playing');
  } else {
    titleEl.textContent  = 'Nothing playing';
    artistEl.textContent = '—';
    albumImg.style.display = 'none';
    albumPh.style.display  = 'flex';
    spinRing.classList.remove('active');
    visualizer.classList.remove('playing');
  }
}

// ── Queue ─────────────────────────────────────────────────────────────

function renderQueue(queue) {
  const list  = document.getElementById('queueList');
  const count = document.getElementById('queueCount');

  count.textContent = `${queue.length} song${queue.length !== 1 ? 's' : ''}`;

  if (!queue.length) {
    list.innerHTML = '<div class="queue-empty">Queue is empty — add a song!</div>';
    return;
  }

  list.innerHTML = queue.map((s, i) => `
    <div class="queue-item" id="qi-${i+1}">
      <span class="queue-num">${i + 1}</span>
      ${s.thumbnail
        ? `<img class="queue-thumb" src="${s.thumbnail}" alt="" loading="lazy" />`
        : `<div class="queue-thumb"></div>`}
      <div class="queue-info">
        <div class="queue-title" title="${escHtml(s.title)}">${escHtml(s.title)}</div>
        <div class="queue-up">${escHtml(s.uploader || '')}${s.duration ? ' · ' + fmtDuration(s.duration) : ''}</div>
      </div>
      <button class="queue-del" title="Remove" onclick="removeFromQueue(${i+1})">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" fill="none"/>
        </svg>
      </button>
    </div>
  `).join('');
}

// ── Rooms ─────────────────────────────────────────────────────────────

function renderRooms(rooms) {
  const list  = document.getElementById('roomList');
  const count = document.getElementById('roomCount');

  count.textContent = `${rooms.length} room${rooms.length !== 1 ? 's' : ''}`;

  if (!rooms.length) {
    list.innerHTML = '<div class="queue-empty">No rooms added yet.</div>';
    return;
  }

  list.innerHTML = rooms.map(r => `
    <div class="room-item">
      <div class="room-dot ${r.active ? 'active' : ''}"></div>
      <div class="room-info">
        <div class="room-name">${escHtml(r.name)}</div>
        <div class="room-id">ID: ${escHtml(r.id)}</div>
      </div>
      <button class="room-del" title="Remove bot from room" onclick="removeRoom('${escHtml(r.id)}')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" fill="none"/>
        </svg>
      </button>
    </div>
  `).join('');
}

// ── Controls ──────────────────────────────────────────────────────────

async function apiSkip() {
  const r = await api('/api/skip', 'POST');
  toast(r.now_playing ? `⏭️ Now playing: ${r.now_playing}` : '⏭️ Skipped — queue empty');
  poll();
}

async function apiStop() {
  await api('/api/stop', 'POST');
  toast('⏹️ Music stopped');
  poll();
}

async function apiClearQueue() {
  await api('/api/queue', 'DELETE');
  toast('🗑️ Queue cleared');
  poll();
}

async function removeFromQueue(index) {
  const r = await api(`/api/queue/${index}`, 'DELETE');
  if (r.success) toast(`🗑️ Removed: ${r.removed}`);
  poll();
}

// ── Dashboard play / add ──────────────────────────────────────────────

async function dashPlay() {
  const q = document.getElementById('dashPlayInput').value.trim();
  if (!q) return;
  document.getElementById('dashPlayInput').value = '';
  feedback('playFeedback', `🔍 Searching: ${q}...`);
  const r = await api('/api/play', 'POST', { query: q });
  feedback('playFeedback', r.success ? `🎵 Playing: ${q}` : `❌ ${r.error || 'Not found'}`,
           r.success ? 'ok' : 'err');
  setTimeout(poll, 3000);
}

async function dashAdd() {
  const q = document.getElementById('dashAddInput').value.trim();
  if (!q) return;
  document.getElementById('dashAddInput').value = '';
  feedback('playFeedback', `🔍 Adding: ${q}...`);
  const r = await api('/api/add', 'POST', { query: q });
  feedback('playFeedback', r.success ? `✅ Added to queue: ${q}` : `❌ ${r.error || 'Not found'}`,
           r.success ? 'ok' : 'err');
  setTimeout(poll, 3000);
}

// Enter key shortcuts
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('dashPlayInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') dashPlay();
  });
  document.getElementById('dashAddInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') dashAdd();
  });
  document.getElementById('roomIdInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') addRoom();
  });
});

// ── Room management ───────────────────────────────────────────────────

async function addRoom() {
  const idVal   = document.getElementById('roomIdInput').value.trim();
  const nameVal = document.getElementById('roomNameInput').value.trim();
  if (!idVal) { feedback('roomFeedback', '❌ Room ID is required', 'err'); return; }

  feedback('roomFeedback', '⏳ Adding room...');
  const r = await api('/api/rooms', 'POST', {
    room_id:   idVal,
    room_name: nameVal || `Room ${idVal}`,
  });

  if (r.success) {
    document.getElementById('roomIdInput').value   = '';
    document.getElementById('roomNameInput').value = '';
    feedback('roomFeedback', `✅ Bot joined room ${idVal}`);
    toast(`🤖 VuTune joined room: ${nameVal || idVal}`);
    poll();
  } else {
    feedback('roomFeedback', `❌ ${r.error || 'Could not add room'}`, 'err');
  }
}

async function removeRoom(roomId) {
  if (!confirm(`Remove bot from room ${roomId}?`)) return;
  const r = await api(`/api/rooms/${roomId}`, 'DELETE');
  if (r.success) { toast('👋 Bot left the room'); poll(); }
}

// ── Settings ──────────────────────────────────────────────────────────

async function loadSettings() {
  try {
    const s = await api('/api/settings');
    document.getElementById('anyoneToggle').checked = s.anyone_can_use;
    document.getElementById('allowedUsersInput').value = (s.allowed_users || []).join(', ');
    document.getElementById('streamUrl').textContent = s.stream_url || 'https://vutune.duckdns.org/stream';
    toggleAllowedRow(!s.anyone_can_use);
  } catch (_) {}
}

document.getElementById('anyoneToggle').addEventListener('change', function () {
  toggleAllowedRow(!this.checked);
  saveSettings();
});

function toggleAllowedRow(show) {
  document.getElementById('allowedUsersRow').style.display = show ? 'flex' : 'none';
}

async function saveSettings() {
  const anyone = document.getElementById('anyoneToggle').checked;
  const usersRaw = document.getElementById('allowedUsersInput').value;
  const users = usersRaw.split(',').map(u => u.trim()).filter(Boolean);
  await api('/api/settings', 'POST', { anyone_can_use: anyone, allowed_users: users });
}

function copyStream() {
  const url = document.getElementById('streamUrl').textContent;
  navigator.clipboard.writeText(url).then(() => toast('📋 Stream URL copied!'));
}

// ── Helpers ───────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Boot ──────────────────────────────────────────────────────────────

(async function init() {
  await loadSettings();
  await poll();
  setInterval(poll, POLL_MS);
})();
