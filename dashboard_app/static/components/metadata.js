import {elements, setText} from "../dom.js";
import {API, postJson} from "../api.js";

export function createMetadataComponent() {
  const els = elements(["metadataForm", "metadataStatus"]);

  function fill(metadata) {
    for (const [key, value] of Object.entries(metadata)) {
      const field = els.metadataForm.elements.namedItem(key);
      if (field && field.value !== value) field.value = value || "";
    }
    setText(els.metadataStatus, "Saved");
  }

  els.metadataForm.addEventListener("input", () => setText(els.metadataStatus, "Unsaved"));
  els.metadataForm.addEventListener("submit", async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(els.metadataForm).entries());
    const payload = await postJson(API.metadata, data);
    fill(payload.metadata);
  });

  return {fill};
}
