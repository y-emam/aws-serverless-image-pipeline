// Demo client. Everything is same-origin through CloudFront:
//   POST /api/uploads -> pre-signed S3 POST     GET /api/images -> gallery data
//   /images/...       -> processed images (private bucket, CloudFront OAC)
// Set window.API_BASE / window.CDN_BASE to run this page from localhost.

const API = window.API_BASE || "";
const CDN = window.CDN_BASE || "";
const HARD_MAX_BYTES = 50 * 1024 * 1024; // sanity cap only; the API returns the real limit (max_bytes)
const ALLOWED = ["image/jpeg", "image/png", "image/webp"];

const $ = (id) => document.getElementById(id);
const form = $("upload-form");
const fileInput = $("file");
const dropzone = $("dropzone");
const uploadBtn = $("upload-btn");
const clearBtn = $("clear-btn");
const statusEl = $("status");
const progress = $("progress");
const barFill = $("bar-fill");
const gallery = $("gallery");
const viewer = $("viewer");

let images = [];
let filter = "all";
let pendingId = null;
let previewUrl = null;
let pollTimer = null;
let lastJson = "";        // skip re-rendering when the gallery data has not changed
const seen = new Set();   // image ids already shown: only new cards animate in

// ---------------------------------------------------------------- helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c); // strings become text nodes: no HTML injection
  return node;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${kind}`;
}

function setStep(step, state = "active") {
  const order = ["url", "upload", "process", "done"];
  const idx = order.indexOf(step);
  progress.hidden = false;
  progress.querySelectorAll(".steps li").forEach((li, i) => {
    li.className = i < idx ? "done" : i === idx ? state : "";
  });
}

function setBar(percent, indeterminate = false) {
  barFill.parentElement.classList.toggle("indeterminate", indeterminate);
  barFill.style.width = `${percent}%`;
  if (indeterminate) barFill.parentElement.removeAttribute("aria-valuenow");
  else barFill.parentElement.setAttribute("aria-valuenow", String(percent));
}

// ---------------------------------------------------------------- file selection

function selectFile(file) {
  if (!file) return;
  if (!ALLOWED.includes(file.type)) return setStatus("Please choose a JPEG, PNG or WebP image.", "bad");
  if (file.size > HARD_MAX_BYTES) return setStatus(`That file is ${formatBytes(file.size)}, which is too large.`, "bad");

  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  $("preview-img").src = previewUrl;
  $("preview-name").textContent = file.name;
  $("preview-size").textContent = formatBytes(file.size);
  dropzone.querySelector(".dz-empty").hidden = true;
  dropzone.querySelector(".dz-preview").hidden = false;
  uploadBtn.disabled = false;
  clearBtn.hidden = false;
  progress.hidden = true;
  setStatus("");
  form.file = file;
}

function resetForm() {
  form.reset();
  form.file = null;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  dropzone.querySelector(".dz-empty").hidden = false;
  dropzone.querySelector(".dz-preview").hidden = true;
  uploadBtn.disabled = true;
  clearBtn.hidden = true;
}

fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));
clearBtn.addEventListener("click", () => { resetForm(); progress.hidden = true; setStatus(""); });

// Counter instead of a flag: dragleave also fires when moving over child elements.
let dragDepth = 0;
dropzone.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; dropzone.classList.add("drag"); });
dropzone.addEventListener("dragover", (e) => e.preventDefault());
dropzone.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; dropzone.classList.remove("drag"); } });
dropzone.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; dropzone.classList.remove("drag"); });
dropzone.addEventListener("drop", (e) => selectFile(e.dataTransfer.files[0]));

// ---------------------------------------------------------------- upload

async function requestUpload(file) {
  const res = await fetch(`${API}/api/uploads`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ filename: file.name, content_type: file.type }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 429) throw new Error("Too many uploads right now. Please wait a moment and try again.");
  if (!res.ok) throw new Error(data.error || data.message || `API returned ${res.status}`);
  return data;
}

function uploadToS3(target, file) {
  // XMLHttpRequest instead of fetch: it reports upload progress.
  return new Promise((resolve, reject) => {
    const body = new FormData();
    for (const [k, v] of Object.entries(target.fields)) body.append(k, v);
    body.append("file", file); // the file must be the last field
    const xhr = new XMLHttpRequest();
    xhr.open("POST", target.url);
    xhr.upload.onprogress = (e) => e.lengthComputable && setBar(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`S3 rejected the upload (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("Network error while uploading"));
    xhr.send(body);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = form.file;
  if (!file) return;
  uploadBtn.disabled = true;
  clearBtn.hidden = true;
  let step = "url";
  try {
    setStep(step);
    setBar(5);
    setStatus("Requesting a pre-signed upload URL...");
    const target = await requestUpload(file);
    if (file.size > target.max_bytes) {
      throw new Error(`That file is ${formatBytes(file.size)}. The limit is ${formatBytes(target.max_bytes)}.`);
    }

    step = "upload";
    setStep(step);
    setStatus("Uploading straight to S3...");
    await uploadToS3(target, file);

    setStep("process");
    setBar(100, true);
    setStatus("Uploaded. Validating, resizing, watermarking and running AI...");
    pendingId = target.image_id;
    images = [{ image_id: pendingId, status: "PROCESSING", uploaded_at: new Date().toISOString(), _local: previewUrl }, ...images];
    previewUrl = null; // the gallery card owns it now
    resetForm();
    render();
    pollUntilDone(pendingId);
  } catch (err) {
    setStep(step, "failed");
    setBar(0);
    setStatus(err.message, "bad");
    uploadBtn.disabled = false;
    clearBtn.hidden = false;
  }
});

