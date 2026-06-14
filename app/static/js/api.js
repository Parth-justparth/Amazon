/* =========================================================================
   Shared client utilities: API calls, toasts, formatting, chrome injection.
   ========================================================================= */

const API_BASE = "";

/** Perform a JSON API call. Throws Error(message) on non-2xx, after toasting. */
async function api(endpoint, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== null) opts.body = JSON.stringify(body);

  let res, data;
  try {
    res = await fetch(`${API_BASE}${endpoint}`, opts);
  } catch (e) {
    toast("Network error — is the server running?", "error");
    throw e;
  }
  try { data = await res.json(); } catch (e) { data = null; }

  if (!res.ok) {
    const detail = data && data.detail;
    const msg =
      (detail && detail.message) ||
      (typeof detail === "string" ? detail : null) ||
      `Request failed (HTTP ${res.status})`;
    toast(msg, "error");
    const err = new Error(msg);
    err.detail = detail;
    err.status = res.status;
    throw err;
  }
  return data;
}

/** Toast notification. type: success | error | info */
function toast(message, type = "success") {
  let box = document.getElementById("toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    document.body.appendChild(box);
  }
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = message;
  box.appendChild(t);
  setTimeout(() => {
    t.style.transition = "opacity .3s";
    t.style.opacity = "0";
    setTimeout(() => t.remove(), 300);
  }, 3400);
}

/** Format integer minor units (paise) as Indian Rupees. */
function inr(minor, currency = "INR") {
  const value = (Number(minor) || 0) / 100;
  const sym = currency === "INR" ? "₹" : "";
  return (
    sym +
    value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  );
}

/** Pretty-print an UPPER_SNAKE enum as Title Case. */
function pretty(value) {
  if (!value) return "";
  return String(value)
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Escape text for safe innerHTML insertion. */
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ----- Shared Amazon chrome (nav + sub-nav + footer) -------------------- */

function renderChrome(active) {
  const navLinks = [
    { href: "/static/return.html", label: "Return or Replace", key: "return" },
    { href: "/static/marketplace.html", label: "Marketplace", key: "market" },
    { href: "/static/wallet.html", label: "Green Points", key: "wallet" },
  ];

  const nav = document.createElement("header");
  nav.innerHTML = `
    <div class="az-nav">
      <a class="az-logo" href="/static/index.html">
        <span class="mark">second<b>life</b></span><span class="tld">.ai</span>
      </a>
      <div class="az-loc">
        <span class="ico">📍</span>
        <div class="lines"><small>Deliver to</small><span>Bengaluru 560001</span></div>
      </div>
      <div class="az-search">
        <select aria-label="category">
          <option>All</option>
          <option>Returns</option>
          <option>Marketplace</option>
        </select>
        <input type="text" placeholder="Search returns, items, listings" aria-label="search" />
        <button type="button" title="Search">🔍</button>
      </div>
      <div class="az-nav-right">
        <a class="az-pill" href="/static/index.html"><small>EN</small><span>🇮🇳</span></a>
        <a class="az-pill" href="/static/wallet.html"><small>Hello, Aarav</small><span>Account &amp; Points</span></a>
        <a class="az-pill" href="/static/return.html"><small>Returns</small><span>&amp; Orders</span></a>
        <a class="az-pill az-cart" href="/static/marketplace.html"><span class="ico">🛒</span><span>Cart</span></a>
      </div>
    </div>
    <nav class="az-subnav">
      <span class="all">☰ All</span>
      ${navLinks
        .map(
          (l) =>
            `<a href="${l.href}" class="${active === l.key ? "active" : ""}">${l.label}</a>`
        )
        .join("")}
      <a href="/static/index.html">Today's Deals</a>
      <a href="/static/marketplace.html">Hyperlocal Resale</a>
      <a href="/static/return.html">How it works</a>
    </nav>`;
  document.body.insertBefore(nav, document.body.firstChild);

  const foot = document.createElement("footer");
  foot.innerHTML = `
    <div class="az-top" onclick="window.scrollTo({top:0,behavior:'smooth'})">Back to top</div>
    <div class="az-foot">
      SecondLife AI — smarter, greener returns &nbsp;·&nbsp;
      <a href="/static/return.html">Return an item</a> ·
      <a href="/static/marketplace.html">Marketplace</a> ·
      <a href="/static/wallet.html">Green Points</a>
      <div style="margin-top:8px;color:#9aa4ad">© 2026 SecondLife AI · Demo environment</div>
    </div>`;
  document.body.appendChild(foot);
}
