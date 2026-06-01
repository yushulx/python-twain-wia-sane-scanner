const tokenKey = "remote-scan-token";
const state = {
  token: sessionStorage.getItem(tokenKey) || "",
  currentUser: null,
  pollHandle: null,
  capturedPages: [],
  authMode: "hidden",
  publicSetup: {
    adminUsernames: [],
    configuredAdminUsername: "",
  },
  scanners: [],
  selectedScannerId: "",
};

const registerForm = document.getElementById("registerForm");
const loginForm = document.getElementById("loginForm");
const logoutButton = document.getElementById("logoutButton");
const refreshButton = document.getElementById("refreshButton");
const refreshUsersButton = document.getElementById("refreshUsersButton");
const sessionState = document.getElementById("sessionState");
const userBadge = document.getElementById("userBadge");
const heroActionRow = document.getElementById("heroActionRow");
const authEntrySection = document.getElementById("authEntrySection");
const authPanel = document.getElementById("authPanel");
const authTitle = document.getElementById("authTitle");
const authCopy = document.getElementById("authCopy");
const closeAuthPanelButton = document.getElementById("closeAuthPanel");
const workspaceSection = document.getElementById("workspaceSection");
const scannerSelect = document.getElementById("scannerSelect");
const scannerDetail = document.getElementById("scannerDetail");
const scanSelectedButton = document.getElementById("scanSelectedButton");
const serviceCaption = document.getElementById("serviceCaption");
const messageBanner = document.getElementById("messageBanner");
const resultMeta = document.getElementById("resultMeta");
const resultStage = document.getElementById("resultStage");
const pageIndicator = document.getElementById("pageIndicator");
const exportPngButton = document.getElementById("exportPngButton");
const exportPdfButton = document.getElementById("exportPdfButton");
const usersPanel = document.getElementById("usersPanel");
const userList = document.getElementById("userList");
const resultPanel = document.getElementById("resultPanel");
const scanControlsSection = document.getElementById("scanControlsSection");
const scannerStatusSection = document.getElementById("scannerStatusSection");
const lightbox = document.getElementById("lightbox");
const lightboxImage = document.getElementById("lightboxImage");
const lightboxClose = document.getElementById("lightboxClose");
const adminSummary = document.getElementById("adminSummary");
const workspaceRolePill = document.getElementById("workspaceRolePill");
const authTriggerButtons = document.querySelectorAll("[data-open-auth]");
const authSwitchButtons = document.querySelectorAll("[data-switch-auth]");
const registerSubmitButton = registerForm.querySelector('button[type="submit"]');
const loginSubmitButton = loginForm.querySelector('button[type="submit"]');

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));
}

function setBanner(message, tone = "info") {
  messageBanner.textContent = message;
  messageBanner.dataset.tone = tone;
}

function formatError(error) {
  if (!error) {
    return "Request failed.";
  }
  if (Array.isArray(error)) {
    return error.map((entry) => formatError(entry)).join(" ");
  }
  if (typeof error === "string") {
    return error;
  }
  if (error.msg) {
    const fieldName = Array.isArray(error.loc) && error.loc.length
      ? error.loc[error.loc.length - 1]
      : "field";
    return `${fieldName}: ${error.msg}`;
  }
  if (error.detail) {
    return formatError(error.detail);
  }
  if (error.message) {
    return error.message;
  }
  if (error.error) {
    return error.error;
  }
  return JSON.stringify(error);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) {
    headers.set("Authorization", `Bearer ${state.token}`);
  }
  if (options.json) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.json ? JSON.stringify(options.json) : options.body,
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    throw payload;
  }
  return payload;
}

function setSubmitBusy(button, isBusy, busyText, idleText) {
  button.disabled = isBusy;
  button.textContent = isBusy ? busyText : idleText;
}

