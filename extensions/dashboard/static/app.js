// OpenBeast status dashboard. Read-only: it GETs /api/status and /api/cluster every 3 s and
// updates text in place. Every value from the server is written with textContent (never
// innerHTML), so a hostile model id or hostname renders as text. Lists (nodes, ports,
// services, tools, events) are keyed: an existing row is updated, not rebuilt, so nothing
// flickers. A missing field shows as a dash, never as a made-up 0.
'use strict';
(function () {
  const POLL_MS = 3000;
  const STALE_MS = 10000;
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
      f.power = isNum(g.power_w) ? g.power_w.toFixed(0) + ' W' : DASH;
    } else {
      f.gpu = n.state === 'up' ? 'no GPU detected' : DASH;
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
    for (const el of Array.from(box.children)) have.set(el.dataset.key, el);
    let i = 0;
    for (const it of items) {
      const k = keyOf(it);
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
  function spark(svg, values, readout, fmt, maxHint) {
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
        const i = Math.max(0, Math.min(v.length - 1, Math.round((ev.clientX - r.left) / r.width * (v.length - 1))));
        const x = v.length > 1 ? i / (v.length - 1) * 120 : 120;
        cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.style.display = '';
        const ago = (v.length - 1 - i) * (svg._step || 5);
        set(readout, (has(v[i]) ? svg._fmt(v[i]) : DASH) + (ago ? ' · ' + dur(ago) + ' ago' : ' · now'));
      };
      svg.addEventListener('pointermove', pick);
      svg.addEventListener('pointerdown', pick);
      svg.addEventListener('pointerleave', () => { cross.style.display = 'none'; set(readout, ''); });
    }
    svg._values = values; svg._fmt = fmt;
    const nums = values.filter(isNum);
    const max = Math.max(maxHint || 0, ...nums, 1e-9);
    let d = '', open = false, first = null, last = null;
    const n = values.length;
    values.forEach((v, i) => {
      const x = n > 1 ? i / (n - 1) * 120 : 120;
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
    spark(svg, hist, $('spark-toks-read'), v => toks(v) + ' tok/s');
    setId('sig-reqs', (isNum(sv.requests_running) ? sv.requests_running : DASH) + ' · ' + (isNum(sv.requests_waiting) ? sv.requests_waiting : DASH));
    setId('sig-kv', frac(sv.kv_usage, 1));
    width($('sig-kv-bar'), isNum(sv.kv_usage) ? sv.kv_usage * 100 : null);
    setId('sig-ctx', ctxLabel(m.ctx));

    setId('serving-note', str(m.engine, 24) + (sv.tok_s_source === 'gate' ? ' · tok/s from beast-gate' : ''));
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
    setId('gt-denied', int(g.denied_total));

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
      set(field(card, 'cpu'), f.cpu);
      set(field(card, 'load'), f.load);
      set(field(card, 'ram'), f.ram);
      set(field(card, 'uptime'), f.uptime);
      set(field(card, 'disk'), f.disk);
      const sp = field(card, 'sparks'); if (sp) sp.hidden = !n.gpu;
      const h = n.history || {};
      const step = n.interval_s || 5;
      const su = field(card, 'spark-util'), sm = field(card, 'spark-mem');
      if (su) su._step = step;
      if (sm) sm._step = step;
      spark(su, h.util || [], field(card, 'spark-util-read'), v => pct(v), 100);
      spark(sm, h.mem || [], field(card, 'spark-mem-read'), v => pct(v), 100);
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

  // ------------------------------------------------------------ polling
  let lastOk = 0, lastStatus = null, lastCluster = null, busy = false;

  async function getJSON(url) {
    const ctl = typeof AbortController === 'function' ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), 5000) : null;
    try {
      const r = await fetch(url, { cache: 'no-store', credentials: 'same-origin', signal: ctl ? ctl.signal : undefined });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } finally { if (t) clearTimeout(t); }
  }

  function renderLive() {
    const live = $('live');
    if (!lastOk) { setState(live, 'wait'); setId('live-text', 'waiting for data'); return; }
    const age = Date.now() - lastOk;
    setState(live, age > STALE_MS ? 'stale' : 'ok');
    setId('live-text', age > STALE_MS ? 'stale · last update ' + dur(age / 1000) + ' ago' : 'live · updated ' + Math.max(0, Math.round(age / 1000)) + 's ago');
  }

  function guard(fn, arg) { try { fn(arg); } catch (e) { if (globalThis.console) console.error(e); } }

  async function tick() {
    if (busy) return;
    busy = true;
    try {
      const [s, c] = await Promise.allSettled([getJSON('/api/status'), getJSON('/api/cluster')]);
      if (s.status === 'fulfilled' && s.value) { lastStatus = s.value; lastOk = Date.now(); guard(renderStatus, s.value); }
      if (c.status === 'fulfilled' && c.value) { lastCluster = c.value; guard(renderCluster, c.value); }
      const o = overall(lastStatus, lastCluster);
      setState($('health-chip'), o.state);
      setId('health-text', o.text);
    } finally { busy = false; renderLive(); }
  }

  const api = { nodeFields, portFields, serviceFields, overall, renderStatus, renderCluster, fmt: { int, pct, frac, gib, bytes, bps, dur, toks, ctxLabel } };
  if (globalThis.__OBDASH_TEST__) { globalThis.__obdash = api; return; }
  tick();
  setInterval(tick, POLL_MS);
  setInterval(renderLive, 1000);
})();
