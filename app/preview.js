(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'preview-dialog';
  dialog.setAttribute('aria-label', '캡처 확대 미리보기');
  dialog.innerHTML = '<div class="preview-panel"><button class="preview-close" type="button" aria-label="미리보기 닫기">×</button><img alt=""></div>';
  document.body.appendChild(dialog);
  const image = dialog.querySelector('img');
  const close = dialog.querySelector('.preview-close');
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { image.removeAttribute('src'); });
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('.preview-trigger');
    if (!trigger) return;
    const thumbnail = trigger.querySelector('img');
    if (!thumbnail) return;
    image.src = thumbnail.currentSrc || thumbnail.src;
    image.alt = thumbnail.alt;
    dialog.showModal();
    close.focus();
  });
})();
