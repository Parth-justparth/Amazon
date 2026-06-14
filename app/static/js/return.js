/* =========================================================================
   Return wizard — full lifecycle orchestration.
   Start → (Seller auth) → Assess → (DOA) → Decision → Fulfil → Done
   Covers: initiation, FBM seller-auth/A-to-z, photo assessment, DOA gate,
   hybrid decision, Keep It, warehouse / resale / donation flows, PoD bank
   details, refund, carbon impact + Green Points.
   ========================================================================= */

renderChrome("return");

const S = {
  rrId: null,
  currency: "INR",
  paymentMethod: null,
  sellerType: null,
  disposition: null,
  status: null,
  customerId: "cust_01",
  photos: [],
};

const PRESETS = {
  warehouse: { orderId: "ord_1001", itemId: "item_elec_01", customerId: "cust_01", reason: "DAMAGED_IN_TRANSIT", returnAction: "REPLACEMENT", submittedAt: "2025-01-15" },
  resale:    { orderId: "ord_2002", itemId: "item_appl_02", customerId: "cust_02", reason: "DEFECTIVE",          returnAction: "REPLACEMENT", submittedAt: "2025-01-15" },
  donation:  { orderId: "ord_1003", itemId: "item_foot_01", customerId: "cust_01", reason: "SIZE_OR_FIT",        returnAction: "REFUND",      submittedAt: "2025-01-05" },
  keepit:    { orderId: "ord_1004", itemId: "item_keepit_01", customerId: "cust_01", reason: "MINOR_DEFECT",     returnAction: "REPLACEMENT", submittedAt: "2025-01-14" },
};

const PANEL_STAGE = {
  start: "start", sellerauth: "start",
  assess: "assess", doa: "assess", processing: "assess",
  decision: "decision", fulfil: "fulfil", done: "done",
};
const STAGE_ORDER = ["start", "assess", "decision", "fulfil", "done"];

