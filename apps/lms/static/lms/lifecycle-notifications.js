(() => {
  const panel = document.querySelector('[data-lifecycle-notifications]');
  if (!panel) return;
  const list = panel.querySelector('[data-notification-list]');
  const error = panel.querySelector('[data-notification-error]');
  const errorText = panel.querySelector('[data-notification-error-text]');
  const token = panel.querySelector('[name="csrfmiddlewaretoken"]').value;
  const control = document.querySelector('[data-quiz-lifecycle-control]');
  const courseControl = document.querySelector('[data-course-notification-control]');
  const shown = new Set();
  let cursor = null;
  let busy = false;
  let paused = false;
  let stopped = false;
  let socket;
  let refreshPending = false;
  let pushRevision = 0;
  let errorHideTimer;

  const navbar = document.querySelector('.course-nav-panel');
  const header = document.querySelector('#page-content > .navbar');
  let observer;
  if (navbar && header) {
    const positionNavbar = () => navbar.style.setProperty('--course-navbar-top', `${header.getBoundingClientRect().height}px`);
    positionNavbar();
    observer = new ResizeObserver(positionNavbar);
    observer.observe(header);
  }

  function hideError() {
    clearTimeout(errorHideTimer);
    errorHideTimer = undefined;
    error.hidden = true;
  }

  function pause(failure) {
    paused = true;
    clearTimeout(errorHideTimer);
    errorText.textContent = `Consultas de avisos detenidas: ${failure.message}. No se reintentará automáticamente. La recepción en vivo es independiente.`;
    error.hidden = false;
    errorHideTimer = setTimeout(hideError, 5000);
    console.error('Notification update stopped.', failure);
    document.dispatchEvent(new Event('lms:notifications-paused'));
  }

  async function fetchJSON(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', cache: 'no-store', ...options,
        signal: controller.signal,
        headers: {'Accept': 'application/json', ...options.headers},
      });
      if (!response.ok || response.redirected) {
        throw new Error(response.redirected ? 'La sesión ha caducado' : `Error HTTP ${response.status}`);
      }
      return await response.json();
    } catch (failure) {
      if (failure.name === 'AbortError') throw new Error('El servidor no respondió en 15 segundos');
      if (failure instanceof TypeError) throw new Error(`No se pudo conectar con el servidor (${failure.message})`);
      throw failure;
    } finally {
      clearTimeout(timeout);
    }
  }

  function applyCounts(data) {
    if (!Number.isInteger(data.count) || data.count < 0 || !data.course_counts) {
      throw new Error('Respuesta de contadores no válida');
    }
    document.querySelectorAll('[data-live-notification-count]').forEach(badge => {
      badge.textContent = data.count;
      badge.hidden = !data.count;
      badge.setAttribute('aria-label', `Avisos pendientes: ${data.count}`);
    });
    if (Array.isArray(data.unread_ids)) {
      const unread = new Set(data.unread_ids.map(String));
      list.querySelectorAll('[data-notification-id]').forEach(item => {
        if (!unread.has(item.dataset.notificationId)) item.remove();
      });
    }
    document.querySelectorAll('[data-notification-area-count]').forEach(badge => {
      if (!control) return;
      const count = data.course_counts[badge.dataset.notificationAreaCount];
      if (!Number.isInteger(count) || count < 0) throw new Error('Contador de curso no válido');
      badge.textContent = count;
      badge.hidden = !count;
      badge.setAttribute('aria-label', `Avisos pendientes: ${count}`);
    });
    document.dispatchEvent(new CustomEvent('lms:notification-counts', {detail: data}));
  }

  function localURL(value) {
    const url = new URL(value, location.origin);
    if (url.origin !== location.origin) throw new Error('Destino de notificación no válido');
    return url;
  }

  function showNotification(notification) {
    if (shown.has(notification.id)) return;
    const destination = localURL(notification.url);
    const readURL = localURL(notification.read_url);
    shown.add(notification.id);
    const item = document.createElement('li');
    item.dataset.notificationId = notification.id;
    item.className = 'bg-body border rounded shadow p-3 mb-2';
    const link = document.createElement('a');
    link.href = destination.href;
    const title = document.createElement('strong');
    title.textContent = notification.title;
    const message = document.createElement('p');
    message.className = 'small mb-2';
    message.textContent = notification.message;
    link.append(title);
    const read = document.createElement('button');
    read.type = 'button';
    read.className = 'btn btn-sm btn-outline-secondary';
    read.textContent = 'Marcar como leído';
    read.addEventListener('click', async () => {
      read.disabled = true;
      try {
        await fetchJSON(readURL.href, {
          method: 'POST', body: new URLSearchParams({read: '1'}),
          headers: {'X-CSRFToken': token, 'X-Requested-With': 'XMLHttpRequest'},
        });
        item.remove();
        const centerItem = document.querySelector(`[data-notification-center-item="${notification.id}"]`);
        if (centerItem) location.reload();
        else update();
      } catch (failure) {
        pause(failure);
      } finally {
        read.disabled = false;
      }
    });
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'btn-close float-end';
    dismiss.setAttribute('aria-label', 'Ocultar aviso sin marcarlo como leído');
    dismiss.addEventListener('click', () => item.remove());
    item.append(dismiss, link, message, read);
    list.prepend(item);
    while (list.children.length > 5) list.lastElementChild.remove();
  }

  async function update() {
    if (paused || stopped || document.hidden) return;
    if (busy) {
      refreshPending = true;
      return;
    }
    busy = true;
    refreshPending = false;
    const revision = pushRevision;
    const url = new URL(panel.dataset.url, location.origin);
    if (control && courseControl) url.searchParams.set('course_id', courseControl.dataset.courseId);
    if (courseControl && courseControl.dataset.studentPreview === 'true') url.searchParams.set('preview', '1');
    try {
      if (cursor === null) {
        const initial = await fetchJSON(url.href);
        if (revision === pushRevision) applyCounts(initial);
        cursor = Math.max(cursor || 0, initial.cursor);
      }
      let processingFailure;
      if (control) {
        try {
          await fetchJSON(control.action, {method: 'POST', body: new FormData(control)});
        } catch (failure) {
          processingFailure = failure;
        }
      }
      url.searchParams.set('after', cursor);
      const data = await fetchJSON(url.href);
      if (!Number.isInteger(data.cursor) || !Array.isArray(data.notifications)) {
        throw new Error('Respuesta de notificaciones no válida');
      }
      if (revision === pushRevision) applyCounts(data);
      data.notifications.forEach(showNotification);
      cursor = Math.max(cursor || 0, data.cursor);
      hideError();
      document.dispatchEvent(new Event('lms:states-updated'));
      if (processingFailure) pause(new Error(`No se procesaron los estados del curso (${processingFailure.message})`));
    } catch (failure) {
      pause(failure);
    } finally {
      busy = false;
      if (refreshPending && !paused) update();
    }
  }

  async function readItem(item) {
    if (!courseControl || item.dataset.notificationReading || item.dataset.notificationRead) return;
    item.dataset.notificationReading = 'true';
    const data = new FormData(courseControl);
    if (item.dataset.notificationEvent) data.set('event_id', item.dataset.notificationEvent);
    else data.set('target', item.dataset.notificationItemTarget);
    if (item.dataset.notificationArea) data.set('area', item.dataset.notificationArea);
    try {
      applyCounts(await fetchJSON(courseControl.action, {method: 'POST', body: data}));
      item.dataset.notificationRead = 'true';
      update();
    } catch (failure) {
      pause(new Error(`No se guardó la lectura (${failure.message})`));
    } finally {
      delete item.dataset.notificationReading;
    }
  }
  document.addEventListener('toggle', event => {
    const item = event.target;
    if (item.matches('details[data-notification-item-target], details[data-notification-event]') && item.open) {
      readItem(item);
    }
  }, true);
  const fragment = document.getElementById(location.hash.slice(1));
  if (fragment && fragment.matches('details[data-notification-item-target]')) fragment.open = true;
  document.querySelectorAll('[data-notification-item-target]:not(details), details[data-notification-item-target][open]').forEach(readItem);
  document.addEventListener('lms:notifications-refresh', update);
  panel.querySelector('[data-notification-resume]').addEventListener('click', () => {
    paused = false;
    hideError();
    document.dispatchEvent(new Event('lms:notifications-resumed'));
    update();
  });
  update();
  const timer = setInterval(update, 5000);
  document.addEventListener('visibilitychange', update);
  if (control) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const preview = courseControl && courseControl.dataset.studentPreview === 'true' ? '?preview=1' : '';
    socket = new WebSocket(`${protocol}//${location.host}/ws/course/${control.dataset.courseId}/${preview}`);
    socket.addEventListener('message', event => {
      try {
        const payload = JSON.parse(event.data);
        const data = payload.notification_state;
        if (!data) {
          update();
          return;
        }
        if (!Number.isInteger(data.cursor) || !Array.isArray(data.notifications)) {
          throw new Error('Respuesta de notificaciones en vivo no válida');
        }
        pushRevision += 1;
        applyCounts(data);
        data.notifications.forEach(showNotification);
        cursor = Math.max(cursor || 0, data.cursor);
        document.dispatchEvent(new Event('lms:states-updated'));
      } catch (failure) {
        pause(failure);
      }
    });
    socket.addEventListener('error', () => console.warn('WebSocket unavailable; scheduled notification updates remain active.'));
  }
  window.addEventListener('pagehide', () => {
    stopped = true;
    clearInterval(timer);
    hideError();
    if (socket) socket.close();
    if (observer) observer.disconnect();
  }, {once: true});
})();
