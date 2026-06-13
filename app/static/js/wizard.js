let currentRrId = null;

function showStep(stepNum) {
  document.querySelectorAll('[id^="step-"]').forEach(el => el.classList.add('hidden'));
  document.getElementById(`step-${stepNum}`).classList.remove('hidden');
}

async function initiateReturn() {
  const body = {
    orderId: document.getElementById('orderId').value,
    itemId: document.getElementById('itemId').value,
    customerId: document.getElementById('customerId').value,
    reason: document.getElementById('reason').value,
    returnAction: document.getElementById('returnAction').value,
    validConditionConfirmed: {
      packaging: document.getElementById('packaging').checked,
      tags: document.getElementById('tags').checked,
      warrantyCard: document.getElementById('warrantyCard').checked,
      manuals: document.getElementById('manuals').checked,
      accessories: document.getElementById('accessories').checked
    },
    damageProofProvided: document.getElementById('damageProofProvided').checked,
    submittedAt: document.getElementById('submittedAt').value
  };

  try {
    const data = await fetchApi('/returns', 'POST', body);
    if (data && data.returnRequestId) {
      currentRrId = data.returnRequestId;
      showStep(2);
      showToast('Return initiated successfully.');
    }
  } catch (e) {
    // Error handled by api.js
  }
}

async function submitAssessment() {
  if (!currentRrId) return;
  const photoSet = document.getElementById('photoSet').value;
  const body = {
    photos: [{ format: 'jpeg', sizeBytes: 2048 }],
    photoSet: photoSet
  };

  showStep(3); // Show processing

  try {
    await fetchApi(`/returns/${currentRrId}/assessment`, 'POST', body);
    // After assessment, immediately trigger decision engine
    await triggerDecision();
  } catch (e) {
    showStep(2); // Go back on error
  }
}

async function triggerDecision() {
  try {
    const data = await fetchApi(`/returns/${currentRrId}/decision`, 'POST');
    
    if (data.keepItOfferPresented) {
      // Fetch Keep It offer details
      const offerData = await fetchApi(`/returns/${currentRrId}/keep-it`, 'GET');
      const amount = (offerData.partialRefundAmountMinor / 100).toFixed(2);
      document.getElementById('offer-amount').textContent = `₹${amount}`;
      
      document.getElementById('decision-view').classList.add('hidden');
      document.getElementById('keep-it-view').classList.remove('hidden');
    } else {
      // Normal disposition
      document.getElementById('disposition-text').textContent = data.disposition.replace('_', ' ');
      document.getElementById('decision-desc').textContent = 'Your return has been routed to the optimal channel based on condition and value.';
      
      document.getElementById('keep-it-view').classList.add('hidden');
      document.getElementById('decision-view').classList.remove('hidden');
    }
    
    showStep(4);
  } catch (e) {
    showStep(2);
  }
}

async function acceptKeepIt() {
  try {
    showStep(3); // Spinner
    const data = await fetchApi(`/returns/${currentRrId}/keep-it/accept`, 'POST');
    
    document.getElementById('carbon-saved').textContent = `${data.carbonSavingsKg} kg`;
    document.getElementById('points-earned').textContent = data.pointsCredited;
    
    showStep(5);
    showToast('Keep It offer accepted!', 'success');
  } catch (e) {
    showStep(4);
  }
}

async function declineKeepIt() {
  try {
    showStep(3);
    const data = await fetchApi(`/returns/${currentRrId}/keep-it/decline`, 'POST');
    
    document.getElementById('disposition-text').textContent = data.disposition.replace('_', ' ');
    document.getElementById('decision-desc').textContent = 'Offer declined. Your return will proceed through the standard channel.';
    
    document.getElementById('keep-it-view').classList.add('hidden');
    document.getElementById('decision-view').classList.remove('hidden');
    showStep(4);
  } catch (e) {
    showStep(4);
  }
}
