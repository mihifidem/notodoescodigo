document.addEventListener('DOMContentLoaded', function () {
  const selectAll = document.querySelector('[data-people-select-all]');
  if (!selectAll) return;
  const recipients = [...document.querySelectorAll('[data-people-recipient]')];

  function syncSelection() {
    const selected = recipients.filter(input => input.checked).length;
    selectAll.checked = recipients.length > 0 && selected === recipients.length;
    selectAll.indeterminate = selected > 0 && selected < recipients.length;
    selectAll.disabled = recipients.length === 0;
  }

  selectAll.addEventListener('change', function () {
    recipients.forEach(input => { input.checked = selectAll.checked; });
    syncSelection();
  });
  recipients.forEach(input => input.addEventListener('change', syncSelection));
  syncSelection();
});
