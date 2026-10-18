document.addEventListener('prose-editor:ready', function (event) {
  const {editor, textarea} = event.detail;
  const shell = textarea.closest('[data-rich-editor-shell]');
  if (!shell) return;
  shell.classList.add('is-editor-ready');
  shell.querySelector('[data-editor-failure]')?.remove();
  editor.on('focus', function () { shell.classList.add('is-focused'); });
  editor.on('blur', function () {
    shell.classList.remove('is-focused');
    textarea.dispatchEvent(new Event('focusout', {bubbles: true}));
  });
});

window.addEventListener('load', function () {
  document.querySelectorAll('[data-rich-editor-shell]').forEach(function (shell) {
    if (shell.classList.contains('is-editor-ready')) return;
    const message = document.createElement('div');
    message.className = 'editor-error';
    message.dataset.editorFailure = '';
    message.setAttribute('role', 'alert');
    message.textContent = shell.dataset.editorError;
    shell.appendChild(message);
  });
});