function pollUntilDone(id, tries = 0) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    await refresh();
    const mine = images.find((i) => i.image_id === id);
    if (mine && mine.status === "COMPLETED") {
      setStep("done", "done");
      setBar(100);
      setStatus("Done. Your image is at the top of the gallery.", "ok");
      pendingId = null;
      return;
    }
    if (mine && mine.status === "FAILED") {
      setStep("done", "failed");
      setBar(100);
      setStatus(`Processing failed: ${mine.error_type || "unknown error"}.`, "bad");
      pendingId = null;
      return;
    }
    if (tries < 40) pollUntilDone(id, tries + 1);
    else setStatus("Still processing. It will show up in the gallery shortly.");
  }, tries === 0 ? 4000 : 2500);
}

// ---------------------------------------------------------------- gallery

async function refresh() {
  const btn = $("refresh");
  btn.classList.add("spin");
  try {
    const res = await fetch(`${API}/api/images`, { cache: "no-store" });
    if (!res.ok) throw new Error(`API returned ${res.status}`);
    const text = await res.text();
    const fresh = JSON.parse(text).images;
    // Keep the local preview card until the server has a final result for the image.
    for (const old of images.filter((i) => i._local && i.image_id !== pendingId)) URL.revokeObjectURL(old._local);
    const local = images.find((i) => i._local && i.image_id === pendingId);
    const keepLocal = local && local.image_id === pendingId &&
      !fresh.some((i) => i.image_id === local.image_id && (i.status === "COMPLETED" || i.status === "FAILED"));
    if (local && !keepLocal) URL.revokeObjectURL(local._local); // free the preview blob
    if (!keepLocal && text === lastJson) return images;         // nothing changed: no re-render, no flicker
    lastJson = text;
    images = keepLocal ? [local, ...fresh.filter((i) => i.image_id !== local.image_id)] : fresh;
    render();
  } catch (err) {
    if (!images.length) gallery.replaceChildren(el("p", { class: "empty" }, `Could not load the gallery: ${err.message}`));
  } finally {
    btn.classList.remove("spin");
  }
}

function tagList(tags = [], max = 4) {
  const box = el("div", { class: "tags" });
  tags.slice(0, max).forEach((t) => box.append(el("span", { class: "tag" }, t)));
  if (tags.length > max) box.append(el("span", { class: "tag more" }, `+${tags.length - max}`));
  return box;
}