function renderAdminSummary() {
  if (state.publicSetup.configuredAdminUsername) {
    adminSummary.textContent = `Configured administrator account: ${state.publicSetup.configuredAdminUsername}`;
    return;
  }

  const usernames = state.publicSetup.adminUsernames || [];
  if (usernames.length) {
    const noun = usernames.length === 1 ? "Administrator account" : "Administrator accounts";
    adminSummary.textContent = `${noun}: ${usernames.join(", ")}`;
    return;
  }

  adminSummary.textContent = "No administrator account is visible yet. Register the first account or configure REMOTE_SCAN_ADMIN_* in .env.";
}

function syncLayout() {
  const signedIn = Boolean(state.currentUser);
  const showAuthPanel = !signedIn && state.authMode !== "hidden";
  heroActionRow.hidden = signedIn;
  authEntrySection.hidden = !showAuthPanel;
  workspaceSection.hidden = !signedIn;
  // Hide userBadge for admins — "Signed in as admin" in sessionState is enough
  userBadge.hidden = !signedIn || Boolean(state.currentUser?.is_admin);
  logoutButton.hidden = !signedIn;
}

function setAuthMode(mode) {
  state.authMode = mode;
  authEntrySection.hidden = mode === "hidden";
  if (mode === "hidden") {
    return;
  }

  const isLogin = mode === "login";
  loginForm.hidden = !isLogin;
  registerForm.hidden = isLogin;
  authTitle.textContent = isLogin ? "Sign In" : "Create Access";
  authCopy.textContent = isLogin
    ? "Use an existing account to open the private scanning workspace."
    : "Register a new account first, then switch back to sign in and unlock the scanner workspace.";

  // Clear any stale form feedback when switching modes
  const loginFeedback = document.getElementById("loginFeedback");
  const registerFeedback = document.getElementById("registerFeedback");
  if (loginFeedback) {
    loginFeedback.hidden = true;
    loginFeedback.textContent = "";
  }
  if (registerFeedback) {
    registerFeedback.hidden = true;
    registerFeedback.textContent = "";
  }

  authSwitchButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.switchAuth === mode);
  });

  const targetInput = document.getElementById(isLogin ? "loginUsername" : "registerUsername");
  if (targetInput) {
    targetInput.focus();
  }
  authEntrySection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function resetUserList() {
  userList.innerHTML = '<div class="empty-state">Only administrators can manage accounts.</div>';
}

function resetResults(message = "Run a scan to preview captured pages here.") {
  state.capturedPages = [];
  resultMeta.textContent = "No pages captured yet. Results remain in this container when you switch scanners.";
  pageIndicator.textContent = "0 pages";
  resultStage.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
  exportPngButton.disabled = true;
  exportPdfButton.disabled = true;
}

function setSignedOutState() {
  state.currentUser = null;
  sessionStorage.removeItem(tokenKey);
  state.token = "";
  sessionState.innerHTML = '<span class="status-dot"></span><span>Waiting for sign-in</span>';
  serviceCaption.textContent = "Service offline";
  workspaceRolePill.textContent = "Signed out";
  state.scanners = [];
  state.selectedScannerId = "";
  scannerSelect.innerHTML = '<option value="">Sign in to load shared scanners</option>';
  scannerSelect.disabled = true;
  scannerDetail.innerHTML = '<div class="empty-state">Sign in to view shared scanners.</div>';
  scanSelectedButton.disabled = true;
  userBadge.textContent = "";
  resetResults();
  resetUserList();
  resultPanel.hidden = false;
  workspaceSection.classList.remove("admin-view");
  setAuthMode("hidden");
  syncLayout();
  setBanner("Sign in to load scanner status and start scans.");
  stopPolling();
}

