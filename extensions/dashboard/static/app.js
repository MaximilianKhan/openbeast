// OpenBeast status dashboard. Read-only: it GETs /api/status and /api/cluster every 3 s (and
// /api/power for the chart: every 3 s for 15 m / 1 h, every 30 s for 6 h / 24 h) and updates
// text in place. Every value from the server is written with textContent (never
// innerHTML), so a hostile model id or hostname renders as text. Lists (nodes, ports,
// services, tools, events) are keyed: an existing row is updated, not rebuilt, so nothing
// flickers. A missing field shows as a dash, never as a made-up 0.
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
  const WIN_LABEL = { '1m': '1 min', '5m': '5 min', '15m': '15 min', '1h': '1 hour', '24h': '24 hours', '6h': '6 hours' };
  const WIN_SHORT = { '1m': '1 min', '5m': '5 min', '15m': '15 min', '1h': '1 h', '24h': '24 h', '6h': '6 h' };
  // Chart x ticks per window: [number of intervals] (15 m: every 5 min; 1 h: 15 min; 6 h: 1 h; 24 h: 6 h).
  const TICKS = { '15m': 3, '1h': 4, '6h': 6, '24h': 4 };
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
    } else {
      f.gpu = n.state === 'up' ? (isNum(n.gpu_error) ? 'GPU not readable (nvidia-smi exit ' + n.gpu_error + ')' : 'no GPU detected') : DASH;
      f.powerTitle = '';
      f.memLabel = 'memory'; f.memKind = 'RAM';
      f.mem = r ? gib(r.used_mib) + ' / ' + gib(r.total_mib, 0) + ' GiB (RAM)' : DASH;
      f.memPct = r ? r.pct : null;
      f.util = f.temp = f.power = DASH;
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
  function serviceFields(s) {
    const state = Object.prototype.hasOwnProperty.call(SVC_TEXT, s.state) ? s.state : 'unknown';
    return { key: str(s.name, 32), state, name: str(s.label || s.name, 40),
      port: isNum(s.port) ? ':' + s.port : (s.name === 'island' ? 'mode ' + str(s.detail, 16) : ''),
      text: SVC_TEXT[state] };
  }

  // Overall health: model down = down; any running service down, or a peer unreachable = degraded.
  function overall(st, cl) {
    if (!st) return { state: 'unknown', text: 'no data' };
    if (!st.model || !st.model.healthy) return { state: 'down', text: 'model down' };
    const bad = (st.service_list || []).filter(s => s.state === 'down').map(s => s.label || s.name);
    const lost = ((cl && cl.nodes) || []).filter(n => n.state === 'unreachable').map(n => n.name);
    const n = bad.length + lost.length;
    if (n) return { state: 'degraded', text: n + (n === 1 ? ' issue: ' : ' issues: ') + bad.concat(lost).slice(0, 3).join(', ') };
    return { state: 'ok', text: 'all systems nominal' };
  }

  // ------------------------------------------------------------ DOM helpers (in place)
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

  // ------------------------------------------------------------ renderers
  function renderStatus(st) {
    const m = st.model || {}, sv = st.serving || {}, slots = sv.slots || {}, cap = sv.capacity || {};
    setId('host-chip', str(st.host && st.host.name, 64));
    setState($('model-dot'), m.healthy ? 'up' : 'down');
    setId('h-model', m.healthy ? 'Serving · healthy' : 'Model server down');
    setId('model-id', str(m.id || m.alias || m.serve_script, 120));
    setId('chip-engine', str(m.engine || m.backend, 24));
    const u = m.unit || {};
    setId('chip-unit', has(u.name) ? str(u.name, 48) + (isNum(u.uptime_s) ? ' · up ' + dur(u.uptime_s) : '') : 'no inference unit');
    setId('chip-endpoint', 'API ' + str(m.endpoint, 64));
    setId('sig-toks', toks(sv.tok_s));
    const hist = (sv.history && sv.history.tok_s) || [];
    const svg = $('spark-toks');
    if (svg) svg._step = sv.interval_s || 3;
    spark(svg, hist, $('spark-toks-read'), v => toks(v) + ' tok/s', 0, sv.history && sv.history.t);
    setId('sig-reqs', (isNum(sv.requests_running) ? sv.requests_running : DASH) + ' · ' + (isNum(sv.requests_waiting) ? sv.requests_waiting : DASH));
    setId('sig-kv', frac(sv.kv_usage, 1));
    width($('sig-kv-bar'), isNum(sv.kv_usage) ? sv.kv_usage * 100 : null);
    setId('sig-ctx', ctxLabel(m.ctx));

    setId('serving-note', str(m.engine, 24) + (sv.tok_s_source === 'gate' ? ' · tok/s from beast-gate'
      : sv.tok_s_source === 'engine-completed' ? ' · tok/s per finished request' : ''));
    setId('sv-slots', (isNum(slots.busy) ? slots.busy : DASH) + ' / ' + (isNum(slots.total) ? slots.total : DASH));
    setId('sv-queue', isNum(cap.queue_deferred) ? cap.queue_deferred : DASH);
    setId('sv-running', isNum(sv.requests_running) ? sv.requests_running : DASH);
    setId('sv-waiting', isNum(sv.requests_waiting) ? sv.requests_waiting : DASH);
    setId('sv-kv', frac(sv.kv_usage, 1));
    setId('sv-pool', isNum(cap.ctx_total) ? int(cap.ctx_total) + (cap.ctx_shared ? ' shared' : '') : (cap.ctx_shared ? 'shared pool' : DASH));
    setId('sv-prompt', toks(sv.prompt_tok_s));
    setId('sv-gen', toks(sv.tok_s));
    setId('sv-profile', str(cap.serving_profile, 32));
    const adm = sv.admission;
    setId('sv-admission', adm ? adm.per_device_inflight + ' in flight · ' + adm.per_device_rate_per_min + '/min' : DASH);
    setId('sv-backend', str(m.backend, 24));
    setId('sv-uptime', dur(u.uptime_s));

    const g = st.gate || {};
    setId('gate-note', !g.enabled ? 'off on this rig' : g.up ? 'up · :' + portOf(st, 'gate') : g.up === false ? 'DOWN' : 'starting');
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

    const svcs = (st.service_list || []).map(serviceFields);
    const up = svcs.filter(s => s.state === 'up' || s.state === 'online').length;
    const on = svcs.filter(s => s.state !== 'off').length;
    setId('services-note', up + ' of ' + on + ' running');
    keyed($('services'), svcs, s => s.key, () => {
      const li = mk('li', 'svc');
      const dot = mk('i', 'dot', li); dot.setAttribute('aria-hidden', 'true');
      const box = mk('span', '', li);
      mk('span', 's-name', box); mk('span', 's-port', box);
      mk('span', 's-state', li);
      return li;
    }, (li, s) => {
      setState(li, s.state);
      set(li.querySelector('.s-name'), s.name);
      li.title = s.name + (s.port ? ' ' + s.port : '') + ': ' + s.text;
      set(li.querySelector('.s-port'), s.port);
      set(li.querySelector('.s-state'), s.text);
    });

    const mt = st.metrics || {};
    setId('tl-calls', int(mt.tool_calls));
    setId('tl-errors', int(mt.tool_errors));
    setId('tl-rate', isNum(mt.tool_calls) && mt.tool_calls > 0 && isNum(mt.tool_errors) ? pct(100 * mt.tool_errors / mt.tool_calls, 1) : DASH);
    const top = Array.isArray(mt.top_tools) ? mt.top_tools : [];
    const max = Math.max(1, ...top.map(t => isNum(t.calls) ? t.calls : 0));
    const te = $('tools-empty'); if (te) te.hidden = top.length > 0;
    keyed($('tools'), top, t => str(t.tool, 48), () => {
      const li = mk('li', 'bar-row');
      mk('span', 'b-name', li);
      const m2 = mk('span', 'meter', li); m2.setAttribute('aria-hidden', 'true'); mk('i', '', m2);
      mk('span', 'b-val', li);
      return li;
    }, (li, t) => {
      set(li.querySelector('.b-name'), str(t.tool, 48));
      li.querySelector('.b-name').title = str(t.tool, 48);
      width(li.querySelector('.meter > i'), isNum(t.calls) ? 100 * t.calls / max : 0);
      set(li.querySelector('.b-val'), int(t.calls) + (t.errors ? ' · ' + int(t.errors) + ' err' : ''));
    });

    const ev = Array.isArray(st.events) ? st.events.slice(0, 30) : [];
    const ee = $('events-empty'); if (ee) ee.hidden = ev.length > 0;
    keyed($('events'), ev, e => e.t + '|' + str(e.text, 160), () => {
      const li = mk('li');
      mk('time', '', li); mk('span', '', li);
      return li;
    }, (li, e) => {
      li.dataset.level = e.level === 'warn' ? 'warn' : 'info';
      const t = isNum(e.t) ? new Date(e.t * 1000) : null;
      const tm = li.querySelector('time');
      set(tm, t ? t.toLocaleTimeString('en-GB', { hour12: false }) : DASH);
      if (t && tm.setAttribute) tm.setAttribute('datetime', t.toISOString());
      set(li.querySelector('span'), str(e.text, 160));
    });
  }

  function portOf(st, name) {
    const s = (st.service_list || []).find(x => x.name === name);
    return s && isNum(s.port) ? s.port : DASH;
  }

  function renderCluster(cl) {
    const nodes = Array.isArray(cl.nodes) ? cl.nodes : [];
    const peers = nodes.filter(n => n.role !== 'head').length;
    const reach = nodes.filter(n => n.state === 'up').length;
    setId('cluster-note', (has(cl.cluster) ? str(cl.cluster, 40) + ' · ' : '') + nodes.length + (nodes.length === 1 ? ' node' : ' nodes')
      + (peers ? ' · ' + peers + (peers === 1 ? ' peer' : ' peers') + ' · ' + reach + ' reachable' : ' · single node'));
    const tpl = $('t-node');
    keyed($('nodes'), nodes, n => str(n.name, 64), () => tpl.content.firstElementChild.cloneNode(true), (card, n) => {
      const f = nodeFields(n);
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
      const ports = (Array.isArray(n.fabric) ? n.fabric : []).map(portFields)
        .sort((a, b) => (a.state === b.state ? a.key.localeCompare(b.key) : a.state === 'up' ? -1 : 1));
      const pe = field(card, 'ports-empty'); pe.hidden = ports.length > 0 || n.state !== 'up';
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
    });
  }

  // ------------------------------------------------------------ power
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
    setId('live-text', age > STALE_MS ? 'stale · last update ' + dur(age / 1000) + ' ago' : 'live · updated ' + Math.max(0, Math.round(age / 1000)) + 's ago');
    renderHealth();
  }

  // The health chip from the last data, unless that data is stale: then it says so instead of
  // a green "all systems nominal" from a server that has stopped answering.
  function renderHealth() {
    const now = Date.now();
    const stale = !!lastOk && now - lastOk > STALE_MS;
    const clStale = !!lastClusterOk && now - lastClusterOk > STALE_MS;
    const o = stale ? { state: 'unknown', text: 'no data · dashboard not answering' } : overall(lastStatus, lastCluster);
    setState($('health-chip'), o.state);
    setId('health-text', o.text);
    const hc = $('health-chip'); if (hc) hc.title = o.text;
    const b = document.body; if (b && b.dataset) { const v = stale ? '1' : ''; if (b.dataset.stale !== v) b.dataset.stale = v; }
    for (const id of ['nodes', 'power']) { const el = $(id); if (el && el.dataset) { const v = clStale ? '1' : ''; if (el.dataset.stale !== v) el.dataset.stale = v; } }
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
    refreshChart(false);
  }

  const api = { nodeFields, portFields, serviceFields, overall, renderStatus, renderCluster, renderPower, keyed,
    powerNodeFields, windowFields, trendText, chartModel, niceCeil, powerFoot, renderHealth,
    _setLast: (ok, clOk) => { lastOk = ok; lastClusterOk = clOk; }, _setData: (st, cl) => { lastStatus = st; lastCluster = cl; },
    fmt: { int, pct, frac, gib, bytes, bps, dur, toks, ctxLabel, watts, energy, money } };
  if (globalThis.__OBDASH_TEST__) { globalThis.__obdash = api; return; }
  wireChart();
  tick();
  setInterval(tick, POLL_MS);
  setInterval(renderLive, 1000);
})();
