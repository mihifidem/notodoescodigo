document.addEventListener('DOMContentLoaded', function () {
  const live = document.querySelector('[data-forum-live-url]');
  if (!live) return;
  const refreshButton = document.querySelector('[data-forum-refresh]');
  const errorMessage = document.createElement('p');
  errorMessage.hidden = true;
  errorMessage.setAttribute('role', 'alert');
  errorMessage.textContent = 'No se pudo actualizar el foro. Usa el botón de actualización para volver a intentarlo.';
  live.before(errorMessage);
  let busy = false;
  let revision = null;
  let stopped = false;
  let pending = null;

  async function fetchJSON(url) {
    const response = await fetch(url, {
      headers: {'X-Requested-With': 'XMLHttpRequest'}, cache: 'no-store',
    });
    if (!response.ok || response.redirected) throw new Error('Forum request failed: ' + response.status);
    return response.json();
  }

  function applyUpdate(update) {
    const expanded = [...live.querySelectorAll('details[open]')].map(node => node.closest('[id]')?.id);
    live.innerHTML = update.html;
    const thread = live.querySelector('[data-thread-closed]');
    const replyForm = document.querySelector('[data-query-reply-form]');
    if (thread && replyForm) {
      const closed = thread.dataset.threadClosed === 'true';
      replyForm.hidden = closed;
      replyForm.disabled = closed;
      const closedMessage = document.querySelector('[data-query-closed-message]');
      if (closedMessage) closedMessage.hidden = !closed;
    }
    expanded.forEach(id => {
      if (!id) return;
      live.querySelector('#' + CSS.escape(id) + ' details')?.setAttribute('open', '');
    });
    revision = update.revision;
    pending = null;
    if (refreshButton) refreshButton.hidden = true;
  }

  async function update() {
    if (busy || stopped || document.visibilityState !== 'visible') return;
    busy = true;
    try {
      if (live) {
        const update = await fetchJSON(live.dataset.forumLiveUrl);
        if (typeof update.html !== 'string' || typeof update.revision !== 'string') throw new Error('Invalid forum content response');
        if (revision !== update.revision) {
          const editing = [...live.querySelectorAll('textarea, input:not([type="hidden"])')].some(node => node.value !== node.defaultValue)
            || live.contains(document.activeElement);
          if (editing) {
            pending = update;
            if (refreshButton) refreshButton.hidden = false;
          } else {
            applyUpdate(update);
          }
        }
      }
      errorMessage.hidden = true;
    } catch (error) {
      stopped = true;
      errorMessage.hidden = false;
      if (refreshButton) refreshButton.hidden = false;
      console.error('LMS forum update failed:', error);
    } finally {
      busy = false;
    }
  }

  if (refreshButton) {
    refreshButton.addEventListener('click', function () {
      stopped = false;
      if (pending) {
        const dirty = [...live.querySelectorAll('textarea')].some(node => node.value !== node.defaultValue);
        if (dirty && !window.confirm('Hay una respuesta sin enviar. ¿Descartar el texto y actualizar?')) return;
        applyUpdate(pending);
      }
      update();
    });
  }

  document.addEventListener('lms:states-updated', update);
  document.addEventListener('lms:notifications-paused', () => { stopped = true; });
  document.addEventListener('lms:notifications-resumed', () => { stopped = false; });
  window.addEventListener('pagehide', function () {
    stopped = true;
    document.removeEventListener('lms:states-updated', update);
  }, {once: true});
});