function setSignedInState(user) {
  state.currentUser = user;
  sessionState.innerHTML = `<span class="status-dot"></span><span>Signed in as ${user.username}</span>`;
  userBadge.textContent = user.is_admin
    ? `${user.full_name} · ${user.username} · Administrator`
    : `${user.full_name} · ${user.username}`;
  workspaceRolePill.textContent = user.is_admin ? "Administrator Workspace" : "Operator Workspace";
  // Show role-appropriate sections
  scanControlsSection.hidden = user.is_admin;
  scannerStatusSection.hidden = user.is_admin;
  usersPanel.hidden = !user.is_admin;
  // Admin: hide scan output panel and center operations; operator: show full two-column layout
  resultPanel.hidden = user.is_admin;
  workspaceSection.classList.toggle("admin-view", user.is_admin);

  const workspaceTitle = document.getElementById("workspaceTitle");
  const workspaceCopy = document.getElementById("workspaceCopy");
  if (workspaceTitle) {
    workspaceTitle.textContent = user.is_admin ? "User Management" : "Scanner Operations";
  }
  if (workspaceCopy) {
    workspaceCopy.textContent = user.is_admin
      ? "Delete accounts or change a user's administrator permissions."
      : "Controls and status change based on your account role.";
  }

  setAuthMode("hidden");
  syncLayout();
}

function stopPolling() {
  if (state.pollHandle) {
    window.clearInterval(state.pollHandle);
    state.pollHandle = null;
  }
}

function startPolling() {
  stopPolling();
  state.pollHandle = window.setInterval(() => {
    loadScanners().catch(() => {
      stopPolling();
    });
  }, 5000);
}

function getSelectedScanner() {
  return state.scanners.find((scanner) => scanner.id === state.selectedScannerId) || null;
}

function syncSelectedScanner(scanners) {
  if (!scanners.length) {
    state.selectedScannerId = "";
    return;
  }

  const hasCurrentSelection = scanners.some((scanner) => scanner.id === state.selectedScannerId);
  if (!hasCurrentSelection) {
    state.selectedScannerId = scanners[0].id;
  }
}

function renderSelectedScanner() {
  const scanner = getSelectedScanner();
  if (!scanner) {
    scannerDetail.innerHTML = '<div class="empty-state">No shared TWAIN scanners are available right now.</div>';
    scanSelectedButton.disabled = true;
    return;
  }

  const isLockedByOther = scanner.locked && scanner.locked_by !== state.currentUser.username;
  const availabilityClass = scanner.locked ? "locked" : "free";
  const stateClass = scanner.lock_status || "idle";
  const ownerLine = scanner.locked
    ? `Locked by <strong>${escapeHtml(scanner.locked_by)}</strong> until ${escapeHtml(scanner.lock_expires_at)}`
    : "Available for the next authenticated user.";

  scannerDetail.innerHTML = `
    <article class="scanner-summary-card">
      <div class="scanner-card-header">
        <div>
          <h3>${escapeHtml(scanner.name)}</h3>
          <p class="scanner-meta">ID: ${escapeHtml(scanner.id)}</p>
        </div>
        <span class="lock-chip ${availabilityClass}">${scanner.locked ? "Locked" : "Ready"}</span>
      </div>
      <p class="scanner-meta">${ownerLine}</p>
      <div class="scanner-card-footer">
        <span class="state-chip ${stateClass}">${scanner.lock_status}</span>
        <span class="scanner-meta">Selected scanner</span>
      </div>
    </article>
  `;
  scanSelectedButton.disabled = isLockedByOther;
  scanSelectedButton.textContent = isLockedByOther ? `Locked by ${scanner.locked_by}` : "Scan Selected Scanner";
}

function renderScanners(scanners) {
  state.scanners = scanners;
  syncSelectedScanner(scanners);

  if (!scanners.length) {
    scannerSelect.innerHTML = '<option value="">No scanners available</option>';
    scannerSelect.disabled = true;
    renderSelectedScanner();
    return;
  }

  scannerSelect.disabled = false;
  scannerSelect.innerHTML = scanners.map((scanner) => {
    const selected = scanner.id === state.selectedScannerId ? " selected" : "";
    const lockLabel = scanner.locked ? ` (${scanner.locked_by || "locked"})` : "";
    return `<option value="${escapeHtml(scanner.id)}"${selected}>${escapeHtml(scanner.name)}${lockLabel}</option>`;
  }).join("");
  renderSelectedScanner();
}

