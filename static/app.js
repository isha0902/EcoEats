(() => {
  // Auto-dismiss toast messages after a moment
  const toasts = document.querySelectorAll(".toast");
  if (!toasts.length) return;

  window.setTimeout(() => {
    toasts.forEach((t) => {
      t.style.transition = "opacity 240ms ease, transform 240ms ease";
      t.style.opacity = "0";
      t.style.transform = "translateY(-6px)";
      window.setTimeout(() => t.remove(), 260);
    });
  }, 2400);
})();
