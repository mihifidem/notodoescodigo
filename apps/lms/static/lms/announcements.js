document.addEventListener('DOMContentLoaded', function () {
  const alert = document.querySelector('[data-announcement-alert]');
  const source = document.querySelector('[data-announcement-count-url]');
  if (!source) return;
  function applyCount(result) {
    if (!Number.isInteger(result.count) || result.count < 0 || typeof result.label !== 'string') {
      throw new Error('Invalid announcement count response');
    }
    if (alert) {
      alert.hidden = result.count === 0;
      const label = alert.querySelector('[data-announcement-label]');
      if (label.textContent !== result.label) label.textContent = result.label;
    }
    const navLink = document.querySelector('[data-announcement-nav-link]');
    if (navLink) {
      let badge = navLink.querySelector('.nav-count');
      if (result.count === 0) {
        if (badge) badge.hidden = true;
      } else {
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'nav-count';
          navLink.appendChild(badge);
        }
        badge.textContent = result.count;
        badge.hidden = false;
        badge.setAttribute('aria-label', result.label);
      }
    }
  }
  document.addEventListener('lms:notification-counts', function (event) {
    const count = event.detail.course_counts.announcements;
    const teacher = source.dataset.announcementCountKind === 'students';
    if (Number.isInteger(count)) applyCount({
      count: count, label: teacher
        ? `${count} ${count === 1 ? 'alumno con anuncios pendientes' : 'alumnos con anuncios pendientes'}`
        : count === 1 ? '1 anuncio pendiente' : `${count} anuncios pendientes`,
    });
    const reading = event.detail.announcement_reading;
    if (!teacher || !reading) return;
    document.querySelectorAll('[data-announcement-reading-count]').forEach(badge => {
      const state = reading.announcements[badge.dataset.announcementReadingCount];
      badge.hidden = !state || !state.pending;
      if (state) badge.textContent = `${state.pending} ${state.pending === 1 ? 'alumno pendiente de leer' : 'alumnos pendientes de leer'}`;
    });
    document.querySelectorAll('[data-announcement-reading-summary]').forEach(summary => {
      const state = reading.announcements[summary.dataset.announcementReadingSummary];
      if (state) summary.textContent = `Lectura: ${state.read} de ${state.total} alumnos.`;
    });
  });
  document.addEventListener('toggle', async function (event) {
      const card = event.target;
      if (!card.matches('[data-announcement-read-url]') || !card.open || card.dataset.reading) return;
      card.dataset.reading = 'true';
      const error = card.querySelector('[data-announcement-read-error]');
      error.hidden = true;
      const controller = new AbortController();
      const timeout = setTimeout(function () { controller.abort(); }, 15000);
      try {
        const response = await fetch(card.dataset.announcementReadUrl, {
          method: 'POST', credentials: 'same-origin',
          signal: controller.signal,
          headers: {
            'X-CSRFToken': card.querySelector('[data-announcement-read-token] [name="csrfmiddlewaretoken"]').value,
            'Accept': 'application/json',
          },
        });
        if (!response.ok || response.redirected) throw new Error(`Announcement read request failed: ${response.status}`);
        applyCount(await response.json());
        card.querySelector('[data-announcement-pending]').remove();
        card.classList.remove('announcement-pending');
        delete card.dataset.announcementReadUrl;
        document.dispatchEvent(new Event('lms:notifications-refresh'));
      } catch (failure) {
        console.error('LMS announcement read failed:', failure);
        error.hidden = false;
      } finally {
        clearTimeout(timeout);
        delete card.dataset.reading;
      }
  }, true);
  function openLinkedAnnouncement() {
    const card = document.getElementById(location.hash.slice(1));
    if (card && card.matches('details.announcement-item')) card.open = true;
  }
  openLinkedAnnouncement();
  window.addEventListener('hashchange', openLinkedAnnouncement);
});
