const bodyDataset = document.body ? document.body.dataset : {};
const boot = {
  connected: bodyDataset.connected === "1",
  clientConfigured: bodyDataset.clientConfigured === "1",
};

const state = {
  connected: Boolean(boot.connected),
  clientConfigured: Boolean(boot.clientConfigured),
  listings: [],
  selected: new Set(),
  uploading: false,
};

const connectBtn = document.getElementById("connectBtn");
const refreshBtn = document.getElementById("refreshListingsBtn");
const selectAllToggle = document.getElementById("selectAllToggle");
const listingContainer = document.getElementById("listingContainer");
const selectionMeta = document.getElementById("selectionMeta");
const uploadBtn = document.getElementById("uploadBtn");
const videoFileInput = document.getElementById("videoFile");
const statusLog = document.getElementById("statusLog");

function appendLog(message, kind = "info") {
  const entry = document.createElement("div");
  entry.className = `log-entry ${kind}`;

  const timestamp = new Date().toLocaleTimeString();
  entry.textContent = `[${timestamp}] ${message}`;

  statusLog.appendChild(entry);
  statusLog.scrollTop = statusLog.scrollHeight;
}

function updateUploadButtonState() {
  const hasFile = Boolean(videoFileInput?.files?.length);
  const hasSelections = state.selected.size > 0;

  uploadBtn.disabled = !(
    state.connected &&
    state.clientConfigured &&
    hasFile &&
    hasSelections &&
    !state.uploading
  );
}

function renderListings() {
  listingContainer.innerHTML = "";

  if (!state.listings.length) {
    const empty = document.createElement("p");
    empty.className = "placeholder";
    empty.textContent = "No active listings found.";
    listingContainer.appendChild(empty);
    updateSelectionMeta();
    updateUploadButtonState();
    return;
  }

  const fragment = document.createDocumentFragment();

  state.listings.forEach((listing) => {
    const wrapper = document.createElement("label");
    wrapper.className = "listing-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "listing-checkbox";
    checkbox.value = String(listing.listing_id);
    checkbox.checked = state.selected.has(Number(listing.listing_id));

    const textWrap = document.createElement("div");

    const title = document.createElement("div");
    title.className = "listing-title";
    title.textContent = listing.title || "Untitled Listing";

    const listingId = document.createElement("div");
    listingId.className = "listing-id";
    listingId.textContent = `ID: ${listing.listing_id}`;

    textWrap.appendChild(title);
    textWrap.appendChild(listingId);

    wrapper.appendChild(checkbox);
    wrapper.appendChild(textWrap);
    fragment.appendChild(wrapper);
  });

  listingContainer.appendChild(fragment);
  updateSelectionMeta();
  updateUploadButtonState();
}

function updateSelectionMeta() {
  const checkboxes = listingContainer.querySelectorAll(".listing-checkbox");
  const checked = listingContainer.querySelectorAll(".listing-checkbox:checked");

  state.selected = new Set(Array.from(checked).map((el) => Number(el.value)));

  selectionMeta.textContent = `${state.selected.size} selected`;

  if (!checkboxes.length) {
    selectAllToggle.checked = false;
    selectAllToggle.indeterminate = false;
    return;
  }

  if (checked.length === 0) {
    selectAllToggle.checked = false;
    selectAllToggle.indeterminate = false;
  } else if (checked.length === checkboxes.length) {
    selectAllToggle.checked = true;
    selectAllToggle.indeterminate = false;
  } else {
    selectAllToggle.checked = false;
    selectAllToggle.indeterminate = true;
  }
}

async function loadListings() {
  if (!state.connected) {
    return;
  }

  refreshBtn.disabled = true;
  listingContainer.innerHTML = '<p class="placeholder">Loading active listings...</p>';

  try {
    const response = await fetch("/api/listings");
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || "Failed to load listings.");
    }

    state.listings = Array.isArray(payload.listings) ? payload.listings : [];
    renderListings();
    appendLog(`Loaded ${state.listings.length} active listing(s).`, "success");
  } catch (error) {
    listingContainer.innerHTML = `<p class="placeholder">${error.message}</p>`;
    appendLog(error.message, "error");
  } finally {
    refreshBtn.disabled = false;
    updateUploadButtonState();
  }
}

function handleUploadEvent(event) {
  if (event.type === "start") {
    appendLog(`Starting upload for ${event.total} listing(s)...`, "info");
    return;
  }

  if (event.type === "listing") {
    const prefix = `(${event.index}/${event.total}) Listing ${event.listing_id}: `;
    const kind = event.status === "success" ? "success" : "error";
    appendLog(prefix + event.message, kind);
    return;
  }

  if (event.type === "complete") {
    appendLog(
      `Upload complete. Success: ${event.success}, Failed: ${event.failed}, Total: ${event.total}.`,
      event.failed > 0 ? "error" : "success"
    );
  }
}

async function streamNdjson(response) {
  if (!response.body) {
    throw new Error("Streaming not supported by this browser.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) {
        continue;
      }
      const event = JSON.parse(line);
      handleUploadEvent(event);
    }
  }

  if (buffer.trim()) {
    const event = JSON.parse(buffer);
    handleUploadEvent(event);
  }
}

async function uploadSelectedListings() {
  if (!state.connected) {
    appendLog("Connect to Etsy before uploading.", "error");
    return;
  }

  if (!videoFileInput.files.length) {
    appendLog("Select an MP4 file before uploading.", "error");
    return;
  }

  if (!state.selected.size) {
    appendLog("Select at least one listing.", "error");
    return;
  }

  state.uploading = true;
  updateUploadButtonState();

  const formData = new FormData();
  formData.append("video", videoFileInput.files[0]);
  formData.append("listing_ids", JSON.stringify(Array.from(state.selected)));

  try {
    const response = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      let errorMessage = "Upload failed before processing listings.";
      try {
        const payload = await response.json();
        errorMessage = payload.error || errorMessage;
      } catch (_) {
        // Ignore JSON parse error and keep fallback message.
      }
      throw new Error(errorMessage);
    }

    await streamNdjson(response);
  } catch (error) {
    appendLog(error.message, "error");
  } finally {
    state.uploading = false;
    updateUploadButtonState();
  }
}

if (connectBtn) {
  connectBtn.addEventListener("click", () => {
    window.location.href = "/login";
  });
}

if (refreshBtn) {
  refreshBtn.addEventListener("click", loadListings);
}

if (listingContainer) {
  listingContainer.addEventListener("change", (event) => {
    if (event.target.classList.contains("listing-checkbox")) {
      updateSelectionMeta();
      updateUploadButtonState();
    }
  });
}

if (selectAllToggle) {
  selectAllToggle.addEventListener("change", () => {
    const shouldSelect = selectAllToggle.checked;
    const checkboxes = listingContainer.querySelectorAll(".listing-checkbox");

    checkboxes.forEach((checkbox) => {
      checkbox.checked = shouldSelect;
    });

    updateSelectionMeta();
    updateUploadButtonState();
  });
}

if (videoFileInput) {
  videoFileInput.addEventListener("change", updateUploadButtonState);
}

if (uploadBtn) {
  uploadBtn.addEventListener("click", uploadSelectedListings);
}

if (state.connected) {
  loadListings();
} else {
  appendLog("Connect to Etsy to begin.", "info");
}

updateUploadButtonState();
