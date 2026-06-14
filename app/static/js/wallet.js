/* Green Points Wallet — balance + redemption (R8.4, R9). */

renderChrome("wallet");

let balance = 0;

// Honor ?c=<customerId> deep-link from the return flow.
const _params = new URLSearchParams(location.search);
if (_params.get("c")) {
  const el = document.getElementById("customerId");
  if (el) el.value = _params.get("c");
}

function cid() {
  return document.getElementById("customerId").value.trim();
}

async function loadBalance() {
  const el = document.getElementById("balance");
  try {
    const data = await api(`/customers/${encodeURIComponent(cid())}/green-points`);
    animate(el, balance, data.balance, 700);
    balance = data.balance;
  } catch (e) {
    el.textContent = "0";
    balance = 0;
  }
}

async function redeem() {
  const input = document.getElementById("redeemAmount");
  const points = parseInt(input.value, 10);
  if (!points || points < 1) {
    toast("Enter a whole number of at least 1 point.", "error");
    return;
  }
  const btn = document.getElementById("redeemBtn");
  btn.disabled = true;
  const original = btn.textContent;
  btn.innerHTML = '<span class="spinner"></span> Redeeming';

  try {
    // Backend expects {"points": <number>}
    const data = await api(
      `/customers/${encodeURIComponent(cid())}/green-points/redeem`,
      "POST",
      { points }
    );
    toast(
      `Redeemed ${data.pointsRedeemed} points → ${inr(data.amazonPayCreditedMinor)} to Amazon Pay`,
      "success"
    );
    input.value = "";
    document.getElementById("redeemPreview").textContent = "";

    const note = document.getElementById("ledgerNote");
    note.classList.remove("hidden");
    note.innerHTML = `<span class="ico">✅</span><div>Redemption <strong>${esc(
      data.redemptionId || ""
    )}</strong> complete. ${inr(
      data.amazonPayCreditedMinor
    )} credited to Amazon Pay. New balance: <strong>${data.balance}</strong> points.</div>`;

    await loadBalance();
  } catch (e) {
    /* toast already shown */
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function animate(el, from, to, ms) {
  if (from === to) { el.textContent = to.toLocaleString("en-IN"); return; }
  const start = performance.now();
  function frame(now) {
    const p = Math.min((now - start) / ms, 1);
    const eased = p * (2 - p);
    el.textContent = Math.floor(from + (to - from) * eased).toLocaleString("en-IN");
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

document.getElementById("redeemAmount").addEventListener("input", (e) => {
  const n = parseInt(e.target.value, 10);
  document.getElementById("redeemPreview").textContent =
    n > 0 ? `≈ ${inr(n * 100)} to Amazon Pay` : "";
});

document.addEventListener("DOMContentLoaded", loadBalance);
loadBalance();
