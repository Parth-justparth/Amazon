async function loadFeed() {
  const city = document.getElementById('city-filter').value;
  const grid = document.getElementById('feed-grid');
  const loader = document.getElementById('loader');
  const empty = document.getElementById('empty-state');
  
  grid.innerHTML = '';
  loader.classList.remove('hidden');
  empty.classList.add('hidden');
  
  try {
    const listings = await fetchApi(`/marketplace?city=${encodeURIComponent(city)}`);
    loader.classList.add('hidden');
    
    if (!listings || listings.length === 0) {
      empty.classList.remove('hidden');
      return;
    }
    
    listings.forEach(item => {
      const card = document.createElement('div');
      card.className = 'glass-panel listing-card animate-fade-in';
      
      const price = (item.discountedPriceMinor / 100).toFixed(2);
      const original = (item.originalPriceMinor / 100).toFixed(2);
      
      // Determine placeholder image based on category
      let icon = '📦';
      if (item.category === 'HOME_APPLIANCES') icon = '📺';
      else if (item.category === 'ELECTRONICS') icon = '💻';
      else if (item.category === 'FOOTWEAR') icon = '👟';
      
      card.innerHTML = `
        <div class="listing-img">
          ${icon}
          <div class="score-badge">AI Score: ${item.secondLifeScore}</div>
        </div>
        <div class="listing-content">
          <h3 style="margin-bottom: 0.25rem;">${item.itemTitle}</h3>
          <p class="text-secondary" style="font-size: 0.9rem; flex: 1;">${item.conditionDescription}</p>
          <div class="listing-price">
            ₹${price} <span class="listing-original">₹${original}</span>
          </div>
          <button class="btn btn-primary" style="width: 100%; margin-top: 1rem;" onclick="purchase('${item.listingId}')">
            Secure & Purchase
          </button>
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (e) {
    loader.classList.add('hidden');
  }
}

async function purchase(listingId) {
  try {
    const body = {
      buyerId: 'cust_buyer_01',
      paymentMethodId: 'pm_01'
    };
    const data = await fetchApi(`/listings/${listingId}/purchase`, 'POST', body);
    
    if (data.status === 'SOLD') {
      document.getElementById('pickup-location').textContent = data.pickupLocation;
      document.getElementById('seller-contact').textContent = data.sellerContact;
      document.getElementById('purchase-modal').classList.remove('hidden');
      loadFeed(); // Refresh feed
    }
  } catch (e) {
    // Error toast handled by api.js
  }
}

function closeModal() {
  document.getElementById('purchase-modal').classList.add('hidden');
}

// Initial load
document.addEventListener('DOMContentLoaded', loadFeed);
