// Event form helpers: show only the fields of the chosen type, add tier rows,
// and show the chosen venue's sections. Plain DOM, no framework.
(function () {
  const typeSelect = document.getElementById("id_type");
  const venueSelect = document.getElementById("id_venue");

  function showFieldsForType() {
    const type = typeSelect.value;
    document.querySelectorAll("[data-for-type]").forEach((el) => {
      el.hidden = !el.dataset.forType.split(" ").includes(type);
    });
  }

  function showVenueSections() {
    document.querySelectorAll(".venue-sections").forEach((el) => {
      el.classList.toggle("d-none", el.dataset.venue !== venueSelect.value);
    });
  }

  // Django formsets: every row's inputs are named tiers-<n>-<field>; the empty
  // template uses __prefix__ instead of <n>, and TOTAL_FORMS says how many rows exist.
  function addTierRow() {
    const total = document.getElementById("id_tiers-TOTAL_FORMS");
    const index = parseInt(total.value, 10);
    const html = document.getElementById("tier-template").innerHTML.replace(/__prefix__/g, index);
    document.getElementById("tier-rows").insertAdjacentHTML("beforeend", html);
    total.value = index + 1;
  }

  typeSelect.addEventListener("change", showFieldsForType);
  venueSelect.addEventListener("change", showVenueSections);
  document.getElementById("add-tier").addEventListener("click", addTierRow);
  showFieldsForType();
  showVenueSections();
})();
