// Minimal demo client. Everything is same-origin through CloudFront:
//   /api/uploads  -> pre-signed POST      /api/images -> gallery data
//   /images/...   -> processed images (private bucket, CloudFront OAC)
// Set window.API_BASE / window.CDN_BASE to run this page from localhost.

const API = window.API_BASE || "";
const CDN = window.CDN_BASE || "";

const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file");
const button = document.getElementById("upload-btn");
const statusEl = document.getElementById("status");
const gallery = document.getElementById("gallery");

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = kind;
}

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text; // textContent: no HTML injection
  return node;
}

async function requestUpload(file) {
  const res = await fetch(`${API}/api/uploads`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ filename: file.name, content_type: file.type }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `API returned ${res.status}`);
  return data;
}

async function uploadToS3(target, file) {
  const body = new FormData();
  for (const [k, v] of Object.entries(target.fields)) body.append(k, v);
  body.append("file", file); // the file must be the last field
  const res = await fetch(target.url, { method: "POST", body });
  if (!res.ok) throw new Error(`S3 rejected the upload (${res.status})`);
}

async function fetchImages() {
  const res = await fetch(`${API}/api/images`);
  if (!res.ok) throw new Error(`API returned ${res.status}`);
  return (await res.json()).images;
}

function renderCard(img) {
  const card = el("article", { class: `card ${img.status === "FAILED" ? "failed" : ""}` });
  if (img.status === "COMPLETED") {
    const link = el("a", { href: `${CDN}/${img.full_key}`, target: "_blank", rel: "noopener" });
    link.append(el("img", {
      src: `${CDN}/${img.thumb_key}`,
      alt: img.caption || (img.tags || []).join(", ") || "Uploaded image",
      loading: "lazy",
    }));
    card.append(link);
  }
  const body = el("div", { class: "body" });
  if (img.status === "COMPLETED") {
    body.append(el("p", { class: "caption" }, img.caption || "(no caption)"));
    const tags = el("div", { class: "tags" });
    for (const t of img.tags || []) tags.append(el("span", { class: "tag" }, t));
    body.append(tags);
    body.append(el("span", { class: "meta" }, `${img.width}x${img.height} ${img.format} - ${new Date(img.uploaded_at).toLocaleString()}`));
  } else {
    body.append(el("strong", {}, img.status));
    if (img.error_type) body.append(el("span", { class: "meta" }, img.error_type));
  }
  card.append(body);
  return card;
}

async function refresh() {
  try {
    const images = await fetchImages();
    gallery.replaceChildren(...images.map(renderCard));
    if (!images.length) gallery.replaceChildren(el("p", { class: "empty" }, "No images yet."));
    return images;
  } catch (err) {
    gallery.replaceChildren(el("p", { class: "empty" }, `Could not load gallery: ${err.message}`));
    return [];
  }
}

async function waitForResult(imageId) {
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    const images = await refresh();
    const mine = images.find((img) => img.image_id === imageId);
    if (mine && mine.status === "COMPLETED") return setStatus("Done. Your image is at the top of the gallery.", "ok");
    if (mine && mine.status === "FAILED") return setStatus(`Processing failed: ${mine.error_type || "unknown error"}`, "bad");
  }
  setStatus("Still processing. Press Refresh in a moment.");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;
  button.disabled = true;
  try {
    setStatus("Requesting upload URL...");
    const target = await requestUpload(file);
    if (file.size > target.max_bytes) throw new Error("File is larger than the 10 MB limit");
    setStatus("Uploading...");
    await uploadToS3(target, file);
    setStatus("Uploaded. Processing (validate, resize, watermark, AI)...");
    form.reset();
    await waitForResult(target.image_id);
  } catch (err) {
    setStatus(err.message, "bad");
  } finally {
    button.disabled = false;
  }
});

document.getElementById("refresh").addEventListener("click", refresh);
refresh();
