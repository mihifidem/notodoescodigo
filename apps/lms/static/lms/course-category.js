document.addEventListener('DOMContentLoaded', function () {
  const dialog = document.querySelector('[data-category-dialog]');
  const select = document.querySelector('[name="categoria"]');
  if (!dialog || !select) return;
  const form = dialog.querySelector('[data-category-form]');
  const error = dialog.querySelector('[data-category-error]');
  const submit = form.querySelector('[type="submit"]');
  document.querySelector('[data-category-open]').addEventListener('click', function () {
    error.hidden = true;
    dialog.showModal();
  });
  dialog.querySelector('[data-category-cancel]').addEventListener('click', function () {
    dialog.close();
  });
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    submit.disabled = true;
    error.hidden = true;
    const controller = new AbortController();
    const timeout = setTimeout(function () { controller.abort(); }, 15000);
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: {'Accept': 'application/json'}, signal: controller.signal,
      });
      if (response.redirected) throw new Error('La sesión ha caducado. Vuelve a iniciar sesión.');
      if (response.status === 400) {
        const data = await response.json();
        throw new Error(Object.values(data.errors).flat().map(item => item.message).join(' '));
      }
      if (!response.ok) throw new Error(`No se pudo crear la categoría (HTTP ${response.status}).`);
      const data = await response.json();
      if (!Number.isInteger(data.id) || typeof data.nombre !== 'string') {
        throw new Error('La respuesta de creación de categoría no es válida.');
      }
      select.add(new Option(data.nombre, data.id, false, true));
      select.dispatchEvent(new Event('change', {bubbles: true}));
      form.reset();
      dialog.close();
    } catch (failure) {
      console.error('Course category creation failed.', failure);
      error.textContent = failure.name === 'AbortError'
        ? 'El servidor no respondió en 15 segundos. Comprueba si la categoría se creó antes de reintentar.'
        : failure instanceof TypeError
          ? 'No se pudo conectar con el servidor. Comprueba la conexión antes de reintentar.'
          : failure.message;
      error.hidden = false;
    } finally {
      clearTimeout(timeout);
      submit.disabled = false;
    }
  });
});
