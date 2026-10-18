(() => {
  const selection = document.getElementById('id_selection');
  const manual = document.querySelector('[data-manual-questions]');
  const random = document.querySelectorAll('[data-random-questions]');
  const bank = document.getElementById('id_bank');
  if (selection && manual) {
    const syncSelection = () => {
      const isManual = selection.value === 'manual';
      manual.hidden = !isManual;
      manual.open = isManual;
      manual.querySelectorAll('input[data-bank-id]').forEach(input => {
        const matchesBank = bank && input.dataset.bankId === bank.value;
        input.disabled = !isManual || !matchesBank;
        input.closest('label').parentElement.hidden = !matchesBank;
        if (!matchesBank) input.checked = false;
      });
      random.forEach(block => {
        const isRandom = selection.value === 'random';
        block.hidden = !isRandom;
        block.querySelectorAll('input, select').forEach(input => { input.disabled = !isRandom; });
      });
    };
    selection.addEventListener('change', syncSelection);
    if (bank) bank.addEventListener('change', syncSelection);
    syncSelection();
  }
  const penalize = document.getElementById('id_penalize_errors');
  const deduction = document.getElementById('id_error_deduction');
  if (penalize && deduction) {
    const syncPenalty = () => {
      deduction.required = penalize.checked;
      deduction.disabled = !penalize.checked;
    };
    penalize.addEventListener('change', syncPenalty);
    syncPenalty();
  }
  const errors = document.getElementById('quiz-form-errors');
  if (errors) {
    errors.focus();
    errors.scrollIntoView({block: 'start'});
  }
  const start = document.getElementById('id_available_from');
  const due = document.getElementById('id_due_at');
  const duration = document.getElementById('id_duration_minutes');
  if (!start || !due || !duration) return;

  const updateDuration = () => {
    const startTime = new Date(start.value).getTime();
    const dueTime = new Date(due.value).getTime();
    const automatic = Number.isFinite(startTime) && Number.isFinite(dueTime) && dueTime > startTime;
    duration.readOnly = automatic;
    if (automatic) duration.value = String(Math.ceil((dueTime - startTime) / 60000));
  };

  start.addEventListener('input', updateDuration);
  due.addEventListener('input', updateDuration);
  updateDuration();
})();
