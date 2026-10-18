document.addEventListener('DOMContentLoaded', function () {
  const panel = document.querySelector('[data-module-create-panel]');
  const form = document.getElementById('module-create-form');
  if (!panel || !form) return;
  const title = form.elements.namedItem('titulo');
  const errorMessage = panel.querySelector('[data-module-create-error]');
  const submit = panel.querySelector('[type="submit"]');
  const select = document.getElementById('id_module');
  let previousModule = select ? select.value : '';
  if (select) {
    select.add(new Option(form.dataset.createOptionLabel, '__create_module__'));
    select.addEventListener('change', function () {
      if (select.value !== '__create_module__') {
        previousModule = select.value;
        return;
      }
      select.value = previousModule;
      panel.open = true;
      title.focus();
    });
  }
  let submitting = false;
  panel.addEventListener('toggle', function () {
    if (!panel.open) return;
    panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    title.focus({preventScroll: true});
  });
  panel.querySelector('[data-module-create-cancel]').addEventListener('click', function () {
    panel.open = false;
    panel.querySelector('summary').focus();
  });
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (submitting) return;
    submitting = true;
    submit.disabled = true;
    errorMessage.hidden = true;
    try {
      const data = new FormData(form);
      const response = await fetch(form.action, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': data.get('csrfmiddlewaretoken'),
        },
        body: data,
      });
      const result = await response.json();
      if (!response.ok || response.redirected || !result.ok) {
        throw new Error(result.error || form.dataset.createFailureMessage);
      }
      if (select) {
        select.add(new Option(result.title, String(result.id), true, true));
        select.dispatchEvent(new Event('change', {bubbles: true}));
        form.reset();
        panel.open = false;
        select.focus();
      } else {
        window.location.reload();
      }
    } catch (error) {
      console.error('LMS module creation failed:', error);
      errorMessage.textContent = error.message || form.dataset.createFailureMessage;
      errorMessage.hidden = false;
    } finally {
      submitting = false;
      submit.disabled = false;
    }
  });
});
