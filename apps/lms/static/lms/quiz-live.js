(() => {
  const region = document.querySelector('[data-quiz-live-region]');
  if (!region) return;
  const error = document.querySelector('[data-quiz-live-error]');
  let paused = false;
  document.addEventListener('lms:notifications-paused', () => { paused = true; });
  document.addEventListener('lms:notifications-resumed', () => { paused = false; });
  function markDeliveryDirty(event) {
    const form = event.target.closest('[data-activity-submission-form]');
    if (form) form.dataset.dirty = 'true';
  }
  document.addEventListener('input', markDeliveryDirty);
  document.addEventListener('change', markDeliveryDirty);
  let busy = false;
  document.addEventListener('input', event => {
    const form = event.target.closest('[data-quiz-feedback-form]');
    if (form) form.dataset.dirty = 'true';
  });
  function showPanel(id) {
    const panel = region.querySelector(id);
    if (!panel) return;
    panel.open = true;
    panel.scrollIntoView({block: 'start'});
  }
  document.addEventListener('click', event => {
    if (event.target.closest('[data-show-solutions]')) showPanel('#quiz-solutions');
    if (event.target.closest('[data-show-history]')) showPanel('#quiz-attempt-history');
  });
  window.addEventListener('hashchange', () => {
    if (location.hash === '#quiz-solutions' || location.hash === '#quiz-attempt-history') showPanel(location.hash);
  });
  if (location.hash === '#quiz-solutions' || location.hash === '#quiz-attempt-history') showPanel(location.hash);
  function signature(element) {
    const copy = element.cloneNode(true);
    copy.querySelectorAll('[name="csrfmiddlewaretoken"]').forEach(input => input.removeAttribute('value'));
    copy.querySelectorAll('details').forEach(details => details.removeAttribute('open'));
    return copy.innerHTML;
  }
  async function update() {
    if (busy || paused || document.hidden) return;
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(location.href, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {'Accept': 'text/html'},
      });
      if (!response.ok || response.redirected) throw new Error(`Quiz status request failed: ${response.status}`);
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const updated = page.querySelector('[data-quiz-live-region]');
      if (!updated) throw new Error('Quiz status region missing in response.');
      if (signature(region) !== signature(updated) && !region.contains(document.activeElement)
          && !region.querySelector('[data-quiz-feedback-form][data-dirty="true"]')) {
        const open = new Map(Array.from(region.querySelectorAll('details'), (details, index) => [
          details.dataset.quizInfo || `detail-${index}`, details.open,
        ]));
        region.replaceChildren(...updated.childNodes);
        region.querySelectorAll('details').forEach((details, index) => {
          const key = details.dataset.quizInfo || `detail-${index}`;
          if (open.has(key)) details.open = open.get(key);
        });
      }
      const nextForm = page.querySelector('[data-activity-submission-form]');
      const submissionForm = document.querySelector('[data-activity-submission-form]');
      const delivery = document.querySelector('[data-activity-delivery-region]');
      const nextDelivery = page.querySelector('[data-activity-delivery-region]');
      if (!nextForm && submissionForm) {
        submissionForm.querySelector('[type="submit"]').disabled = true;
        const message = delivery.querySelector('[data-activity-closed]');
        message.textContent = delivery.dataset.closedMessage;
        message.hidden = false;
      }
      if (delivery && nextDelivery && !delivery.contains(document.activeElement)
          && !delivery.querySelector('[data-dirty="true"]') && signature(delivery) !== signature(nextDelivery)) {
        delivery.replaceChildren(...nextDelivery.childNodes);
      }
      error.hidden = true;
    } catch (failure) {
      paused = true;
      error.hidden = false;
      console.error('LMS states could not be updated.', failure);
    } finally {
      clearTimeout(timeout);
      busy = false;
    }
  }
  document.addEventListener('lms:states-updated', update);
})();
