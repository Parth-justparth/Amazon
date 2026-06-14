/* Hyperlocal Marketplace — feed + atomic purchase (R5, R6). */

renderChrome("market");

const CATEGORY_ICON = {
  ELECTRONICS: "💻",
  MOBILES_LAPTOPS_ELECTRONICS: "📱",
  HOME_APPLIANCES: "🔌",
  HOME_KITCHEN_APPLIANCES: "🍳",
  FOOTWEAR: "👟",
  CLOTHING_FOOTWEAR: "👕",
  BOOKS: "📚",
};

function icon(cat) {
  return CATEGORY_ICON[cat] || "📦";
}

async function loadCities() {
  const sel = document.getElementById("city");
  try {
    const data = await api("/marketplace/cities");
    const opts = [`<option value="ALL">All cities (${data.total})</option>`].concat(
      (data.cities || []).map((c) => `<option value="${esc(c.city)}">${esc(c.city)} (${c.count})</option>`)
    );
    const current = sel.value;
    sel.innerHTML = opts.join("");
    if ([...sel.options].some((o) => o.value === current)) sel.value = current;
  } catch (e) {}
}

async function loadFeed() {
  const city = document.getElementById("city").value || "ALL";
  const feed = document.getElementById("feed");
  const loader = document.getElementById("loader");
  const empty = document.getElementById("empty");

  feed.innerHTML = "";
  empty.classList.add("hidden");
  loader.classList.remove("hidden");

  try {
    const q = city && city !== "ALL" ? `?city=${encodeURIComponent(city)}` : "";
    const data = await api(`/marketplace${q}`);
    const listings = (data && data.listings) || [];
    loader.classList.add("hidden");

    if (listings.length === 0) {
      empty.classList.remove("hidden");
      return;
    }

    listings.forEach((it) => {
      const card = document.createElement("div");
      card.className = "listing fade-in";
      const orig = it.originalPriceMinor
        ? `<span class="strike">${inr(it.originalPriceMinor, it.currency)}</span>`
        : "";
      const band = it.secondLifeScore >= 95 ? "Like New" : it.secondLifeScore >= 80 ? "Excellent" : it.secondLifeScore >= 60 ? "Good" : "Fair";
      card.innerHTML = `
        <div class="thumb">
          ${icon(it.itemCategory)}
          <span class="badge badge-score score">${it.secondLifeScore ?? "—"}/100 · ${band}</span>
        </div>
        <div class="body">
          <div class="ttl">${esc(it.itemTitle || pretty(it.itemCategory))}</div>
          <div class="meta">${esc(pretty(it.itemCategory))} · ${esc(it.city)}</div>
          <div class="trust-badges" style="margin:6px 0;">
            <span class="tbadge">🤖 AI Certified</span>
            <span class="tbadge">✓ Verified Condition</span>
            <span class="tbadge">🔧 Functional Tested</span>
            <span class="tbadge">📍 Local Pickup</span>
          </div>
          <div class="price price-lg"><span class="sym">₹</span>${inr(it.discountedPriceMinor).replace("₹", "")} ${orig}</div>
          ${it.why ? `<div class="alert alert-success" style="padding:8px 10px; font-size:12px; margin:6px 0;"><span class="ico">🌱</span><div>${esc(it.why)}</div></div>` : ""}
          <div class="tiny muted" style="flex:1;">Listing ${esc(it.listingId)}</div>
          <button class="btn btn-cta btn-block mt-1" onclick="buy('${esc(it.listingId)}', this)">Buy now</button>
        </div>`;
      feed.appendChild(card);
    });
  } catch (e) {
    loader.classList.add("hidden");
  }
}

async function buy(listingId, btn) {
  const buyerId = document.getElementById("buyerId").value;
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Securing'; }
  try {
    const data = await api(`/listings/${listingId}/purchase`, "POST", { buyerId });
    if (data.status === "SOLD") {
      document.getElementById("m-loc").textContent = data.pickupLocation || "—";
      document.getElementById("m-contact").textContent = data.pickupContact || "—";
      document.getElementById("m-refund").textContent =
        "Seller refund status: " + pretty(data.refundStatus || "");
      document.getElementById("modal").classList.remove("hidden");
      loadFeed();
    }
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "Buy now"; }
  }
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}

async function init() {
  await loadCities();
  await loadFeed();
}
document.addEventListener("DOMContentLoaded", init);
init();
