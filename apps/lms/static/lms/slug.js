document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('form[data-auto-slug]').forEach(function (form) {
    const titleInput = form.querySelector(`[name="${form.dataset.autoSlugSource || 'title'}"]`);
    const slugInput = form.querySelector('[name="slug"]');
    if (!titleInput || !slugInput) return;
    let automaticSlug = !slugInput.value;
    function updateSlug() {
      if (!automaticSlug) return;
      const slug = titleInput.value
        .normalize('NFKD')
        .replace(/[^\x00-\x7F]/g, '')
        .toLowerCase()
        .replace(/[^\w\s-]/g, '')
        .trim()
        .replace(/[-\s]+/g, '-')
        .replace(/^[-_]+|[-_]+$/g, '')
        .slice(0, slugInput.maxLength > 0 ? slugInput.maxLength : 200);
      if (slugInput.value !== slug) {
        slugInput.value = slug;
        slugInput.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }
    titleInput.addEventListener('input', updateSlug);
    slugInput.addEventListener('input', function () {
      automaticSlug = !slugInput.value;
      if (automaticSlug) updateSlug();
    });
    updateSlug();
  });
});
