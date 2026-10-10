(() => {
  'use strict';
  const key = 'pdfwtf-theme';
  const system = matchMedia('(prefers-color-scheme: dark)');
  let theme = 'auto';
  try { const saved = localStorage.getItem(key); if (['light', 'dark', 'auto'].includes(saved)) theme = saved; } catch (_) {}
  const apply = () => {
    document.documentElement.dataset.bsTheme = theme === 'auto' ? (system.matches ? 'dark' : 'light') : theme;
    document.querySelectorAll('[data-bs-theme-value]').forEach(button => {
      const active = button.dataset.bsThemeValue === theme;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      button.querySelector('.theme-check').classList.toggle('d-none', !active);
      if (active) {
        const toggle = document.getElementById('bd-theme');
        toggle.setAttribute('aria-label', `${toggle.dataset.label} (${button.dataset.label})`);
        toggle.querySelector('use').setAttribute('href', `#theme-${theme}`);
      }
    });
  };
  apply();
  system.addEventListener('change', () => { if (theme === 'auto') apply(); });
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    const picker = document.getElementById('theme-picker');
    document.querySelectorAll('[data-bs-theme-value]').forEach(button => button.addEventListener('click', () => {
      theme = button.dataset.bsThemeValue;
      try { localStorage.setItem(key, theme); } catch (_) {}
      apply(); picker.open = false; document.getElementById('bd-theme').focus();
    }));
    document.addEventListener('click', event => { if (!picker.contains(event.target)) picker.open = false; });
    picker.addEventListener('keydown', event => {
      if (event.key === 'Escape') { picker.open = false; document.getElementById('bd-theme').focus(); }
    });
  });
})();
