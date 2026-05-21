/**
 * Polls for automatic attendance popup at the start of each period (grace window).
 */
(function () {
  const host = document.getElementById('attendancePopupHost');
  if (!host) return;

  let active = false;

  function closePopup() {
    active = false;
    host.innerHTML = '';
  }

  function openPopup(data) {
    active = true;
    const inner = document.createElement('div');
    inner.className = 'attendance-popup';
    inner.innerHTML =
      '<div class="attendance-popup-inner">' +
      '<h3>Attendance — Period ' +
      data.period +
      '</h3>' +
      '<p><strong>' +
      (data.subject || '') +
      '</strong></p>' +
      '<p class="muted">Faculty: ' +
      (data.faculty || 'TBD') +
      '</p>' +
      '<p>Mark within <strong>' +
      data.grace_minutes +
      '</strong> minutes using the camera page.</p>' +
      '<p class="hint" id="popupCountdown"></p>' +
      '<div class="submit-row spaced">' +
      '<a class="link-button" href="/student/attendance">Open camera</a>' +
      '<button type="button" class="link-button" id="popupDismiss">Dismiss</button>' +
      '</div></div>';
    host.appendChild(inner);
    const cd = inner.querySelector('#popupCountdown');
    const tick = function () {
      if (!active) return;
      cd.textContent =
        'Time left in grace window: ~' + Math.max(0, Math.round(data.seconds_left)) + 's';
      data.seconds_left -= 1;
      if (data.seconds_left < -5) {
        closePopup();
        return;
      }
      setTimeout(tick, 1000);
    };
    tick();
    inner.querySelector('#popupDismiss').addEventListener('click', closePopup);
    inner.addEventListener('click', function (e) {
      if (e.target === inner) closePopup();
    });
  }

  async function poll() {
    try {
      const res = await fetch('/api/student/attendance_prompt');
      const data = await res.json();
      if (data.show && !active) {
        openPopup(data);
      }
      if (!data.show && active) {
        closePopup();
      }
    } catch (e) {
      /* ignore */
    }
    setTimeout(poll, 15000);
  }

  poll();
})();
