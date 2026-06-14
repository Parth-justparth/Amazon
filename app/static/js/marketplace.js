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

async function loadFeed() {
  const city = document.getElementById("city").value;
  const feed = document.getElementById("feed");
  const loader = document.getElementById("loader");
  const empty = document.getElementById("empty");

  feed.innerHTML = "";
  empty.classList.add("hidden");
  loader.classList.remove("hidden");

  try {
    // Backend returns { city, listings: [...] }
    const data = await api(`/marketplace?city=${encodeURIComponent(city)}`);
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
      card.innerHTML = `
        <div class="thumb">
          ${icon(it.itemCategory)}
          <span class="badge badge-score score">AI Score ${it.secondLifeScore ?? "—"}</span>
        </div>
        <div class="body">
          <div class="ttl">${esc(it.itemTitle || pretty(it.itemCategory))}</div>
          <div class="meta">${esc(pretty(it.itemCategory))} · Refurbished · ${esc(it.city)}</div>
          <div class="price price-lg"><span class="sym">₹</span>${inr(it.discountedPriceMinor).replace("₹", "")} ${orig}</div>
          <div class="tiny muted" style="flex:1;">Local pickup · Listing ${esc(it.listingId)}</div>
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

document.addEventListener("DOMContentLoaded", loadFeed);
loadFeed();
