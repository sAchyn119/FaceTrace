// ===== FACETRACE MULTI-CAMERA JS =====

// Update clock in camera overlays
function updateClocks() {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-IN', { hour12: false });
  document.querySelectorAll('.cam-time').forEach(el => {
    el.textContent = timeStr;
  });
}
setInterval(updateClocks, 1000);
updateClocks();

// Stop all cameras
function stopAllCameras() {
  fetch('/stop-camera/')
    .then(() => { window.location.href = '/'; });
}

// Open person detail modal
function openPersonModal(name, rollNo, branch, attendanceCount, lastSeen, camName, thumbUrl) {
  document.getElementById('modal-name').textContent   = name;
  document.getElementById('modal-roll').textContent   = rollNo || '—';
  document.getElementById('modal-branch').textContent = branch || '—';
  document.getElementById('modal-att').textContent    = attendanceCount || '0';
  document.getElementById('modal-last').textContent   = lastSeen || '—';
  document.getElementById('modal-cam').textContent    = camName || '—';

  const img = document.getElementById('modal-img');
  if (thumbUrl) {
    img.src   = thumbUrl;
    img.style.display = 'block';
    document.getElementById('modal-placeholder').style.display = 'none';
  } else {
    img.style.display = 'none';
    document.getElementById('modal-placeholder').style.display = 'flex';
    document.getElementById('modal-initials').textContent = name.charAt(0).toUpperCase();
  }

  document.getElementById('person-modal').classList.add('show');
}

function closeModal() {
  document.getElementById('person-modal').classList.remove('show');
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
  const overlay = document.getElementById('person-modal');
  if (e.target === overlay) closeModal();
});

// Refresh live attendance count every 3 seconds
function refreshAttendance() {
  fetch('/api/status/')
    .then(r => r.json())
    .then(data => {
      const el = document.getElementById('live-count');
      if (el) el.textContent = data.count;

      const list = document.getElementById('det-list');
      if (list && data.attendance) {
        list.innerHTML = data.attendance.map(r => `
          <div class="detection-card">
            <div class="det-name">${r.Name}</div>
            <div class="det-meta">${r.Time}</div>
          </div>
        `).join('') || '<div style="color:var(--text-hint);font-size:13px;text-align:center;padding:1rem;">No detections yet</div>';
      }
    })
    .catch(() => {});
}

setInterval(refreshAttendance, 3000);
refreshAttendance();