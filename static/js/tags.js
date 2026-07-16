// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
      let windowCustomTags = [];
      let currentAssignTradeId = null;
      let currentAssignTradeTags = [];
      
      async function loadTagStore() {
        try {
          const res = await fetch('/api/tags-store');
          windowCustomTags = await res.json();
          renderTagStoreList();
          if(typeof renderPointsPerTradeTable === 'function') renderPointsPerTradeTable();
        } catch(e) { console.error(e); }
      }
      
      async function saveTagStore() {
        try {
          await fetch('/api/tags-store', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({tags: windowCustomTags})
          });
        } catch(e) { console.error(e); }
      }
      
      function openTagStoreModal() {
        document.getElementById('tag-store-modal').style.display = 'flex';
        loadTagStore();
      }
      
      function renderTagStoreList() {
        const c = document.getElementById('tag-store-list');
        if (!windowCustomTags.length) { c.innerHTML = '<div style="color:#8b949e; font-size:12px;">No tags created yet.</div>'; return; }
        c.innerHTML = windowCustomTags.map((t, i) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:4px 0; border-bottom:1px solid #30363d;">
            <span style="font-size:12px; background:#1f6feb20; border:1px solid #1f6feb80; color:#58a6ff; padding:2px 6px; border-radius:4px;">${t}</span>
            <button onclick="deleteCustomTag(${i})" style="background:transparent; border:none; color:#f85149; cursor:pointer; font-size:12px;">❌</button>
          </div>
        `).join('');
      }
      
      function addCustomTag() {
        const inp = document.getElementById('new-tag-input');
        const v = inp.value.trim();
        if(v && !windowCustomTags.includes(v)) {
          windowCustomTags.push(v);
          saveTagStore();
          renderTagStoreList();
          inp.value = '';
        }
      }
      
      function deleteCustomTag(idx) {
        if(confirm('Delete this tag?')) {
          windowCustomTags.splice(idx, 1);
          saveTagStore();
          renderTagStoreList();
        }
      }
      
      function openTagAssignModal(tradeId, tagsStr) {
        currentAssignTradeId = tradeId;
        currentAssignTradeTags = tagsStr ? JSON.parse(decodeURIComponent(tagsStr)) : [];
        document.getElementById('tag-assign-trade-id').innerText = `Trade ID: ${tradeId}`;
        
        // Ensure custom tags are loaded
        if (!windowCustomTags.length) {
            loadTagStore().then(() => renderTagAssignList());
        } else {
            renderTagAssignList();
        }
        document.getElementById('tag-assign-modal').style.display = 'flex';
      }
      
      function renderTagAssignList() {
        const c = document.getElementById('tag-assign-list');
        // Show ONLY custom tags in the Assign Tags modal
        let allTags = windowCustomTags;
        
        c.innerHTML = '<input type="text" id="tag-assign-search" placeholder="Search manual tags..." onkeyup="filterTagAssignList()" style="width:100%; background:#161b22; border:1px solid #30363d; color:#c9d1d9; padding:6px; border-radius:4px; margin-bottom:8px; box-sizing:border-box;">' +
          '<div id="tag-assign-cb-list" style="display:flex; flex-direction:column; gap:6px;">' +
          allTags.map(t => {
          const checked = currentAssignTradeTags.includes(t) ? 'checked' : '';
          return `
            <label class="assign-tag-label" style="display:flex; align-items:center; gap:8px; font-size:12px; cursor:pointer;">
              <input type="checkbox" class="assign-tag-cb" value="${t}" ${checked}>
              <span>${t}</span>
            </label>
          `;
        }).join('') + '</div>';
      }
      
      function filterTagAssignList() {
        const q = document.getElementById('tag-assign-search').value.toLowerCase();
        const labels = document.querySelectorAll('.assign-tag-label');
        labels.forEach(l => {
            const txt = l.innerText.toLowerCase();
            if(txt.includes(q)) l.style.display = 'flex';
            else l.style.display = 'none';
        });
      }
      
      async function saveAssignedTags() {
        const cbs = document.querySelectorAll('.assign-tag-cb');
        const newlySelectedManualTags = Array.from(cbs).filter(cb => cb.checked).map(cb => cb.value);
        
        // Retain system tags that were already on the trade (filter out any tags that are in the manual tag store)
        const retainedSystemTags = currentAssignTradeTags.filter(t => !windowCustomTags.includes(t));
        
        // Merge system tags with new manual selection
        const finalTagsToSave = [...retainedSystemTags, ...newlySelectedManualTags];
        
        try {
          await fetch('/api/orders/update-tags', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: currentAssignTradeId, tags: finalTagsToSave})
          });
          document.getElementById('tag-assign-modal').style.display = 'none';
          
          // Update local memory without refreshing the page
          const updateTagsInArray = (arr) => {
              if (arr && Array.isArray(arr)) {
                  const t = arr.find(x => String(x.id) === String(currentAssignTradeId));
                  if (t) t.tags = finalTagsToSave;
              }
          };
          updateTagsInArray(window.currentCalendarTrades);
          if (window.completedTrades) updateTagsInArray(window.completedTrades);
          if (window.activeOrders) updateTagsInArray(window.activeOrders);
          
          // Re-render the tables
          if (typeof renderPointsPerTradeTable === 'function') renderPointsPerTradeTable();
          if (typeof renderOrdCompletedTable === 'function') renderOrdCompletedTable();
          if (typeof renderOrdOpenTable === 'function') renderOrdOpenTable();
        } catch (e) {
          console.error(e);
          alert('Failed to save tags');
        }
      }
      
      // Initialize Tag Store on load
      setTimeout(loadTagStore, 1000);
    