function renderResultStage() {
  if (!state.capturedPages.length) {
    resetResults();
    return;
  }

  resultStage.innerHTML = `
    <div class="result-stream">
      ${state.capturedPages.map((page, index) => `
        <figure class="result-card">
          <figcaption class="result-card-header">
            <span class="result-chip">${escapeHtml(page.scannerName)}</span>
            <span class="caption">Page ${index + 1}</span>
          </figcaption>
          <img class="result-image" src="data:${page.imageType};base64,${page.image}" alt="Captured page ${index + 1} from ${escapeHtml(page.scannerName)}" title="Click to view full size">
        </figure>
      `).join("")}
    </div>
  `;
  pageIndicator.textContent = `${state.capturedPages.length} page${state.capturedPages.length === 1 ? "" : "s"}`;
  exportPngButton.disabled = false;
  exportPdfButton.disabled = false;

  // Bind click-to-expand on every thumbnail
  resultStage.querySelectorAll(".result-image").forEach((img) => {
    img.addEventListener("click", () => {
      lightboxImage.src = img.src;
      lightboxImage.alt = img.alt;
      lightbox.hidden = false;
    });
  });
}

function renderResults(scanResult) {
  if (!scanResult.images.length) {
    resultMeta.textContent = `No pages were returned from ${scanResult.scanner.name}. ${state.capturedPages.length} page(s) remain in the output container.`;
    return;
  }

  const appendedPages = scanResult.images.map((image) => ({
    scannerName: scanResult.scanner.name,
    image,
    imageType: scanResult.image_type,
  }));
  state.capturedPages.push(...appendedPages);
  resultMeta.textContent = `${scanResult.page_count} page(s) captured from ${scanResult.scanner.name}. ${state.capturedPages.length} total page(s) are now in the output container.`;
  renderResultStage();
  window.requestAnimationFrame(() => {
    resultStage.scrollTo({ top: resultStage.scrollHeight, behavior: "smooth" });
  });
}

function renderUsers(users) {
  if (!users.length) {
    userList.innerHTML = '<div class="empty-state">No registered users were found.</div>';
    return;
  }

  userList.innerHTML = users.map((user) => {
    const isCurrentUser = user.username === state.currentUser?.username;
    const adminTag = user.is_admin ? '<span class="tag admin-tag">Admin</span>' : '';
    const currentTag = isCurrentUser ? '<span class="tag current-tag">Current Session</span>' : '';
    const deleteDisabled = isCurrentUser ? 'disabled' : '';
    const deleteLabel = isCurrentUser ? 'In Use' : 'Delete';
    const toggleAdminLabel = user.is_admin ? 'Remove Admin' : 'Make Admin';
    const toggleAdminDisabled = isCurrentUser ? 'disabled' : '';
    return `
      <article class="user-row">
        <div class="user-row-main">
          <div>
            <h3>${escapeHtml(user.full_name)}</h3>
            <p class="scanner-meta">${escapeHtml(user.username)} &middot; Created ${escapeHtml(user.created_at)}</p>
          </div>
          <div class="tag-row">
            ${adminTag}
            ${currentTag}
          </div>
        </div>
        <div class="user-row-actions">
          <button class="ghost-button toggle-admin-button" data-username="${escapeHtml(user.username)}" data-make-admin="${user.is_admin ? 'false' : 'true'}" ${toggleAdminDisabled} type="button">${toggleAdminLabel}</button>
          <button class="ghost-button delete-user-button" data-username="${escapeHtml(user.username)}" ${deleteDisabled} type="button">${deleteLabel}</button>
        </div>
      </article>
    `;
  }).join("");

  document.querySelectorAll(".toggle-admin-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const username = button.dataset.username;
      const makeAdmin = button.dataset.makeAdmin === 'true';
      await toggleAdminUser(username, makeAdmin);
    });
  });

  document.querySelectorAll(".delete-user-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const username = button.dataset.username;
      const confirmed = window.confirm(`Delete ${username}? This removes the account and clears any active scanner lock.`);
      if (!confirmed) {
        return;
      }

      try {
        await api(`/api/users/${encodeURIComponent(username)}`, { method: "DELETE" });
        setBanner(`Deleted ${username}.`, "success");
        await loadUsers();
      } catch (error) {
        setBanner(formatError(error), "error");
      }
    });
  });
}

