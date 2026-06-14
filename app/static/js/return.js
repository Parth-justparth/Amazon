/* =========================================================================
   Return wizard — customer-scoped, live-AI flow.
   Select account → pick one of YOUR items → reason/type/condition →
   upload 3+ photos → AI verifies product + grades condition → decision
   (keep / restock-as-new / local resale / donate) → fulfil → done.
   ========================================================================= */

renderChrome("return");

const S = {
  rrId: null, currency: "INR", paymentMethod: null, sellerType: null,
  disposition: null, status: null, customerId: null, item: null, photos: [],
};

const STAGE = {
  who: "select", items: "select", details: "select", sellerauth: "select",
  assess: "assess", doa: "assess", processing: "assess",
  decision: "decision", fulfil: "fulfil", done: "done",
};
const STAGE_ORDER = ["select", "assess", "decision", "fulfil", "done"];

function show(panel) {
  document.querySelectorAll("section[id^='p-']").forEach((s) => s.classList.add("hidden"));
  document.getElementById("p-" + panel).classList.remove("hidden");
  const idx = STAGE_ORDER.indexOf(STAGE[panel]);
  document.querySelectorAll("#stepper .step").forEach((el) => {
    const i = STAGE_ORDER.indexOf(el.dataset.step);
    el.classList.toggle("active", i === idx);
    el.classList.toggle("done", i < idx);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- init: suggest accounts that actually have returnable items ---------- */
async function init() {
  try {
    const r = await api("/return-reasons");
    window.__reasons = r.reasons || [];
  } catch (e) { window.__reasons = []; }

  try {
    const cat = await api("/catalog/items");
    const today = new Date();
    const open = {};
    (cat.items || []).forEach((it) => {
      if (!it.returnable || it.windowDays == null) return;
      const dd = new Date(it.deliveryDate + "T00:00:00");
      const days = Math.floor((today - dd) / 86400000);
      if (days < it.windowDays) open[it.customerId] = (open[it.customerId] || 0) + 1;
    });
    const ids = Object.keys(open).sort((a, b) => open[b] - open[a]);
    const dl = document.getElementById("whoList");
    dl.innerHTML = ids.map((id) => `<option value="${id}">${id} · ${open[id]} returnable</option>`).join("");
    if (ids.length) {
      document.getElementById("who").value = ids[0];
      document.getElementById("whoHint").textContent =
        `${ids.length} demo accounts have items currently within the return window. Try ${ids[0]}.`;
    }
  } catch (e) {}
}

/* ---------- load a customer's own orders ---------- */
async function loadOrders() {
  S.customerId = document.getElementById("who").value.trim();
  if (!S.customerId) { toast("Enter an account id.", "error"); return; }
  let data;
  try { data = await api(`/customers/${encodeURIComponent(S.customerId)}/orders`); }
  catch (e) { return; }

  document.getElementById("itemsTitle").textContent = `${data.customerName}'s orders`;
  document.getElementById("itemsSub").textContent = `${data.customerId} · ${data.city} · ${data.count} item(s)`;
  const grid = document.getElementById("itemsGrid");

  if (!data.items.length) {
    grid.innerHTML = `<div class="alert alert-info"><span class="ico">📭</span><div>No orders found for this account.</div></div>`;
    show("items"); return;
  }

  S.itemsById = {};
  grid.innerHTML = data.items.map((it) => {
    S.itemsById[it.itemId] = it;
    let badge, action;
    if (it.alreadyReturning) {
      badge = `<span class="badge badge-amber">Return in progress</span>`;
      action = `<button class="btn btn-sm btn-block mt-1" disabled>Already returning</button>`;
    } else if (!it.returnable) {
      badge = `<span class="badge">Non-returnable</span>`;
      action = `<button class="btn btn-sm btn-block mt-1" disabled>Not returnable</button>`;
    } else if (!it.windowOpen) {
      badge = `<span class="badge badge-amber">Window closed</span>`;
      action = `<button class="btn btn-sm btn-block mt-1" disabled>Window elapsed</button>`;
    } else {
      const left = it.windowDays - it.daysSinceDelivery;
      badge = `<span class="badge badge-green">${left} day(s) left</span>`;
      action = `<button class="btn btn-cta btn-sm btn-block mt-1" onclick="chooseItem('${esc(it.itemId)}')">Return / replace</button>`;
    }
    return `
      <div class="listing">
        <div class="thumb">${icon(it)} <span class="badge badge-score score">${it.windowDays ?? "—"}d window</span></div>
        <div class="body">
          <div class="ttl">${esc(it.title)}</div>
          <div class="meta">${esc(it.displayCategory)} · delivered ${esc(it.deliveryDate)} (${it.daysSinceDelivery}d ago)</div>
          <div class="price">${inr(it.priceMinor, it.currency)}</div>
          <div class="tiny muted mt-1">${esc(it.paymentMethod)} · ${esc(it.sellerType)} ${badge}</div>
          ${action}
        </div>
      </div>`;
  }).join("");
  show("items");
}

const imgStyle = 'style="width: 120px; height: 120px; object-fit: contain; margin-bottom: 8px;"';

const PRODUCT_ICON = {
  EARBUDS: `<img src="/static/media/earbud.webp" alt="earbuds" ${imgStyle}>`,
  SMARTPHONE: `<img src="/static/media/smartphone.jpg" alt="smartphone" ${imgStyle}>`,
  LAPTOP: `<img src="/static/media/laptop.jpg" alt="laptop" ${imgStyle}>`,
  SPEAKER: `<img src="/static/media/speaker.jpg" alt="speaker" ${imgStyle}>`,
  SMARTWATCH: `<img src="/static/media/smartwatch.webp" alt="smartwatch" ${imgStyle}>`,
  POWER_BANK: `<img src="/static/media/powerbank.jpg" alt="powerbank" ${imgStyle}>`,
  CAMERA: `<img src="/static/media/camers.jpg" alt="camera" ${imgStyle}>`,
  MOUSE: `<img src="/static/media/mouse.avif" alt="mouse" ${imgStyle}>`,
  MICROWAVE: `<img src="/static/media/microwave.jpg" alt="microwave" ${imgStyle}>`,
  AIR_FRYER: `<img src="/static/media/airfryer.webp" alt="air fryer" ${imgStyle}>`,
  MIXER_GRINDER: `<img src="/static/media/mixer.jpg" alt="mixer grinder" ${imgStyle}>`,
  VACUUM_CLEANER: `<img src="/static/media/vaccum.jpg" alt="vacuum cleaner" ${imgStyle}>`,
  KETTLE: `<img src="/static/media/kettle.jpg" alt="kettle" ${imgStyle}>`,
  INDUCTION: `<img src="/static/media/induction.webp" alt="induction" ${imgStyle}>`,
  WASHING_MACHINE: `<img src="/static/media/washingmachine.jpg" alt="washing machine" ${imgStyle}>`,
  REFRIGERATOR: `<img src="/static/media/refridgerator.jpg" alt="refrigerator" ${imgStyle}>`,
  FOOTWEAR: `<img src="/static/media/sneakers.jpg" alt="footwear" ${imgStyle}>`,
  HEADPHONES: `<img src="/static/media/headphone.jpg" alt="headphones" ${imgStyle}>`,
  AIR_CONDITIONER: `<img src="/static/media/air-conditioner.jpg" alt="air conditioner" ${imgStyle}>`,
  BLENDER: `<img src="/static/media/electric-blender.webp" alt="blender" ${imgStyle}>`,
  INNERWEAR: `<img src="/static/media/innerwear.jpg" alt="innerwear" ${imgStyle}>`,
  PERSONALIZED: `<img src="/static/media/personalised.webp" alt="personalized" ${imgStyle}>`,
  GIFT_CARD: `<img src="/static/media/giftcards.jpg" alt="gift card" ${imgStyle}>`,
  SWIMWEAR: `<img src="/static/media/swimming wear.webp" alt="swimwear" ${imgStyle}>`
};

function icon(it) {
  if (it.productClassification && PRODUCT_ICON[it.productClassification]) {
    return PRODUCT_ICON[it.productClassification];
  }
  return { ELECTRONICS: "💻", HOME_APPLIANCES: "🔌", FOOTWEAR: "👟", CLOTHING_FOOTWEAR: "👕" }[it.category] || "📦";
}

/* ---------- details: reason / type / condition ---------- */
function chooseItem(itemId) {
  const it = S.itemsById[itemId];
  if (!it) return;
  S.item = it;
  document.getElementById("detailsItem").innerHTML =
    `<strong>${esc(it.title)}</strong> · ${esc(it.displayCategory)} · ${inr(it.priceMinor, it.currency)} · window ${it.windowDays} days`;

  // reasons
  const rsel = document.getElementById("reason");
  rsel.innerHTML = (window.__reasons || []).map((x) => `<option value="${x}">${pretty(x)}</option>`).join("");
  if ((window.__reasons || []).includes(it.suggestedReason)) rsel.value = it.suggestedReason;

  // allowed actions for this item's category
  const asel = document.getElementById("returnAction");
  const acts = it.allowableActions && it.allowableActions.length ? it.allowableActions : ["REFUND", "REPLACEMENT", "EXCHANGE"];
  asel.innerHTML = acts.map((a) => `<option value="${a}">${pretty(a)}</option>`).join("");
  if (acts.includes(it.suggestedAction)) asel.value = it.suggestedAction;
  document.getElementById("actionHint").textContent =
    acts.length === 1
      ? `Amazon policy allows only ${pretty(acts[0])} for ${it.displayCategory}.`
      : `Allowed for ${it.displayCategory}: ${acts.map(pretty).join(", ")}.`;

  // reason eligibility hint per category
  const damageCats = ["ELECTRONICS", "HOME_APPLIANCES", "MOBILES_LAPTOPS_ELECTRONICS", "HOME_KITCHEN_APPLIANCES", "BOOKS"];
  document.getElementById("reasonHint").textContent = damageCats.includes(it.category)
    ? "This category accepts returns only for a defective or damaged item."
    : "Choose the reason that best matches your situation.";

  // reset ticks
  ["packaging", "tags", "warrantyCard", "manuals", "accessories"].forEach((id) => (document.getElementById(id).checked = true));
  document.getElementById("damageProofProvided").checked = false;
  show("details");
}

/* ---------- initiate ---------- */
async function startReturn() {
  const body = {
    orderId: S.item.orderId,
    itemId: S.item.itemId,
    customerId: S.customerId,
    reason: val("reason"),
    returnAction: val("returnAction"),
    damageProofProvided: chk("damageProofProvided"),
    validConditionConfirmed: {
      packaging: chk("packaging"), tags: chk("tags"), warrantyCard: chk("warrantyCard"),
      manuals: chk("manuals"), accessories: chk("accessories"),
    },
  };
  try {
    const data = await api("/returns", "POST", body);
    S.rrId = data.returnRequestId;
    S.currency = data.currency || "INR";
    S.paymentMethod = data.paymentMethod;
    S.sellerType = data.sellerType;
    S.status = data.status;
    toast("Return started · " + S.rrId, "success");
    if (data.status === "AWAITING_SELLER_AUTH") show("sellerauth");
    else show("assess");
  } catch (e) {}
}

async function sellerAuth(authorized) {
  try {
    const data = await api(`/returns/${S.rrId}/seller-auth`, "POST", { authorized });
    if (data.atozApplied) {
      document.getElementById("doneBody").innerHTML =
        banner("🛡️", "A-to-z Guarantee refund issued", data.message || "") +
        `<dl class="kv"><dt>Refund status</dt><dd>${pretty(data.refundStatus || "")}</dd>
         <dt>Amount</dt><dd>${data.refundAmount != null ? inr(data.refundAmount, data.currency) : "—"}</dd></dl>
         <div class="mt-2"><a class="btn btn-primary" href="/static/return.html">Start another return</a></div>`;
      show("done");
    } else { toast(data.message || "Seller authorized.", "success"); show("assess"); }
  } catch (e) {}
}

/* ---------- photos ---------- */
function onPhotos(ev) {
  const files = Array.from(ev.target.files).slice(0, 10);
  S.photos = [];
  const preview = document.getElementById("photoPreview");
  preview.innerHTML = "";
  let pending = files.length;
  if (!pending) { document.getElementById("photoCount").textContent = ""; return; }
  files.forEach((f) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      S.photos.push({ format: f.type || "image/jpeg", sizeBytes: f.size, base64Data: e.target.result });
      const img = document.createElement("img");
      img.src = e.target.result;
      img.style.cssText = "width:74px;height:74px;object-fit:cover;border-radius:6px;border:1px solid var(--border)";
      preview.appendChild(img);
      if (--pending === 0) {
        const n = S.photos.length;
        document.getElementById("photoCount").innerHTML =
          n >= 3 ? `${n} photos attached ✓` : `<span style="color:var(--error)">${n} attached — add at least ${3 - n} more (need 3).</span>`;
      }
    };
    reader.readAsDataURL(f);
  });
}

async function runAssessment() {
  if (S.photos.length < 3) { toast("Please upload at least 3 photos from different angles.", "error"); return; }
  procView("Analyzing your photos…", "Verifying the product and grading its condition.");
  show("processing");
  try {
    const a = await api(`/returns/${S.rrId}/assessment`, "POST", { photos: S.photos });
    S.score = a.secondLifeScore;
    S.summary = a.conditionSummary;
    S.defects = a.defects || [];
    S.confidence = a.confidence != null ? a.confidence : 1;
    S.photosAnalyzed = a.photosAnalyzed || S.photos.length;
    S.sellableAsNew = a.sellableAsNew;
    const rr = await api(`/returns/${S.rrId}`);
    if (rr.status === "AWAITING_DOA" || rr.doaStatus === "REQUIRED") { show("doa"); return; }
    await runDecision();
  } catch (e) {
    // Product mismatch / blurry / too few photos → back to upload with the reason shown.
    show("assess");
    const c = document.getElementById("photoCount");
    if (c) c.innerHTML = `<span style="color:var(--error)">${esc(e.message || "Assessment failed — try clearer photos.")}</span>`;
  }
}

async function submitDoa(confirmsDoa) {
  try {
    const d = await api(`/returns/${S.rrId}/doa`, "POST", { source: val("doaSource"), confirmsDoa });
    if (d.doaStatus === "FAILED") {
      document.getElementById("doneBody").innerHTML =
        banner("⚠️", "DOA not confirmed", "The item did not pass DOA verification and was flagged for manual resolution.") +
        `<div class="mt-2"><a class="btn btn-primary" href="/static/return.html">Start another return</a></div>`;
      show("done"); return;
    }
    toast("DOA verification satisfied.", "success");
    procView("Choosing the best outcome…", "");
    show("processing");
    await runDecision();
  } catch (e) { show("doa"); }
}

/* ---------- decision ---------- */
async function runDecision() {
  procView("Choosing the best outcome…", "Weighing condition, reason, value and logistics.");
  show("processing");
  try {
    const d = await api(`/returns/${S.rrId}/decision`, "POST");
    if (d.keepItOfferPresented) {
      const offer = await api(`/returns/${S.rrId}/keep-it`);
      renderKeepIt(offer);
    } else { S.disposition = d.disposition; renderDecision(d); }
    show("decision");
  } catch (e) { show("assess"); }
}

/* ---------- presentation building blocks ---------- */
const POINTS = { WAREHOUSE_RETURN: 0, HYPERLOCAL_RESALE: 500, GREEN_DONATION: 300, KEEP_IT: 200 };
const DISPO = {
  WAREHOUSE_RETURN: { emoji: "🏭", name: "Restock as new (Warehouse)", short: "Standard return" },
  HYPERLOCAL_RESALE: { emoji: "🏙️", name: "Local marketplace resale", short: "Hyperlocal resale" },
  GREEN_DONATION: { emoji: "💚", name: "Green donation", short: "Donation" },
  KEEP_IT: { emoji: "🌱", name: "Keep-It offer", short: "Keep-It" },
};

function scoreBand(s) {
  if (s >= 95) return { label: "Like New", cls: "likenew" };
  if (s >= 80) return { label: "Excellent", cls: "excellent" };
  if (s >= 60) return { label: "Good", cls: "good" };
  if (s >= 40) return { label: "Fair", cls: "fair" };
  return { label: "Poor", cls: "poor" };
}

function scoreMeter(score) {
  const s = score == null ? 0 : score;
  const b = scoreBand(s);
  return `
    <div class="score-meter">
      <div class="top">
        <span class="num">${s}</span><span class="den">/ 100</span>
        <span class="band band-${b.cls}">${b.label}</span>
      </div>
      <div class="meter-track"><div class="meter-fill fill-${b.cls}" style="width:${s}%"></div></div>
      <div class="meter-legend">
        <span><b>95–100</b> Like New</span><span><b>80–94</b> Excellent</span>
        <span><b>60–79</b> Good</span><span><b>40–59</b> Fair</span><span><b>0–39</b> Poor</span>
      </div>
    </div>`;
}

function detectedList() {
  const items = [`<li><span class="ok">✓</span> Verified as the ordered product</li>`];
  if (S.summary) items.push(`<li><span class="ok">✓</span> ${esc(S.summary)}</li>`);
  (S.defects || []).forEach((d) => items.push(`<li><span class="warn">⚠</span> ${esc(d)}</li>`));
  if (!S.defects || !S.defects.length) items.push(`<li><span class="ok">✓</span> No major defects detected</li>`);
  return `<ul class="feat-list">${items.join("")}</ul>`;
}

function trustBlock() {
  const conf = Math.round((S.confidence != null ? S.confidence : 1) * 100);
  return `
    <div class="card" style="box-shadow:none;border-style:dashed;">
      <h3 class="mb-1">🔬 Trust &amp; transparency — how the AI assessed this</h3>
      <div class="report-grid mb-1">
        <div class="report-cell"><div class="k">Photos analyzed</div><div class="v">${S.photosAnalyzed || 0}</div></div>
        <div class="report-cell"><div class="k">Condition score</div><div class="v">${S.score}/100</div></div>
        <div class="report-cell"><div class="k">Confidence</div><div class="v">${conf}%</div></div>
      </div>
      <div class="lbl">What the AI detected</div>
      ${detectedList()}
      <div class="lbl mt-2">Match confidence</div>
      <div class="conf-track"><div class="conf-fill" style="width:${conf}%"></div></div>
    </div>`;
}

function benefitsFor(d) {
  const pts = POINTS[d] != null ? POINTS[d] : 0;
  const map = {
    WAREHOUSE_RETURN: ["Full refund", "Prepaid shipping label", "Restocked as new"],
    HYPERLOCAL_RESALE: ["Full refund on sale", "Free local pickup", "Keep it home for 48h"],
    GREEN_DONATION: ["Full refund", "Free pickup or bin drop", "Helps a local charity"],
    KEEP_IT: ["Partial refund now", "Keep the item", "No shipping or pickup"],
  };
  const lines = (map[d] || []).slice();
  lines.push(pts > 0 ? `+${pts} Green Points` : `+0 Green Points`);
  return lines;
}

function optionCard(d, recommended) {
  const m = DISPO[d] || { emoji: "📦", name: pretty(d) };
  const lines = benefitsFor(d).map((l) => `<li><span class="ok">✓</span> ${esc(l)}</li>`).join("");
  return `
    <div class="option ${recommended ? "recommended" : "muted-opt"}">
      ${recommended ? `<span class="tag">⭐ Recommended by AI</span>` : ""}
      <h4>${m.emoji} ${esc(m.name)}</h4>
      <ul>${lines}</ul>
    </div>`;
}

function optionsBlock(recommended) {
  const alts = ["KEEP_IT", "WAREHOUSE_RETURN", "HYPERLOCAL_RESALE", "GREEN_DONATION"].filter((d) => d !== recommended);
  const cards = [optionCard(recommended, true)].concat(alts.slice(0, 3).map((d) => optionCard(d, false)));
  return `<div class="options">${cards.join("")}</div>`;
}

function nextSteps(d) {
  const steps = {
    WAREHOUSE_RETURN: ["Confirm your pickup address", "Print the prepaid shipping label", "Hand over the item — it's inspected at the warehouse", "Your refund is processed to your original payment method"],
    HYPERLOCAL_RESALE: ["Your item is listed in your city", "Local buyers can see it for 48 hours", "Once purchased, your full refund is issued", "Green Points are credited to your wallet"],
    GREEN_DONATION: ["Choose the nearest bin or free worker pickup", "Hand over the item", "Your refund is issued", "Green Points are credited to your wallet"],
    KEEP_IT: ["Keep the item — nothing to ship", "Your partial refund goes to your original payment method", "Green Points are credited to your wallet"],
  }[d] || [];
  return `<ol class="next-steps">${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`;
}

function scoreLine() {
  return scoreMeter(S.score);
}

function renderKeepIt(offer) {
  document.getElementById("decisionBody").innerHTML = `
    <div class="result-banner mb-2"><span class="emoji">🌱</span>
      <div><div class="badge badge-green mb-1">⭐ Recommended by AI</div><h2>Keep it &amp; save the planet</h2>
      <p class="muted">A minor issue plus great condition — the best outcome for you is to keep the item and take a partial refund. No shipping, no waiting.</p></div></div>
    ${scoreMeter(S.score)}
    <div class="offer mt-2">
      <div class="muted">Partial refund to your original payment method</div>
      <div class="amt">${inr(offer.partialRefundAmount, offer.currency)}</div>
      <div class="tiny muted">+ ${POINTS.KEEP_IT} Green Points · item stays with you</div>
    </div>
    <h3 class="mt-3 mb-1">Your options</h3>
    ${optionsBlock("KEEP_IT")}
    <div class="alert alert-info mt-2"><span class="ico">💡</span><div><strong>Why we recommend this:</strong> the item is in good condition with only a minor reported issue, so keeping it avoids return shipping entirely while still putting money back in your pocket and earning rewards.</div></div>
    ${trustBlock()}
    <div class="row wrap-row mt-2">
      <button class="btn btn-cta" style="flex:1;min-width:180px;" onclick="acceptKeepIt()">Accept &amp; keep it</button>
      <button class="btn" style="flex:1;min-width:180px;" onclick="declineKeepIt()">No thanks, continue return</button>
    </div>`;
}

async function acceptKeepIt() {
  procView("Applying your Keep It offer…", "");
  show("processing");
  try {
    const r = await api(`/returns/${S.rrId}/keep-it/accept`, "POST");
    S.disposition = "KEEP_IT";
    renderDone({ title: "Item kept — thanks for going green!", emoji: "🌱",
      refundStatus: r.refundStatus, refundAmount: r.partialRefundAmount, currency: r.currency,
      points: r.pointsCredited, carbonKg: r.carbonSavingsKg, impactMessage: r.impactMessage });
  } catch (e) { show("decision"); }
}

async function declineKeepIt() {
  procView("Re-routing your return…", "");
  show("processing");
  try {
    const d = await api(`/returns/${S.rrId}/keep-it/decline`, "POST");
    S.disposition = d.disposition;
    renderDecision({ disposition: d.disposition });
    show("decision");
  } catch (e) { show("decision"); }
}

function renderDecision(d) {
  const m = DISPO[d.disposition] || { emoji: "📦", name: pretty(d.disposition) };
  const pts = POINTS[d.disposition] != null ? POINTS[d.disposition] : 0;

  const econ = d.reverseLogisticsCost != null ? `
    <div class="report-grid mt-2">
      <div class="report-cell"><div class="k">Return reason</div><div class="v" style="font-size:14px">${pretty(d.reason || (S.item && S.item.suggestedReason) || "")}</div></div>
      <div class="report-cell"><div class="k">Reverse-logistics cost</div><div class="v" style="font-size:15px">${inr(d.reverseLogisticsCost, d.currency)}</div></div>
      <div class="report-cell"><div class="k">Recoverable value</div><div class="v" style="font-size:15px">${inr(d.depreciatedItemValue, d.currency)}</div></div>
      <div class="report-cell"><div class="k">Est. Green Points</div><div class="v">+${pts}</div></div>
    </div>` : "";

  const why = d.llmReasoning
    ? esc(d.llmReasoning)
    : `Based on the AI condition score and your return reason, this is the outcome that recovers the most value for you while keeping the item out of landfill.`;

  document.getElementById("decisionBody").innerHTML = `
    <div class="result-banner mb-2"><span class="emoji">${m.emoji}</span>
      <div><div class="badge badge-green mb-1">⭐ Recommended by AI</div><h2>${esc(m.name)}</h2></div></div>

    <h3 class="mb-1">Condition</h3>
    ${scoreMeter(d.secondLifeScore != null ? d.secondLifeScore : S.score)}
    ${econ}

    <h3 class="mt-3 mb-1">Your options</h3>
    ${optionsBlock(d.disposition)}

    <div class="alert alert-success mt-2"><span class="ico">🤖</span><div><strong>Why we recommend this:</strong> ${why}</div></div>

    ${trustBlock()}

    <h3 class="mt-3 mb-1">What happens next</h3>
    ${nextSteps(d.disposition)}

    <div class="row-between mt-3">
      <button class="btn-link" onclick="location.reload()">Start over</button>
      <button class="btn btn-primary" onclick="startFulfil()">Continue with ${esc(DISPO[d.disposition] ? DISPO[d.disposition].short : "this")}</button>
    </div>`;
}

/* ---------- fulfilment (warehouse / resale / donation) ---------- */
function setFulfil(title, sub, html) {
  document.getElementById("fulfilTitle").textContent = title;
  document.getElementById("fulfilSub").textContent = sub;
  document.getElementById("fulfilBody").innerHTML = html;
  show("fulfil");
}
function startFulfil() {
  if (S.disposition === "WAREHOUSE_RETURN") whPickup();
  else if (S.disposition === "HYPERLOCAL_RESALE") rsPickup();
  else if (S.disposition === "GREEN_DONATION") dnOptions();
  else { toast("Nothing further to fulfil.", "info"); show("decision"); }
}
const isPoD = () => S.paymentMethod === "PAY_ON_DELIVERY";

function pickupForm(next) {
  return `
    <div class="alert alert-info mb-2"><span class="ico">📦</span><div>A pickup address is required before we schedule collection (R20.4).</div></div>
    <div class="field"><label>Address line</label><input type="text" id="addr1" value="221B, MG Road" /></div>
    <div class="grid-2">
      <div class="field"><label>City</label><input type="text" id="addrCity" value="${esc((S.itemsById && S.item && (S.itemsById[S.item.itemId] || {}).city) || 'Bengaluru')}" /></div>
      <div class="field"><label>Pincode</label><input type="text" id="addrPin" value="560001" /></div>
    </div>
    <button class="btn btn-primary" onclick="${next}">Save pickup address</button>`;
}
async function savePickup() {
  await api(`/returns/${S.rrId}/step/pickup`, "POST",
    { addressLine1: val("addr1"), city: val("addrCity"), pincode: val("addrPin") });
}
function bankForm(next) {
  return `
    <div class="alert alert-warn mb-2"><span class="ico">🏦</span><div>Pay-on-Delivery refunds need NEFT bank details before the refund timeline can start (R18).</div></div>
    <div class="field"><label>IFSC (11 chars)</label><input type="text" id="ifsc" value="HDFC0001234" /></div>
    <div class="field"><label>Account number (9–18 digits)</label><input type="text" id="acct" value="123456789012" /></div>
    <button class="btn btn-primary" onclick="${next}">Save bank details securely</button>`;
}
async function saveBank() {
  const d = await api(`/returns/${S.rrId}/bank-details`, "POST", { ifsc: val("ifsc"), accountNumber: val("acct") });
  toast("Bank details stored · " + (d.bankDetailsId || ""), "success");
}

async function whPickup() { setFulfil("Restock as new", "Pickup address", pickupForm("whAfterPickup()")); }
async function whAfterPickup() {
  try { await savePickup(); } catch (e) { return; }
  if (isPoD()) setFulfil("Restock as new", "Bank details (Pay-on-Delivery)", bankForm("whAfterBank()"));
  else whInspect();
}
async function whAfterBank() { try { await saveBank(); } catch (e) { return; } whInspect(); }
function whInspect() {
  setFulfil("Restock as new", "Inspection", `
    <div class="alert alert-info mb-2"><span class="ico">🔍</span><div>Record the inspection outcome (R20.5).</div></div>
    <div class="row wrap-row"><button class="btn btn-primary" onclick="whDoInspect('PASS')">Inspection passes</button>
    <button class="btn" onclick="whDoInspect('FAIL')">Inspection fails</button></div>`);
}
async function whDoInspect(outcome) {
  try {
    const r = await api(`/returns/${S.rrId}/step/inspection`, "POST", { outcome });
    if (outcome === "FAIL") { renderDone({ title: "Flagged for manual resolution", emoji: "⚠️", note: r.message }); return; }
  } catch (e) { return; }
  whLabel();
}
function whLabel() {
  setFulfil("Restock as new", "Shipping label", `<button class="btn btn-primary" onclick="whDoLabel()">Generate shipping label</button><div id="labelOut" class="mt-2"></div>`);
}
async function whDoLabel() {
  try {
    const r = await api(`/returns/${S.rrId}/warehouse/label`, "POST");
    document.getElementById("labelOut").innerHTML =
      `<div class="alert alert-success"><span class="ico">🏷️</span><div>${esc(r.message)}<div class="tiny mt-1"><a href="${esc(r.shippingLabelUrl)}" target="_blank" rel="noopener">${esc(r.shippingLabelUrl)}</a></div></div></div>
       <button class="btn btn-cta mt-2" onclick="whReceipt()">Confirm warehouse receipt</button>`;
  } catch (e) {}
}
async function whReceipt() {
  procView("Processing refund…", ""); show("processing");
  try { const r = await api(`/returns/${S.rrId}/warehouse/receipt`, "POST"); await finishRefunded(r.refundStatus); }
  catch (e) { show("fulfil"); }
}

async function rsPickup() { setFulfil("Local marketplace resale", "Pickup address (item stays with you for the 48h window)", pickupForm("rsAfterPickup()")); }
async function rsAfterPickup() {
  try { await savePickup(); } catch (e) { return; }
  procView("Listing on the marketplace…", ""); show("processing");
  try {
    const r = await api(`/returns/${S.rrId}/resale/list`, "POST");
    const impact = await safeImpact();
    document.getElementById("doneBody").innerHTML = `
      ${banner("🏙️", "Listed on the Hyperlocal Marketplace", r.message)}
      <dl class="kv mt-2"><dt>Listing ID</dt><dd>${esc(r.listingId)}</dd>
        <dt>Discounted price</dt><dd class="price">${inr(r.discountedPriceMinor, S.currency)}</dd>
        <dt>Window closes</dt><dd>${r.windowExpiresAt ? new Date(r.windowExpiresAt).toLocaleString() : "—"}</dd></dl>
      ${impactBlock(impact)}
      <div class="alert alert-info mt-2"><span class="ico">💡</span><div>You'll get a full refund automatically once a local buyer purchases it.</div></div>
      <div class="row wrap-row mt-2"><a class="btn btn-cta" href="/static/marketplace.html">View on marketplace</a>
        <a class="btn" href="/static/return.html">Start another return</a></div>`;
    show("done");
  } catch (e) { show("fulfil"); }
}

async function dnOptions() {
  procView("Finding donation options…", ""); show("processing");
  try {
    const o = await api(`/returns/${S.rrId}/donation/options`);
    if (o.reEvaluate) { setFulfil("Green donation", "", `<div class="alert alert-warn"><span class="ico">⚠️</span><div>${esc(o.message)}</div></div>`); return; }
    let opts = "";
    if (o.nearestBin) opts += `<div class="alert alert-success mb-2"><span class="ico">📍</span><div><strong>Nearest bin:</strong> ${esc(o.nearestBin.binId)} in ${esc(o.nearestBin.city)} · ${o.nearestBin.distanceKm} km</div></div>`;
    if (o.pickupAvailable) opts += `<div class="alert alert-info mb-2"><span class="ico">🚚</span><div>Free charity worker pickup is available in your city.</div></div>`;
    setFulfil("Green donation", "Choose how to hand over the item", `
      ${opts}
      <div class="row wrap-row">
        ${o.pickupAvailable ? `<button class="btn btn-primary" onclick="dnSchedule()">Schedule worker pickup</button>` : ""}
        <button class="btn ${o.pickupAvailable ? "" : "btn-primary"}" onclick="dnPreConfirm()">I'll drop it at the bin</button>
      </div><div id="dnOut" class="mt-2"></div>`);
  } catch (e) { show("decision"); }
}
async function dnSchedule() {
  try {
    const r = await api(`/returns/${S.rrId}/donation/pickup`, "POST", { charityId: null });
    document.getElementById("dnOut").innerHTML =
      `<div class="alert alert-success"><span class="ico">🗓️</span><div>${esc(r.message)} Scheduled for <strong>${esc(r.scheduledDate)}</strong>.</div></div>
       <button class="btn btn-cta mt-2" onclick="dnPreConfirm()">Confirm donation</button>`;
  } catch (e) {}
}
function dnPreConfirm() {
  if (isPoD()) setFulfil("Green donation", "Bank details (Pay-on-Delivery refund)", bankForm("dnAfterBank()"));
  else dnConfirm();
}
async function dnAfterBank() { try { await saveBank(); } catch (e) { return; } dnConfirm(); }
async function dnConfirm() {
  procView("Confirming donation &amp; refund…", ""); show("processing");
  try { const r = await api(`/returns/${S.rrId}/donation/confirm`, "POST"); await finishRefunded(r.refundStatus); }
  catch (e) { show("fulfil"); }
}

/* ---------- done / impact / points ---------- */
async function finishRefunded(refundStatus) {
  try { await api(`/returns/${S.rrId}/step/closure`, "POST"); } catch (e) {}
  const impact = await safeImpact();
  let balance = null;
  try { const g = await api(`/customers/${encodeURIComponent(S.customerId)}/green-points`); balance = g.balance; } catch (e) {}
  renderDone({
    title: refundStatus === "WITHHELD_BANK_DETAILS" ? "Refund pending bank details" : "All done — thank you!",
    emoji: refundStatus === "WITHHELD_BANK_DETAILS" ? "🏦" : "🎉",
    refundStatus,
    carbonKg: impact ? impact.carbonSavingsKg : null,
    impactMessage: impact ? impact.impactMessage : null,
    moneySaved: impact ? impact.moneySavedMinor : null,
    balance,
  });
}
async function safeImpact() { try { return await api(`/returns/${S.rrId}/impact`); } catch (e) { return null; } }
function impactBlock(impact) {
  if (!impact) return "";
  const co2 = impact.carbonSavingsKg != null ? `${impact.carbonSavingsKg} kg` : "—";
  return `<div class="impact mt-2"><div class="sub">CO₂ emissions avoided</div><div class="big">${co2}</div>
    ${impact.impactMessage ? `<div class="muted">${esc(impact.impactMessage)}</div>` : ""}</div>`;
}
function renderDone(o) {
  let html = banner(o.emoji || "🎉", o.title || "Done", o.note || "");
  const rows = [];
  if (o.refundStatus) rows.push(["Refund status", pretty(o.refundStatus)]);
  if (o.refundAmount != null) rows.push(["Partial refund", inr(o.refundAmount, o.currency || S.currency)]);
  if (o.moneySaved != null) rows.push(["Value recovered", inr(o.moneySaved, S.currency)]);
  if (o.points != null) rows.push(["Green Points earned", "+" + o.points]);
  if (o.balance != null) rows.push([`Wallet (${S.customerId})`, o.balance + " pts"]);
  if (rows.length) html += `<dl class="kv mt-2">${rows.map((r) => `<dt>${r[0]}</dt><dd>${r[1]}</dd>`).join("")}</dl>`;
  if (o.carbonKg != null || o.impactMessage) html += impactBlock({ carbonSavingsKg: o.carbonKg, impactMessage: o.impactMessage });
  html += `<div class="row wrap-row mt-3"><a class="btn btn-cta" href="/static/wallet.html?c=${encodeURIComponent(S.customerId || "")}">View Green Points</a>
    <a class="btn" href="/static/return.html">Start another return</a></div>`;
  document.getElementById("doneBody").innerHTML = html;
  show("done");
}

/* ---------- helpers ---------- */
function procView(t, s) { document.getElementById("procTitle").textContent = t; document.getElementById("procSub").textContent = s; }
function banner(emoji, title, sub) {
  return `<div class="result-banner mb-1"><span class="emoji">${emoji}</span><div><h2>${esc(title)}</h2>${sub ? `<p class="muted">${esc(sub)}</p>` : ""}</div></div>`;
}
function val(id) { return document.getElementById(id).value.trim(); }
function chk(id) { return document.getElementById(id).checked; }

init();
