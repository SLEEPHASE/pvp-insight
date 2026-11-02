(function () {
  'use strict';

  var RANK_COLORS = {
    'D': '#7d8797', 'D+': '#94a3b8', 'C': '#4ade80', 'C+': '#22c55e',
    'B': '#38bdf8', 'B+': '#818cf8', 'A': '#c084fc', 'A+': '#fb7185',
    'S': '#f5b544', '未定级': '#6b7280'
  };
  var state = null;
  var stateTimer = null;
  var loginTimer = null;
  var polling = false;
  var activeLogin = false;
  var lastSignature = null;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
  function fmt(value, digits) {
    if (value === null || value === undefined || value === '') return '—';
    var number = Number(value);
    if (!isFinite(number)) return '—';
    return number.toFixed(digits == null ? 0 : digits);
  }
  function ratingColor(value) {
    if (value === null || value === undefined) return '#6f7a8a';
    if (value >= 1.30) return '#ff6b57';
    if (value >= 1.18) return '#ff8a2b';
    if (value >= 1.08) return '#f5c451';
    if (value >= 1.00) return '#6ee7b7';
    if (value >= 0.90) return '#93a4ba';
    return '#7c8a9a';
  }
  function rankColor(label) { return RANK_COLORS[label] || '#8a95a6'; }
  function rankLabel(player) {
    var text = String(player.rank || '—');
    return text.split(' ')[0] || '—';
  }
  function rankStars(player) {
    if (player.stars_known && player.rank && String(player.rank).indexOf('★') >= 0) {
      var match = String(player.rank).match(/★\s*(\d+)/);
      if (match) return '★' + match[1];
      return '★' + (player.stars || 0);
    }
    return '';
  }
  function initial(name, sid) {
    var text = String(name || '').trim();
    if (text) return text.slice(0, 1).toUpperCase();
    return String(sid || '?').slice(-2);
  }

  function toast(message) {
    var node = $('toast');
    node.textContent = message;
    node.classList.remove('hidden');
    clearTimeout(node._timer);
    node._timer = setTimeout(function () { node.classList.add('hidden'); }, 2600);
  }

  async function api(path, options) {
    options = options || {};
    var headers = options.headers || {};
    if (options.body) headers['Content-Type'] = 'application/json';
    var response = await fetch(path, {
      method: options.method || 'GET',
      headers: headers,
      body: options.body || undefined,
      cache: 'no-store'
    });
    var data;
    try { data = await response.json(); } catch (e) { data = {}; }
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }

  /* ------------------------------------------------------------ 状态拉取 */

  async function pollState() {
    if (polling || document.hidden) return;
    polling = true;
    try {
      var next = await api('/api/state');
      var account = next.account || {};
      var signature = [
        next.revision,
        next.logged_in ? 1 : 0,
        next.steam_id,
        account.nickname || '',
        next.auth_error || ''
      ].join('|');
      state = next;
      if (signature !== lastSignature) {
        lastSignature = signature;
        render();
      }
    } catch (error) {
      $('statusPill').className = 'status-pill error';
      $('statusText').textContent = '本地服务已断开';
    } finally {
      polling = false;
    }
  }

  function scheduleState() {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(async function () {
      await pollState();
      scheduleState();
    }, 2000);
  }

  function statusClass(value) {
    return ['live', 'waiting', 'connecting', 'reconnecting', 'error', 'ended'].indexOf(value) >= 0
      ? value : 'waiting';
  }

  /* ------------------------------------------------------------ 渲染 */

  function render() {
    if (!state) return;
    var status = state.status || {};
    var pill = $('statusPill');
    var statusText = status.text || '等待中';
    var cls = statusClass(status.state);
    if (state.auth_error) { cls = 'error'; statusText = '登录已失效，请重新扫码'; }
    pill.className = 'status-pill ' + cls;
    $('statusText').textContent = statusText;

    var account = state.account || {};
    var loginBtn = $('loginBtn');
    var chip = $('accountChip');
    if (state.logged_in) {
      loginBtn.classList.add('hidden');
      chip.classList.remove('hidden');
      $('accountName').textContent = account.nickname || '已登录';
    } else {
      loginBtn.classList.remove('hidden');
      chip.classList.add('hidden');
    }

    $('footRight').textContent = '本地只读 · v' + ((state.server && state.server.version) || '1.0');

    if (state.match) {
      $('emptyView').classList.add('hidden');
      $('matchView').classList.remove('hidden');
      renderMatch();
    } else {
      $('matchView').classList.add('hidden');
      $('emptyView').classList.remove('hidden');
      renderEmpty();
    }
  }

  function renderEmpty() {
    var sid = state.steam_id || '';
    var hint = $('steamHint');
    var url = location.origin;
    if (sid) {
      hint.innerHTML = '正在监控 <code>' + esc(sid) + '</code> · 进入对局后自动识别';
    } else {
      hint.innerHTML = '未读取到 Steam 账号，请先在设置中填写 SteamID';
    }
    var actions = $('emptyActions');
    actions.innerHTML = '';
    var overlay = document.createElement('div');
    overlay.className = 'steam-hint';
    overlay.innerHTML = 'Steam 覆盖层浏览器打开 <code>' + esc(url) + '</code>';
    var copy = document.createElement('button');
    copy.className = 'btn'; copy.textContent = '复制地址';
    copy.onclick = function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function () { toast('地址已复制'); });
      } else {
        toast(url);
      }
    };
    overlay.appendChild(copy);
    actions.appendChild(overlay);
    if (!sid) {
      var setBtn = document.createElement('button');
      setBtn.className = 'btn primary'; setBtn.textContent = '设置 SteamID';
      setBtn.onclick = openSettings;
      actions.appendChild(setBtn);
    }
  }

  function renderMatch() {
    var match = state.match;
    var teams = state.teams || {};
    var own = teams.own || { players: [] };
    var opp = teams.opp || { players: [] };
    var summary = state.summary || {};
    var ended = !!match.ended;

    $('mapName').textContent = match.map || '未知地图';
    $('matchId').textContent = (match.id || '实时对局') + '  ·  完美平台';
    $('ctScore').textContent = match.ct_score == null ? 0 : match.ct_score;
    $('tScore').textContent = match.t_score == null ? 0 : match.t_score;
    $('roundLabel').textContent = ended ? '已结束' : ('第 ' + (match.round || 1) + ' 回合');
    $('playerCount').textContent = (own.players.length + opp.players.length) + ' 名玩家';
    $('liveDot').classList.toggle('ended', ended);
    $('matchState').textContent = ended ? 'ENDED' : 'LIVE';
    document.querySelector('.live-line').classList.toggle('ended', ended);

    if (match.ct_half != null && match.t_half != null) {
      $('halfScore').textContent = '半场 ' + match.ct_half + ' : ' + match.t_half;
    } else {
      $('halfScore').textContent = '';
    }

    $('ownTitle').textContent = own.title || '自己家';
    $('oppTitle').textContent = opp.title || '对手';
    setSide($('ownSide'), own.side || 'CT');
    setSide($('oppSide'), opp.side || 'T');
    $('ownAvg').textContent = avgText(summary.own_rating, summary.own_score);
    $('oppAvg').textContent = avgText(summary.opp_rating, summary.opp_score);

    renderList($('ownList'), own.players || []);
    renderList($('oppList'), opp.players || []);

    var pct = clamp(Number(summary.win_pct || 50), 5, 95);
    $('winPct').textContent = pct.toFixed(0) + '%';
    $('winPct').style.color = pct >= 50 ? '#ff8a2b' : '#ff5d6c';
    $('probFill').style.width = pct + '%';
    $('probMark').style.left = pct + '%';
    $('summaryDetail').textContent = summary.own_rating != null && summary.opp_rating != null
      ? '己方均 Rating ' + fmt(summary.own_rating, 2) + '  ·  对方 ' + fmt(summary.opp_rating, 2)
      : '仅根据段位分估算，仅供参考';
  }

  function setSide(node, side) {
    side = String(side || 'CT').toUpperCase();
    node.textContent = side;
    node.className = 'side ' + (side === 'CT' ? 'ct' : 't');
  }

  function avgText(rating, score) {
    var parts = [];
    if (rating != null) parts.push('均 Rating ' + fmt(rating, 2));
    if (score != null) parts.push('均分 ' + Math.round(score));
    return parts.length ? parts.join('  ·  ') : '均分 —';
  }

  function renderList(container, players) {
    if (!players.length) {
      container.innerHTML = '<div class="empty-row">等待玩家数据…</div>';
      container._rows = new Map();
      return;
    }
    if (container.querySelector(':scope > .empty-row')) {
      container.innerHTML = '';
      container._rows = new Map();
    }
    if (!container._rows) container._rows = new Map();
    var seen = new Set();
    for (var i = 0; i < players.length; i++) {
      var player = players[i];
      var sid = String(player.sid || '');
      var row = container._rows.get(sid);
      if (!row) {
        row = createRow();
        container._rows.set(sid, row);
      }
      updateRow(row, player);
      if (container.children[i] !== row) {
        container.insertBefore(row, container.children[i] || null);
      }
      seen.add(sid);
    }
    container._rows.forEach(function (row, sid) {
      if (!seen.has(sid)) {
        row.remove();
        container._rows.delete(sid);
      }
    });
  }

  function createRow() {
    var row = document.createElement('article');
    row.className = 'player';
    row.innerHTML =
      '<div class="avatar-wrap">' +
        '<div class="avatar"><span class="avatar-fallback">?</span>' +
          '<img alt="" decoding="async" referrerpolicy="no-referrer"></div>' +
        '<span class="rank-chip">—</span>' +
      '</div>' +
      '<div class="who">' +
        '<div class="name">加载中…</div>' +
        '<div class="meta">正在读取平台数据</div>' +
        '<div class="peak"></div>' +
      '</div>' +
      '<div class="kda"><b>— / — / —</b><span>ADR — · RT —</span></div>' +
      '<div class="rating"><b>—</b><span>RATING</span></div>';
    var wrap = row.querySelector('.avatar-wrap');
    var image = row.querySelector('.avatar img');
    image.addEventListener('load', function () { wrap.querySelector('.avatar').classList.add('has-img'); });
    image.addEventListener('error', function () {
      wrap.querySelector('.avatar').classList.remove('has-img');
      image.removeAttribute('src');
      delete image.dataset.avatar;
    });
    return row;
  }

  function updateRow(row, player) {
    var sid = String(player.sid || '');
    var isSelf = sid && sid === String(state.steam_id || '');
    var label = rankLabel(player);
    var color = rankColor(label);
    var stars = rankStars(player);
    var loaded = !!player.loaded;
    var name = player.name || ('玩家 ' + sid.slice(-4));
    var rankText = player.rank || '—';
    if (label === 'S' && stars && rankText.indexOf('★') < 0) rankText = 'S ' + stars;
    var wins = player.win_rate == null ? '—' : Math.round(player.win_rate) + '%';
    var kd = player.kd == null ? '—' : fmt(player.kd, 2);
    var meta = loaded
      ? rankText + ' · ' + (player.matches || 0) + ' 场 · 胜率 ' + wins + ' · K/D ' + kd
      : '正在读取平台数据…';
    var peak = player.peak && player.peak !== '—'
      ? '最高 ' + player.peak
      : (state.logged_in ? '最高 暂无数据' : '最高 登录后可见');
    var rating = player.rating == null ? '—' : fmt(player.rating, 2);
    var rcolor = player.rating == null ? '#5d6a7c' : ratingColor(player.rating);
    var kda = (player.kills || 0) + ' / ' + (player.deaths || 0) + ' / ' + (player.assists || 0);
    var adr = player.adr == null ? '—' : Math.round(player.adr);
    var rt = player.rt == null ? '—' : fmt(player.rt, 2);
    var border = hexToRgba(color, 0.62);
    var chipText = label === 'S' && stars ? 'S ' + stars : label;

    row.classList.toggle('self', !!isSelf);
    row.dataset.sid = sid;
    row.title = name + '\nSteamID: ' + sid;
    var wrap = row.querySelector('.avatar-wrap');
    var avatar = row.querySelector('.avatar');
    var image = avatar.querySelector('img');
    var fallback = avatar.querySelector('.avatar-fallback');
    var chip = row.querySelector('.rank-chip');

    avatar.style.setProperty('--rank-border', border);
    chip.style.setProperty('--rc', color);
    chip.style.setProperty('--rank-border', border);
    chip.textContent = chipText;

    fallback.textContent = initial(name, sid);
    if (player.avatar) {
      if (image.dataset.avatar !== player.avatar) {
        image.dataset.avatar = player.avatar;
        avatar.classList.remove('has-img');
        image.src = '/api/avatar?sid=' + encodeURIComponent(sid) +
          '&v=' + encodeURIComponent(player.avatar.slice(-14));
      }
    } else if (image.getAttribute('src')) {
      avatar.classList.remove('has-img');
      image.removeAttribute('src');
      delete image.dataset.avatar;
    }

    row.querySelector('.name').textContent = name;
    row.querySelector('.meta').textContent = meta;
    row.querySelector('.peak').textContent = peak;
    row.querySelector('.kda b').textContent = kda;
    row.querySelector('.kda span').textContent = 'ADR ' + adr + ' · RT ' + rt;
    var ratingNode = row.querySelector('.rating b');
    ratingNode.textContent = rating;
    ratingNode.style.color = rcolor;
    row.querySelector('.rating span').textContent =
      player.rating == null ? (state.logged_in ? 'RATING' : '登录可见') : 'RATING';
  }

  function hexToRgba(hex, alpha) {
    var value = String(hex || '#8a95a6').replace('#', '');
    if (value.length === 3) value = value[0] + value[0] + value[1] + value[1] + value[2] + value[2];
    var number = parseInt(value, 16);
    return 'rgba(' + ((number >> 16) & 255) + ',' + ((number >> 8) & 255) + ',' + (number & 255) + ',' + alpha + ')';
  }

  /* ------------------------------------------------------------ 登录 */

  async function startLogin() {
    activeLogin = true;
    openModal('loginModal');
    $('qrImg').style.display = 'none';
    $('qrPlaceholder').style.display = 'block';
    $('qrPlaceholder').textContent = '正在申请二维码…';
    $('loginMsg').textContent = '正在连接登录服务器…';
    $('loginMsg').className = 'login-msg';
    try {
      var data = await api('/api/login/start', { method: 'POST', body: '{}' });
      renderLogin(data);
      pollLogin();
    } catch (error) {
      $('loginMsg').textContent = error.message || '登录启动失败';
      $('loginMsg').className = 'login-msg err';
    }
  }

  function pollLogin() {
    clearTimeout(loginTimer);
    loginTimer = setTimeout(async function () {
      if (!activeLogin) return;
      try {
        var data = await api('/api/login/status');
        renderLogin(data);
        if (data.status === 'success') {
          activeLogin = false;
          clearTimeout(loginTimer);
          setTimeout(function () {
            closeModal('loginModal', true);
            pollState();
          }, 700);
          return;
        }
        if (data.status === 'expired' || data.status === 'error' || data.status === 'idle') {
          return;
        }
      } catch (error) {
        $('loginMsg').textContent = '状态获取失败，请重试';
        $('loginMsg').className = 'login-msg err';
      }
      pollLogin();
    }, 1200);
  }

  function renderLogin(login) {
    var status = login && login.status || 'idle';
    var message = login && login.message || '';
    var qr = login && login.qr || '';
    if (qr) {
      $('qrImg').src = qr;
      $('qrImg').style.display = 'block';
      $('qrPlaceholder').style.display = 'none';
    }
    $('loginMsg').textContent = message || '请使用完美世界电竞 APP 扫码';
    $('loginMsg').className = 'login-msg';
    if (status === 'success') {
      $('loginMsg').textContent = '登录成功';
      $('loginMsg').className = 'login-msg ok';
      $('qrImg').style.display = 'none';
    } else if (status === 'scanned') {
      $('loginMsg').className = 'login-msg ok';
    } else if (status === 'expired' || status === 'error') {
      $('loginMsg').className = 'login-msg err';
      $('qrImg').style.display = 'none';
    }
  }

  async function cancelLogin() {
    activeLogin = false;
    clearTimeout(loginTimer);
    try { await api('/api/login/cancel', { method: 'POST', body: '{}' }); } catch (e) {}
  }

  /* ------------------------------------------------------------ 设置 */

  function openSettings() {
    $('steamInput').value = (state && state.steam_id) || '';
    renderAccount();
    openModal('settingsModal');
  }

  function renderAccount() {
    var box = $('accountBox');
    var logout = $('logoutBtn');
    if (state && state.logged_in) {
      box.innerHTML = '<b>已登录：' + esc((state.account && state.account.nickname) || '平台账号') + '</b>' +
        '<div class="muted">登录令牌只保存在本机服务端，使用 Windows DPAPI 加密，不会下发到浏览器。</div>';
      logout.classList.remove('hidden');
    } else {
      box.innerHTML = '<b style="color:#f5c451">未登录</b>' +
        '<div class="muted">Rating、最高段位与 S 段星数需要扫码登录后才能读取。</div>';
      logout.classList.add('hidden');
    }
  }

  async function saveSettings() {
    try {
      await api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({ steam_id: $('steamInput').value.trim() })
      });
      closeModal('settingsModal');
      toast('设置已保存');
      await pollState();
    } catch (error) {
      toast(error.message || '保存失败');
    }
  }

  async function logout() {
    try {
      await api('/api/logout', { method: 'POST', body: '{}' });
      toast('已退出登录');
      closeModal('settingsModal');
      await pollState();
    } catch (error) {
      toast(error.message || '退出失败');
    }
  }

  /* ------------------------------------------------------------ 弹层 */

  function openModal(id) {
    document.querySelectorAll('.modal').forEach(function (node) { node.classList.add('hidden'); });
    $(id).classList.remove('hidden');
  }

  function closeModal(id, keepLogin) {
    $(id).classList.add('hidden');
    if (id === 'loginModal' && !keepLogin) cancelLogin();
  }

  document.querySelectorAll('[data-close]').forEach(function (button) {
    button.addEventListener('click', function () {
      closeModal(button.getAttribute('data-close'));
    });
  });

  document.querySelectorAll('.modal').forEach(function (modal) {
    modal.addEventListener('mousedown', function (event) {
      if (event.target === modal) closeModal(modal.id);
    });
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      document.querySelectorAll('.modal:not(.hidden)').forEach(function (modal) {
        closeModal(modal.id);
      });
    }
  });

  /* ------------------------------------------------------------ 事件 */

  $('loginBtn').addEventListener('click', startLogin);
  $('accountChip').addEventListener('click', openSettings);
  $('refreshBtn').addEventListener('click', async function () {
    try {
      await api('/api/refresh', { method: 'POST', body: '{}' });
      toast('正在刷新玩家资料');
      await pollState();
    } catch (error) { toast(error.message || '刷新失败'); }
  });
  $('settingsBtn').addEventListener('click', openSettings);
  $('saveSettingsBtn').addEventListener('click', saveSettings);
  $('logoutBtn').addEventListener('click', logout);
  $('autoDetectBtn').addEventListener('click', async function () {
    try {
      var data = await api('/api/detect');
      if (data.steam_id) {
        $('steamInput').value = data.steam_id;
        toast('已读取 Steam 账号');
      } else {
        toast('未读取到 Steam 账号，请手动填写');
      }
    } catch (error) { toast(error.message || '检测失败'); }
  });
  $('safetyBtn').addEventListener('click', function () { openModal('safetyModal'); });
  $('qrRefreshBtn').addEventListener('click', startLogin);

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) pollState();
  });

  /* ------------------------------------------------------------ 启动 */

  pollState();
  scheduleState();

})();