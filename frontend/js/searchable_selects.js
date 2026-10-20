/* Keep every filter's option list visible while also adding type-to-search. */
(function () {
  let nextId = 0;
  function enhance(select) {
    if (select.dataset.searchable === 'true') return;
    select.dataset.searchable = 'true';
    select.classList.add('searchable-options');
    const list = document.createElement('datalist');
    list.id = `filter-options-${++nextId}`;
    const input = document.createElement('input');
    input.type = 'search';
    input.className = 'filter-search';
    input.setAttribute('list', list.id);
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('placeholder', 'Type to search…');
    input.setAttribute('aria-label', `${select.id || 'Filter'} search`);
    select.before(input);
    select.after(list);
    const options = () => [...select.options];
    const selectedLabel = () => select.selectedOptions[0]?.textContent?.trim() || '';
    function refresh() {
      list.replaceChildren(...options().map(option => {
        const item = document.createElement('option');
        item.value = option.textContent.trim();
        return item;
      }));
      input.disabled = select.disabled;
      if (document.activeElement !== input) input.value = selectedLabel();
    }
    function applyTypedValue() {
      const query = input.value.trim().toLocaleLowerCase();
      let match = options().find(option =>
        option.textContent.trim().toLocaleLowerCase() === query || option.value.toLocaleLowerCase() === query);
      if (!match && query) {
        const partial = options().filter(option => option.textContent.trim().toLocaleLowerCase().includes(query));
        if (partial.length === 1) match = partial[0];
      }
      if (match && select.value !== match.value) {
        select.value = match.value;
        select.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }
    input.addEventListener('input', applyTypedValue);
    input.addEventListener('change', applyTypedValue);
    input.addEventListener('blur', () => { applyTypedValue(); input.value = selectedLabel(); });
    select.addEventListener('change', () => { input.value = selectedLabel(); });
    new MutationObserver(refresh).observe(select, {childList: true, subtree: true});
    refresh();
  }
  const scan = () => document.querySelectorAll('select').forEach(enhance);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
