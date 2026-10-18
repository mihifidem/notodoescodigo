document.addEventListener('DOMContentLoaded', function () {
  const selectAll = document.querySelector('[data-question-select-all]');
  if (!selectAll) return;
  const questions = [...document.querySelectorAll('[data-question-select]')];
  const publish = document.querySelector('[data-publish-questions]');

  function syncSelection() {
    const selected = questions.filter(input => input.checked).length;
    selectAll.checked = questions.length > 0 && selected === questions.length;
    selectAll.indeterminate = selected > 0 && selected < questions.length;
    selectAll.disabled = questions.length === 0;
    publish.disabled = selected === 0;
  }

  selectAll.addEventListener('change', function () {
    questions.forEach(input => { input.checked = selectAll.checked; });
    syncSelection();
  });
  questions.forEach(input => input.addEventListener('change', syncSelection));
  syncSelection();
});
