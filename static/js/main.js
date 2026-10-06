// Auto-dismiss flash alerts after 5s
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".alert-dismissible").forEach((el) => {
    setTimeout(() => {
      const alert = bootstrap.Alert.getOrCreateInstance(el);
      alert.close();
    }, 5000);
  });

  // Add-to-cart with AJAX (optional polish)
  document.querySelectorAll(".add-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button");
      const original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-check-lg"></i> Added';

      try {
        await fetch(form.action, {
          method: "POST",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        // Update cart badge
        const res = await fetch(window.location.href, { method: "HEAD" });
        setTimeout(() => {
          btn.innerHTML = original;
          btn.disabled = false;
        }, 1200);
      } catch (err) {
        form.submit();
      }
    });
  });
});