async function toggleAdminUser(username, makeAdmin) {
  try {
    await api(`/api/users/${encodeURIComponent(username)}/admin`, {
      method: "PATCH",
      json: { is_admin: makeAdmin },
    });
    setBanner(
      makeAdmin
        ? `${username} now has administrator access.`
        : `Removed administrator access from ${username}.`,
      "success"
    );
    await loadUsers();
  } catch (error) {
    setBanner(formatError(error), "error");
  }
}

async function loadCurrentUser() {
  const user = await api("/api/auth/me");
  setSignedInState(user);
}

async function loadPublicSetup() {
  try {
    const setup = await api("/api/public/setup");
    state.publicSetup = {
      adminUsernames: setup.admin_usernames || [],
      configuredAdminUsername: setup.configured_admin_username || "",
    };
  } catch {
    state.publicSetup = {
      adminUsernames: [],
      configuredAdminUsername: "",
    };
  }
  renderAdminSummary();
}

async function loadUsers() {
  if (!state.currentUser?.is_admin) {
    resetUserList();
    return;
  }

  const users = await api("/api/users");
  renderUsers(users);
}

async function loadScanners() {
  const payload = await api("/api/scanners");
  const server = payload.server || {};
  serviceCaption.textContent = server.compatible === false
    ? "Service reachable but incompatible"
    : `Host ${payload.service_host}`;
  renderScanners(payload.scanners || []);
}

async function signIn(username, password) {
  const formPayload = new URLSearchParams();
  formPayload.set("username", username);
  formPayload.set("password", password);

  const token = await api("/api/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: formPayload,
  });
  state.token = token.access_token;
  sessionStorage.setItem(tokenKey, state.token);
  await loadCurrentUser();
  await Promise.all([
    loadScanners(),
    loadUsers(),
  ]);
  startPolling();
  setBanner("Authentication succeeded. Shared scanner status is live.", "success");
}

async function registerUser(event) {
  event.preventDefault();
  const payload = {
    username: document.getElementById("registerUsername").value.trim(),
    full_name: document.getElementById("registerFullName").value.trim(),
    password: document.getElementById("registerPassword").value,
  };

  const registerFeedback = document.getElementById("registerFeedback");

  try {
    setSubmitBusy(registerSubmitButton, true, "Registering...", "Register");
    await api("/api/auth/register", { method: "POST", json: payload });
    registerForm.reset();
    setAuthMode("hidden");
    // Auto sign-in after successful registration
    await signIn(payload.username, payload.password);
    await loadPublicSetup();
  } catch (error) {
    setBanner(formatError(error), "error");
    if (registerFeedback) {
      registerFeedback.textContent = formatError(error);
      registerFeedback.dataset.tone = "error";
      registerFeedback.hidden = false;
    }
  } finally {
    setSubmitBusy(registerSubmitButton, false, "Registering...", "Register");
  }
}

async function loginUser(event) {
  event.preventDefault();
  const username = document.getElementById("loginUsername").value.trim();
  const password = document.getElementById("loginPassword").value;

  try {
    setSubmitBusy(loginSubmitButton, true, "Signing In...", "Get Access Token");
    await signIn(username, password);
    loginForm.reset();
  } catch (error) {
    setBanner(formatError(error), "error");
  } finally {
    setSubmitBusy(loginSubmitButton, false, "Signing In...", "Get Access Token");
  }
}

async function runScan(scannerId, scannerName) {
  const payload = {
    resolution: Number(document.getElementById("resolutionInput").value),
    pixel_type: Number(document.getElementById("pixelTypeInput").value),
    image_type: document.getElementById("imageTypeInput").value,
    feeder_enabled: document.getElementById("feederInput").checked,
    duplex_enabled: document.getElementById("duplexInput").checked,
    show_ui: document.getElementById("showUiInput").checked,
  };

  try {
    setBanner(`Locking ${scannerName} and waiting for scanned pages...`);
    const result = await api(`/api/scanners/${scannerId}/scan`, {
      method: "POST",
      json: payload,
    });
    renderResults(result);
    setBanner(`Completed ${result.page_count} page(s) on ${scannerName}.`, "success");
  } catch (error) {
    setBanner(formatError(error), "error");
  } finally {
    await loadScanners().catch(() => {});
  }
}

