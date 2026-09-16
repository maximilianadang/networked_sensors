// PANEL: Test Metadata — editing, saving and derived motion setpoints.
// Markup: index.html; appearance: dashboard.css (search Test Metadata).
import {elements, setText} from "../dom.js";
import {API, postJson} from "../api.js";

export function createMetadataComponent({
  geometry,
  stepperLimits,
  applyMotionPlan,
  onPowderFlowChange
}) {
  const els = elements([
    "metadataForm", "metadataStatus", "metadataMotionPlan"
  ]);
  const powderFlowField = els.metadataForm.elements.namedItem(
    "powder_flow_rate_g_per_s",
  );
  const durationField = els.metadataForm.elements.namedItem("test_duration_s");
  const powderMassPerTravel = Number(
    geometry.powder_mass_per_stepper_travel_g_per_mm,
  );

  function powderFlowRateGPerS() {
    const parsed = Number(powderFlowField.value);
    return powderFlowField.value.trim() !== "" &&
      Number.isFinite(parsed) &&
      parsed > 0
      ? parsed
      : null;
  }

  function updateMotionPlan() {
    powderFlowField.setCustomValidity("");
    durationField.setCustomValidity("");
    const powderFlowGPerS = powderFlowRateGPerS();
    onPowderFlowChange(powderFlowGPerS);

    if (powderFlowField.value.trim() === "") {
      setText(
        els.metadataMotionPlan,
        `Enter powder flow and duration to calculate Positional setpoints · geometry ${powderMassPerTravel} g/mm`,
      );
      return;
    }
    if (powderFlowGPerS === null) {
      powderFlowField.setCustomValidity(
        "Enter a powder flow rate greater than 0 g/s.",
      );
      setText(els.metadataMotionPlan, "Powder flow must be greater than 0 g/s.");
      return;
    }

    const speedMmS = powderFlowGPerS / powderMassPerTravel;
    const rawDuration = durationField.value.trim();
    const durationS = rawDuration === "" ? null : Number(rawDuration);
    const validDuration = durationS === null ||
      (Number.isFinite(durationS) && durationS > 0);
    const distanceMm = validDuration && durationS !== null
      ? speedMmS * durationS
      : null;
    applyMotionPlan({speedMmS, distanceMm});

    const errors = [];
    if (
      speedMmS < stepperLimits.min_speed_mm_s ||
      speedMmS > stepperLimits.max_speed_mm_s
    ) {
      const message = `Calculated speed ${speedMmS.toFixed(3)} mm/s is outside the ${stepperLimits.min_speed_mm_s}–${stepperLimits.max_speed_mm_s} mm/s range.`;
      powderFlowField.setCustomValidity(message);
      errors.push(message);
    }
    if (!validDuration) {
      const message = "Test duration must be greater than 0 seconds.";
      durationField.setCustomValidity(message);
      errors.push(message);
    } else if (
      distanceMm !== null &&
      (
        distanceMm < stepperLimits.min_distance_mm ||
        distanceMm > stepperLimits.max_distance_mm
      )
    ) {
      const message = `Calculated travel ${distanceMm.toFixed(2)} mm is outside the ${stepperLimits.min_distance_mm}–${stepperLimits.max_distance_mm} mm range.`;
      durationField.setCustomValidity(message);
      errors.push(message);
    }

    if (errors.length > 0) {
      setText(els.metadataMotionPlan, errors[0]);
    } else if (distanceMm === null) {
      setText(
        els.metadataMotionPlan,
        `Positional speed ${speedMmS.toFixed(3)} mm/s · enter duration to calculate travel`,
      );
    } else {
      setText(
        els.metadataMotionPlan,
        `Positional setpoints ${speedMmS.toFixed(3)} mm/s · ${distanceMm.toFixed(2)} mm travel · geometry ${powderMassPerTravel} g/mm`,
      );
    }
  }

  function fill(metadata) {
    for (const [key, value] of Object.entries(metadata)) {
      const field = els.metadataForm.elements.namedItem(key);
      if (field && field.value !== value) field.value = value || "";
    }
    updateMotionPlan();
    setText(els.metadataStatus, "Saved");
  }

  els.metadataForm.addEventListener("input", event => {
    setText(els.metadataStatus, "Unsaved");
    if (event.target === powderFlowField || event.target === durationField) {
      updateMotionPlan();
    }
  });
  els.metadataForm.addEventListener("submit", async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(els.metadataForm).entries());
    const payload = await postJson(API.metadata, data);
    fill(payload.metadata);
  });

  updateMotionPlan();
  return {fill, powderFlowRateGPerS};
}