/* ---------- panel + stepper control ---------- */
function show(panel) {
  document.querySelectorAll("section[id^='p-']").forEach((s) => s.classList.add("hidden"));
  document.getElementById("p-" + panel).classList.remove("hidden");
  const stage = PANEL_STAGE[panel];
  const idx = STAGE_ORDER.indexOf(stage);
  document.querySelectorAll("#stepper .step").forEach((el) => {
    const i = STAGE_ORDER.indexOf(el.dataset.step);
    el.classList.toggle("active", i === idx);
    el.classList.toggle("done", i < idx);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- init ---------- */
async function init() {
  try {
    const r = await api("/return-reasons");
    const sel = document.getElementById("reason");
    sel.innerHTML = (r.reasons || []).map((x) => `<option value="${x}">${pretty(x)}</option>`).join("");
  } catch (e) {}
  await loadCatalog();
  preset("warehouse");
}

let CATALOG = [];
async function loadCatalog() {
  const cat = document.getElementById("catFilter").value;
  const sel = document.getElementById("catalogPick");
  sel.innerHTML = `<option value="">Loading items…</option>`;
  try {
    const q = cat ? `?category=${encodeURIComponent(cat)}` : "";
    const data = await api(`/catalog/items${q}`);
    CATALOG = data.items || [];
    if (CATALOG.length === 0) {
      sel.innerHTML = `<option value="">No items available</option>`;
      return;
    }
    sel.innerHTML =
      `<option value="">Select one of ${data.count} items…</option>` +
      CATALOG.map(
        (it, i) =>
          `<option value="${i}">${esc(it.title)} — ${inr(it.priceMinor, it.currency)} · ${esc(it.paymentMethod)}/${esc(it.sellerType)}${it.returnable ? "" : " · NON-RETURNABLE"}</option>`
      ).join("");
  } catch (e) {
    sel.innerHTML = `<option value="">Could not load catalog</option>`;
  }
}

function pickCatalogItem() {
  const i = document.getElementById("catalogPick").value;
  if (i === "") return;
  const it = CATALOG[Number(i)];
  if (!it) return;
  document.getElementById("orderId").value = it.orderId;
  document.getElementById("itemId").value = it.itemId;
  // ensure the customer option exists, else add it
  const custSel = document.getElementById("customerId");
  if (![...custSel.options].some((o) => o.value === it.customerId)) {
    const opt = document.createElement("option");
    opt.value = it.customerId;
    opt.textContent = it.customerId;
    custSel.appendChild(opt);
  }
  custSel.value = it.customerId;
  document.getElementById("returnAction").value = it.suggestedAction;
  const reasonSel = document.getElementById("reason");
  if ([...reasonSel.options].some((o) => o.value === it.suggestedReason)) reasonSel.value = it.suggestedReason;
  // valid in-window date = delivery date + 1 day
  const d = new Date(it.deliveryDate + "T00:00:00");
  d.setDate(d.getDate() + 1);
  document.getElementById("submittedAt").value = d.toISOString().slice(0, 10);
  document.getElementById("catalogHint").innerHTML =
    `Selected <strong>${esc(it.title)}</strong> · ${esc(it.displayCategory)} · window ${it.windowDays ?? "—"} days · ${it.returnable ? "returnable" : "non-returnable (will be rejected)"}.`;
}

function preset(kind) {
  const p = PRESETS[kind];
  document.getElementById("orderId").value = p.orderId;
  document.getElementById("itemId").value = p.itemId;
  document.getElementById("customerId").value = p.customerId;
  document.getElementById("returnAction").value = p.returnAction;
  document.getElementById("submittedAt").value = p.submittedAt;
  const reasonSel = document.getElementById("reason");
  if ([...reasonSel.options].some((o) => o.value === p.reason)) reasonSel.value = p.reason;
}

/* ---------- step 1: initiation ---------- */
async function startReturn() {
  const body = {
    orderId: val("orderId"),
    itemId: val("itemId"),
    customerId: val("customerId"),
    reason: val("reason"),
    returnAction: val("returnAction"),
    submittedAt: val("submittedAt") || null,
    damageProofProvided: chk("damageProofProvided"),
    validConditionConfirmed: {
      packaging: chk("packaging"),
      tags: chk("tags"),
      warrantyCard: chk("warrantyCard"),
      manuals: chk("manuals"),
      accessories: chk("accessories"),
    },
  };
  try {
    const data = await api("/returns", "POST", body);
    S.rrId = data.returnRequestId;
    S.currency = data.currency || "INR";
    S.paymentMethod = data.paymentMethod;
    S.sellerType = data.sellerType;
    S.status = data.status;
    S.customerId = body.customerId;
    toast("Return initiated · " + S.rrId, "success");

    if (data.status === "AWAITING_SELLER_AUTH") {
      show("sellerauth");
    } else {
      show("assess");
    }
  } catch (e) {}
}

/* ---------- FBM seller authorization (R19) ---------- */
async function sellerAuth(authorized) {
  try {
    const data = await api(`/returns/${S.rrId}/seller-auth`, "POST", { authorized });
    if (data.atozApplied) {
      // A-to-z platform refund issued — terminal.
      show("done");
      document.getElementById("doneBody").innerHTML = `
        <div class="result-banner mb-2"><span class="emoji">🛡️</span><div><h2>A-to-z Guarantee refund issued</h2><p class="muted">${esc(data.message || "")}</p></div></div>
        <dl class="kv">
          <dt>Refund status</dt><dd>${pretty(data.refundStatus || "")}</dd>
          <dt>Amount</dt><dd>${data.refundAmount != null ? inr(data.refundAmount, data.currency) : "—"}</dd>
        </dl>
        <div class="mt-2"><a class="btn btn-primary" href="/static/return.html">Start another return</a></div>`;
    } else {
      toast(data.message || "Seller authorized the return.", "success");
      show("assess");
    }
  } catch (e) {}
}

/* ---------- step 2: photo assessment ---------- */
function onPhotos(ev) {
  const files = Array.from(ev.target.files).slice(0, 10);
  S.photos = [];
  let pending = files.length;
  if (pending === 0) { document.getElementById("photoCount").textContent = ""; return; }
  files.forEach((f) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      // Live OpenAI vision needs the image bytes; send base64 with metadata.
      S.photos.push({ format: f.type || "image/jpeg", sizeBytes: f.size, base64Data: e.target.result });
      if (--pending === 0) {
        document.getElementById("photoCount").textContent = `${S.photos.length} photo(s) attached.`;
      }
    };
    reader.readAsDataURL(f);
  });
}

function useSamplePhotos() {
  // Generate small real PNGs so live vision calls have valid image data.
  const make = (label, bg) => {
    const c = document.createElement("canvas");
    c.width = 320; c.height = 240;
    const x = c.getContext("2d");
    x.fillStyle = bg; x.fillRect(0, 0, 320, 240);
    x.fillStyle = "#0f1111"; x.font = "20px Arial"; x.fillText(label, 18, 130);
    const data = c.toDataURL("image/png");
    return { format: "image/png", sizeBytes: Math.round((data.length - 22) * 0.75), base64Data: data };
  };
  S.photos = [
    make("Sample item — front", "#d7e3ea"),
    make("Sample item — side", "#e3ead7"),
    make("Sample item — label", "#eae0d7"),
  ];
  document.getElementById("photoCount").textContent = "3 sample photo(s) attached.";
  toast("Sample photos attached.", "info");
}

async function runAssessment() {
  if (S.photos.length === 0) { toast("Attach at least one photo.", "error"); return; }
  show("processing");
  try {
    const a = await api(`/returns/${S.rrId}/assessment`, "POST", { photos: S.photos });
    S.status = a.status;
    S.score = a.secondLifeScore;
    S.summary = a.conditionSummary;
    // Check whether a DOA gate now applies.
    const rr = await api(`/returns/${S.rrId}`);
    if (rr.status === "AWAITING_DOA" || rr.doaStatus === "REQUIRED") {
      show("doa");
      return;
    }
    await runDecision();
  } catch (e) {
    show("assess");
  }
}

/* ---------- DOA gate (R16) ---------- */
async function submitDoa(confirmsDoa) {
  const source = val("doaSource");
  try {
    const d = await api(`/returns/${S.rrId}/doa`, "POST", { source, confirmsDoa });
    if (d.doaStatus === "FAILED") {
      show("done");
      document.getElementById("doneBody").innerHTML = banner("⚠️", "DOA not confirmed",
        "The item did not pass DOA verification and was flagged for manual resolution.") +
        `<div class="mt-2"><a class="btn btn-primary" href="/static/return.html">Start another return</a></div>`;
      return;
    }
    toast("DOA verification satisfied.", "success");
    show("processing");
    await runDecision();
  } catch (e) { show("doa"); }
}

/* ---------- step 3: decision ---------- */
async function runDecision() {
  show("processing");
  try {
    const d = await api(`/returns/${S.rrId}/decision`, "POST");
    if (d.keepItOfferPresented) {
      const offer = await api(`/returns/${S.rrId}/keep-it`);
      renderKeepIt(offer);
    } else {
      S.disposition = d.disposition;
      renderDecision(d);
    }
    show("decision");
  } catch (e) {
    show("assess");
  }
}

function renderKeepIt(offer) {
  document.getElementById("decisionBody").innerHTML = `
    <div class="card-head"><h2>Keep it &amp; save the planet 🌍</h2><p class="muted">Skip the return entirely. Keep your item, get a partial refund, earn Green Points, and avoid all reverse-logistics CO₂.</p></div>
    <div class="offer">
      <div class="muted">Partial refund to your original payment method</div>
      <div class="amt">${inr(offer.partialRefundAmount, offer.currency)}</div>
      <div class="tiny muted">No shipping. No pickup. The item stays with you.</div>
    </div>
    <div class="row wrap-row mt-2">
      <button class="btn btn-cta" style="flex:1; min-width:180px;" onclick="acceptKeepIt()">Accept &amp; keep it</button>
      <button class="btn" style="flex:1; min-width:180px;" onclick="declineKeepIt()">No thanks, continue return</button>
    </div>`;
}

async function acceptKeepIt() {
  show("processing");
  try {
    const r = await api(`/returns/${S.rrId}/keep-it/accept`, "POST");
    S.disposition = "KEEP_IT";
    S.status = r.status;
    renderDone({
      title: "Item kept — thanks for going green!",
      emoji: "🌱",
      refundStatus: r.refundStatus,
      refundAmount: r.partialRefundAmount,
      currency: r.currency,
      points: r.pointsCredited,
      carbonKg: r.carbonSavingsKg,
      impactMessage: r.impactMessage,
    });
  } catch (e) { show("decision"); }
}

async function declineKeepIt() {
  show("processing");
  try {
    const d = await api(`/returns/${S.rrId}/keep-it/decline`, "POST");
    S.disposition = d.disposition;
    // re-fetch full decision economics for display
    let full = { disposition: d.disposition };
    renderDecision(full);
    show("decision");
  } catch (e) { show("decision"); }
}

function renderDecision(d) {
  const meta = {
    WAREHOUSE_RETURN: { emoji: "🏭", name: "Standard Warehouse Return", desc: "Pristine and valuable — sending it back to the fulfilment centre for resale as new." },
    HYPERLOCAL_RESALE: { emoji: "🏙️", name: "Hyperlocal Resale", desc: "Great condition and heavy to ship back — we'll list it locally for a nearby buyer." },
    GREEN_DONATION: { emoji: "💚", name: "Green Donation", desc: "Lower resale value — donating it does the most good and saves the most CO₂." },
  }[d.disposition] || { emoji: "📦", name: pretty(d.disposition), desc: "" };

  const econ = d.reverseLogisticsCost != null ? `
    <dl class="kv mt-2">
      <dt>SecondLife score</dt><dd>${d.secondLifeScore ?? S.score ?? "—"}/100</dd>
      <dt>Reverse-logistics cost</dt><dd>${inr(d.reverseLogisticsCost, d.currency)}</dd>
      <dt>Depreciated item value</dt><dd>${inr(d.depreciatedItemValue, d.currency)}</dd>
      <dt>Item weight</dt><dd>${d.weightGrams != null ? (d.weightGrams / 1000).toFixed(2) + " kg" : "—"}</dd>
      <dt>Decision source</dt><dd>${pretty(d.decisionSource || "")}</dd>
    </dl>` : "";

  document.getElementById("decisionBody").innerHTML = `
    <div class="result-banner mb-2"><span class="emoji">${meta.emoji}</span>
      <div><div class="badge badge-blue mb-1">AI Disposition</div><h2>${meta.name}</h2><p class="muted">${meta.desc}</p></div></div>
    ${d.llmReasoning ? `<div class="alert alert-info"><span class="ico">🤖</span><div>${esc(d.llmReasoning)}</div></div>` : ""}
    ${econ}
    <div class="row-between mt-3">
      <button class="btn-link" onclick="show('start')">Start over</button>
      <button class="btn btn-primary" onclick="startFulfil()">Continue</button>
    </div>`;
}

/* ---------- step 4: fulfilment ---------- */
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

function pickupForm(nextFn) {
  return `
    <div class="alert alert-info mb-2"><span class="ico">📦</span><div>A pickup address is required before we schedule collection (R20.4).</div></div>
    <div class="field"><label>Address line</label><input type="text" id="addr1" value="221B, MG Road" /></div>
    <div class="grid-2">
      <div class="field"><label>City</label><input type="text" id="addrCity" value="Bengaluru" /></div>
      <div class="field"><label>Pincode</label><input type="text" id="addrPin" value="560001" /></div>
    </div>
    <button class="btn btn-primary" onclick="${nextFn}">Save pickup address</button>`;
}

async function savePickup() {
  const body = { addressLine1: val("addr1"), city: val("addrCity"), pincode: val("addrPin") };
  await api(`/returns/${S.rrId}/step/pickup`, "POST", body);
}

function bankForm(nextFn) {
  return `
    <div class="alert alert-warn mb-2"><span class="ico">🏦</span><div>Pay-on-Delivery refunds need NEFT bank details before the refund timeline can start (R18).</div></div>
    <div class="field"><label>IFSC code (11 chars)</label><input type="text" id="ifsc" value="HDFC0001234" /></div>
    <div class="field"><label>Account number (9–18 digits)</label><input type="text" id="acct" value="123456789012" /></div>
    <button class="btn btn-primary" onclick="${nextFn}">Save bank details securely</button>`;
}

async function saveBank() {
  const data = await api(`/returns/${S.rrId}/bank-details`, "POST", { ifsc: val("ifsc"), accountNumber: val("acct") });
  toast("Bank details encrypted &amp; stored · " + (data.bankDetailsId || ""), "success");
}

/* ----- Warehouse flow (R4, R20) ----- */
async function whPickup() {
  setFulfil("Standard warehouse return", "Step 1 of " + (isPoD() ? "5" : "4") + " · Pickup address",
    pickupForm("whAfterPickup()"));
}
async function whAfterPickup() {
  try { await savePickup(); } catch (e) { return; }
  if (isPoD()) {
    setFulfil("Standard warehouse return", "Step 2 · Bank details (Pay-on-Delivery)", bankForm("whAfterBank()"));
  } else {
    whInspect();
  }
}
async function whAfterBank() {
  try { await saveBank(); } catch (e) { return; }
  whInspect();
}
function whInspect() {
  setFulfil("Standard warehouse return", "Inspection · confirm the item matches", `
    <div class="alert alert-info mb-2"><span class="ico">🔍</span><div>Record the inspection outcome (R20.5).</div></div>
    <div class="row wrap-row">
      <button class="btn btn-primary" onclick="whDoInspect('PASS')">Inspection passes</button>
      <button class="btn" onclick="whDoInspect('FAIL')">Inspection fails</button>
    </div>`);
}
async function whDoInspect(outcome) {
  try {
    const r = await api(`/returns/${S.rrId}/step/inspection`, "POST", { outcome });
    if (outcome === "FAIL") {
      renderDone({ title: "Flagged for manual resolution", emoji: "⚠️", note: r.message });
      return;
    }
  } catch (e) { return; }
  whLabel();
}
function whLabel() {
  setFulfil("Standard warehouse return", "Generate your prepaid shipping label", `
    <button class="btn btn-primary" onclick="whDoLabel()">Generate shipping label</button>
    <div id="labelOut" class="mt-2"></div>`);
}
async function whDoLabel() {
  try {
    const r = await api(`/returns/${S.rrId}/warehouse/label`, "POST");
    document.getElementById("labelOut").innerHTML = `
      <div class="alert alert-success"><span class="ico">🏷️</span><div>${esc(r.message)}<div class="tiny mt-1"><a href="${esc(r.shippingLabelUrl)}" target="_blank" rel="noopener">${esc(r.shippingLabelUrl)}</a></div></div></div>
      <button class="btn btn-cta mt-2" onclick="whReceipt()">Confirm warehouse receipt</button>`;
  } catch (e) {}
}
async function whReceipt() {
  show("processing");
  try {
    const r = await api(`/returns/${S.rrId}/warehouse/receipt`, "POST");
    S.status = r.status;
    await finishRefunded(r.refundStatus, r.message);
  } catch (e) { show("fulfil"); }
}

/* ----- Resale flow (R5) ----- */
async function rsPickup() {
  setFulfil("Hyperlocal resale", "Pickup address (item stays with you during the 48h window)", pickupForm("rsAfterPickup()"));
}
async function rsAfterPickup() {
  try { await savePickup(); } catch (e) { return; }
  show("processing");
  try {
    const r = await api(`/returns/${S.rrId}/resale/list`, "POST");
    S.status = "RESALE";
    const impact = await safeImpact();
    document.getElementById("doneBody").innerHTML = `
      ${banner("🏙️", "Listed on the Hyperlocal Marketplace", r.message)}
      <dl class="kv mt-2">
        <dt>Listing ID</dt><dd>${esc(r.listingId)}</dd>
        <dt>Discounted price</dt><dd class="price">${inr(r.discountedPriceMinor, S.currency)}</dd>
        <dt>Window closes</dt><dd>${r.windowExpiresAt ? new Date(r.windowExpiresAt).toLocaleString() : "—"}</dd>
      </dl>
      ${impactBlock(impact)}
      <div class="alert alert-info mt-2"><span class="ico">💡</span><div>You'll receive a full refund automatically once a local buyer purchases the item.</div></div>
      <div class="row wrap-row mt-2">
        <a class="btn btn-cta" href="/static/marketplace.html">View on marketplace</a>
        <a class="btn" href="/static/return.html">Start another return</a>
      </div>`;
    show("done");
  } catch (e) { show("fulfil"); }
}

/* ----- Donation flow (R7) ----- */
async function dnOptions() {
  show("processing");
  try {
    const o = await api(`/returns/${S.rrId}/donation/options`);
    if (o.reEvaluate) {
      setFulfil("Green donation", "", `<div class="alert alert-warn"><span class="ico">⚠️</span><div>${esc(o.message)}</div></div>`);
      return;
    }
    let opts = "";
    if (o.nearestBin) {
      opts += `<div class="alert alert-success mb-2"><span class="ico">📍</span><div><strong>Nearest donation bin:</strong> ${esc(o.nearestBin.binId)} in ${esc(o.nearestBin.city)} · ${o.nearestBin.distanceKm} km away</div></div>`;
    }
    if (o.pickupAvailable) {
      opts += `<div class="alert alert-info mb-2"><span class="ico">🚚</span><div>Free charity worker pickup is available in your city.</div></div>`;
    }
    setFulfil("Green donation", "Choose how to hand over the item", `
      ${opts}
      <div class="row wrap-row">
        ${o.pickupAvailable ? `<button class="btn btn-primary" onclick="dnSchedule()">Schedule worker pickup</button>` : ""}
        <button class="btn ${o.pickupAvailable ? "" : "btn-primary"}" onclick="dnPreConfirm()">I'll drop it at the bin</button>
      </div>
      <div id="dnOut" class="mt-2"></div>`);
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
  if (isPoD()) {
    setFulfil("Green donation", "Bank details (Pay-on-Delivery refund)", bankForm("dnAfterBank()"));
  } else {
    dnConfirm();
  }
}
async function dnAfterBank() {
  try { await saveBank(); } catch (e) { return; }
  dnConfirm();
}
async function dnConfirm() {
  show("processing");
  try {
    const r = await api(`/returns/${S.rrId}/donation/confirm`, "POST");
    S.status = "REFUNDED";
    await finishRefunded(r.refundStatus, r.message);
  } catch (e) { show("fulfil"); }
}

/* ---------- step 5: done / impact ---------- */
async function finishRefunded(refundStatus, note) {
  // Close the return (computes carbon) when possible, then pull impact + balance.
  try { await api(`/returns/${S.rrId}/step/closure`, "POST"); } catch (e) {}
  const impact = await safeImpact();
  let points = null;
  try { const g = await api(`/customers/${encodeURIComponent(S.customerId)}/green-points`); points = g.balance; } catch (e) {}
  renderDone({
    title: refundStatus === "WITHHELD_BANK_DETAILS" ? "Refund pending bank details" : "All done — thank you!",
    emoji: refundStatus === "WITHHELD_BANK_DETAILS" ? "🏦" : "🎉",
    refundStatus, note,
    carbonKg: impact ? impact.carbonSavingsKg : null,
    impactMessage: impact ? impact.impactMessage : null,
    moneySaved: impact ? impact.moneySavedMinor : null,
    balance: points,
  });
}

async function safeImpact() {
  try { return await api(`/returns/${S.rrId}/impact`); } catch (e) { return null; }
}

function impactBlock(impact) {
  if (!impact) return "";
  const co2 = impact.carbonSavingsKg != null ? `${impact.carbonSavingsKg} kg` : "—";
  return `
    <div class="impact mt-2">
      <div class="sub">CO₂ emissions avoided</div>
      <div class="big">${co2}</div>
      ${impact.impactMessage ? `<div class="muted">${esc(impact.impactMessage)}</div>` : ""}
    </div>`;
}

function renderDone(o) {
  let html = banner(o.emoji || "🎉", o.title || "Done", o.note || "");
  const rows = [];
  if (o.refundStatus) rows.push(["Refund status", pretty(o.refundStatus)]);
  if (o.refundAmount != null) rows.push(["Partial refund", inr(o.refundAmount, o.currency || S.currency)]);
  if (o.moneySaved != null) rows.push(["Value recovered", inr(o.moneySaved, S.currency)]);
  if (o.points != null) rows.push(["Green Points earned", "+" + o.points]);
  if (o.balance != null) rows.push(["Wallet balance", o.balance + " pts"]);
  if (rows.length) {
    html += `<dl class="kv mt-2">${rows.map((r) => `<dt>${r[0]}</dt><dd>${r[1]}</dd>`).join("")}</dl>`;
  }
  if (o.carbonKg != null || o.impactMessage) {
    html += impactBlock({ carbonSavingsKg: o.carbonKg, impactMessage: o.impactMessage });
  }
  html += `
    <div class="row wrap-row mt-3">
      <a class="btn btn-cta" href="/static/wallet.html">View Green Points</a>
      <a class="btn" href="/static/return.html">Start another return</a>
    </div>`;
  document.getElementById("doneBody").innerHTML = html;
  show("done");
}

/* ---------- helpers ---------- */
function banner(emoji, title, sub) {
  return `<div class="result-banner mb-1"><span class="emoji">${emoji}</span><div><h2>${esc(title)}</h2>${sub ? `<p class="muted">${esc(sub)}</p>` : ""}</div></div>`;
}
function val(id) { return document.getElementById(id).value.trim(); }
function chk(id) { return document.getElementById(id).checked; }

init();
