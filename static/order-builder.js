function initializeOrderBuilder(root = document) {
  root.querySelectorAll('[data-order-builder]').forEach(form => {
    if (form.dataset.builderInitialized) return;
    form.dataset.builderInitialized = 'true';
    let timer;
    let pending;
    const summary = name => form.querySelector(`[data-summary="${name}"]`);
    const checked = name => Array.from(form.querySelectorAll(`input[name="${name}"]:checked`)).map(input => input.value);
    const control = name => form.elements.namedItem(name);
    const toggle = (element, visible) => {
      element.hidden = !visible;
      element.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = !visible; });
    };
    const update = () => {
      clearTimeout(timer);
      if (pending) pending.abort();
      const selected = form.querySelector('[name=service]:checked');
      const kind = selected?.dataset.kind;
      const surgicalGuide = kind === 'surgical_guide';
      const tiBase = kind === 'implant' && control('implant_type').value === 'ti_base';
      form.querySelector('[data-no-service]').hidden = Boolean(kind);
      toggle(form.querySelector('[data-service-fields]'), Boolean(kind));
      toggle(form.querySelector('[data-service-files]'), Boolean(kind));
      form.querySelectorAll('[data-kinds]').forEach(group => toggle(group, Boolean(kind && group.dataset.kinds.split(' ').includes(kind))));
      control('material').required = Boolean(kind && !surgicalGuide);
      control('ct_image').required = surgicalGuide;
      form.querySelector('label[for=id_working]').firstChild.textContent = surgicalGuide ? 'Снимок STL ' : 'Рабочая челюсть ';
      form.querySelectorAll('[data-ti-base]').forEach(group => toggle(group, tiBase));
      form.querySelectorAll('[data-implant-parameter]').forEach(group => {
        const unknown = group.querySelector('input[type=checkbox]');
        group.querySelector('input[type=text]').disabled = !tiBase || unknown.checked;
      });
      const embedded = control('scanbody_on_working').checked;
      toggle(form.querySelector('[data-scanbody-upload]'), tiBase && !embedded);
      form.querySelector('[data-clarification-warning]').hidden = !tiBase || !form.querySelector('[data-implant-parameter] input[type=checkbox]:checked');
      if (kind === 'bridge') {
        for (const [name, other] of [['support_teeth','pontic_teeth'],['pontic_teeth','support_teeth']]) {
          const unavailable = new Set(checked(other));
          form.querySelectorAll(`input[name="${name}"]`).forEach(input => { input.disabled = unavailable.has(input.value) && !input.checked; });
        }
      }
      const teeth = kind === 'bridge' ? [...new Set([...checked('support_teeth'), ...checked('pontic_teeth')])] : checked('selected_teeth');
      teeth.sort((a,b) => Number(a)-Number(b));
      summary('service').textContent = selected?.dataset.title || '—';
      summary('teeth').textContent = teeth.join(', ') || '—';
      summary('units').textContent = surgicalGuide ? '1 шаблон' : `${teeth.length} ед.`;
      summary('material').textContent = control('material').selectedOptions[0]?.textContent || '—';
      summary('price').textContent = surgicalGuide || teeth.length ? 'Рассчитываем…' : 'Стоимость уточняется';
      summary('promotion').hidden = true;
      form.querySelector('[data-occlusion-warning]').hidden = surgicalGuide || Boolean(control('antagonist').files.length && control('bite').files.length);
      if (!kind || (!surgicalGuide && !teeth.length)) return;
      // Quote requests carry only service and positions, never the scan files.
      const payload = new URLSearchParams({service:selected.value});
      const fields = surgicalGuide ? [] : (kind === 'bridge' ? ['support_teeth','pontic_teeth'] : ['selected_teeth']);
      fields.forEach(name => checked(name).forEach(value => payload.append(name,value)));
      timer = setTimeout(async () => {
        pending = new AbortController();
        try {
          const response = await fetch(form.dataset.quoteUrl, {method:'POST',body:payload,
            headers:{'X-CSRFToken':control('csrfmiddlewaretoken').value},signal:pending.signal});
          if (!response.ok) throw new Error('Quote unavailable');
          const quote = await response.json();
          summary('price').textContent = quote.total === null ? 'Стоимость уточняется' : new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB'}).format(Number(quote.total));
          summary('promotion').hidden = !quote.free_units;
          summary('promotion').textContent = `По акции: ${quote.free_units} бесплатных коронок.`;
        } catch (error) {
          if (error.name !== 'AbortError') summary('price').textContent = 'Стоимость уточняется';
        }
      }, 250);
    };
    form.addEventListener('change', update);
    update();
  });
}
initializeOrderBuilder();
