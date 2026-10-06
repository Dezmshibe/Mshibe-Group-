document.addEventListener("DOMContentLoaded", () => {
  const serviceEl = document.getElementById("service_id");
  const dateEl = document.getElementById("date");
  const slotWrap = document.getElementById("slots");
  const slotInput = document.getElementById("time");
  const msg = document.getElementById("slot-msg");
  const form = document.getElementById("booking-form");

  if (!serviceEl || !dateEl || !slotWrap) return;

  const today = new Date().toISOString().split("T")[0];
  dateEl.min = today;

  function to12Hour(value) {
    const [h, m] = value.split(":").map(Number);
    const suffix = h < 12 ? "AM" : "PM";
    return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${suffix}`;
  }

  async function loadSlots() {
    const serviceId = serviceEl.value;
    const date = dateEl.value;
    slotWrap.innerHTML = "";
    slotInput.value = "";
    msg.style.color = "";
    msg.textContent = "";

    if (!serviceId || !date) {
      msg.textContent = "Select a service and date to see available times.";
      return;
    }

    msg.textContent = "Loading available times…";

    try {
      const res = await fetch(
        `/api/slots?service_id=${encodeURIComponent(serviceId)}&date=${encodeURIComponent(date)}`
      );
      const data = await res.json();

      if (!data.slots || data.slots.length === 0) {
        msg.textContent = data.message || "No available times for this date.";
        return;
      }

      msg.textContent = "";
      data.slots.forEach((slot) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "slot";
        btn.textContent = to12Hour(slot);
        btn.dataset.value = slot;

        btn.addEventListener("click", () => {
          slotWrap.querySelectorAll(".slot.selected").forEach((el) =>
            el.classList.remove("selected")
          );
          btn.classList.add("selected");
          slotInput.value = slot;
        });

        slotWrap.appendChild(btn);
      });
    } catch (err) {
      msg.textContent = "Could not load times. Please try again.";
    }
  }

  serviceEl.addEventListener("change", loadSlots);
  dateEl.addEventListener("change", loadSlots);

  form.addEventListener("submit", (e) => {
    if (!slotInput.value) {
      e.preventDefault();
      msg.textContent = "Please select an available time slot.";
      msg.style.color = "#dc2626";
      slotWrap.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });

  if (serviceEl.value && dateEl.value) loadSlots();
});
