document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('form[data-lms-validation-url]').forEach(function (form) {
    const revisions = new WeakMap();
    let formRevision = 0;
    function markChanged(event) {
      formRevision += 1;
      revisions.set(event.target, (revisions.get(event.target) || 0) + 1);
    }
    document.addEventListener('input', function (event) {
      if (event.target.form === form) markChanged(event);
    });
    document.addEventListener('change', function (event) {
      if (event.target.form === form) markChanged(event);
    });
    document.addEventListener('focusout', async function (event) {
      const editorShell = event.target.closest('[data-rich-editor-shell]');
      if (editorShell && event.target.tagName !== 'TEXTAREA') return;
      const field = editorShell ? editorShell.querySelector('textarea') : event.target;
      if (!field) return;
      if (field.form !== form) return;
      if (!field.matches('input, select, textarea') || !field.name || field.type === 'hidden') return;
      const container = field.closest('.field, .field-block');
      if (!container) return;
      const revision = (revisions.get(field) || 0) + 1;
      const validatedFormRevision = formRevision;
      const validatedValue = field.value;
      revisions.set(field, revision);
      const data = new FormData(form);
      data.set('_field', field.name);
      data.set('_course_id', form.dataset.courseId || '');
      data.set('_object_id', form.dataset.objectId || '');
      try {
        const response = await fetch(form.dataset.lmsValidationUrl, {
          method: 'POST',
          headers: {
            'X-CSRFToken': data.get('csrfmiddlewaretoken'),
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: data,
        });
        if (!response.ok || response.redirected) throw new Error('Field validation request failed');
        const result = await response.json();
        if (revisions.get(field) !== revision || formRevision !== validatedFormRevision || field.value !== validatedValue) return;
        const removedIds = [];
        container.querySelectorAll('.form-error, .field-errors, .errorlist').forEach(function (error) {
          if (error.id) removedIds.push(error.id);
          error.remove();
        });
        const descriptionIds = (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(function (id) {
          return id && !removedIds.includes(id);
        });
        field.setAttribute('aria-describedby', descriptionIds.join(' '));
        const errors = result.errors.concat(result.non_field_errors);
        field.setAttribute('aria-invalid', errors.length ? 'true' : 'false');
        field.classList.toggle('is-invalid', errors.length > 0);
        if (errors.length) {
          const message = document.createElement('div');
          message.className = 'form-error';
          message.id = field.id + '_errors';
          message.setAttribute('role', 'alert');
          message.textContent = errors.join(' ');
          container.appendChild(message);
          const describedBy = new Set((field.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
          describedBy.add(message.id);
          field.setAttribute('aria-describedby', Array.from(describedBy).join(' '));
        }
        form.querySelectorAll('[data-validation-failure]').forEach(function (message) { message.remove(); });
      } catch (error) {
        console.error('LMS field validation failed:', error);
        if (!form.querySelector('[data-validation-failure]')) {
          const message = document.createElement('div');
          message.className = 'form-error';
          message.dataset.validationFailure = '';
          message.setAttribute('role', 'alert');
          message.textContent = form.dataset.validationFailureMessage;
          form.appendChild(message);
        }
      }
    });
  });
});
