let currentBalance = 0;

async function fetchBalance() {
  const customerId = document.getElementById('customerId').value;
  if (!customerId) return;
  
  try {
    const data = await fetchApi(`/customers/${encodeURIComponent(customerId)}/green-points`);
    currentBalance = data.balance;
    animateValue('balance', 0, currentBalance, 1000);
  } catch (e) {
    document.getElementById('balance').textContent = '0';
  }
}

async function redeemPoints() {
  const customerId = document.getElementById('customerId').value;
  const amountInput = document.getElementById('redeemAmount');
  const amount = parseInt(amountInput.value, 10);
  
  if (!amount || amount <= 0) {
    showToast('Please enter a valid amount to redeem.', 'error');
    return;
  }
  
  const btn = document.getElementById('redeemBtn');
  btn.disabled = true;
  btn.textContent = 'Redeeming...';
  
  try {
    const body = { amountToRedeem: amount };
    const data = await fetchApi(`/customers/${encodeURIComponent(customerId)}/green-points/redeem`, 'POST', body);
    
    showToast(`Successfully redeemed ${data.pointsRedeemed} points!`, 'success');
    amountInput.value = '';
    
    // Refresh balance
    fetchBalance();
  } catch (e) {
    // Error handled by api.js
  } finally {
    btn.disabled = false;
    btn.textContent = 'Redeem Now';
  }
}

// Animation helper for numbers
function animateValue(id, start, end, duration) {
  if (start === end) {
    document.getElementById(id).textContent = end;
    return;
  }
  const obj = document.getElementById(id);
  let startTimestamp = null;
  const step = (timestamp) => {
    if (!startTimestamp) startTimestamp = timestamp;
    const progress = Math.min((timestamp - startTimestamp) / duration, 1);
    // Ease out quad
    const easeOut = progress * (2 - progress);
    obj.innerHTML = Math.floor(easeOut * (end - start) + start);
    if (progress < 1) {
      window.requestAnimationFrame(step);
    }
  };
  window.requestAnimationFrame(step);
}

// Initial fetch
document.addEventListener('DOMContentLoaded', fetchBalance);
