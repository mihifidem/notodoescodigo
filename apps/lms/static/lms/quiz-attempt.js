(() => {
  const form = document.querySelector('[data-quiz-attempt]');
  if (!form) return;
  const status = form.querySelector('[data-save-status]');
  let queue = Promise.resolve();
  let revision = 0;
  let savedRevision = 0;
  let delivering = false;
  let readyToSubmit = false;
  let finished = false;
  function save() {
    const data = new FormData(form);
    data.set('action', 'save');
    const version = revision;
    queue = queue.then(async () => {
      status.textContent = status.dataset.saving;
      try {
        const response = await fetch(form.action || location.href, {
          method: 'POST', body: data, credentials: 'same-origin', headers: {'Accept': 'application/json'}
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        savedRevision = version;
        status.textContent = status.dataset.saved;
        if (result.finished) {
          finished = true;
          delivering = true;
          location.assign(result.url);
        }
      } catch (error) {
        status.textContent = status.dataset.error;
        console.error('Quiz answers could not be saved.', error);
      }
    });
  }
  form.addEventListener('change', () => {
    revision++;
    if (!delivering) save();
  });
  form.addEventListener('submit', event => {
    if (readyToSubmit) return;
    event.preventDefault();
    if (delivering) return;
    delivering = true;
    const submitter = event.submitter;
    queue.then(() => {
      if (finished) return;
      readyToSubmit = true;
      form.requestSubmit(submitter || form.querySelector('[data-deliver]'));
    });
  });
  window.addEventListener('beforeunload', event => {
    if (!delivering && revision !== savedRevision) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  if (form.dataset.seconds !== undefined) {
    const end = Date.now() + Number(form.dataset.seconds) * 1000;
    const timer = document.querySelector('[data-attempt-timer]');
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((end - Date.now()) / 1000));
      timer.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
      if (!seconds && !delivering) {
        status.textContent = status.dataset.expired;
        form.requestSubmit(form.querySelector('[data-deliver]'));
      }
    };
    tick();
    setInterval(tick, 1000);
  }
})();
