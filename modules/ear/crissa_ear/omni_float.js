/*! house-omni-float — single floating omni rail (sticky log).
 * GUARDS against nesting: no-op inside iframes; one root max; never re-inject omni into omni.
 * Prefer crissa-shell.html (left rail + main stage) over injecting into another page.
 */
(function () {
  'use strict';
  var ID = 'house-omni-float';
  var STYLE_ID = 'house-omni-pad-style';
  // the ear that served this script (2026-10-10: the house spelled its own port here, so beside another install the
  // rail framed that install's ear)
  var SRC = (function () {
    try { return new URL('/omni?float=1', document.currentScript.src).href; } catch (_) { return '/omni?float=1'; }
  })();
  var HIDDEN_KEY = 'house.omniFloatHidden';

  // --- anti-nest ---
  // 1) never run inside an iframe (would stack omnis)
  try {
    if (window.self !== window.top) {
      console.warn('[omni-float] skip: inside iframe (no nest)');
      return;
    }
  } catch (_) {
    // cross-origin top — treat as nested
    console.warn('[omni-float] skip: cross-origin frame');
    return;
  }
  // 2) never on the omni page itself
  try {
    if (location.pathname.indexOf('/omni') === 0) {
      console.warn('[omni-float] skip: already on omni');
      return;
    }
  } catch (_) {}

  // singleton
  if (window.__houseOmniFloatInstalled) {
    try {
      if (localStorage.getItem(HIDDEN_KEY) !== '1' && window.houseOmniFloat) window.houseOmniFloat();
    } catch (_) {}
    return;
  }
  window.__houseOmniFloatInstalled = true;

  function widthDefault() {
    var w = Math.min(400, Math.floor((window.innerWidth || 1200) * 0.34));
    return w < 280 ? 280 : w;
  }
  function wantVisible() {
    try { return localStorage.getItem(HIDDEN_KEY) !== '1'; } catch (_) { return true; }
  }
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent =
      'html.house-omni-float-on body{padding-left:var(--house-omni-pad,360px)!important;box-sizing:border-box;}' +
      '#' + ID + '{position:fixed!important;left:0!important;top:0!important;bottom:0!important;' +
      'z-index:2147483646!important;display:flex!important;flex-direction:column!important;' +
      'box-shadow:6px 0 28px rgba(0,0,0,.45);border-right:1px solid #1e2b30;background:#0d1214;' +
      'font:12px system-ui,sans-serif;}' +
      '#' + ID + '[data-hidden="1"]{display:none!important;}' +
      '#' + ID + ' iframe{flex:1;width:100%;border:0;background:#0d1214;min-height:0;}';
    (document.head || document.documentElement).appendChild(st);
  }
  function pad(el) {
    if (!el || el.getAttribute('data-hidden') === '1') {
      document.documentElement.classList.remove('house-omni-float-on');
      return;
    }
    var w = el.getBoundingClientRect().width || widthDefault();
    document.documentElement.style.setProperty('--house-omni-pad', w + 'px');
    document.documentElement.classList.add('house-omni-float-on');
  }
  function build() {
    ensureStyle();
    // destroy any duplicate roots first (the User closed three nests)
    document.querySelectorAll('[data-house="omni-float"]').forEach(function (n) {
      if (n.id !== ID) n.remove();
    });
    var root = document.createElement('div');
    root.id = ID;
    root.setAttribute('data-house', 'omni-float');
    root.style.width = widthDefault() + 'px';
    var bar = document.createElement('div');
    bar.style.cssText =
      'flex:0 0 auto;display:flex;align-items:center;gap:8px;padding:6px 10px;' +
      'background:#121a1d;color:#7d9490;border-bottom:1px solid #1e2b30;user-select:none';
    bar.innerHTML =
      '<b style="color:#6fb5ad;letter-spacing:.06em;text-transform:uppercase;font-size:11px">omni</b>' +
      '<span style="opacity:.7;font-size:10px">live</span><span style="flex:1"></span>' +
      '<button type="button" data-act="narrow" style="all:unset;cursor:pointer;color:#7d9490;padding:0 4px">⟨</button>' +
      '<button type="button" data-act="wide" style="all:unset;cursor:pointer;color:#7d9490;padding:0 4px">⟩</button>' +
      '<button type="button" data-act="hide" style="all:unset;cursor:pointer;color:#e08a76;padding:0 4px">✕</button>';
    var iframe = document.createElement('iframe');
    iframe.src = SRC;
    iframe.title = 'House omni-log';
    iframe.allow = 'autoplay';
    iframe.setAttribute('data-house', 'omni-iframe');
    // sandbox: no top-nav that could open another shell
    // (allow-scripts + allow-same-origin needed for omni poll)
    root.appendChild(bar);
    root.appendChild(iframe);
    bar.addEventListener('click', function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;
      var act = t.getAttribute('data-act');
      if (act === 'hide') {
        try { localStorage.setItem(HIDDEN_KEY, '1'); } catch (_) {}
        root.setAttribute('data-hidden', '1');
        pad(root);
        return;
      }
      if (act === 'narrow' || act === 'wide') {
        var cur = root.getBoundingClientRect().width || widthDefault();
        root.style.width = (act === 'wide' ? Math.min(560, cur + 40) : Math.max(260, cur - 40)) + 'px';
        pad(root);
      }
    }, true);
    return root;
  }
  function mount() {
    if (!wantVisible()) return null;
    ensureStyle();
    var root = document.getElementById(ID);
    if (!root) {
      root = build();
      document.documentElement.appendChild(root);
    } else if (!root.isConnected) {
      document.documentElement.appendChild(root);
    } else if (root.parentNode !== document.documentElement) {
      document.documentElement.appendChild(root);
    }
    // collapse accidental duplicates
    document.querySelectorAll('[data-house="omni-float"]').forEach(function (n, i) {
      if (n !== root) n.remove();
    });
    root.setAttribute('data-hidden', '0');
    var ifr = root.querySelector('iframe[data-house="omni-iframe"]');
    if (ifr && (!ifr.src || ifr.src.indexOf('/omni') < 0)) ifr.src = SRC;
    pad(root);
    return root;
  }
  function hide() {
    try { localStorage.setItem(HIDDEN_KEY, '1'); } catch (_) {}
    var root = document.getElementById(ID);
    if (root) { root.setAttribute('data-hidden', '1'); pad(root); }
    document.documentElement.classList.remove('house-omni-float-on');
  }
  function show() {
    try { localStorage.removeItem(HIDDEN_KEY); } catch (_) {}
    return mount();
  }
  window.houseOmniFloat = show;
  window.houseOmniFloatHide = hide;
  window.houseOmniFloatToggle = function () {
    if (wantVisible() && document.getElementById(ID) && document.getElementById(ID).getAttribute('data-hidden') !== '1') hide();
    else show();
  };

  // light persistence (no click storm — the shell is preferred)
  function hookHistory() {
    ['pushState', 'replaceState'].forEach(function (type) {
      var orig = history[type];
      if (typeof orig !== 'function') return;
      history[type] = function () {
        var ret = orig.apply(this, arguments);
        setTimeout(mount, 30);
        return ret;
      };
    });
    window.addEventListener('popstate', function () { setTimeout(mount, 30); });
  }
  if (window.MutationObserver) {
    var obs = new MutationObserver(function () {
      if (!wantVisible()) return;
      var root = document.getElementById(ID);
      if (!root || !root.isConnected) mount();
      else if (root.parentNode !== document.documentElement) {
        document.documentElement.appendChild(root);
        pad(root);
      }
      // kill nests
      document.querySelectorAll('[data-house="omni-float"]').forEach(function (n, i) {
        if (i > 0) n.remove();
      });
    });
    obs.observe(document.documentElement, { childList: true, subtree: false });
  }
  setInterval(function () {
    if (!wantVisible()) return;
    var root = document.getElementById(ID);
    if (!root || !root.isConnected) mount();
  }, 1500);

  hookHistory();
  if (document.documentElement) mount();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  }
})();