function buildExportFileStem() {
  const baseName = getSelectedScanner()?.name || state.capturedPages.at(-1)?.scannerName || "scan-output";
  const timestamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-");
  return `${baseName}-${timestamp}`;
}

function getFilename(response, fallbackName) {
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallbackName;
}

async function exportScan(format) {
  if (!state.capturedPages.length) {
    return;
  }

  try {
    setBanner(`Preparing ${format.toUpperCase()} export...`);
    const response = await fetch(`/api/exports/${format}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${state.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        images: state.capturedPages.map((page) => page.image),
        image_type: state.capturedPages.at(-1)?.imageType || "image/png",
        file_stem: buildExportFileStem(),
      }),
    });

    if (!response.ok) {
      const contentType = response.headers.get("content-type") || "";
      const payload = contentType.includes("application/json")
        ? await response.json()
        : await response.text();
      throw payload;
    }

    const blob = await response.blob();
    const defaultName = format === "pdf" ? "scan-output.pdf" : "scan-output.png";
    const filename = getFilename(response, defaultName);
    const objectUrl = URL.createObjectURL(blob);
    const downloadLink = document.createElement("a");
    downloadLink.href = objectUrl;
    downloadLink.download = filename;
    document.body.append(downloadLink);
    downloadLink.click();
    downloadLink.remove();
    URL.revokeObjectURL(objectUrl);
    setBanner(`Downloaded ${filename}.`, "success");
  } catch (error) {
    setBanner(formatError(error), "error");
  }
}

async function restoreSession() {
  await loadPublicSetup();

  if (!state.token) {
    setSignedOutState();
    return;
  }

  try {
    await loadCurrentUser();
    await Promise.all([
      loadScanners(),
      loadUsers(),
    ]);
    startPolling();
    setBanner("Scanner status refreshed.");
  } catch (error) {
    setSignedOutState();
    const message = formatError(error);
    if (message === "Could not validate credentials.") {
      setBanner("Session expired. Sign in again to reopen the scanner workspace.");
      return;
    }
    setBanner(message, "error");
  }
}

registerForm.addEventListener("submit", registerUser);
loginForm.addEventListener("submit", loginUser);
scannerSelect.addEventListener("change", () => {
  state.selectedScannerId = scannerSelect.value;
  renderSelectedScanner();
});
authTriggerButtons.forEach((button) => {
  button.addEventListener("click", () => setAuthMode(button.dataset.openAuth));
});
authSwitchButtons.forEach((button) => {
  button.addEventListener("click", () => setAuthMode(button.dataset.switchAuth));
});
closeAuthPanelButton.addEventListener("click", () => setAuthMode("hidden"));
refreshButton.addEventListener("click", () => {
  loadScanners().catch((error) => setBanner(formatError(error), "error"));
});
refreshUsersButton.addEventListener("click", () => {
  loadUsers().catch((error) => setBanner(formatError(error), "error"));
});
scanSelectedButton.addEventListener("click", () => {
  const scanner = getSelectedScanner();
  if (!scanner) {
    return;
  }
  runScan(scanner.id, scanner.name);
});
logoutButton.addEventListener("click", () => {
  setSignedOutState();
});
exportPngButton.addEventListener("click", () => {
  exportScan("png");
});
exportPdfButton.addEventListener("click", () => {
  exportScan("pdf");
});

// Lightbox close
function closeLightbox() {
  lightbox.hidden = true;
  lightboxImage.src = "";
}
lightboxClose.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) {
    closeLightbox();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !lightbox.hidden) {
    closeLightbox();
  }
});

restoreSession();