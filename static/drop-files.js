function initializeDropFiles(root = document) {
  root.querySelectorAll('[data-drop-zone]').forEach(zone => {
    if (zone.dataset.initialized) return;
    zone.dataset.initialized = 'true';
    const input = zone.querySelector('input[type=file]');
    const status = zone.querySelector('.drop-status');
    let dragDepth = 0;
    const showStatus = (message, error = false) => {
      status.textContent = message;
      status.hidden = !message;
      zone.classList.toggle('has-error', error);
    };
    const validate = files => {
      if (files.length !== 1) return 'Перетащите один файл в это поле.';
      if (!files[0].name.toLowerCase().endsWith('.stl')) return 'Принимаются только STL-файлы.';
      if (!files[0].size || files[0].size > 200 * 1024 * 1024) return 'Файл должен быть непустым и не больше 200 МБ.';
      return '';
    };
    input.addEventListener('change', () => {
      const error = input.files.length ? validate(input.files) : '';
      if (error) input.value = '';
      zone.classList.toggle('has-file', input.files.length > 0);
      showStatus(error || (input.files.length ? `Выбран: ${input.files[0].name}` : ''), Boolean(error));
    });
    zone.addEventListener('dragenter', event => {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      dragDepth += 1;
      zone.classList.add('is-dragging');
    });
    zone.addEventListener('dragover', event => {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = input.disabled ? 'none' : 'copy';
    });
    zone.addEventListener('dragleave', event => {
      event.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) zone.classList.remove('is-dragging');
    });
    zone.addEventListener('drop', event => {
      event.preventDefault();
      dragDepth = 0;
      zone.classList.remove('is-dragging');
      if (input.disabled) return;
      const error = validate(event.dataTransfer.files);
      if (error) {
        showStatus(error, true);
        return;
      }
      input.files = event.dataTransfer.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
    });
  });
}

// Prevent accidental navigation when a file misses its drop zone.
['dragover', 'drop'].forEach(type => {
  document.addEventListener(type, event => {
    if (event.dataTransfer.types.includes('Files')) event.preventDefault();
  });
});
initializeDropFiles();
