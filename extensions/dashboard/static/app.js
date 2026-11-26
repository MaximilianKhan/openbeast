// OpenBeast status dashboard, v3: one page, hash routes (#/, #/serving, #/nodes, #/nodes/<name>,
// #/power, #/gate, #/services, #/tools, #/events, #/history). Read-only: it GETs /api/status and
// /api/cluster every 3 s for the header and the overview, and the shown detail page's own
// document (/api/serving, /api/node/<name>, /api/power, /api/history, /api/events, /api/alerts,
// /api/daily) at that page's pace. Every value from the server is written with textContent
// (never innerHTML), so a hostile model id or hostname renders as text. Lists (nodes, ports,
// services, tools, events, charts) are keyed: an existing row is updated, not rebuilt, so
// nothing flickers. A missing field shows as a dash, never as a made-up 0.
'use strict';
(function () {
  const POLL_MS = 3000;
  const STALE_MS = 10000;
  const FETCH_ABORT_MS = 8000;      // well above the server's worst case (GATHER_DEADLINE_S 3 s)
  const SPAN_S = 900;               // sparklines: the last 15 minutes
  const SVGNS = 'http://www.w3.org/2000/svg';
  const DASH = '—';

  // ------------------------------------------------------------ formatting (pure)
  const has = v => v !== null && v !== undefined && v !== '' && !(typeof v === 'number' && !isFinite(v));
  const isNum = v => typeof v === 'number' && isFinite(v);
  const int = v => isNum(v) ? Math.round(v).toLocaleString('en-US') : DASH;
  const pct = (v, d = 0) => isNum(v) ? v.toFixed(d) + '%' : DASH;
  const frac = (v, d = 0) => isNum(v) ? (v * 100).toFixed(d) + '%' : DASH;
  const gib = (mib, d = 1) => isNum(mib) ? (mib / 1024).toFixed(d) : DASH;
  const bytes = b => {
    if (!isNum(b)) return DASH;
    const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let i = 0;
    while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; }
    return (b >= 100 || i === 0 ? b.toFixed(0) : b.toFixed(1)) + ' ' + u[i];
  };
  const bps = v => {
    if (!isNum(v)) return DASH;
    const u = ['b/s', 'kb/s', 'Mb/s', 'Gb/s', 'Tb/s'];
    let i = 0;
    while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
    return (v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + u[i];
  };
  const dur = s => {
    if (!isNum(s) || s < 0) return DASH;
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return d + 'd ' + h + 'h';
    if (h) return h + 'h ' + m + 'm';
    if (m) return m + 'm';
    return Math.floor(s) + 's';
  };
  const toks = v => !isNum(v) ? DASH : v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v >= 100 ? v.toFixed(0) : v.toFixed(1);
  const ctxLabel = v => !isNum(v) ? DASH : v >= 1048576 && v % 1048576 === 0 ? (v / 1048576) + 'M'
    : v >= 1024 && v % 1024 === 0 ? (v / 1024) + 'K' : int(v);
  const str = (v, max = 120) => has(v) ? String(v).slice(0, max) : DASH;
  // Watts: one decimal under 100 W, whole watts above; kW past 10 kW.
  const watts = v => !isNum(v) ? DASH : v >= 10000 ? (v / 1000).toFixed(1) + ' kW' : (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + ' W';
  const wnum = v => !isNum(v) ? DASH : v >= 100 ? v.toFixed(0) : v.toFixed(1);
  // Energy: Wh below 1 kWh, kWh above (and always kWh when asked).
  const energy = (wh, kwh) => !isNum(wh) ? DASH : (kwh || wh >= 1000) ? (wh / 1000).toFixed(wh >= 10000 ? 1 : 2) + ' kWh'
    : (wh >= 100 ? wh.toFixed(0) : wh >= 10 ? wh.toFixed(1) : wh.toFixed(2)) + ' Wh';
  const money = (v, cur) => !isNum(v) ? DASH : (/^[A-Za-z]/.test(cur || '') ? v.toFixed(2) + ' ' + cur : (cur || '$') + v.toFixed(2));
  // Latency: ms under a second, seconds under 100 s, then a duration.
  const secs = v => !isNum(v) ? DASH : v < 1 ? (v * 1000).toFixed(v < 0.1 ? 1 : 0) + ' ms' : v < 100 ? v.toFixed(v < 10 ? 2 : 1) + ' s' : dur(v);
  // Big counts: 12,345 / 123.4k / 4.53M / 1.20B.
  const big = v => !isNum(v) ? DASH : v >= 1e9 ? (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? (v / 1e6).toFixed(v >= 1e8 ? 0 : v >= 1e7 ? 1 : 2) + 'M'
    : v >= 1e5 ? (v / 1e3).toFixed(0) + 'k' : v >= 1e4 ? (v / 1e3).toFixed(1) + 'k' : int(v);
  const mhz = v => isNum(v) ? Math.round(v) + ' MHz' : DASH;
  const degC = v => isNum(v) ? Math.round(v) + ' °C' : DASH;
  const hhmm = t => isNum(t) ? new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false }) : DASH;
  const hhmmss = t => isNum(t) ? new Date(t * 1000).toLocaleTimeString('en-GB', { hour12: false }) : DASH;
  const dayLabel = t => isNum(t) ? new Date(t * 1000).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }) : DASH;
  const when = t => {
    if (!isNum(t)) return DASH;
    const now = Date.now() / 1000;
    return now - t < 86400 * 0.9 && new Date(t * 1000).getDate() === new Date().getDate() ? hhmm(t) : dayLabel(t) + ' ' + hhmm(t);
  };
  const ago = s => !isNum(s) ? DASH : s < 5 ? 'just now' : dur(s) + ' ago';
  const unit = (v, f, u) => isNum(v) ? f(v) + u : DASH;
  const WIN_LABEL = { '1m': '1 min', '5m': '5 min', '15m': '15 min', '1h': '1 hour', '24h': '24 hours', '6h': '6 hours', '7d': '7 days' };
  const WIN_SHORT = { '1m': '1 min', '5m': '5 min', '15m': '15 min', '1h': '1 h', '24h': '24 h', '6h': '6 h', '7d': '7 d' };
  // Chart x ticks per window: [number of intervals] (15 m: every 5 min; 1 h: 15 min; 6 h: 1 h; 24 h: 6 h).
  const TICKS = { '15m': 3, '1h': 4, '6h': 6, '24h': 4, '7d': 7 };
  const ARROW = { rising: '↑', falling: '↓', steady: '→' };
  // The fields one node card shows, as plain strings.
  function nodeFields(n) {
    const g = n.gpu || null, c = n.cpu || {}, r = n.ram || null, k = n.disk || null;
    const f = {};
    f.name = str(n.name, 64);
    const role = n.role === 'head' ? 'head' : 'peer';
    f.sub = [role + (isNum(n.rank) ? ' · rank ' + n.rank : ''), has(n.hostname) ? str(n.hostname, 64) : null]
      .filter(Boolean).join(' · ');
    f.state = n.state === 'up' ? 'up' : n.state === 'unreachable' ? 'unreachable' : 'pending';
    f.stateText = f.state === 'up' ? 'online' : f.state === 'unreachable' ? 'unreachable' : 'connecting';
    f.error = f.state === 'unreachable'
      ? str(n.error || 'no answer', 160) + (isNum(n.last_ok_age_s) ? ' · last reading ' + dur(n.last_ok_age_s) + ' ago' : '')
      : '';
    if (g) {
      f.gpu = (g.count > 1 ? g.count + '× ' : '') + str(g.name, 80);
      f.memLabel = g.unified ? 'unified memory' : 'VRAM';
      f.memKind = g.unified ? 'unified' : 'VRAM';
      f.mem = has(g.mem_total_mib) ? gib(g.mem_used_mib) + ' / ' + gib(g.mem_total_mib, 0) + ' GiB' + (g.unified ? ' (CPU + GPU)' : '')
        : 'not reported';
      f.memPct = g.mem_pct;
      f.util = pct(g.util_pct);
      f.temp = isNum(g.temp_c) ? g.temp_c + ' °C' : DASH;
      const p = n.power || {};
      const now = isNum(p.gpu_w) ? p.gpu_w : g.power_w;
      f.power = isNum(now) ? watts(now) + (isNum(g.power_limit_w) ? ' of ' + watts(g.power_limit_w) : '') : DASH;
      // What the number is: a GB10 reads its GPU rail only, a discrete card its whole board.
      f.powerTitle = /GB10/.test(String(g.name || '')) ? 'GPU rail only; GB10 exposes no module or system power via nvidia-smi'
        : 'board power (nvidia-smi ' + (isNum(g.power_instant_w) ? 'power.draw.instant' : 'power.draw') + ')';
      f.clock = isNum(g.sm_clock_mhz) ? mhz(g.sm_clock_mhz) + (isNum(g.sm_clock_max_mhz) ? ' of ' + mhz(g.sm_clock_max_mhz) : '') : DASH;
      const th = g.throttle || null;
      f.throttle = th && Array.isArray(th.reasons) ? th.reasons.filter(r => r !== 'idle').map(r => str(r, 24)).join(', ') : '';
      f.slowdown = !!(th && th.slowdown);
    } else {
      f.gpu = n.state === 'up' ? (isNum(n.gpu_error) ? 'GPU not readable (nvidia-smi exit ' + n.gpu_error + ')' : 'no GPU detected') : DASH;
      f.powerTitle = '';
      f.memLabel = 'memory'; f.memKind = 'RAM';
      f.mem = r ? gib(r.used_mib) + ' / ' + gib(r.total_mib, 0) + ' GiB (RAM)' : DASH;
      f.memPct = r ? r.pct : null;
      f.util = f.temp = f.power = f.clock = DASH;
      f.throttle = ''; f.slowdown = false;
    }
    f.cpu = (isNum(c.util_pct) ? pct(c.util_pct) : DASH) + (isNum(c.ncpu) ? ' · ' + c.ncpu + ' cores' : '');
    f.load = Array.isArray(c.load) ? c.load.map(x => isNum(x) ? x.toFixed(2) : DASH).join('  ') : DASH;
    f.ram = r ? gib(r.used_mib) + ' / ' + gib(r.total_mib, 0) + ' GiB · ' + pct(r.pct) : DASH;
    f.uptime = dur(n.uptime_s);
    f.disk = k ? bytes(k.free_b) + ' free of ' + bytes(k.total_b) + ' · ' + pct(k.pct) + ' used' : DASH;
    return f;
  }

  function portFields(p) {
    const up = String(p.state || '').toUpperCase() === 'ACTIVE';
    return {
      key: str(p.hca, 32) + '/' + str(p.port, 4),
      state: up ? 'up' : 'down',
      name: (has(p.netdev) ? str(p.netdev, 32) + ' · ' : '') + str(p.hca, 32) + ' p' + str(p.port, 4),
      rate: up ? str(p.rate, 40).replace(/\s*\(.*\)$/, '') : String(p.state || 'down').toLowerCase(),
      io: up ? '↑ ' + bps(p.tx_bps) + '  ↓ ' + bps(p.rx_bps) + (isNum(p.mtu) ? '  · MTU ' + p.mtu : '') : (isNum(p.mtu) ? 'MTU ' + p.mtu : ''),
    };
  }


  const SVC_TEXT = { up: 'up', down: 'down', off: 'off', online: 'online', islanded: 'islanded', stale: 'stale', unknown: 'n/a' };
  function serviceFields(s, now) {
    const state = Object.prototype.hasOwnProperty.call(SVC_TEXT, s.state) ? s.state : 'unknown';
    now = isNum(now) ? now : Date.now() / 1000;
    // `since` is exact only once the dashboard saw the state change (last_change);
    // before that it is when the dashboard first saw this state: a lower bound.
    const exact = isNum(s.last_change);
    const since = isNum(s.since) && state !== 'off'
      ? SVC_TEXT[state] + (exact ? ' for ' : ' ≥ ') + dur(now - s.since) + (exact ? '' : ' (since dashboard start)') : '';
    return { key: str(s.name, 32), state, name: str(s.label || s.name, 40),
      port: isNum(s.port) ? ':' + s.port : (s.name === 'island' ? 'mode ' + str(s.detail, 16) : ''),
      text: SVC_TEXT[state], since,
      change: exact ? 'last change ' + when(s.last_change) : isNum(s.since) ? 'no change seen' : '' };
  }

  // Overall health. The server's verdict (alerts with hold times) leads; on top of it, at once:
  // the model down = down; a running service down or a node unreachable = degraded.
  const RANK = { down: 0, critical: 1, degraded: 2, ok: 3, unknown: 4 };
  function overall(st, cl) {
    if (!st) return { state: 'unknown', text: 'no data' };
    if (!st.model || !st.model.healthy) return { state: 'down', text: 'model down' };
    const bad = (st.service_list || []).filter(s => s.state === 'down').map(s => str(s.label || s.name, 40));
    const lost = ((cl && cl.nodes) || []).filter(n => n.state === 'unreachable').map(n => str(n.name, 64));
    const n = bad.length + lost.length;
    const h = st.health && typeof st.health === 'object' ? st.health : null;
    if (h && (h.state === 'critical' || h.state === 'degraded')) return { state: h.state, text: str(h.reason, 140) };
    if (n) return { state: 'degraded', text: n + (n === 1 ? ' issue: ' : ' issues: ') + bad.concat(lost).slice(0, 3).join(', ') };
    return { state: 'ok', text: 'all systems nominal' };
  }
  const VERDICT = { ok: 'All systems nominal', degraded: 'Degraded', critical: 'Critical', down: 'Model down', unknown: 'No data' };
  const $ = id => document.getElementById(id);
  const set = (el, text) => { if (el) { const t = String(text); if (el.textContent !== t) el.textContent = t; } };
  const setId = (id, text) => set($(id), text);
  const setState = (el, s) => { if (el && el.dataset.state !== s) el.dataset.state = s; };
  const width = (el, p) => { if (el) el.style.width = (isNum(p) ? Math.max(0, Math.min(100, p)) : 0) + '%'; };
  const field = (root, name) => root.querySelector('[data-f="' + name + '"]');

  // children of `box` keyed by item key: create the missing, update all, drop the gone, keep order.
  function keyed(box, items, keyOf, create, update) {
    if (!box) return;
    const have = new Map();
    for (const el of Array.from(box.children)) {
      if (have.has(el.dataset.key)) box.removeChild(el); else have.set(el.dataset.key, el);
    }
    // A key seen twice (two nodes of one name, two events of one second and
    // text) gets an occurrence suffix, so every item owns exactly one row:
    // a duplicate used to leak one new row per poll.
    const seen = new Map();
    let i = 0;
    for (const it of items) {
      const base = keyOf(it);
      const n = seen.get(base) || 0;
      seen.set(base, n + 1);
      const k = n ? base + '#' + n : base;
      let el = have.get(k);
      if (!el) { el = create(it); el.dataset.key = k; }
      have.delete(k);
      update(el, it);
      if (box.children[i] !== el) box.insertBefore(el, box.children[i] || null);
      i++;
    }
    for (const el of have.values()) box.removeChild(el);
  }

  function mk(tag, cls, parent) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (parent) parent.appendChild(el);
    return el;
  }

  // Sparkline: one series, a 2px line over a faint area, a crosshair and readout on hover.
  // With `times` (epoch s per point) a point sits at its real time in the last SPAN_S and a
  // gap reads as a gap; without them the points are spaced evenly (svg._step apart).
  function spark(svg, values, readout, fmt, maxHint, times) {
    if (!svg) return;
    if (!svg._parts) {
      const area = document.createElementNS(SVGNS, 'path'); area.setAttribute('class', 's-area');
      const line = document.createElementNS(SVGNS, 'path'); line.setAttribute('class', 's-line');
      const cross = document.createElementNS(SVGNS, 'line'); cross.setAttribute('class', 's-cross');
      cross.setAttribute('y1', '0'); cross.setAttribute('y2', '28'); cross.style.display = 'none';
      svg.appendChild(area); svg.appendChild(line); svg.appendChild(cross);
      svg._parts = { area, line, cross };
      const pick = ev => {
        const v = svg._values || [];
        if (!v.length || !svg.getBoundingClientRect) return;
        const r = svg.getBoundingClientRect();
        const px = (ev.clientX - r.left) / r.width * 120;
        let i = 0, best = Infinity;
        for (let j = 0; j < v.length; j++) { const d = Math.abs(svg._xs[j] - px); if (d < best) { best = d; i = j; } }
        const x = svg._xs[i];
        cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.style.display = '';
        const t = svg._times;
        const ago = t ? t[t.length - 1] - t[i] : (v.length - 1 - i) * (svg._step || 5);
        set(readout, (has(v[i]) ? svg._fmt(v[i]) : DASH) + (ago ? ' · ' + dur(ago) + ' ago' : ' · now'));
      };
      svg.addEventListener('pointermove', pick);
      svg.addEventListener('pointerdown', pick);
      svg.addEventListener('pointerleave', () => { cross.style.display = 'none'; set(readout, ''); });
    }
    const n = values.length;
    const timed = Array.isArray(times) && times.length === n && n > 0 && times.every(isNum);
    const tEnd = timed ? times[n - 1] : 0;
    const xs = values.map((_, i) => timed ? Math.max(0, 120 - (tEnd - times[i]) / SPAN_S * 120) : n > 1 ? i / (n - 1) * 120 : 120);
    svg._values = values; svg._fmt = fmt; svg._xs = xs; svg._times = timed ? times : null;
    const nums = values.filter(isNum);
    const max = Math.max(maxHint || 0, ...nums, 1e-9);
    let d = '', open = false, first = null, last = null;
    values.forEach((v, i) => {
      const x = xs[i];
      if (!isNum(v)) { open = false; return; }
      const y = 26 - (v / max) * 24;
      d += (open ? 'L' : 'M') + x.toFixed(2) + ' ' + y.toFixed(2);
      open = true;
      if (first === null) first = x;
      last = x;
    });
    svg._parts.line.setAttribute('d', d);
    svg._parts.area.setAttribute('d', d && first !== null ? d + 'L' + last.toFixed(2) + ' 28L' + first.toFixed(2) + ' 28Z' : '');
  }

  // The trend chip's words: "rising 8.2% · 5 min avg vs 15 min".
  function trendText(t) {
    if (t && t.partial) return { dir: 'none', arrow: '·', text: 'trend: partial (a node is not measured now)' };
    if (!t || !t.dir) return { dir: 'none', arrow: '·', text: 'trend: collecting (needs 5 min)' };
    const p = isNum(t.pct) ? (t.pct > 0 ? '+' : '') + t.pct.toFixed(1) + '%' : '';
    return { dir: t.dir, arrow: ARROW[t.dir] || '·', text: t.dir + ' ' + p + ' · 5 min avg ' + watts(t.now_avg_w) + ' vs ' + (WIN_LABEL[t.vs] || t.vs) + ' ' + watts(t.base_avg_w) };
  }

  // One window chip: average, low and peak, energy, coverage.
  function windowFields(k, w, slotS) {
    w = w || {};
    const cov = isNum(w.coverage) ? w.coverage : 0;
    return {
      key: k, label: (WIN_LABEL[k] || k) + ' avg',
      value: isNum(w.avg_w) ? watts(w.avg_w) : w.partial ? 'partial' : cov > 0 ? 'collecting' : DASH,
      range: isNum(w.min_w) && isNum(w.max_w) ? 'range ' + wnum(w.min_w) + '–' + wnum(w.max_w) + '\u00a0W' : 'range —',
      energy: w.partial && !isNum(w.avg_w) ? 'measured nodes only: ' + energy(w.energy_wh, k === '24h')
        : energy(w.energy_wh, k === '24h') + ' · ' + Math.round(cov * 100) + '% covered',
      low: cov < 0.95,
      title: 'Time-weighted average over the last ' + (WIN_LABEL[k] || k) + '; the range is the lowest to the highest '
        + (w.peak_resolution_s === 60 ? '1 min' : '~5 s') + ' mean; energy is what was measured in the covered time',
    };
  }

  function powerNodeFields(n) {
    const now = n.now || null, last = n.last || null, p = now || last || {};
    const ws = n.windows || {};
    const rails = Array.isArray(p.rails) ? p.rails : [];
    const parts = rails.map(r => {
      const nm = r.key === 'gpu' ? 'GPU' : str(r.label, 40);
      let t = nm + ' ' + watts(r.w);
      if (r.key === 'gpu') {
        t += p.gpu_source === 'power.draw.instant' ? ' instant' : ' (1 s avg)';
        if (p.gpu_source === 'power.draw.instant' && isNum(p.gpu_avg_w)) t += ' · 1 s avg ' + watts(p.gpu_avg_w);
        if (isNum(p.gpu_limit_w)) t += ' · limit ' + watts(p.gpu_limit_w);
      }
      return t + (r.note ? ' †' : '');
    });
    const tr = trendText(n.trend);
    const lim = isNum(p.gpu_limit_w) && isNum(p.gpu_w) ? 100 * p.gpu_w / p.gpu_limit_w : null;
    const state = n.state === 'up' ? 'up' : n.state === 'unreachable' ? 'unreachable' : 'pending';
    return {
      key: str(n.name, 64), name: str(n.name, 64), state,
      now: now ? watts(now.total_w) : last ? watts(last.total_w) : DASH,
      label: state === 'up' ? str(p.label || 'no power sensor', 60)
        : state === 'unreachable' ? (last ? 'unreachable · last reading' : 'unreachable · no reading since the dashboard started') : 'connecting',
      rails: parts.length ? parts.join(' · ') : state === 'up' ? 'no readable power sensor' : DASH,
      avg: 'avg ' + ['5m', '1h', '24h'].map(k => (WIN_SHORT[k] + ' ' + (isNum((ws[k] || {}).avg_w) ? watts(ws[k].avg_w) : DASH)).replace(/ /g, '\u00a0')).join(' · '),
      trendDir: tr.dir, trend: tr.dir === 'none' ? '' : tr.arrow + ' ' + (isNum(n.trend.pct) ? (n.trend.pct > 0 ? '+' : '') + n.trend.pct.toFixed(1) + '%' : ''),
      limitPct: lim, limitText: isNum(lim) ? lim.toFixed(0) + '% of the ' + watts(p.gpu_limit_w) + ' board limit' : '',
      scope: str(p.gpu_scope || '', 300),
    };
  }

  // The footnote: what each node's figure covers, the sensor caveats, how averages are made.
  function powerFoot(pw) {
    const by = new Map();
    const notes = new Set();
    for (const n of (pw && pw.nodes) || []) {
      const p = n.now || n.last;
      if (!p) continue;
      if (p.gpu_scope) { if (!by.has(p.gpu_scope)) by.set(p.gpu_scope, []); by.get(p.gpu_scope).push(str(n.name, 64)); }
      for (const r of p.rails || []) if (r.note) notes.add('† ' + str(r.label, 40) + ': ' + str(r.note, 120) + '.');
    }
    const out = [];
    for (const [scope, names] of by) out.push(names.join(', ') + ' GPU: ' + scope.replace(/\.$/, '') + '.');
    out.push(...notes);
    out.push('Averages are energy ÷ covered time (NVML energy counter where the driver has one, else the 1 s readings), so a gap (a node unreachable, the dashboard down) lowers the coverage and is never counted as zero.');
    return out.join(' ');
  }

  function renderPower(pw) {
    const sec = $('power');
    if (!pw || !pw.cluster) { if (sec) sec.hidden = true; return; }
    if (sec) sec.hidden = false;
    const c = pw.cluster, nodes = Array.isArray(pw.nodes) ? pw.nodes : [];
    const multi = c.nodes > 1;
    setId('pw-now', isNum(c.now_w) ? wnum(c.now_w) : DASH);
    // A node down: the figure is a partial sum, and the label names what it sums.
    const partial = multi && isNum(c.now_w) && c.nodes_measured < c.nodes;
    const names = Array.isArray(c.measured) ? c.measured.map(n => str(n, 64)).join(', ') : '';
    setId('pw-now-k', (partial ? (names ? names + ' only' : 'partial') + ' · ' + c.nodes_measured + ' of ' + c.nodes + ' nodes · '
      : multi ? 'cluster · ' : str(nodes[0] && nodes[0].name, 64) + ' · ') + str(c.label || 'power', 60) + ' now');
    const nv = $('pw-now'); const nvp = nv && nv.parentNode;
    if (nvp && nvp.dataset) { const v = partial ? 'partial' : ''; if (nvp.dataset.partial !== v) nvp.dataset.partial = v; }
    setId('power-note', (multi ? c.nodes_measured + ' of ' + c.nodes + ' nodes measured' : c.nodes_measured ? 'measured' : 'not measured')
      + ' · sampled every ' + (pw.sample_s || 5) + ' s');
    const tr = trendText(c.trend);
    const pt = $('pw-trend'); if (pt && pt.dataset.dir !== tr.dir) pt.dataset.dir = tr.dir;
    setId('pw-arrow', tr.arrow);
    setId('pw-trend-text', tr.text);
    const wins = (pw.windows || []).map(k => windowFields(k, (c.windows || {})[k]));
    keyed($('pw-windows'), wins, w => w.key, () => {
      const li = mk('li', 'pw-win');
      mk('span', 'w-k', li); mk('span', 'w-v', li); mk('span', 'w-r', li); mk('span', 'w-e', li);
      return li;
    }, (li, w) => {
      set(li.querySelector('.w-k'), w.label); set(li.querySelector('.w-v'), w.value);
      set(li.querySelector('.w-r'), w.range); set(li.querySelector('.w-e'), w.energy);
      li.dataset.cov = w.low ? 'low' : 'ok'; li.title = w.title;
    });
    const cost = $('pw-cost'), co = pw.cost;
    if (cost) {
      cost.hidden = !co;
      if (co) set(cost, 'cost at ' + money(co.price_per_kwh, co.currency) + '/kWh: ' + money(co.per_day, co.currency) + ' / day · '
        + money(co.per_month, co.currency) + ' / month (at the ' + (WIN_SHORT[co.basis_window] || '—') + ' average) · last 24 h '
        + money(co.window_24h, co.currency) + ' for ' + energy((co.window_24h_kwh || 0) * 1000, true)
        + (isNum(co.window_24h_coverage) && co.window_24h_coverage < 0.95 ? ' (' + Math.round(co.window_24h_coverage * 100) + '% covered)' : '')
        + ' · ' + str(co.note, 80));
    }
    keyed($('pw-nodes'), nodes.map(powerNodeFields), n => n.key, () => {
      const li = mk('li', 'pw-node');
      const nm = mk('span', 'n-name', li); const dot = mk('i', 'dot', nm); dot.setAttribute('aria-hidden', 'true'); mk('span', '', nm);
      mk('span', 'n-now', li); mk('span', 'n-label', li); mk('span', 'n-rails', li);
      const av = mk('span', 'n-avg', li); mk('span', 'n-avg-t', av); mk('span', 'n-trend', av);
      const m = mk('span', 'meter', li); m.setAttribute('aria-hidden', 'true'); mk('i', '', m);
      return li;
    }, (li, n) => {
      setState(li, n.state);
      set(li.querySelector('.n-name > span'), n.name);
      set(li.querySelector('.n-now'), n.now);
      set(li.querySelector('.n-label'), n.label + (n.limitText ? ' · ' + n.limitText : ''));
      li.querySelector('.n-label').title = n.scope;
      set(li.querySelector('.n-rails'), n.rails);
      set(li.querySelector('.n-avg-t'), n.avg + (n.trend ? ' · ' : ''));
      const t = li.querySelector('.n-trend'); set(t, n.trend); t.dataset.dir = n.trendDir;
      const m = li.querySelector('.meter'); m.hidden = !isNum(n.limitPct);
      width(li.querySelector('.meter > i'), n.limitPct);
    });
    setId('pw-foot', powerFoot(pw));
  }

  // ------------------------------------------------------------ power chart (inline SVG)
  const CHART_WINDOWS = ['15m', '1h', '6h', '24h'];
  let chartWin = '15m', chart = null, chartAt = 0, chartIdx = null;
  try { const w = globalThis.localStorage && localStorage.getItem('obdash.powerWindow'); if (CHART_WINDOWS.includes(w)) chartWin = w; } catch (e) { /* private mode */ }

  // A round axis top: 1, 2, 2.5 or 5 × 10^k at or above v.
  function niceCeil(v) {
    if (!isNum(v) || v <= 0) return 10;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * e >= v) return m * e;
    return 10 * e;
  }
  const agoLabel = s => s <= 0 ? 'now' : '−' + (s >= 3600 && s % 3600 === 0 ? (s / 3600) + 'h' : Math.round(s / 60) + 'm');

  // The chart's series and scale, from /api/power (pure: tested without a DOM).
  function chartModel(d) {
    const nodes = (d && Array.isArray(d.nodes)) ? d.nodes : [];
    const multi = nodes.length > 1;
    const series = nodes.map((n, i) => ({ name: str(n.name, 64), cls: multi ? 'c' + (i % 4) : 'solo', values: Array.isArray(n.values) ? n.values : [], avg: n.avg_w }));
    if (multi && Array.isArray(d.total)) series.push({ name: 'total', cls: 'ln-total', values: d.total, avg: d.total_avg_w });
    let max = 0;
    for (const s of series) { for (const v of s.values) if (isNum(v) && v > max) max = v; if (isNum(s.avg) && s.avg > max) max = s.avg; }
    const n = Math.max(0, ...series.map(s => s.values.length));
    return { series, n, top: niceCeil(max * 1.08), windowS: (d && d.window_s) || 900, stepS: (d && d.step_s) || 5, marked: series[series.length - 1] };
  }

  function svgEl(tag, attrs, parent) {
    const el = document.createElementNS(SVGNS, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); }

  function drawChart() {
    const svg = $('pw-svg'), plot = $('pw-plot');
    if (!svg || !plot || !chart || !svg.setAttribute || !plot.getBoundingClientRect) return;
    const r = plot.getBoundingClientRect();
    const W = Math.max(160, Math.round(r.width)), H = Math.max(120, Math.round(r.height));
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    const m = chartModel(chart);
    if (!svg._g) {
      svg._g = { grid: svgEl('g', { class: 'grid' }, svg), avg: svgEl('g', {}, svg), lines: svgEl('g', {}, svg),
        cross: svgEl('line', { class: 'cross', y1: 0, y2: 0, x1: -10, x2: -10 }, svg) };
    }
    const g = svg._g;
    clear(g.grid); clear(g.avg); clear(g.lines);
    // Margins in the page's own text size, so the axis labels fit at 200 % text too.
    let em = 16;
    try { em = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16; } catch (e) { /* no CSSOM */ }
    const fs = em * 0.6875;
    const L = Math.round(fs * 4.2), R = 10, T = Math.round(fs + 2), B = Math.round(fs * 2), pw = W - L - R, ph = H - T - B;
    const x = i => L + (m.n > 1 ? i / (m.n - 1) : 1) * pw;
    const y = v => T + (1 - v / m.top) * ph;
    svg._geom = { L, R, T, B, W, H, x, n: m.n };
    for (let k = 0; k <= 4; k++) {
      const v = m.top * k / 4, yy = y(v);
      svgEl('line', { x1: L, x2: W - R, y1: yy, y2: yy }, g.grid);
      const t = svgEl('text', { x: L - 6, y: yy + fs * 0.35, 'text-anchor': 'end' }, g.grid);
      t.textContent = (v >= 1000 ? (v / 1000) + 'k' : String(+v.toFixed(1))) + (k === 4 ? ' W' : '');
    }
    const nt = TICKS[chartWin] || 4;
    for (let k = 0; k <= nt; k++) {
      // On a narrow chart, every other label (the ends always).
      if (pw < fs * 24 && nt >= 3 && k % 2 && k !== nt) continue;
      const f = k / nt, t = svgEl('text', { x: L + f * pw, y: H - Math.round(fs * 0.55), 'text-anchor': k === 0 ? 'start' : k === nt ? 'end' : 'middle' }, g.grid);
      t.textContent = agoLabel(Math.round(m.windowS * (1 - f)));
    }
    for (const s of m.series) {
      let d = '', open = false;
      s.values.forEach((v, i) => {
        if (!isNum(v)) { open = false; return; }
        d += (open ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1);
        open = true;
      });
      svgEl('path', { class: 'ln ' + s.cls, d }, g.lines);
      if (isNum(s.avg)) {
        const yy = y(s.avg).toFixed(1);
        svgEl('line', { class: 'avg ' + (s.cls === 'ln-total' ? 'ln-total' : s.cls), x1: L, x2: W - R, y1: yy, y2: yy }, g.avg);
      }
    }
    if (m.marked && isNum(m.marked.avg) && W >= 480) {
      const t = svgEl('text', { class: 'avg-label', x: W - R - 4, y: Math.max(T + fs, y(m.marked.avg) - 5), 'text-anchor': 'end' }, g.avg);
      t.textContent = 'avg ' + watts(m.marked.avg);
      // A panel-coloured backing behind the label, so the lines under it never cut through it.
      try {
        const bb = t.getBBox();
        if (bb && bb.width) g.avg.insertBefore(svgEl('rect', { class: 'avg-bg', x: (bb.x - 4).toFixed(1), y: (bb.y - 2).toFixed(1),
          width: (bb.width + 8).toFixed(1), height: (bb.height + 4).toFixed(1), rx: 4 }), t);
      } catch (e) { /* no layout (tests) */ }
    }
    svg.setAttribute('aria-label', 'Power over the last ' + (WIN_LABEL[chartWin] || chartWin) + (m.marked && isNum(m.marked.avg) ? ', average ' + watts(m.marked.avg) : ''));
    const lg = $('pw-legend');
    keyed(lg, m.series, s => s.name, () => { const li = mk('li'); mk('i', '', li); mk('span', '', li); return li; }, (li, s) => {
      li.querySelector('i').className = s.cls === 'ln-total' ? 'total' : s.cls === 'solo' ? '' : s.cls;
      set(li.querySelector('span'), s.name + ' · avg ' + watts(s.avg));
    });
    if (chartIdx !== null) showRead(chartIdx);
  }

  function showRead(i) {
    const svg = $('pw-svg'), rd = $('pw-read');
    if (!svg || !svg._geom || !chart || !rd) return;
    const m = chartModel(chart), G = svg._geom;
    if (!m.n) return;
    i = Math.max(0, Math.min(m.n - 1, i));
    chartIdx = i;
    const xx = G.x(i);
    const c = svg._g.cross;
    c.setAttribute('x1', xx); c.setAttribute('x2', xx); c.setAttribute('y1', G.T); c.setAttribute('y2', G.H - G.B);
    clear(rd);
    const ago = (m.n - 1 - i) * m.stepS;
    const head = mk('div', '', rd); set(head, ago ? dur(ago) + ' ago' : 'now');
    for (const s of m.series) {
      const row = mk('div', '', rd); const b = mk('b', '', row);
      set(b, watts(s.values[i])); const sp = mk('span', '', row); set(sp, ' ' + s.name);
    }
    rd.hidden = false;
    const left = xx > G.W / 2;
    rd.style.left = left ? '8px' : 'auto';
    rd.style.right = left ? 'auto' : '8px';
  }
  function hideRead() {
    chartIdx = null;
    const rd = $('pw-read'); if (rd) rd.hidden = true;
    const svg = $('pw-svg'); if (svg && svg._g) { svg._g.cross.setAttribute('x1', -10); svg._g.cross.setAttribute('x2', -10); }
  }

  function wireChart() {
    const svg = $('pw-svg');
    if (!svg || !svg.addEventListener) return;
    const pick = ev => {
      const G = svg._geom; if (!G || !G.n) return;
      const r = svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) / r.width * G.W;
      showRead(Math.round((px - G.L) / (G.W - G.L - G.R) * (G.n - 1)));
    };
    // Touch and pen: pointerleave follows pointerup at once, so the readout of a tap stays
    // for a few seconds (or until a tap elsewhere) instead of vanishing with the finger.
    let readTimer = null;
    const holdRead = ev => {
      if (readTimer) { clearTimeout(readTimer); readTimer = null; }
      if (ev && (ev.pointerType === 'touch' || ev.pointerType === 'pen')) readTimer = setTimeout(hideRead, 4000);
    };
    svg.addEventListener('pointermove', ev => { pick(ev); holdRead(ev); });
    svg.addEventListener('pointerdown', ev => { pick(ev); holdRead(ev); });
    svg.addEventListener('pointerleave', ev => { if (ev.pointerType === 'touch' || ev.pointerType === 'pen') return; holdRead(); hideRead(); });
    svg.addEventListener('blur', () => { holdRead(); hideRead(); });
    document.addEventListener('pointerdown', ev => {
      if (chartIdx !== null && ev.target && !svg.contains(ev.target)) { holdRead(); hideRead(); }
    });
    svg.addEventListener('keydown', ev => {
      const G = svg._geom; if (!G || !G.n) return;
      const stepK = ev.shiftKey ? 12 : 1;
      if (ev.key === 'ArrowLeft') { showRead((chartIdx === null ? G.n - 1 : chartIdx) - stepK); ev.preventDefault(); }
      else if (ev.key === 'ArrowRight') { showRead((chartIdx === null ? G.n - 1 : chartIdx) + stepK); ev.preventDefault(); }
      else if (ev.key === 'Home') { showRead(0); ev.preventDefault(); }
      else if (ev.key === 'End') { showRead(G.n - 1); ev.preventDefault(); }
      else if (ev.key === 'Escape') hideRead();
    });
    const seg = $('pw-range');
    if (seg) {
      for (const b of Array.from(seg.querySelectorAll('button'))) {
        b.setAttribute('aria-pressed', String(b.dataset.w === chartWin));
        b.addEventListener('click', () => {
          if (!CHART_WINDOWS.includes(b.dataset.w)) return;
          chartWin = b.dataset.w;
          chartAt = 0;
          try { localStorage.setItem('obdash.powerWindow', chartWin); } catch (e) { /* private mode */ }
          for (const o of Array.from(seg.querySelectorAll('button'))) o.setAttribute('aria-pressed', String(o === b));
          hideRead();
          refreshChart(true);
        });
      }
    }
    if (typeof ResizeObserver === 'function') new ResizeObserver(() => drawChart()).observe($('pw-plot'));
    else globalThis.addEventListener('resize', drawChart);
  }

  // A sequence number, not a busy flag: a window change (force) always fetches, even with
  // a tick's fetch in flight, and only the latest reply for the window shown is drawn
  // (review 2026-11-22: a click during a fetch was dropped for up to 30 s).
  let chartSeq = 0, chartInflight = 0;
  async function refreshChart(force) {
    const every = chartWin === '15m' || chartWin === '1h' ? POLL_MS - 200 : 30000;
    if (!force && (chartInflight || Date.now() - chartAt < every)) return;
    const seq = ++chartSeq, want = chartWin;
    chartInflight++;
    try {
      const d = await getJSON('/api/power?window=' + encodeURIComponent(want));
      if (seq === chartSeq && want === chartWin && d) { chart = d; chartAt = Date.now(); guard(drawChart); }
    } catch (e) { /* the next tick retries; the page's stale marker covers it */ }
    finally { chartInflight--; }
  }


  // ------------------------------------------------------------ time-series charts (inline SVG)
  // One generic chart for every detail page: a card with a title, the latest value, an SVG plot
  // (grid, y labels, relative-time x labels, a line per series, gaps as gaps), a legend and a
  // readout on hover, touch or the arrow keys. A chart is redrawn in place (its groups are reused).
  const agoTick = s => s <= 0 ? 'now' : '−' + (s >= 86400 && s % 86400 === 0 ? (s / 86400) + 'd'
    : s >= 3600 && s % 3600 === 0 ? (s / 3600) + 'h' : Math.round(s / 60) + 'm');

  // [values] summed into buckets of `k` points (a counter's per-slot deltas -> per bucket);
  // a bucket with no reading at all is a gap.
  function bucketSum(values, k) {
    if (k <= 1) return values.slice();
    const out = [];
    for (let i = 0; i < values.length; i += k) {
      let s = 0, any = false;
      for (let j = i; j < Math.min(values.length, i + k); j++) if (isNum(values[j])) { s += values[j]; any = true; }
      out.push(any ? s : null);
    }
    return out;
  }

  // The plot model from a /api/* series document and a chart spec (pure: tested without a DOM).
  // spec: {title, fmt, max, series: [[metric, label]], perMin (a "sum" metric shown per minute),
  // scale (multiply), dedupe (drop a series identical to the one before)}.
  function tsModel(doc, spec) {
    const ser = (doc && doc.series) || {};
    let step = (doc && doc.step) || 5;
    const out = [];
    for (let i = 0; i < spec.series.length; i++) {
      const [metric, label] = spec.series[i];
      let v = Array.isArray(ser[metric]) ? ser[metric] : null;
      if (!v || !v.some(isNum)) continue;
      if (spec.perMin) { const k = Math.max(1, Math.round(60 / step)); v = bucketSum(v, k).map(x => isNum(x) ? x * 60 / (k * step) : x); }
      if (spec.scale) v = v.map(x => isNum(x) ? x * spec.scale : x);
      if (spec.dedupe && out.length && JSON.stringify(out[out.length - 1].values) === JSON.stringify(v)) continue;
      out.push({ name: label, values: v, cls: 's' + (i % 4) });
    }
    const k = spec.perMin ? Math.max(1, Math.round(60 / step)) : 1;
    const n = Math.max(0, ...out.map(s => s.values.length));
    let max = 0;
    for (const s of out) for (const x of s.values) if (isNum(x) && x > max) max = x;
    const top = isNum(spec.max) ? Math.max(spec.max, max) : niceTop(max * 1.08 || 1);
    const last = out.map(s => { for (let i = s.values.length - 1; i >= 0; i--) if (isNum(s.values[i])) return s.values[i]; return null; });
    const nums = out.length ? out[0].values.filter(isNum) : [];
    return { series: out, n, top, stepS: step * k, windowS: (doc && doc.window_s) || n * step * k, t0: doc && doc.t0,
      last, fmt: spec.fmt || (x => toks(x)),
      stats: nums.length ? { avg: nums.reduce((a, b) => a + b, 0) / nums.length, min: Math.min(...nums), max: Math.max(...nums) } : null };
  }

  // A round axis top with finer steps than niceCeil (2640 MHz -> 3000, not 5000).
  function niceTop(v) {
    if (!isNum(v) || v <= 0) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * e >= v) return m * e;
    return 10 * e;
  }

  const charts = new Map();          // card element -> {model, idx}
  let chartRO = null;
  function chartBox(box, specs, doc, emptyNote) {
    if (!box) return [];
    const shown = specs.map(sp => ({ sp, m: tsModel(doc, sp) })).filter(x => x.m.series.length);
    keyed(box, shown, x => x.sp.key, () => {
      const fig = mk('figure', 'cc panel');
      const cap = mk('figcaption', 'cc-head', fig); mk('span', 'cc-title', cap); mk('span', 'cc-now', cap);
      const plot = mk('div', 'cc-plot', fig);
      const svg = document.createElementNS(SVGNS, 'svg'); svg.setAttribute('tabindex', '0'); svg.setAttribute('role', 'img');
      plot.appendChild(svg);
      const rd = mk('div', 'cc-read', plot); rd.hidden = true;
      mk('ul', 'cc-legend', fig); mk('p', 'cc-foot', fig);
      wireTs(fig, svg);
      if (chartRO) chartRO.observe(plot);
      return fig;
    }, (fig, x) => {
      const st = charts.get(fig) || { idx: null };
      st.model = x.m; st.spec = x.sp; charts.set(fig, st);
      set(fig.querySelector('.cc-title'), x.sp.title);
      set(fig.querySelector('.cc-now'), x.m.series.map((s, i) => (x.m.series.length > 1 ? s.name + ' ' : '') + x.m.fmt(x.m.last[i])).join(' · '));
      const lg = fig.querySelector('.cc-legend');
      if (lg) {
        lg.hidden = x.m.series.length < 2;
        keyed(lg, x.m.series, s => s.name, () => { const li = mk('li'); mk('i', '', li); mk('span', '', li); return li; }, (li, s) => {
          const i = li.querySelector('i'); if (i) i.className = s.cls; set(li.querySelector('span'), s.name);
        });
      }
      const s0 = x.m.stats;
      set(fig.querySelector('.cc-foot'), s0 ? WIN_SHORT[doc.window] + ' · avg ' + x.m.fmt(s0.avg) + ' · min ' + x.m.fmt(s0.min) + ' · max ' + x.m.fmt(s0.max) : '');
      drawTs(fig);
    });
    for (const fig of Array.from(charts.keys())) if (!fig.parentNode) charts.delete(fig);
    if (emptyNote) {
      const missing = specs.filter(sp => !shown.some(x => x.sp === sp)).map(sp => sp.title);
      emptyNote(missing);
    }
    return shown;
  }

  function drawTs(fig) {
    const st = charts.get(fig);
    const plot = fig.querySelector('.cc-plot'), svg = plot && plot.querySelector('svg');
    if (!st || !svg || !plot.getBoundingClientRect || !svg.setAttribute) return;
    const r = plot.getBoundingClientRect();
    if (!r.width) return;                                 // hidden: drawn when shown (ResizeObserver)
    const W = Math.max(160, Math.round(r.width)), H = Math.max(90, Math.round(r.height));
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    const m = st.model;
    if (!svg._g) svg._g = { grid: svgEl('g', { class: 'grid' }, svg), lines: svgEl('g', {}, svg),
      cross: svgEl('line', { class: 'cross', x1: -10, x2: -10, y1: 0, y2: 0 }, svg) };
    const g = svg._g;
    clear(g.grid); clear(g.lines);
    let em = 16;
    try { em = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16; } catch (e) { /* no CSSOM */ }
    const fs = em * 0.6875;
    const ylab = [0, 0.5, 1].map(f => m.fmt(m.top * f));
    const L = Math.round(Math.min(W * 0.32, fs * 0.62 * Math.max(...ylab.map(t => t.length)) + 10)), R = 8, T = Math.round(fs * 0.9), B = Math.round(fs * 2), pw = W - L - R, ph = H - T - B;
    const x = i => L + (m.n > 1 ? i / (m.n - 1) : 1) * pw;
    const y = v => T + (1 - Math.max(0, Math.min(1, v / m.top))) * ph;
    svg._geom = { L, R, T, B, W, H, x, n: m.n };
    [0, 0.5, 1].forEach((f, k) => {
      const yy = y(m.top * f);
      svgEl('line', { x1: L, x2: W - R, y1: yy, y2: yy }, g.grid);
      const t = svgEl('text', { x: L - 5, y: yy + fs * 0.35, 'text-anchor': 'end' }, g.grid);
      t.textContent = ylab[k];
    });
    const win = Object.keys(WIN_SHORT).find(k => k === (st.spec && st.spec.win)) || null;
    const nt = TICKS[win || docWin(m.windowS)] || 4;
    for (let k = 0; k <= nt; k++) {
      if (pw < fs * 22 && nt >= 3 && k % 2 && k !== nt) continue;
      const f = k / nt, t = svgEl('text', { x: L + f * pw, y: H - Math.round(fs * 0.5), 'text-anchor': k === 0 ? 'start' : k === nt ? 'end' : 'middle' }, g.grid);
      t.textContent = agoTick(Math.round(m.windowS * (1 - f)));
    }
    for (const s of m.series) {
      let d = '', open = false, pts = 0;
      s.values.forEach((v, i) => {
        if (!isNum(v)) { open = false; return; }
        d += (open ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1);
        open = true; pts++;
      });
      if (pts) svgEl('path', { class: 'ln ' + s.cls, d }, g.lines);
      // An isolated point (a gap on each side) still shows, as a dot.
      s.values.forEach((v, i) => {
        if (isNum(v) && !isNum(s.values[i - 1]) && !isNum(s.values[i + 1])) svgEl('circle', { class: 'pt ' + s.cls, cx: x(i).toFixed(1), cy: y(v).toFixed(1), r: 1.6 }, g.lines);
      });
    }
    svg.setAttribute('aria-label', (st.spec ? st.spec.title : 'chart') + ', last ' + (WIN_LABEL[docWin(m.windowS)] || dur(m.windowS))
      + ': ' + m.series.map((s, i) => s.name + ' now ' + m.fmt(m.last[i])).join(', '));
    if (st.idx !== null) tsRead(fig, st.idx);
  }
  const docWin = s => ({ 900: '15m', 3600: '1h', 21600: '6h', 86400: '24h', 604800: '7d' })[s] || '1h';

  function tsRead(fig, i) {
    const st = charts.get(fig);
    const svg = fig.querySelector('svg'), rd = fig.querySelector('.cc-read');
    if (!st || !svg || !svg._geom || !rd) return;
    const m = st.model, G = svg._geom;
    if (!m.n) return;
    i = Math.max(0, Math.min(m.n - 1, i));
    st.idx = i;
    const xx = G.x(i), c = svg._g.cross;
    c.setAttribute('x1', xx); c.setAttribute('x2', xx); c.setAttribute('y1', G.T); c.setAttribute('y2', G.H - G.B);
    clear(rd);
    const ago = (m.n - 1 - i) * m.stepS;
    const t = isNum(m.t0) ? m.t0 + i * m.stepS * 1 : null;
    set(mk('div', 'rd-t', rd), (t ? hhmm(t) + ' · ' : '') + (ago ? dur(ago) + ' ago' : 'now'));
    m.series.forEach(s => { const row = mk('div', '', rd); const b = mk('b', '', row); set(b, m.fmt(s.values[i])); set(mk('span', '', row), ' ' + s.name); });
    rd.hidden = false;
    const left = xx > G.W / 2;
    rd.style.left = left ? '8px' : 'auto'; rd.style.right = left ? 'auto' : '8px';
  }
  function tsHide(fig) {
    const st = charts.get(fig); if (st) st.idx = null;
    const rd = fig.querySelector('.cc-read'); if (rd) rd.hidden = true;
    const svg = fig.querySelector('svg'); if (svg && svg._g) { svg._g.cross.setAttribute('x1', -10); svg._g.cross.setAttribute('x2', -10); }
  }
  function wireTs(fig, svg) {
    if (!svg.addEventListener) return;
    const pick = ev => {
      const G = svg._geom; if (!G || !G.n || !svg.getBoundingClientRect) return;
      const r = svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) / r.width * G.W;
      tsRead(fig, Math.round((px - G.L) / (G.W - G.L - G.R) * (G.n - 1)));
    };
    let timer = null;
    const hold = ev => { if (timer) { clearTimeout(timer); timer = null; } if (ev && (ev.pointerType === 'touch' || ev.pointerType === 'pen')) timer = setTimeout(() => tsHide(fig), 4000); };
    svg.addEventListener('pointermove', ev => { pick(ev); hold(ev); });
    svg.addEventListener('pointerdown', ev => { pick(ev); hold(ev); });
    svg.addEventListener('pointerleave', ev => { if (ev.pointerType === 'touch' || ev.pointerType === 'pen') return; hold(); tsHide(fig); });
    svg.addEventListener('blur', () => { hold(); tsHide(fig); });
    svg.addEventListener('keydown', ev => {
      const G = svg._geom, st = charts.get(fig); if (!G || !G.n || !st) return;
      const k = ev.shiftKey ? 12 : 1, cur = st.idx === null ? G.n - 1 : st.idx;
      if (ev.key === 'ArrowLeft') { tsRead(fig, cur - k); ev.preventDefault(); }
      else if (ev.key === 'ArrowRight') { tsRead(fig, cur + k); ev.preventDefault(); }
      else if (ev.key === 'Home') { tsRead(fig, 0); ev.preventDefault(); }
      else if (ev.key === 'End') { tsRead(fig, G.n - 1); ev.preventDefault(); }
      else if (ev.key === 'Escape') tsHide(fig);
    });
  }

  // Vertical bars (tokens per hour / day): an <li> per bar, its height set through the CSSOM
  // (style-src 'self' allows it), the value in text for screen readers and on hover.
  function vbars(box, items, fmt) {
    if (!box) return;
    const max = Math.max(1, ...items.map(b => isNum(b.value) ? b.value : 0));
    keyed(box, items, b => String(b.key), () => {
      const li = mk('li', 'vb'); const bar = mk('span', 'vb-bar', li); mk('i', '', bar);
      mk('span', 'vb-l', li); mk('span', 'sr-only', li); return li;
    }, (li, b) => {
      const i = li.querySelector('.vb-bar > i');
      if (i && i.style) i.style.height = (isNum(b.value) ? Math.max(b.value > 0 ? 2 : 0, 100 * b.value / max) : 0) + '%';
      li.dataset.gap = isNum(b.value) ? '' : '1';
      set(li.querySelector('.vb-l'), b.label);
      const t = b.title + ': ' + (isNum(b.value) ? fmt(b.value) : 'no reading');
      set(li.querySelector('.sr-only'), t); li.title = t;
    });
  }

  // Horizontal bars, one scale: [{key, name, value, text}].
  function hbars(box, items) {
    if (!box) return;
    const max = Math.max(1, ...items.map(t => isNum(t.value) ? t.value : 0));
    keyed(box, items, t => String(t.key), () => {
      const li = mk('li', 'bar-row');
      mk('span', 'b-name', li);
      const m2 = mk('span', 'meter', li); m2.setAttribute('aria-hidden', 'true'); mk('i', '', m2);
      mk('span', 'b-val', li);
      return li;
    }, (li, t) => {
      const nm = li.querySelector('.b-name'); set(nm, t.name); if (nm) nm.title = t.name;
      width(li.querySelector('.meter > i'), isNum(t.value) ? 100 * t.value / (t.max || max) : 0);
      set(li.querySelector('.b-val'), t.text);
    });
  }

  // A segmented window control: a button per window, `cur` pressed; the page keeps the choice.
  function segWindows(el, wins, cur, onPick) {
    if (!el || !el.appendChild) return;
    if (!el.children.length) {
      for (const w of wins) {
        const b = mk('button', '', el); b.type = 'button'; b.dataset.w = w; set(b, WIN_SHORT[w] || w);
        b.addEventListener('click', () => {
          for (const o of Array.from(el.children)) o.setAttribute('aria-pressed', String(o.dataset.w === w));
          onPick(w);
        });
      }
    }
    for (const o of Array.from(el.children)) o.setAttribute('aria-pressed', String(o.dataset.w === cur));
  }

  // Stat tiles: [{key, k (label), v (value), s (sub), state}] into a <ul class="stat-grid">.
  function tiles(box, items) {
    keyed(box, items, t => t.key, () => {
      const li = mk('li', 'stat'); mk('span', 'stat-v', li); mk('span', 'stat-k', li); mk('span', 'stat-s', li); return li;
    }, (li, t) => {
      set(li.querySelector('.stat-v'), t.v); set(li.querySelector('.stat-k'), t.k);
      const s = li.querySelector('.stat-s'); set(s, t.s || ''); if (s) s.hidden = !t.s;
      if (li.dataset) { const v = t.state || ''; if (li.dataset.state !== v) li.dataset.state = v; }
    });
  }

  // ------------------------------------------------------------ overview + the status document
  function renderStatus(st) {
    const m = st.model || {}, sv = st.serving || {}, slots = sv.slots || {}, cap = sv.capacity || {};
    const det = sv.detail || {};
    setId('host-chip', str(st.host && st.host.name, 64));
    setState($('model-dot'), m.healthy ? 'up' : 'down');
    setId('h-model', m.healthy ? 'Serving · healthy' : 'Model server down');
    setId('model-id', str(m.id || m.alias || m.serve_script, 120));
    setId('chip-engine', str(m.engine || m.backend, 24));
    const u = m.unit || {};
    setId('chip-unit', has(u.name) ? str(u.name, 48) + (isNum(u.uptime_s) ? ' · up ' + dur(u.uptime_s) : '') : 'no inference unit');
    setId('chip-endpoint', 'API ' + str(m.endpoint, 64));

    // KPI tiles
    setId('sig-toks', toks(sv.tok_s));
    const sm = sv.streams || {};
    setId('sig-toks-sub', 'per stream ' + streamLine(sm));
    const hist = (sv.history && sv.history.tok_s) || [];
    const svg = $('spark-toks');
    if (svg) svg._step = sv.interval_s || 3;
    spark(svg, hist, $('spark-toks-read'), v => toks(v) + ' tok/s', 0, sv.history && sv.history.t);
    setId('sig-reqs', (isNum(sv.requests_running) ? sv.requests_running : DASH) + ' · ' + (isNum(sv.requests_waiting) ? sv.requests_waiting : DASH));
    setId('sig-reqs-sub', 'running · waiting' + (isNum(slots.total) ? ' · ' + slots.total + (slots.total === 1 ? ' slot' : ' slots') : ''));
    const kvKnown = isNum(sv.kv_usage);
    const fill = isNum(sv.ctx_fill_pct) ? sv.ctx_fill_pct : null;
    setId('sig-kv-k', kvKnown || fill === null ? 'KV cache' : 'context fill');
    setId('sig-kv', kvKnown ? frac(sv.kv_usage, 1) : fill !== null ? pct(fill, 1) : DASH);
    width($('sig-kv-bar'), kvKnown ? sv.kv_usage * 100 : fill);
    setId('sig-kv-sub', (isNum(m.ctx) ? ctxLabel(m.ctx) + ' context' : 'context —') + (!kvKnown && fill !== null ? ' · busiest slot' : ''));
    const lat = det.latency || {}, pr = det.per_request || {};
    const tt = lat.ttft || null;
    if (tt && (isNum(tt.p50) || isNum(tt.life_p50))) {
      const win = isNum(tt.p50);
      setId('sig-lat-k', 'TTFT p50');
      setId('sig-lat', secs(win ? tt.p50 : tt.life_p50));
      setId('sig-lat-sub', 'p95 ' + secs(win ? tt.p95 : tt.life_p95) + (lat.e2e ? ' · e2e p50 ' + secs(win ? lat.e2e.p50 : lat.e2e.life_p50) : '') + (win ? ' · 5 min' : ' · since start'));
    } else if (isNum(pr.decode_tok_s) || isNum(pr.life_decode_tok_s)) {
      setId('sig-lat-k', 'decode / request');
      setId('sig-lat', unit(isNum(pr.decode_tok_s) ? pr.decode_tok_s : pr.life_decode_tok_s, toks, ' t/s'));
      setId('sig-lat-sub', isNum(pr.prefill_tok_s) ? 'prefill ' + toks(pr.prefill_tok_s) + ' tok/s' : 'mean speed per request');
    } else { setId('sig-lat-k', 'latency'); setId('sig-lat', DASH); setId('sig-lat-sub', 'not reported by this engine'); }

    const g = st.gate || {};
    setId('sig-gate', !g.enabled ? 'off' : isNum(g.req_per_min) ? g.req_per_min.toFixed(g.req_per_min < 10 ? 1 : 0) + '/min' : DASH);
    setId('sig-gate-sub', !g.enabled ? 'gate off on this rig' : g.up === false ? 'gate DOWN'
      : 'errors 5 min ' + (isNum(g.errors_5m) ? int(g.errors_5m) : DASH) + ' · ' + (isNum(g.active_devices) ? g.active_devices : DASH) + ' active');

    const svcs = (st.service_list || []).map(s => serviceFields(s));
    const up = svcs.filter(s => s.state === 'up' || s.state === 'online').length;
    const on = svcs.filter(s => s.state !== 'off').length;
    const down = svcs.filter(s => s.state === 'down').map(s => s.name);
    setId('sig-svc', up + ' / ' + on);
    setId('sig-svc-sub', down.length ? 'down: ' + down.slice(0, 2).join(', ') + (down.length > 2 ? ' +' + (down.length - 2) : '') : 'all running');
    setId('services-note', up + ' of ' + on + ' running');

    renderAlerts(st.alerts);
    renderHomeCards(st);

    // Serving page: the engine block (status-driven).
    setId('serving-note', str(m.engine, 24) + (has(u.name) ? ' · ' + str(u.name, 48) : '') + (sv.tok_s_source === 'gate' ? ' · tok/s from beast-gate'
      : sv.tok_s_source === 'engine-completed' ? ' · tok/s per finished request' : ''));
    setId('engine-note', str(m.engine, 24) + ' · ' + str(m.endpoint, 64));
    setId('sv-model', str(m.id || m.alias, 120));
    setId('sig-ctx', ctxLabel(m.ctx) + (isNum(m.ctx) ? ' tokens' : ''));
    setId('sv-slots', (isNum(slots.busy) ? slots.busy : DASH) + ' / ' + (isNum(slots.total) ? slots.total : DASH));
    setId('sv-queue', isNum(cap.queue_deferred) ? cap.queue_deferred : DASH);
    setId('sv-running', isNum(sv.requests_running) ? sv.requests_running : DASH);
    setId('sv-waiting', isNum(sv.requests_waiting) ? sv.requests_waiting : DASH);
    setId('sv-kv', kvKnown ? frac(sv.kv_usage, 1) : fill !== null ? 'context fill ' + pct(fill, 1) : DASH);
    setId('sv-pool', isNum(cap.ctx_total) ? int(cap.ctx_total) + (cap.ctx_shared ? ' shared' : '') : (cap.ctx_shared ? 'shared pool' : DASH));
    setId('sv-prompt', toks(sv.prompt_tok_s));
    setId('sv-gen', toks(sv.tok_s) + (isNum(sm.aggregate_60s_tok_s) ? ' · 60 s ' + toks(sm.aggregate_60s_tok_s) : ''));
    setId('sv-per-stream', isNum(sm.per_stream_tok_s) ? toks(sm.per_stream_tok_s) + ' · ' + PER_STREAM_FROM[sm.per_stream_source || ''] : DASH);
    setId('sv-streams', (isNum(sm.decoding) ? sm.decoding : DASH) + ' · ' + (isNum(sm.prefilling) ? sm.prefilling : DASH) + (isNum(sm.active) ? ' of ' + sm.active + ' active' : ''));
    setId('sv-profile', str(cap.serving_profile, 32));
    const adm = sv.admission;
    setId('sv-admission', adm ? adm.per_device_inflight + ' in flight · ' + adm.per_device_rate_per_min + '/min' : DASH);
    setId('sv-backend', str(m.backend, 24));
    setId('sv-uptime', dur(u.uptime_s));
    setId('sv-life', big(sv.lifetime_gen_tokens) + ' generated');
    setId('sv-preempt', isNum(det.preemptions) ? int(det.preemptions) : DASH);

    // Gate page: the status-driven numbers.
    setId('gate-note', !g.enabled ? 'off on this rig' : g.up ? 'up · :' + portOf(st, 'gate') + ' · auth ' + str(g.auth || st.auth, 24) : g.up === false ? 'DOWN' : 'starting');
    setId('gt-devices', isNum(g.devices) ? g.devices : DASH);
    setId('gt-active', isNum(g.active_devices) ? g.active_devices : DASH);
    setId('gt-rpm', isNum(g.req_per_min) ? g.req_per_min.toFixed(g.req_per_min < 10 ? 1 : 0) : DASH);
    setId('gt-errors', int(g.errors_total));
    setId('gt-auth', str(g.auth || st.auth, 24));
    setId('gt-total', int(g.requests_total));
    setId('gt-denied-id', int(g.denied_identity));
    setId('gt-denied', int(g.denied_total));
    const why = g.denied_by_reason && typeof g.denied_by_reason === 'object'
      ? Object.keys(g.denied_by_reason).map(k => str(k, 32) + ' ' + int(g.denied_by_reason[k])).join(' · ') : '';
    const gd = $('gt-denied'); if (gd) gd.title = why;
    const tl = $('gt-tiles');
    if (tl) tiles(tl, [
      { key: 'rpm', k: 'requests / min', v: isNum(g.req_per_min) ? g.req_per_min.toFixed(1) : DASH, s: 'last minute' },
      { key: 'err', k: 'errors · 5 min', v: isNum(g.errors_5m) ? int(g.errors_5m) : DASH, s: isNum(g.requests_5m) && g.requests_5m ? pct(100 * (g.errors_5m || 0) / g.requests_5m, 1) + ' of ' + int(g.requests_5m) : 'of requests', state: isNum(g.errors_5m) && g.errors_5m > 0 ? 'warn' : '' },
      { key: 'dev', k: 'devices', v: isNum(g.devices) ? String(g.devices) : DASH, s: (isNum(g.active_devices) ? g.active_devices : DASH) + ' active · 15 min' },
      { key: 'ref', k: 'refused · total', v: int(g.denied_total), s: int(g.denied_identity) + ' with no identity' },
      { key: 'auth', k: 'auth', v: str(g.auth || st.auth, 24), s: g.enabled ? (g.up ? 'gate up' : g.up === false ? 'gate DOWN' : 'starting') : 'gate off' },
      { key: 'mtd', k: 'default max_tokens', v: isNum(g.max_tokens_default) ? (g.max_tokens_default > 0 ? String(g.max_tokens_default) : 'off') : DASH,
        s: isNum(g.max_tokens_defaulted) ? int(g.max_tokens_defaulted) + ' requests given it' : 'requests with no cap' },
    ]);
    const rb = g.denied_by_reason && typeof g.denied_by_reason === 'object' ? g.denied_by_reason : {};
    const rw = g.denied_by_reason_window && typeof g.denied_by_reason_window === 'object' ? g.denied_by_reason_window : null;
    const reasons = Object.keys(rb).map(k => ({ key: str(k, 32), name: str(k, 32), value: rb[k],
      text: int(rb[k]) + (rw && isNum(rw[k]) ? ' · ' + int(rw[k]) + ' last ' + dur(g.denied_window_s) : '') }))
      .sort((a, b) => b.value - a.value);
    hbars($('refused'), reasons);
    const re = $('refused-empty'); if (re) re.hidden = reasons.length > 0;

    // Services page.
    const nowS = isNum(st.generated_at) ? st.generated_at : Date.now() / 1000;
    keyed($('services'), (st.service_list || []).map(s => serviceFields(s, nowS)), s => s.key, () => {
      const li = mk('li', 'svc');
      const dot = mk('i', 'dot', li); dot.setAttribute('aria-hidden', 'true');
      const box = mk('span', 's-main', li);
      mk('span', 's-name', box); mk('span', 's-port', box);
      mk('span', 's-state', li);
      const meta = mk('span', 's-meta', li); mk('span', 's-since', meta); mk('span', 's-change', meta);
      return li;
    }, (li, s) => {
      setState(li, s.state);
      set(li.querySelector('.s-name'), s.name);
      li.title = s.name + (s.port ? ' ' + s.port : '') + ': ' + s.text;
      set(li.querySelector('.s-port'), s.port);
      set(li.querySelector('.s-state'), s.text);
      set(li.querySelector('.s-since'), s.since);
      set(li.querySelector('.s-change'), s.change);
    });

    // Tools page.
    const mt = st.metrics || {};
    setId('tl-calls', int(mt.tool_calls));
    setId('tl-errors', int(mt.tool_errors));
    setId('tl-rate', isNum(mt.tool_calls) && mt.tool_calls > 0 && isNum(mt.tool_errors) ? pct(100 * mt.tool_errors / mt.tool_calls, 1) : DASH);
    const top = Array.isArray(mt.top_tools) ? mt.top_tools : [];
    const te = $('tools-empty'); if (te) te.hidden = top.length > 0;
    hbars($('tools'), top.map(t => ({ key: str(t.tool, 48), name: str(t.tool, 48), value: isNum(t.calls) ? t.calls : 0,
      text: int(t.calls) + (t.errors ? ' · ' + int(t.errors) + ' err' : '') })));

    // Recent events (the overview card).
    const ev = Array.isArray(st.events) ? st.events.slice(0, 4) : [];
    const ee = $('events-empty'); if (ee) ee.hidden = ev.length > 0;
    eventRows($('events'), ev);
  }

  const LEVEL_TEXT = { crit: 'critical', warn: 'warning', info: 'info' };
  function eventRows(box, ev, withKind) {
    keyed(box, ev, e => (isNum(e.id) ? 'i' + e.id : e.t + '|' + str(e.text, 160)), () => {
      const li = mk('li');
      mk('time', '', li); const sp = mk('span', 'e-text', li);
      if (withKind) mk('span', 'e-kind', li);
      return li;
    }, (li, e) => {
      li.dataset.level = e.level === 'warn' || e.level === 'crit' ? e.level : 'info';
      const t = isNum(e.t) ? new Date(e.t * 1000) : null;
      const tm = li.querySelector('time');
      set(tm, t ? (withKind ? hhmmss(e.t) : when(e.t)) : DASH);
      if (t && tm && tm.setAttribute) tm.setAttribute('datetime', t.toISOString());
      set(li.querySelector('.e-text') || li.querySelector('span'), str(e.text, 160));
      const k = li.querySelector('.e-kind'); if (k) set(k, str(e.kind, 16));
    });
  }

  function portOf(st, name) {
    const s = (st.service_list || []).find(x => x.name === name);
    return s && isNum(s.port) ? s.port : DASH;
  }

  // Where an alert is about: its node, the serving engine, the gate, the services; else the log.
  const NODE_RULES = ['peer', 'temp', 'throttle', 'mem', 'disk', 'node'];
  // The GPU temperature the alerts call hot (DASHBOARD_ALERT_GPU_TEMP_C), from /api/status.
  function hotC() {
    const t = lastStatus && lastStatus.alerts && lastStatus.alerts.thresholds;
    return t && isNum(t.gpu_temp_c) ? t.gpu_temp_c : 85;
  }
  function alertHref(a) {
    const r = String((a && a.rule) || ''), k = String((a && a.key) || '');
    if (NODE_RULES.includes(r)) { const n = k.slice(k.indexOf(':') + 1).slice(0, 64); return n ? '#/nodes/' + encodeURIComponent(n) : '#/nodes'; }
    if (r === 'service') return '#/services';
    if (r === 'gate') return '#/gate';
    if (r === 'engine' || r === 'stall' || r === 'kv') return '#/serving';
    return '#/events';
  }
  function alertRows(box, list) {
    keyed(box, list, a => str(a.key, 96), () => {
      const li = mk('li', 'alert-item');
      const el = mk('a', 'alert', li);
      mk('span', 'a-level', el);
      const b = mk('span', 'a-body', el); mk('span', 'a-title', b); mk('span', 'a-detail', b);
      mk('span', 'a-since', el);
      return li;
    }, (li, a) => {
      const el = li.querySelector('.alert') || li;
      const h = alertHref(a);
      if (!el.getAttribute || el.getAttribute('href') !== h) el.setAttribute('href', h);
      const lv = a.level === 'crit' ? 'crit' : a.level === 'warn' ? 'warn' : 'info';
      if (el.dataset.level !== lv) el.dataset.level = lv;
      set(el.querySelector('.a-level'), LEVEL_TEXT[lv]);
      set(el.querySelector('.a-title'), str(a.title, 80));
      set(el.querySelector('.a-detail'), str(a.detail, 160));
      set(el.querySelector('.a-since'), isNum(a.since) ? 'since ' + hhmm(a.since) + ' · ' + dur(Date.now() / 1000 - a.since) : '');
    });
  }

  function renderAlerts(al) {
    const act = al && Array.isArray(al.active) ? al.active : [];
    const c = (al && al.counts) || {};
    const strip = $('alerts-strip'); if (strip) strip.hidden = !act.length;
    alertRows($('alert-list'), act);
    alertRows($('ev-alerts'), act);
    const ea = $('ev-alerts-empty'); if (ea) ea.hidden = act.length > 0;
    setId('ev-active-note', act.length ? (c.crit || 0) + ' critical · ' + (c.warn || 0) + ' warning' : 'all clear');
    setId('sig-alerts', String(act.length));
    setId('sig-alerts-sub', act.length ? str(act[0].title, 60) : 'none active');
    const k = $('kpi-alerts'); if (k && k.dataset) { const v = c.crit ? 'crit' : c.warn ? 'warn' : act.length ? 'info' : 'none'; if (k.dataset.level !== v) k.dataset.level = v; }
    const nb = $('nav-alerts');
    if (nb) { nb.hidden = !act.length; set(nb, String(act.length)); if (nb.dataset) nb.dataset.level = c.crit ? 'crit' : 'warn'; }
  }

  function renderHomeCards(st) {
    const sv = st.serving || {}, det = sv.detail || {}, lat = det.latency || {}, sm = st.summary || {};
    const e2e = lat.e2e || null, tt = lat.ttft || null, pr = det.per_request || {};
    if (tt && (isNum(tt.p95) || isNum(tt.life_p95))) { setId('c-sv-a-k', 'TTFT p95'); setId('c-sv-a', secs(isNum(tt.p95) ? tt.p95 : tt.life_p95)); }
    else if (isNum(pr.decode_tok_s)) { setId('c-sv-a-k', 'decode / request'); setId('c-sv-a', toks(pr.decode_tok_s) + ' t/s'); }
    else { setId('c-sv-a-k', 'latency'); setId('c-sv-a', DASH); }
    const spc = det.spec || {};
    setId('c-sv-b', isNum(spc.accept_pct) ? pct(spc.accept_pct, 0) : DASH);
    setId('c-sv-c', big(sm.gen_tokens));
    setId('c-sv-foot', (e2e && isNum(e2e.p50) ? 'e2e p50 ' + secs(e2e.p50) + ' · ' : '') + (isNum(det.prefix_hit_pct) ? 'prefix cache ' + pct(det.prefix_hit_pct, 0) + ' hit' : 'engine detail, latency, tokens'));
    const g = st.gate || {};
    setId('c-gt-a', g.enabled ? (isNum(g.devices) ? g.devices : DASH) + ' · ' + (isNum(g.active_devices) ? g.active_devices : DASH) : 'off');
    setId('c-gt-b', g.enabled ? int(g.errors_5m) : DASH);
    setId('c-gt-c', g.enabled ? int(g.denied_total) : DASH);
    setId('c-gt-foot', g.enabled ? 'auth ' + str(g.auth || st.auth, 24) + ' · ' + int(g.requests_total) + ' requests since start' : 'beast-gate is off on this rig');
    const svcs = (st.service_list || []).map(s => serviceFields(s));
    keyed($('c-svc-dots'), svcs, s => s.key, () => { const sp = mk('span', 'sd'); const d = mk('i', 'dot', sp); d.setAttribute('aria-hidden', 'true'); mk('span', '', sp); return sp; }, (sp, s) => {
      setState(sp, s.state); set(sp.querySelector('span'), s.name);
    });
    const down = svcs.filter(s => s.state === 'down').length;
    setId('c-svc-foot', down ? down + ' down' : 'all running · island ' + str(((st.service_list || []).find(s => s.name === 'island') || {}).state, 16));
    const mt = st.metrics || {}, top = Array.isArray(mt.top_tools) ? mt.top_tools : [];
    setId('c-tl-a', int(mt.tool_calls));
    setId('c-tl-b', isNum(mt.tool_calls) && mt.tool_calls > 0 ? pct(100 * (mt.tool_errors || 0) / mt.tool_calls, 1) : DASH);
    setId('c-tl-c', top.length ? str(top[0].tool, 24) : DASH);
    setId('c-hs-a', isNum(sm.uptime_pct) ? pct(sm.uptime_pct, sm.uptime_pct >= 99.95 ? 0 : 2) : DASH);
    setId('c-hs-b', isNum(sm.incidents) ? String(sm.incidents) + (sm.incidents_crit ? ' (' + sm.incidents_crit + ' crit)' : '') : DASH);
    setId('c-hs-c', big(isNum(sm.requests) ? sm.requests : sm.gate_requests));
    setId('c-hs-foot', isNum(sm.observed_pct) && sm.observed_pct < 99 ? 'observed ' + pct(sm.observed_pct, 0) + ' of the day · daily rollups' : 'daily rollups and the serving history');
  }

  // ------------------------------------------------------------ cluster: tiles, node cards, the power KPI
  function tileFields(n) {
    const f = nodeFields(n), g = n.gpu || {}, p = n.power || {};
    const pw = isNum(p.gpu_w) ? p.gpu_w : g.power_w;
    const lim = isNum(g.power_limit_w) ? g.power_limit_w : null;
    return Object.assign(f, {
      memShort: isNum(f.memPct) ? pct(f.memPct) : DASH, memK: g && g.unified ? 'unified mem' : n.gpu ? 'VRAM' : 'RAM',
      utilPct: g.util_pct, tempPct: isNum(g.temp_c) ? Math.min(100, g.temp_c) : null,
      powerShort: isNum(pw) ? watts(pw) : DASH, powerPct: isNum(pw) ? Math.min(100, 100 * pw / (lim || Math.max(pw, 1) * 1.25)) : null,
      hot: isNum(g.temp_c) && g.temp_c >= hotC(),
    });
  }

  function renderCluster(cl) {
    const nodes = Array.isArray(cl.nodes) ? cl.nodes : [];
    const peers = nodes.filter(n => n.role !== 'head').length;
    const reach = nodes.filter(n => n.state === 'up').length;
    const note = (has(cl.cluster) ? str(cl.cluster, 40) + ' · ' : '') + nodes.length + (nodes.length === 1 ? ' node' : ' nodes')
      + (peers ? ' · ' + reach + ' of ' + nodes.length + ' reachable' : ' · single node');
    setId('cluster-note', note);
    setId('home-nodes-note', note);
    const tt = $('t-tile');
    if (tt && tt.content) keyed($('home-nodes'), nodes, n => str(n.name, 64), () => tt.content.firstElementChild.cloneNode(true), (li, n) => {
      const f = tileFields(n), a = li.querySelector('a');
      if (a) { const h = '#/nodes/' + encodeURIComponent(String(n.name || '')); if (a.getAttribute('href') !== h) a.setAttribute('href', h); setState(a, f.state); if (a.dataset) a.dataset.hot = f.hot ? '1' : ''; }
      set(field(li, 'name'), f.name);
      setState(field(li, 'state'), f.state); set(field(li, 'state-text'), f.stateText);
      set(field(li, 'sub'), f.sub);
      set(field(li, 'mem-k'), f.memK); set(field(li, 'mem'), f.memShort); width(field(li, 'mem-bar'), f.memPct);
      set(field(li, 'util'), f.util); width(field(li, 'util-bar'), f.utilPct);
      set(field(li, 'temp'), f.temp); width(field(li, 'temp-bar'), f.tempPct);
      set(field(li, 'power'), f.powerShort); width(field(li, 'power-bar'), f.powerPct);
      const er = field(li, 'error'); if (er) { er.hidden = !f.error; set(er, f.error); }
    });
    const tpl = $('t-node');
    if (tpl && tpl.content) keyed($('nodes'), nodes, n => str(n.name, 64), () => tpl.content.firstElementChild.cloneNode(true), (card, n) => {
      const f = nodeFields(n);
      const href = '#/nodes/' + encodeURIComponent(String(n.name || ''));
      for (const k of ['link', 'more']) { const a = field(card, k); if (a && a.getAttribute('href') !== href) a.setAttribute('href', href); }
      setState(card, f.state);
      set(field(card, 'name'), f.name);
      set(field(card, 'sub'), f.sub);
      const chip = field(card, 'state'); setState(chip, f.state);
      set(field(card, 'state-text'), f.stateText);
      const er = field(card, 'error'); er.hidden = !f.error; set(er, f.error);
      set(field(card, 'gpu'), f.gpu);
      set(field(card, 'mem-label'), f.memLabel);
      set(field(card, 'mem-kind'), f.memKind);
      set(field(card, 'mem'), f.mem);
      set(field(card, 'mem-pct'), pct(f.memPct));
      const ring = field(card, 'mem-ring');
      const off = 169.65 * (1 - (isNum(f.memPct) ? Math.max(0, Math.min(100, f.memPct)) / 100 : 0));
      if (ring) ring.style.strokeDashoffset = String(off);
      set(field(card, 'util'), f.util);
      set(field(card, 'temp'), f.temp);
      set(field(card, 'power'), f.power);
      set(field(card, 'clock'), f.clock + (f.slowdown ? ' · throttled' : ''));
      const pd2 = field(card, 'power'); if (pd2) pd2.title = f.powerTitle || '';
      set(field(card, 'cpu'), f.cpu);
      set(field(card, 'load'), f.load);
      set(field(card, 'ram'), f.ram);
      set(field(card, 'uptime'), f.uptime);
      set(field(card, 'disk'), f.disk);
      const sp = field(card, 'sparks'); if (sp) sp.hidden = !n.gpu;
      const h = n.history || {};
      const step = n.interval_s || 5;
      const su = field(card, 'spark-util'), sm = field(card, 'spark-mem'), sp2 = field(card, 'spark-pow');
      for (const el of [su, sm, sp2]) if (el) el._step = step;
      spark(su, h.util || [], field(card, 'spark-util-read'), v => pct(v), 100, h.t);
      spark(sm, h.mem || [], field(card, 'spark-mem-read'), v => pct(v), 100, h.t);
      spark(sp2, h.power || [], field(card, 'spark-pow-read'), v => watts(v), 0, h.t);
      renderPorts(card, n);
    });
    // The overview's power KPI and card: cluster now and the 1 h average, from the power block.
    const pw = cl.power || {}, c = pw.cluster || {}, w = c.windows || {};
    setId('sig-pow', isNum(c.now_w) ? watts(c.now_w) : DASH);
    setId('sig-pow-sub', '1 h avg ' + (isNum((w['1h'] || {}).avg_w) ? watts(w['1h'].avg_w) : (w['1h'] || {}).partial ? 'partial' : 'collecting')
      + (c.nodes > 1 ? ' · ' + c.nodes_measured + '/' + c.nodes + ' nodes' : ''));
    setId('c-pw-a', isNum((w['1h'] || {}).avg_w) ? watts(w['1h'].avg_w) : DASH);
    setId('c-pw-b', energy((w['24h'] || {}).energy_wh, true));
    const tr = trendText(c.trend);
    setId('c-pw-c', tr.dir === 'none' ? 'collecting' : tr.arrow + ' ' + tr.dir);
    setId('c-pw-foot', str(c.label || 'measured power', 60) + (pw.cost && isNum(pw.cost.per_day) ? ' · ' + money(pw.cost.per_day, pw.cost.currency) + ' / day' : ''));
    if (route.page === 'node') renderNodeHead(nodes.find(n => String(n.name) === route.arg) || null);
  }

  function renderPorts(card, n) {
    const ports = (Array.isArray(n.fabric) ? n.fabric : []).map(portFields)
      .sort((a, b) => (a.state === b.state ? a.key.localeCompare(b.key) : a.state === 'up' ? -1 : 1));
    const pe = field(card, 'ports-empty'); if (pe) pe.hidden = ports.length > 0 || n.state !== 'up';
    // Linked ports get a row each; the ports without a link fold into one line.
    const down = ports.filter(p => p.state !== 'up');
    const pd = field(card, 'ports-down');
    if (pd) { pd.hidden = !down.length; set(pd, down.length ? down.length + ' more ' + (down.length === 1 ? 'port' : 'ports') + ' without link: ' + down.map(p => p.name.split(' · ')[0]).join(', ') : ''); }
    keyed(field(card, 'ports'), ports.filter(p => p.state === 'up'), p => p.key, () => {
      const li = mk('li', 'port');
      const dot = mk('i', 'dot', li); dot.setAttribute('aria-hidden', 'true');
      mk('span', 'p-name', li); mk('span', 'p-rate', li); mk('span', 'p-io', li);
      return li;
    }, (li, p) => {
      setState(li, p.state);
      set(li.querySelector('.p-name'), p.name);
      set(li.querySelector('.p-rate'), p.rate);
      set(li.querySelector('.p-io'), p.io);
    });
  }

  // ------------------------------------------------------------ node detail
  const NODE_CHARTS = [
    { key: 'util', title: 'GPU utilization', max: 100, fmt: v => pct(v), series: [['gpu_util', 'GPU']] },
    { key: 'mem', title: 'GPU memory', max: 100, fmt: v => pct(v), series: [['gpu_mem', 'used']] },
    { key: 'temp', title: 'GPU temperature', fmt: degC, series: [['gpu_temp', 'GPU']] },
    { key: 'pow', title: 'Power', fmt: watts, dedupe: true, series: [['gpu_power', 'GPU'], ['power', 'measured total']] },
    { key: 'clock', title: 'SM clock', fmt: mhz, series: [['sm_clock', 'SM']] },
    { key: 'cpu', title: 'CPU', max: 100, fmt: v => pct(v), series: [['cpu', 'CPU']] },
    { key: 'ram', title: 'RAM', max: 100, fmt: v => pct(v), series: [['ram', 'used']] },
    { key: 'disk', title: 'Model volume', max: 100, fmt: v => pct(v), series: [['disk', 'used']] },
    { key: 'up', title: 'Reachable', max: 100, scale: 100, fmt: v => pct(v), series: [['up', 'reachable']] },
  ];

  function renderNodeHead(n) {
    const miss = $('nd-missing');
    if (miss) miss.hidden = !!n || !lastCluster;
    setId('nd-crumb', str(route.arg, 64));
    setId('nd-name', str(route.arg, 64));
    if (!n) {
      setId('nd-sub', lastCluster ? 'not a configured node' : 'loading');
      if (lastCluster) { setState($('nd-state'), 'unknown'); setId('nd-state-text', 'unknown'); }
      return;
    }
    const f = nodeFields(n), g = n.gpu || {};
    setId('nd-sub', f.sub + (has(f.gpu) && f.gpu !== DASH ? ' · ' + f.gpu : ''));
    setState($('nd-state'), f.state); setId('nd-state-text', f.stateText);
    const er = $('nd-error'); if (er) { er.hidden = !f.error; set(er, f.error); }
    const r = n.ram || {}, k = n.disk || {};
    const c = n.cpu || {}, p = n.power || {};
    const gpuW = isNum(p.gpu_w) ? p.gpu_w : g.power_w;
    // A node that is not answering keeps its last reading on the page, marked and dimmed as
    // such (as the overview dims its card), never at full brightness as if it were live.
    const old = f.state !== 'up';
    const last = sub => old ? (isNum(n.last_ok_age_s) ? 'last reading ' + ago(n.last_ok_age_s) : 'last reading') : sub;
    for (const id of ['nd-tiles', 'nd-charts']) { const el = $(id); if (el && el.dataset) { const v = old ? '1' : ''; if (el.dataset.stale !== v) el.dataset.stale = v; } }
    tiles($('nd-tiles'), [
      { key: 'util', k: 'GPU utilization', v: f.util, s: last(n.gpu ? str(g.name, 40) + (g.count > 1 ? ' ×' + g.count : '') : f.gpu) },
      { key: 'mem', k: f.memLabel, v: pct(f.memPct), s: last(f.mem) },
      { key: 'temp', k: 'GPU temperature', v: f.temp, s: old ? last('') : '', state: !old && isNum(g.temp_c) && g.temp_c >= hotC() ? 'warn' : '' },
      { key: 'power', k: 'GPU power', v: watts(gpuW), s: last((/GB10/.test(String(g.name || '')) ? 'GPU rail only (GB10)' : n.gpu ? 'board power' : '')
        + (isNum(g.power_limit_w) ? ' · limit ' + watts(g.power_limit_w) : '')) },
      { key: 'clock', k: 'SM clock', v: mhz(g.sm_clock_mhz), s: last((isNum(g.sm_clock_max_mhz) ? 'max ' + mhz(g.sm_clock_max_mhz) + ' · ' : '')
        + (f.throttle ? f.throttle : isNum(g.sm_clock_mhz) ? 'no clock event' : 'not reported')), state: !old && f.slowdown ? 'warn' : '' },
      { key: 'cpu', k: 'CPU', v: pct(c.util_pct), s: last((isNum(c.ncpu) ? c.ncpu + ' cores · ' : '') + 'load ' + f.load) },
      { key: 'ram', k: 'RAM', v: r.pct !== undefined ? pct(r.pct) : DASH, s: last(r.total_mib ? gib(r.total_mib - r.used_mib) + ' GiB available' : '') },
      { key: 'disk', k: 'model volume', v: isNum(k.pct) ? pct(k.pct) + ' used' : DASH, s: last(k.free_b ? bytes(k.free_b) + ' free' : '') },
      { key: 'up', k: 'uptime', v: f.uptime, s: isNum(n.last_ok_age_s) ? 'read ' + ago(n.last_ok_age_s) : '' },
    ]);
  }

  function renderNode(doc) {
    if (!doc || !doc.node) return;
    const n = doc.node;
    renderNodeHead(n);
    alertRows($('nd-alerts'), Array.isArray(doc.alerts) ? doc.alerts : []);
    chartBox($('nd-charts'), NODE_CHARTS.map(c => Object.assign({ win: doc.window }, c)), doc);
    const fab = doc.fabric_series || {};
    const ports = Object.keys(fab).sort();
    const fdoc = { window: doc.window, window_s: doc.window_s, t0: doc.t0, step: doc.step, series: {} };
    const specs = ports.map(p => {
      fdoc.series[p + ':tx'] = fab[p].tx || []; fdoc.series[p + ':rx'] = fab[p].rx || [];
      const live = (n.fabric || []).find(x => (x.hca + '/' + x.port) === p) || {};
      return { key: 'fab-' + p, win: doc.window, title: (has(live.netdev) ? str(live.netdev, 32) + ' · ' : '') + str(p, 40), fmt: bps,
        series: [[p + ':tx', 'tx ↑'], [p + ':rx', 'rx ↓']] };
    });
    chartBox($('nd-fab'), specs, fdoc);
    const fe = $('nd-fab-empty'); if (fe) fe.hidden = ports.length > 0;
    setId('nd-fab-note', ports.length ? ports.length + (ports.length === 1 ? ' linked port' : ' linked ports') + ' · bits per second' : '—');
    const pwin = doc.power_windows || {};
    keyed($('nd-pw'), ['1m', '5m', '15m', '1h', '24h'].filter(k => pwin[k]).map(k => windowFields(k, pwin[k])), w => w.key, () => {
      const li = mk('li', 'pw-win');
      mk('span', 'w-k', li); mk('span', 'w-v', li); mk('span', 'w-r', li); mk('span', 'w-e', li);
      return li;
    }, (li, w) => {
      set(li.querySelector('.w-k'), w.label); set(li.querySelector('.w-v'), w.value);
      set(li.querySelector('.w-r'), w.range); set(li.querySelector('.w-e'), w.energy);
      li.dataset.cov = w.low ? 'low' : 'ok'; li.title = w.title;
    });
  }

  // ------------------------------------------------------------ serving detail
  const SV_CHARTS = [
    { key: 'tok', title: 'Generation', fmt: v => toks(v) + ' tok/s', series: [['serve:tok_s', 'all streams'], ['serve:per_stream_tok_s', 'per stream']] },
    { key: 'prompt', title: 'Prompt processing', fmt: v => toks(v) + ' tok/s', series: [['serve:prompt_tok_s', 'prompt']] },
    { key: 'req', title: 'Requests', fmt: v => isNum(v) ? (Math.round(v * 10) / 10).toString() : DASH, series: [['serve:running', 'running'], ['serve:waiting', 'waiting']] },
    { key: 'kv', title: 'KV cache / context', max: 100, fmt: v => pct(v, 1), series: [['serve:kv_pct', 'KV use'], ['serve:ctx_fill_pct', 'context fill']] },
    { key: 'ttft', title: 'Time to first token', fmt: secs, series: [['serve:ttft_p50', 'p50'], ['serve:ttft_p95', 'p95']] },
    { key: 'e2e', title: 'End-to-end latency', fmt: secs, series: [['serve:e2e_p50', 'p50'], ['serve:e2e_p95', 'p95']] },
    { key: 'itl', title: 'Inter-token latency', fmt: secs, series: [['serve:itl_p50', 'p50']] },
    { key: 'queue', title: 'Queue time', fmt: secs, series: [['serve:queue_p95', 'p95']] },
    { key: 'spec', title: 'Speculative acceptance', max: 100, fmt: v => pct(v, 1), series: [['serve:spec_accept_pct', 'accepted']] },
    { key: 'prefix', title: 'Prefix cache hit', max: 100, fmt: v => pct(v, 1), series: [['serve:prefix_hit_pct', 'hit']] },
    { key: 'reqs', title: 'Requests completed', perMin: true, fmt: v => isNum(v) ? v.toFixed(v < 10 ? 1 : 0) + '/min' : DASH, series: [['serve:requests', 'per min']] },
  ];

  // Where the per-stream rate comes from (dashboard.py streams_view).
  const PER_STREAM_FROM = { slots: 'median of the slots', batch: 'batched: each decoding stream',
    estimate: 'all streams / running (a lower bound)', finished: 'finished requests, 5 min', '': '' };
  function streamLine(sm) {
    if (!sm || !isNum(sm.per_stream_tok_s)) return DASH;
    const n = isNum(sm.decoding) ? sm.decoding : sm.active;
    return toks(sm.per_stream_tok_s) + (isNum(n) ? ' · ' + n + (n === 1 ? ' stream' : ' streams') : '');
  }

  function renderServing(doc) {
    if (!doc) return;
    const nw = doc.now || {}, det = doc.detail || {}, lat = det.latency || {}, spc = det.spec || null, pr = det.per_request || {};
    const tok = doc.tokens || {}, l24 = tok.last_24h || {};
    const lw = det.window_s ? 'last ' + dur(det.window_s) : 'since engine start';
    const L = (h, q) => h ? (isNum(h[q]) ? h[q] : h['life_' + q]) : null;
    // Each value says where it came from: the window, or (when the window had
    // no observation and the value falls back to the lifetime) the engine's start.
    const W = (h, q) => h && isNum(h[q]) ? lw : 'since engine start';
    const items = [
      { key: 'gen', k: 'generation · all streams', v: unit(nw.tok_s, toks, ' tok/s'), s: nw.tok_s_source === 'engine-completed' ? 'per finished request'
        : 'aggregate' + (nw.streams && isNum(nw.streams.active) ? ' over ' + nw.streams.active + ' active' : '') },
      { key: 'stream', k: 'generation · per stream', v: unit((nw.streams || {}).per_stream_tok_s, toks, ' tok/s'),
        s: nw.streams && isNum(nw.streams.per_stream_tok_s) ? PER_STREAM_FROM[nw.streams.per_stream_source || ''] + (isNum(nw.streams.decoding) ? ' · ' + nw.streams.decoding + ' decoding' : '')
          + (isNum(nw.streams.prefilling) && nw.streams.prefilling ? ' · ' + nw.streams.prefilling + ' prefilling' : '') : 'no stream decoding' },
      { key: 'prompt', k: 'prompt', v: unit(nw.prompt_tok_s, toks, ' tok/s'), s: nw.prompt_tok_s_source === 'engine-completed' ? 'per finished prefill' : 'computed prefill, cache hits excluded' },
      { key: 'req', k: 'running · waiting', v: (isNum(nw.running) ? nw.running : DASH) + ' · ' + (isNum(nw.waiting) ? nw.waiting : DASH),
        s: isNum(nw.stalled_s) && nw.stalled_s > 30 ? 'no token for ' + dur(nw.stalled_s) : isNum(det.requests_per_min) ? det.requests_per_min.toFixed(1) + ' done / min' : '',
        state: isNum(nw.stalled_s) && nw.stalled_s > 60 ? 'warn' : '' },
      { key: 'kv', k: isNum(nw.kv_pct) ? 'KV cache' : 'context fill', v: pct(isNum(nw.kv_pct) ? nw.kv_pct : nw.ctx_fill_pct, 1), s: isNum(nw.kv_pct) ? 'of the block pool' : 'busiest slot' },
    ];
    if (lat.ttft) {
      const n = isNum(lat.ttft.p50) ? lat.ttft.count : lat.ttft.life_count;
      // p50 as the value, p95 on the line below: a "p50 · p95" pair broke mid-pair in a tile.
      items.push({ key: 'ttft', k: 'TTFT p50', v: secs(L(lat.ttft, 'p50')), s: 'p95 ' + secs(L(lat.ttft, 'p95')) + ' · ' + W(lat.ttft, 'p50') + (isNum(n) ? ' · ' + int(n) + ' req' : '') });
    }
    if (lat.e2e) items.push({ key: 'e2e', k: 'end-to-end p50', v: secs(L(lat.e2e, 'p50')), s: 'p95 ' + secs(L(lat.e2e, 'p95')) + ' · ' + W(lat.e2e, 'p50') });
    if (isNum(pr.decode_tok_s) || isNum(pr.life_decode_tok_s)) items.push({ key: 'dec', k: 'decode per request', v: unit(isNum(pr.decode_tok_s) ? pr.decode_tok_s : pr.life_decode_tok_s, toks, ' tok/s'),
      s: (isNum(pr.decode_tok_s) ? '' : 'since engine start · ') + (pr.from ? 'from ' + pr.from : isNum(pr.prefill_tok_s) ? 'prefill ' + toks(pr.prefill_tok_s) + ' tok/s' : 'mean, per stream') });
    if (spc) {
      const ml = isNum(spc.mean_len) ? spc.mean_len : spc.life_mean_len;
      items.push({ key: 'spec', k: 'spec. acceptance', v: pct(isNum(spc.accept_pct) ? spc.accept_pct : spc.life_accept_pct, 1),
        s: (isNum(spc.accept_pct) ? lw : 'since engine start') + (isNum(ml) ? ' · mean ' + ml.toFixed(2) + ' / step' : '') });
    }
    if (isNum(det.prefix_hit_pct)) items.push({ key: 'prefix', k: 'prefix cache hit', v: pct(det.prefix_hit_pct, 1), s: lw });
    items.push({ key: 'tok24', k: 'tokens · 24 h', v: big(l24.gen_tokens), s: big(l24.prompt_tokens) + ' prompt' });
    tiles($('sv-tiles'), items);
    // Under speculative decoding one inter-token-latency sample is one engine
    // step (1 + accepted tokens), not one token: say so on the chart.
    const tps = spc ? (isNum(spc.mean_len) ? spc.mean_len : spc.life_mean_len) : null;
    chartBox($('sv-charts'), SV_CHARTS.map(c => Object.assign({ win: doc.window }, c,
      c.key === 'itl' && spc ? { title: 'Decode step latency' + (isNum(tps) ? ' (~' + tps.toFixed(1) + ' tok/step)' : ' (spec decode)') } : {})), doc);
    const hourly = Array.isArray(tok.hourly) ? tok.hourly : [];
    vbars($('tok-hourly'), hourly.map(h => ({ key: h.t, value: h.gen, label: new Date(h.t * 1000).getHours() % 6 === 0 ? hhmm(h.t) : '', title: hhmm(h.t) + '–' + hhmm(h.t + 3600) })), big);
    const sum24 = hourly.reduce((a, h) => a + (isNum(h.gen) ? h.gen : 0), 0);
    setId('tok-h-note', 'last 24 h · ' + big(sum24) + ' total');
    const daily = (Array.isArray(tok.daily) ? tok.daily : []).slice().reverse();
    vbars($('tok-daily'), daily.map(d => ({ key: d.day, value: d.gen_tokens, label: String(d.day).slice(8), title: str(d.day, 10) })), big);
    setId('tok-d-note', daily.length ? 'last ' + daily.length + (daily.length === 1 ? ' day' : ' days') + ' · local days' : 'collecting');
    setId('spec-note', spc ? (isNum(spc.accept_pct) ? lw : 'since engine start') : 'not reported');
    setId('sp-acc', spc ? pct(spc.accept_pct, 1) : DASH);
    setId('sp-len', spc && isNum(spc.mean_len) ? spc.mean_len.toFixed(2) + ' tokens / step' : DASH);
    setId('sp-life', spc ? pct(spc.life_accept_pct, 1) + (isNum(spc.life_mean_len) ? ' · ' + spc.life_mean_len.toFixed(2) + ' / step' : '') : DASH);
    setId('sp-prefix', isNum(det.prefix_hit_pct) ? pct(det.prefix_hit_pct, 1) + (isNum(det.life_prefix_hit_pct) ? ' · ' + pct(det.life_prefix_hit_pct, 1) + ' since start' : '') : DASH);
    const pos = spc && Array.isArray(spc.per_pos_pct) ? spc.per_pos_pct : [];
    hbars($('spec-pos'), pos.map((v, i) => ({ key: 'p' + i, name: 'position ' + (i + 1), value: v, max: 100, text: pct(v, 1) })));
    const se = $('spec-empty'); if (se) se.hidden = pos.length > 0;
    const fin = det.finished && typeof det.finished === 'object' ? det.finished : {};
    const fk = Object.keys(fin);
    const ftot = fk.reduce((a, k) => a + (isNum(fin[k]) ? fin[k] : 0), 0);
    hbars($('finished'), fk.map(k => ({ key: str(k, 24), name: str(k, 24), value: fin[k], text: int(fin[k]) + (ftot ? ' · ' + pct(100 * fin[k] / ftot, 1) : '') }))
      .sort((a, b) => b.value - a.value));
    const fe = $('finished-empty'); if (fe) fe.hidden = fk.length > 0;
  }

  // ------------------------------------------------------------ gate / tools history
  const GATE_CHARTS = [
    { key: 'rpm', title: 'Requests per minute', fmt: v => isNum(v) ? v.toFixed(v < 10 ? 1 : 0) + '/min' : DASH, series: [['gate:rpm', 'requests']] },
    { key: 'err', title: 'Errors and refusals', perMin: true, fmt: v => isNum(v) ? v.toFixed(v < 10 ? 1 : 0) + '/min' : DASH, series: [['gate:errors', 'errors'], ['gate:denied', 'refused']] },
  ];
  const TOOL_CHARTS = [
    { key: 'calls', title: 'Tool calls', perMin: true, fmt: v => isNum(v) ? v.toFixed(v < 10 ? 1 : 0) + '/min' : DASH, series: [['tools:calls', 'calls'], ['tools:errors', 'errors']] },
  ];

  // ------------------------------------------------------------ events page
  const EV_KINDS = ['all', 'alert', 'node', 'model', 'swap', 'service', 'gate', 'island', 'dashboard'];
  let evKind = 'all';
  function renderEvents(doc) {
    if (!doc) return;
    const all = Array.isArray(doc.events) ? doc.events : [];
    const by = doc.by_kind || {};
    const seg = $('ev-kind');
    if (seg && seg.appendChild && !seg.children.length) {
      for (const k of EV_KINDS) {
        const b = mk('button', '', seg); b.type = 'button'; b.dataset.w = k;
        mk('span', '', b); mk('small', '', b);
        b.addEventListener('click', () => { evKind = k; renderEvents(lastEvents); });
      }
    }
    if (seg) for (const b of Array.from(seg.children)) {
      b.setAttribute('aria-pressed', String(b.dataset.w === evKind));
      set(b.querySelector('span'), b.dataset.w);
      set(b.querySelector('small'), b.dataset.w === 'all' ? String(all.length) : String(by[b.dataset.w] || 0));
    }
    const list = evKind === 'all' ? all : all.filter(e => e.kind === evKind);
    // Grouped by local day: a header row per day, then the day's events.
    const rows = [];
    let day = null;
    for (const e of list.slice(0, 500)) {
      const dl = dayLabel(e.t);
      if (dl !== day) { rows.push({ hdr: true, key: 'd' + dl, text: dl }); day = dl; }
      rows.push(e);
    }
    keyed($('ev-timeline'), rows, r => r.hdr ? r.key : (isNum(r.id) ? 'i' + r.id : r.t + '|' + r.text), r => {
      const li = mk('li', r.hdr ? 'tl-day' : 'tl-ev');
      if (!r.hdr) { mk('time', '', li); mk('span', 'e-kind', li); mk('span', 'e-text', li); }
      return li;
    }, (li, r) => {
      if (r.hdr) { set(li, r.text); return; }
      li.dataset.level = r.level === 'warn' || r.level === 'crit' ? r.level : 'info';
      li.dataset.tag = r.tag === 'raise' || r.tag === 'clear' ? r.tag : '';
      set(li.querySelector('time'), hhmmss(r.t));
      set(li.querySelector('.e-kind'), str(r.kind, 16));
      set(li.querySelector('.e-text'), str(r.text, 160));
    });
    const ee = $('ev-empty'); if (ee) ee.hidden = list.length > 0;
    setId('events-note', all.length + ' of at most ' + (doc.max || 500) + ' kept · persisted across restarts');
  }

  function renderRules(doc) {
    if (!doc || !Array.isArray(doc.thresholds)) return;
    const tb = $('rules') && $('rules').querySelector ? $('rules').querySelector('tbody') : null;
    keyed(tb, doc.thresholds, t => str(t.name, 64), () => {
      const tr = mk('tr'); mk('td', 'mono', tr); mk('td', 'num', tr); mk('td', '', tr); return tr;
    }, (tr, t) => {
      const td = tr.children;
      set(td[0], str(t.name, 64));
      set(td[1], isNum(t.value) ? String(+t.value.toFixed(2)) + (t.value !== t.default && isNum(t.default) ? ' (default ' + (+t.default.toFixed(2)) + ')' : '') : DASH);
      set(td[2], str(t.about, 120));
    });
    setId('rules-note', 'DASHBOARD_ALERT_* in openbeast.conf or the environment' + (Array.isArray(doc.off) && doc.off.length ? ' · off: ' + doc.off.map(x => str(x, 16)).join(', ') : '')
      + ' · a condition clears after ' + (doc.clear_s || 10) + ' s');
    renderAlerts(doc);
  }

  // ------------------------------------------------------------ history page
  function renderHistory(doc) {
    if (!doc) return;
    const l = doc.last_24h || {};
    const nodes = Array.isArray(l.nodes) ? l.nodes : [];
    tiles($('hs-tiles'), [
      { key: 'en', k: 'energy', v: energy(l.energy_wh, true), s: (isNum(l.avg_w) ? 'avg ' + watts(l.avg_w) : 'avg —') + (isNum(l.peak_w) ? ' · peak ' + watts(l.peak_w) : '') + (isNum(l.energy_coverage) && l.energy_coverage < 0.95 ? ' · ' + pct(100 * l.energy_coverage, 0) + ' covered' : '') },
      { key: 'gen', k: 'tokens generated', v: big(l.gen_tokens), s: big(l.prompt_tokens) + ' prompt' },
      { key: 'req', k: 'requests', v: big(isNum(l.requests) ? l.requests : l.gate_requests), s: isNum(l.gate_requests) ? big(l.gate_requests) + ' through the gate · ' + int(l.gate_errors) + ' errors' : '' },
      { key: 'up', k: 'model uptime', v: isNum(l.uptime_pct) ? pct(l.uptime_pct, l.uptime_pct >= 99.95 ? 0 : 2) : DASH, s: 'observed ' + pct(l.observed_pct, 0) + ' of 24 h', state: isNum(l.uptime_pct) && l.uptime_pct < 99 ? 'warn' : '' },
      { key: 'inc', k: 'incidents', v: isNum(l.incidents) ? String(l.incidents) : DASH, s: (l.incidents_crit || 0) + ' critical', state: l.incidents_crit ? 'crit' : l.incidents ? 'warn' : '' },
      ...nodes.map(n => ({ key: 'n-' + str(n.name, 64), k: str(n.name, 64) + ' reachable', v: isNum(n.reachable_pct) ? pct(n.reachable_pct, n.reachable_pct >= 99.95 ? 0 : 2) : DASH, s: 'last 24 h' })),
    ]);
    const hourly = Array.isArray(l.tokens_hourly) ? l.tokens_hourly : [];
    vbars($('hs-hourly'), hourly.map(h => ({ key: h.t, value: h.gen, label: new Date(h.t * 1000).getHours() % 6 === 0 ? hhmm(h.t) : '', title: hhmm(h.t) + '–' + hhmm(h.t + 3600) })), big);
    const inc = Array.isArray(l.incident_list) ? l.incident_list : [];
    eventRows($('hs-incidents'), inc);
    const ie = $('hs-incidents-empty'); if (ie) ie.hidden = inc.length > 0;
    const days = Array.isArray(doc.days) ? doc.days : [];
    const tb = $('days') && $('days').querySelector ? $('days').querySelector('tbody') : null;
    keyed(tb, days, d => str(d.day, 10), () => { const tr = mk('tr'); for (let i = 0; i < 7; i++) mk(i ? 'td' : 'th', i ? 'num' : '', tr); return tr; }, (tr, d) => {
      const c = tr.children;
      set(c[0], str(d.day, 10)); if (c[0].setAttribute) c[0].setAttribute('scope', 'row');
      set(c[1], energy(d.energy_wh, true)); set(c[2], big(d.gen_tokens)); set(c[3], big(d.prompt_tokens));
      set(c[4], big(d.requests || d.gate_requests)); set(c[5], isNum(d.uptime_pct) ? pct(d.uptime_pct, d.uptime_pct >= 99.95 ? 0 : 2) : DASH);
      set(c[6], String(d.incidents || 0));
      if (tr.dataset) tr.dataset.bad = d.incidents ? '1' : '';
    });
    const de = $('days-empty'); if (de) de.hidden = days.length > 0;
    setId('days-note', days.length + ' of ' + (doc.keep_days || 90) + ' kept · local days');
    const sw = Array.isArray(doc.swaps) ? doc.swaps : [];
    const sb = $('swaps') && $('swaps').querySelector ? $('swaps').querySelector('tbody') : null;
    keyed(sb, sw, s => s.from + '|' + str(s.model, 120), () => { const tr = mk('tr'); for (let i = 0; i < 6; i++) mk('td', i >= 3 ? 'num' : '', tr); return tr; }, (tr, s) => {
      const c = tr.children;
      set(c[0], str(s.model, 120)); set(c[1], str(s.unit, 64)); set(c[2], str(s.engine, 24));
      set(c[3], when(s.from)); set(c[4], isNum(s.to) ? when(s.to) : 'serving now');
      set(c[5], dur((isNum(s.to) ? s.to : (isNum(s.last_seen) ? s.last_seen : Date.now() / 1000)) - s.from));
      if (tr.dataset) tr.dataset.now = isNum(s.to) ? '' : '1';
    });
    const se = $('swaps-empty'); if (se) se.hidden = sw.length > 0;
    setId('history-note', 'generated ' + hhmmss(doc.generated_at));
  }

  // ------------------------------------------------------------ routing
  // Hash routes, so a deep link (#/nodes/spark-2) needs nothing from the server and Back /
  // Forward walk the pages. An unknown route is the overview.
  const PAGES = ['home', 'serving', 'nodes', 'node', 'power', 'gate', 'services', 'tools', 'events', 'history'];
  const TITLES = { home: 'Overview', serving: 'Serving', nodes: 'Nodes', power: 'Power', gate: 'Gate', services: 'Services',
    tools: 'Tools', events: 'Events', history: 'History' };
  function parseRoute(hash) {
    const h = String(hash || '').replace(/^#\/?/, '');
    const parts = h.split('/').filter(Boolean);
    if (!parts.length) return { page: 'home', arg: null };
    if (parts[0] === 'nodes' && parts.length > 1) {
      let a = parts.slice(1).join('/');
      try { a = decodeURIComponent(a); } catch (e) { /* a malformed escape stays as typed */ }
      return { page: 'node', arg: a.slice(0, 64) };
    }
    return PAGES.includes(parts[0]) && parts[0] !== 'node' ? { page: parts[0], arg: null } : { page: 'home', arg: null };
  }
  let route = { page: 'home', arg: null };
  let shownNode = null;

  function showRoute(focus) {
    route = parseRoute(globalThis.location ? location.hash : '');
    for (const sec of Array.from(document.querySelectorAll('[data-page]'))) sec.hidden = sec.dataset.page !== route.page;
    const navKey = route.page === 'node' ? 'nodes' : route.page;
    for (const a of Array.from(document.querySelectorAll('.nav a'))) {
      if (a.dataset.route === navKey) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
    document.title = (route.page === 'node' ? str(route.arg, 64) : TITLES[route.page]) + ' · OpenBeast Status';
    const cur = document.querySelector('.nav a[aria-current]');
    if (cur && cur.scrollIntoView) { try { cur.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (e) { /* old engine */ } }
    if (focus) {
      if (globalThis.scrollTo) scrollTo(0, 0);
      const sec = document.querySelector('[data-page="' + route.page + '"]');
      const h = sec && sec.querySelector('h1');
      if (h && h.focus) h.focus({ preventScroll: true });
    }
    // Another node: its charts are not the last node's charts, not even for a moment.
    if (route.page === 'node' && shownNode !== route.arg) {
      for (const id of ['nd-charts', 'nd-fab', 'nd-alerts', 'nd-tiles', 'nd-pw']) clear($(id));
      shownNode = route.arg;
    }
    // Render what is already here at once; the page's own document follows.
    if (lastStatus) guard(renderStatus, lastStatus);
    if (lastCluster) { guard(renderCluster, lastCluster); guard(renderPower, lastCluster.power); }
    pageReset();
    pageLoading();
    renderPageStale();
    pageFetch(true);
    if (route.page === 'power') { chartAt = 0; refreshChart(true); }
    for (const fig of charts.keys()) drawTs(fig);
  }

  // ------------------------------------------------------------ each page's own document
  const pageDocs = new Map();       // url -> fetched-at (ms)
  const pageOk = new Map(), pageErr = new Map();   // url -> last success (ms) / last error text
  let pageGen = 0;                  // bumped on a route or window change: older answers are dropped
  function pageReset() { pageDocs.clear(); pageOk.clear(); pageErr.clear(); pageGen++; }
  let lastEvents = null;
  const winOf = {};
  const WINS = ['15m', '1h', '6h', '24h', '7d'];
  function pageWin(page, def) {
    const ids = { serving: 'sv-range', node: 'nd-range', gate: 'gt-range', tools: 'tl-range' };
    if (!winOf[page]) {
      winOf[page] = def || '1h';
      try { const v = localStorage.getItem('obdash.win.' + page); if (WINS.includes(v)) winOf[page] = v; } catch (e) { /* private mode */ }
    }
    segWindows($(ids[page]), WINS, winOf[page], w => {
      winOf[page] = w;
      try { localStorage.setItem('obdash.win.' + page, w); } catch (e) { /* private mode */ }
      pageReset(); renderPageStale(); pageFetch(true);
    });
    return winOf[page];
  }
  // [url, renderer, refresh ms] for the page shown.
  function pageWants() {
    const fast = w => (w === '15m' || w === '1h') ? 5000 : 30000;
    switch (route.page) {
      case 'serving': { const w = pageWin('serving'); return [['/api/serving?window=' + w, renderServing, fast(w)]]; }
      case 'node': { const w = pageWin('node'); return [['/api/node/' + encodeURIComponent(route.arg || '') + '?window=' + w, renderNode, fast(w)]]; }
      case 'gate': { const w = pageWin('gate', '6h'); return [['/api/history?metric=gate:rpm,gate:errors,gate:denied&window=' + w, d => chartBox($('gt-charts'), GATE_CHARTS.map(c => Object.assign({ win: d.window }, c)), d), fast(w)]]; }
      case 'tools': { const w = pageWin('tools', '24h'); return [['/api/history?metric=tools:calls,tools:errors&window=' + w, d => chartBox($('tl-charts'), TOOL_CHARTS.map(c => Object.assign({ win: d.window }, c)), d), fast(w)]]; }
      case 'events': return [['/api/events?limit=500', d => { lastEvents = d; renderEvents(d); }, 5000], ['/api/alerts', renderRules, 15000]];
      case 'history': return [['/api/daily', renderHistory, 30000]];
      default: return [];
    }
  }
  const inflight = new Set();
  async function pageFetch(force) {
    for (const [url, fn, every] of pageWants()) {
      const at = pageDocs.get(url) || 0;
      if (inflight.has(url) || (!force && Date.now() - at < every - 200)) continue;
      inflight.add(url);
      const want = route.page + '|' + route.arg;
      // An answer for another page or another window (the range changed while it was in
      // flight) is dropped: it must not overwrite the newer one.
      const gen = pageGen;
      const current = () => gen === pageGen && want === route.page + '|' + route.arg;
      getJSON(url).then(d => {
        if (gen !== pageGen) return;
        pageDocs.set(url, Date.now());
        pageOk.set(url, Date.now()); pageErr.delete(url);
        if (current() && d) { guard(fn, d); pageLoaded(true); }
      }, err => {
        if (gen !== pageGen) return;
        pageDocs.set(url, Date.now() - every + 3000);   // retry soon
        const why = String((err && err.message) || err || 'fetch failed').slice(0, 60);
        pageErr.set(url, why);
        if (!current()) return;
        if (route.page === 'node' && /HTTP 404/.test(why)) renderNodeHead(null);
        else if (!pageOk.has(url)) pageLoaded(false, why);
      }).finally(() => { inflight.delete(url); renderPageStale(); });
    }
  }

  // Before a page's first document and when it fails, its notes say so, and the "nothing
  // here" messages stay hidden: no false "not reported" from a fetch still in flight.
  const PAGE_NOTE = { serving: 'spec-note', history: 'history-note' };
  const PAGE_EMPTY = { serving: ['spec-empty', 'finished-empty'], history: ['hs-incidents-empty', 'days-empty', 'swaps-empty'] };
  function pageLoaded(ok, why) {
    if (ok) return;
    const note = PAGE_NOTE[route.page];
    if (note) setId(note, 'could not load · ' + why + ' · retrying');
    for (const id of PAGE_EMPTY[route.page] || []) { const e = $(id); if (e) e.hidden = true; }
  }
  function pageLoading() {
    const note = PAGE_NOTE[route.page];
    if (note) setId(note, 'loading…');
    for (const id of PAGE_EMPTY[route.page] || []) { const e = $(id); if (e) e.hidden = true; }
  }

  // A detail page whose own feed stopped answering says so and dims, as the overview does.
  function renderPageStale() {
    const now = Date.now();
    let worst = 0, why = '';
    for (const [url, , every] of route.page === 'home' ? [] : pageWants()) {
      const ok = pageOk.get(url) || 0;
      const age = ok ? now - ok : 0;
      if (ok && age > Math.max(2.5 * every, STALE_MS) && age > worst) { worst = age; why = pageErr.get(url) || ''; }
    }
    const v = worst ? '1' : '';
    const secs_ = Array.from(document.querySelectorAll ? document.querySelectorAll('[data-page]') : []);
    for (const sec of secs_) { const w = sec.dataset.page === route.page ? v : ''; if (sec.dataset.stale !== w) sec.dataset.stale = w; }
    const el = $('page-stale');
    if (el) { el.hidden = !worst; set(el, worst ? 'detail stale · last update ' + dur(worst / 1000) + ' ago' + (why ? ' · ' + why : '') : ''); }
  }

  // ------------------------------------------------------------ polling
  let lastOk = 0, lastClusterOk = 0, lastStatus = null, lastCluster = null, busy = false;

  async function getJSON(url) {
    const ctl = typeof AbortController === 'function' ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), FETCH_ABORT_MS) : null;
    try {
      const r = await fetch(url, { cache: 'no-store', credentials: 'same-origin', signal: ctl ? ctl.signal : undefined });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } finally { if (t) clearTimeout(t); }
  }

  function renderLive() {
    const live = $('live');
    if (!lastOk) { setState(live, 'wait'); setId('live-text', 'waiting for data'); renderHealth(); return; }
    const age = Date.now() - lastOk;
    setState(live, age > STALE_MS ? 'stale' : 'ok');
    setId('live-text', age > STALE_MS ? 'stale · ' + dur(age / 1000) + ' ago' : 'live · ' + Math.max(0, Math.round(age / 1000)) + 's');
    renderHealth();
    renderPageStale();
  }

  // The health chip and the overview's verdict from the last data, unless that data is stale:
  // then they say so instead of a green "all systems nominal" from a server that stopped answering.
  function renderHealth() {
    const now = Date.now();
    const stale = !!lastOk && now - lastOk > STALE_MS;
    const clStale = !!lastClusterOk && now - lastClusterOk > STALE_MS;
    const o = stale ? { state: 'unknown', text: 'no data · dashboard not answering' } : overall(lastStatus, lastCluster);
    setState($('health-chip'), o.state);
    setId('health-text', o.text);
    const hc = $('health-chip'); if (hc) hc.title = o.text;
    setState($('verdict'), o.state);
    setId('verdict-text', lastOk || stale ? VERDICT[o.state] || o.state : 'Connecting');
    let reason = o.text;
    if (o.state === 'ok' && lastStatus) {
      const nodes = ((lastCluster && lastCluster.nodes) || []).length;
      const al = lastStatus.alerts && lastStatus.alerts.counts;
      reason = (nodes ? nodes + (nodes === 1 ? ' node' : ' nodes') + ' · ' : '') + 'no active alert' + (al && al.info ? ' · ' + al.info + ' info' : '');
    }
    setId('verdict-reason', lastOk || stale ? reason : 'waiting for the first reading');
    const b = document.body; if (b && b.dataset) { const v = stale ? '1' : ''; if (b.dataset.stale !== v) b.dataset.stale = v; }
    for (const id of ['nodes', 'power', 'home-nodes']) { const el = $(id); if (el && el.dataset) { const v = clStale ? '1' : ''; if (el.dataset.stale !== v) el.dataset.stale = v; } }
  }

  function guard(fn, arg) { try { fn(arg); } catch (e) { if (globalThis.console) console.error(e); } }

  async function tick() {
    if (busy) return;
    busy = true;
    try {
      const [s, c] = await Promise.allSettled([getJSON('/api/status'), getJSON('/api/cluster')]);
      if (s.status === 'fulfilled' && s.value) { lastStatus = s.value; lastOk = Date.now(); guard(renderStatus, s.value); }
      if (c.status === 'fulfilled' && c.value) {
        lastCluster = c.value; lastClusterOk = Date.now();
        guard(renderCluster, c.value); guard(renderPower, c.value.power);
      }
    } finally { busy = false; renderLive(); }
    if (route.page === 'power') refreshChart(false);
    pageFetch(false);
  }

  // A hidden tab polls every 15 s instead of every 3 s; showing it again catches up at once.
  let hiddenSkip = 0;
  function loop() {
    if (typeof document.visibilityState === 'string' && document.visibilityState === 'hidden' && (hiddenSkip++ % 5)) return;
    tick();
  }

  const api = { nodeFields, portFields, serviceFields, overall, renderStatus, renderCluster, renderPower, keyed,
    powerNodeFields, windowFields, trendText, chartModel, niceCeil, powerFoot, renderHealth, tsModel, bucketSum, parseRoute,
    tileFields, renderServing, renderEvents, renderHistory, renderNode, renderRules, renderAlerts,
    _setLast: (ok, clOk) => { lastOk = ok; lastClusterOk = clOk; }, _setData: (st, cl) => { lastStatus = st; lastCluster = cl; },
    _setRoute: r => { route = r; }, alertHref, renderNodeHead, renderPageStale,
    _pageOk: pageOk, pageWants,
    fmt: { int, pct, frac, gib, bytes, bps, dur, toks, ctxLabel, watts, energy, money, secs, big } };
  if (globalThis.__OBDASH_TEST__) { globalThis.__obdash = api; return; }
  if (typeof ResizeObserver === 'function') chartRO = new ResizeObserver(entries => {
    for (const e of entries) { const fig = e.target.closest && e.target.closest('.cc'); if (fig) drawTs(fig); }
  });
  wireChart();
  // Only #/... is a route: an in-page anchor (the skip link's #main) leaves the page shown as it is.
  // Back / Forward put the reader where they were on that page; a fresh link opens at the top.
  const saveY = () => { try { history.replaceState({ y: Math.round(globalThis.scrollY || 0) }, ''); } catch (e) { /* no history */ } };
  try { if ('scrollRestoration' in history) history.scrollRestoration = 'manual'; } catch (e) { /* old engine */ }
  document.addEventListener('click', e => { if (e.target && e.target.closest && e.target.closest('a[href^="#/"]')) saveY(); }, true);
  let ySave = 0;
  globalThis.addEventListener('scroll', () => { clearTimeout(ySave); ySave = setTimeout(saveY, 200); }, { passive: true });
  globalThis.addEventListener('hashchange', () => {
    if (!/^#\/|^$/.test(location.hash)) return;
    const y = history.state && typeof history.state.y === 'number' ? history.state.y : null;
    showRoute(y === null);
    if (y !== null) requestAnimationFrame(() => scrollTo(0, y));
  });
  if (document.visibilityState !== undefined) document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { hiddenSkip = 0; tick(); } });
  showRoute(false);
  tick();
  setInterval(loop, POLL_MS);
  setInterval(renderLive, 1000);
})();
