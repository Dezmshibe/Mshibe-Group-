/* ============================================================
   SITE SCRIPT — consolidated, idempotent, safe
   ============================================================ */
(function () {
  "use strict";

  /* ----------------------------------------------------------
     Utility helpers
     ---------------------------------------------------------- */
  const prefersReducedMotion = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const safe = (fn) => {
    try {
      fn();
    } catch (err) {
      console.error("[site]", err);
    }
  };

  /* ----------------------------------------------------------
     Flash alerts — auto-dismiss after 5s
     ---------------------------------------------------------- */
  function initFlashAlerts() {
    const alerts = document.querySelectorAll(".alert-dismissible");
    if (!alerts.length) return;

    alerts.forEach((el) => {
      window.setTimeout(() => {
        if (window.bootstrap && window.bootstrap.Alert) {
          window.bootstrap.Alert.getOrCreateInstance(el).close();
        } else {
          el.remove();
        }
      }, 5000);
    });
  }

  /* ----------------------------------------------------------
     Add-to-cart — AJAX with graceful fallback
     ---------------------------------------------------------- */
  function initAddToCart() {
    const forms = document.querySelectorAll(".add-form");
    if (!forms.length) return;

    forms.forEach((form) => {
      if (form.dataset.ajaxBound === "true") return;
      form.dataset.ajaxBound = "true";

      form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const btn = form.querySelector("button");
        if (!btn) return;

        const original = btn.innerHTML;
        const restore = () => {
          btn.innerHTML = original;
          btn.disabled = false;
        };

        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-check-lg"></i> Added';

        try {
          const res = await fetch(form.action, {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
            credentials: "same-origin",
          });

          if (!res.ok) throw new Error("Request failed: " + res.status);

          // Update cart badge from the response body if available.
          const html = await res.text();
          const doc = new DOMParser().parseFromString(html, "text/html");
          const newBadge = doc.querySelector(".cart-badge");
          const oldBadge = document.querySelector(".cart-badge");
          if (newBadge && oldBadge) {
            oldBadge.textContent = newBadge.textContent;
          }

          window.setTimeout(restore, 1200);
        } catch (err) {
          // Fallback: submit the form normally so the user still gets the item.
          restore();
          form.submit();
        }
      });
    });
  }

  /* ==========================================================
     SCROLL LOADING / REVEAL EFFECT
     — Replays on every entry, scrolling DOWN or UP
     ========================================================== */
  function initScrollReveal() {
    const sections = document.querySelectorAll(
      "main > section, main > div > section"
    );

    if (!sections.length) return;

    // Respect accessibility preferences.
    const reducedMotion = prefersReducedMotion();

    if (reducedMotion || !("IntersectionObserver" in window)) {
      sections.forEach(function (section) {
        section.classList.add("is-visible");
      });
      return;
    }

    sections.forEach(function (section) {
      section.classList.add("scroll-reveal");
    });

    const observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            // Entering viewport (from either direction) → reveal
            entry.target.classList.add("is-visible");
          } else {
            // Fully out of viewport (up or down) → reset so it can replay
            entry.target.classList.remove("is-visible");
          }
        });
      },
      {
        threshold: [0, 0.12],
        rootMargin: "0px 0px -40px 0px",
      }
    );

    // NOTE: no observer.unobserve() — the effect must keep firing.
    sections.forEach(function (section) {
      observer.observe(section);
    });
  }

  /* ----------------------------------------------------------
     Boot
     ---------------------------------------------------------- */
  function boot() {
    safe(initFlashAlerts);
    safe(initAddToCart);
    safe(initScrollReveal);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();