function card(img) {
  const done = img.status === "COMPLETED";
  const failed = img.status === "FAILED";
  const isNew = !seen.has(img.image_id + img.status);
  seen.add(img.image_id + img.status);
  const node = el("article", {
    class: `card${done ? " clickable" : ""}${isNew ? " new" : ""}`,
    tabindex: done ? "0" : null,
    role: done ? "button" : null,
    "aria-label": done ? `View details: ${img.caption || (img.tags || []).join(", ") || "processed image"}` : null,
  });
  const media = el("div", { class: "media" });

  if (done) {
    media.append(el("img", {
      src: `${CDN}/${img.thumb_key}`,
      alt: img.caption || (img.tags || []).join(", ") || "Processed image",
      loading: "lazy",
    }));
  } else if (failed) {
    const ph = el("div", { class: "placeholder failed" });
    ph.append(el("div", {}, "Could not process this file"), el("small", {}, img.error_type || ""));
    media.append(ph);
  } else if (img._local) {
    media.append(el("img", { src: img._local, alt: "Uploaded image, processing", style: "filter: grayscale(.6) blur(1px); opacity: .7" }));
  } else {
    media.className += " shimmer";
  }
  media.append(el("span", { class: `badge ${img.status}` }, done ? "Processed" : failed ? "Failed" : "Processing"));
  node.append(media);

  const body = el("div", { class: "body" });
  if (done) {
    body.append(el("p", { class: "caption" }, img.caption || "No caption"));
    body.append(tagList(img.tags));
    body.append(el("div", { class: "meta" },
      el("span", {}, `${img.full_width || img.width} x ${img.full_height || img.height} ${img.format || ""}`),
      el("span", {}, timeAgo(img.uploaded_at))));
    node.addEventListener("click", () => openViewer(img));
    node.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), openViewer(img)));
  } else if (failed) {
    body.append(el("p", { class: "caption" }, img.error_type === "InvalidImage" ? "Rejected: not a valid image" : "Processing failed"));
    body.append(el("div", { class: "meta" }, el("span", {}, img.error_type || "Error"), el("span", {}, timeAgo(img.uploaded_at))));
  } else {
    body.append(el("div", { class: "skeleton-line shimmer", style: "width: 80%" }));
    body.append(el("div", { class: "skeleton-line shimmer", style: "width: 50%" }));
  }
  node.append(body);
  return node;
}

function render() {
  const visible = filter === "all" ? images : images.filter((i) => i.status === filter);
  const done = images.filter((i) => i.status === "COMPLETED").length;
  $("gallery-count").textContent = images.length ? `${done} processed of ${images.length} uploads` : "";
  if (!visible.length) {
    gallery.replaceChildren(el("p", { class: "empty" },
      images.length ? "Nothing here for this filter." : "No images yet. Upload the first one above."));
    return;
  }
  gallery.replaceChildren(...visible.map(card));
}

document.querySelectorAll(".tabs button").forEach((btn) =>
  btn.addEventListener("click", () => {
    filter = btn.dataset.filter;
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    render();
  }));
$("refresh").addEventListener("click", refresh);

// ---------------------------------------------------------------- viewer

function openViewer(img) {
  const full = `${CDN}/${img.full_key}`;
  $("viewer-img").src = full;
  $("viewer-img").alt = img.caption || "Processed image";
  $("viewer-caption").textContent = img.caption || "No caption";
  $("viewer-tags").replaceChildren(...(img.tags || []).map((t) => el("span", { class: "tag" }, t)));
  const meta = [
    ["Original", `${img.width} x ${img.height} ${img.format || ""}`],
    ["Full-size copy", img.full_width ? `${img.full_width} x ${img.full_height} JPEG, watermarked` : "-"],
    ["Uploaded", img.uploaded_at ? new Date(img.uploaded_at).toLocaleString() : "-"],
    ["Processed", img.processed_at ? new Date(img.processed_at).toLocaleString() : "-"],
    ["Image id", img.image_id],
  ];
  $("viewer-meta").replaceChildren(...meta.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
  $("viewer-open").href = full;
  viewer.showModal();
}

$("viewer-close").addEventListener("click", () => viewer.close());
viewer.addEventListener("click", (e) => e.target === viewer && viewer.close()); // click on backdrop

// ---------------------------------------------------------------- start

gallery.replaceChildren(...Array.from({ length: 4 }, () => {
  const c = el("article", { class: "card" });
  c.append(el("div", { class: "media shimmer" }), el("div", { class: "body" },
    el("div", { class: "skeleton-line shimmer", style: "width: 80%" }),
    el("div", { class: "skeleton-line shimmer", style: "width: 45%" })));
  return c;
}));
refresh();
