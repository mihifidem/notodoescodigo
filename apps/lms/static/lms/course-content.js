document.addEventListener('DOMContentLoaded', function () {
  const list = document.querySelector('.learning-module-list');
  if (!list) return;
  const toggles = list.querySelectorAll('[data-module-toggle]');
  function expand(toggle, open) {
    toggle.setAttribute('aria-expanded', String(open));
    document.getElementById(toggle.getAttribute('aria-controls')).hidden = !open;
  }
  toggles.forEach(function (toggle) {
    expand(toggle, toggle.getAttribute('aria-expanded') === 'true');
    toggle.addEventListener('click', function () {
      expand(toggle, toggle.getAttribute('aria-expanded') !== 'true');
    });
  });
  [['[data-modules-collapse]', false], ['[data-modules-expand]', true]].forEach(function ([selector, open]) {
    const button = document.querySelector(selector);
    if (!button) return;
    button.hidden = false;
    button.addEventListener('click', function () {
      toggles.forEach(function (toggle) { expand(toggle, open); });
      list.querySelectorAll('details.formative-unit').forEach(function (unit) { unit.open = open; });
    });
  });

  const drawer = document.querySelector('[data-content-drawer]');
  if (!drawer || typeof drawer.showModal !== 'function') return;
  const body = drawer.querySelector('[data-drawer-body]');
  const error = drawer.querySelector('[data-drawer-error]');
  let opener;
  let pending = false;
  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }
  function responseError(response) {
    if (response.redirected) return drawer.dataset.sessionError;
    if (response.status === 403) return drawer.dataset.permissionError;
    return drawer.dataset.saveError + ' (HTTP ' + response.status + ').';
  }
  drawer.addEventListener('close', function () { if (opener) opener.focus(); });
  document.addEventListener('click', async function (event) {
    const close = event.target.closest('[data-drawer-close]');
    if (close && drawer.contains(close)) {
      if (!pending) drawer.close();
      return;
    }
    const link = event.target.closest('[data-content-panel]');
    if (!link || pending || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    opener = link;
    pending = true;
    error.hidden = true;
    body.replaceChildren();
    drawer.querySelector('h2').textContent = link.dataset.panelTitle;
    drawer.showModal();
    body.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(link.href, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok || response.redirected) throw new Error(drawer.dataset.loadError);
      body.innerHTML = await response.text();
      const first = body.querySelector('select, input:not([type="hidden"])');
      if (first) first.focus();
    } catch (failure) {
      console.error('LMS content panel loading failed:', failure);
      showError(drawer.dataset.loadError);
    } finally {
      pending = false;
      body.removeAttribute('aria-busy');
    }
  });
  drawer.addEventListener('cancel', function (event) { if (pending) event.preventDefault(); });
  drawer.addEventListener('submit', async function (event) {
    const form = event.target.closest('[data-drawer-form]');
    if (!form) return;
    event.preventDefault();
    if (pending) return;
    pending = true;
    error.hidden = true;
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const response = await fetch(form.getAttribute('action'), {
        method: 'POST', body: new FormData(form),
        headers: {'X-Requested-With': 'XMLHttpRequest'},
      });
      if (response.redirected) {
        showError(responseError(response));
        return;
      }
      if (response.status === 400 && (response.headers.get('Content-Type') || '').includes('text/html')) {
        body.innerHTML = await response.text();
        const errors = Array.from(body.querySelectorAll('.errorlist li')).map(function (item) {
          return item.textContent.trim();
        });
        showError(errors.length ? Array.from(new Set(errors)).join(' ') : responseError(response));
        const invalid = body.querySelector('[aria-invalid="true"], .form-errors');
        if (invalid) invalid.scrollIntoView({block: 'nearest'});
        return;
      }
      if (!response.ok) {
        showError(responseError(response));
        return;
      }
      const result = await response.json();
      if (!result.ok) throw new Error(drawer.dataset.saveError);
      window.location.reload();
    } catch (failure) {
      console.error('LMS module content saving failed:', failure);
      showError(failure instanceof TypeError ? drawer.dataset.networkError : drawer.dataset.saveError);
    } finally {
      pending = false;
      submit.disabled = false;
    }
  });
});
