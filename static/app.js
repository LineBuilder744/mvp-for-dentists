document.querySelectorAll('[data-upload-form]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!form.checkValidity()) return;
    event.preventDefault();
    const button = form.querySelector('[type=submit]');
    if (button.disabled) return;
    const status = form.querySelector('.upload-status');
    const progress = status.querySelector('progress');
    const label = status.querySelector('span');
    const xhr = new XMLHttpRequest();
    button.disabled = true;
    status.hidden = false;
    label.textContent = 'Загрузка файлов…';
    xhr.open('POST', form.action || window.location.href);
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) {
        progress.value = Math.round(e.loaded / e.total * 100);
        label.textContent = progress.value === 100 ? 'Файлы загружены. Проверяем заказ…' : `Загрузка: ${progress.value}%`;
      }
    };
    const failed = () => {
      button.disabled = false;
      label.textContent = 'Не удалось отправить заказ. Проверьте соединение и повторите отправку.';
    };
    xhr.onerror = failed;
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        if (new URL(xhr.responseURL).pathname !== window.location.pathname) {
          window.location.assign(xhr.responseURL);
        } else {
          const doc = new DOMParser().parseFromString(xhr.responseText, 'text/html');
          document.querySelector('main').replaceWith(doc.querySelector('main'));
          const script = document.createElement('script');
          script.src = '/static/app.js';
          document.body.appendChild(script);
        }
      } else failed();
    };
    xhr.send(new FormData(form));
  });
});
