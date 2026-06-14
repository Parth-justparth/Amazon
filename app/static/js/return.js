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
        <div class="thumb">${catIcon(it.category)} <span class="badge badge-score score">${it.windowDays ?? "—"}d window</span></div>
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

function catIcon(c) {
  return { ELECTRONICS: "💻", HOME_APPLIANCES: "🔌", FOOTWEAR: "👟", CLOTHING_FOOTWEAR: "👕" }[c] || "📦";
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

function scoreLine() {
  const defects = (S.defects && S.defects.length) ? ` · Defects: ${S.defects.map(esc).join(", ")}` : "";
  return `<div class="alert alert-info"><span class="ico">🔎</span><div><strong>AI condition score: ${S.score}/100</strong><div class="tiny mt-1">${esc(S.summary || "")}${defects}</div></div></div>`;
}

function renderKeepIt(offer) {
  document.getElementById("decisionBody").innerHTML = `
    <div class="card-head"><h2>Keep it &amp; save the planet 🌍</h2><p class="muted">A minor issue + great condition — keep the item, take a partial refund, and skip the shipping entirely.</p></div>
    ${scoreLine()}
    <div class="offer mt-2">
      <div class="muted">Partial refund to your original payment method</div>
      <div class="amt">${inr(offer.partialRefundAmount, offer.currency)}</div>
      <div class="tiny muted">No shipping. No pickup. The item stays with you.</div>
    </div>
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
  const meta = {
    WAREHOUSE_RETURN: { emoji: "🏭", name: "Restock as new (Warehouse)", desc: "Pristine, complete, and worth more than it costs to ship — going back to the fulfilment centre to be sold as new." },
    HYPERLOCAL_RESALE: { emoji: "🏙️", name: "Local marketplace resale", desc: "In good shape but can't be sold as new — we'll list it for a nearby buyer so it isn't wasted." },
    GREEN_DONATION: { emoji: "💚", name: "Green donation", desc: "Lower resale value — donating it does the most good and saves the most CO₂." },
  }[d.disposition] || { emoji: "📦", name: pretty(d.disposition), desc: "" };

  const econ = d.reverseLogisticsCost != null ? `
    <dl class="kv mt-2">
      <dt>AI condition score</dt><dd>${d.secondLifeScore ?? S.score ?? "—"}/100</dd>
      <dt>Return reason</dt><dd>${pretty(d.reason || (S.item && S.item.suggestedReason) || "")}</dd>
      <dt>Reverse-logistics cost</dt><dd>${inr(d.reverseLogisticsCost, d.currency)}</dd>
      <dt>Recoverable value</dt><dd>${inr(d.depreciatedItemValue, d.currency)}</dd>
      <dt>Decision source</dt><dd>${pretty(d.decisionSource || "")}</dd>
    </dl>` : "";

  document.getElementById("decisionBody").innerHTML = `
    <div class="result-banner mb-2"><span class="emoji">${meta.emoji}</span>
      <div><div class="badge badge-blue mb-1">AI Disposition</div><h2>${meta.name}</h2><p class="muted">${meta.desc}</p></div></div>
    ${scoreLine()}
    ${d.llmReasoning ? `<div class="alert alert-success mt-1"><span class="ico">🤖</span><div>${esc(d.llmReasoning)}</div></div>` : ""}
    ${econ}
    <div class="row-between mt-3">
      <button class="btn-link" onclick="location.reload()">Start over</button>
      <button class="btn btn-primary" onclick="startFulfil()">Continue</button>
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
