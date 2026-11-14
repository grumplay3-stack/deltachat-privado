(() => {
  const ALLOWED_EMOJIS = ["👍", "❤️", "😂", "🔥", "😮"];
  const COMPOSER_EMOJIS = ["😀", "😂", "😎", "🔥", "❤️", "👍", "👀", "💀", "👾", "🎉", "😭", "🤡"];
  const DAYS_ES = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];

  const state = {
    token: localStorage.getItem("dc_token") || "",
    user: null,
    channels: [],
    users: [],
    onlineIds: new Set(),
    activeMode: "channel", // "channel" | "dm"
    activeChannelId: null,
    activeDmUserId: null,
    messages: [],
    replyTo: null, // { id, alias, snippet }
    pendingFile: null,
    ws: null,
    reconnectTimer: null,
    searchTimeout: null,
    hdTimerInterval: null,
  };

  // --- DOM ELEMENTS ---
  const $ = (id) => document.getElementById(id);

  const authScreen = $("auth-screen");
  const chatScreen = $("chat-screen");
  const authError = $("auth-error");
  const authSuccess = $("auth-success");

  const topBoardLinks = $("top-board-links");
  const channelsList = $("channels-list");
  const usersList = $("users-list");
  const activeRoomTitle = $("active-room-title");
  const activeRoomDesc = $("active-room-desc");
  const messagesContainer = $("messages-container");
  const searchInput = $("search-input");

  const messageInput = $("message-input");
  const fileInput = $("file-input");
  const chkHdMode = $("chk-hd-mode");
  const imagePreviewBar = $("image-preview-bar");
  const imgPrepThumb = $("img-prep-thumb");
  const imgPrepName = $("img-prep-name");
  const replyBar = $("reply-bar");
  const replyPreviewText = $("reply-preview-text");
  const emojiPopover = $("emoji-popover");

  const wsStatus = $("ws-status");
  const myAliasLabel = $("my-alias-label");
  const btnOpenAdmin = $("btn-open-admin");
  const sidebar = $("sidebar");
  const sidebarBackdrop = $("sidebar-backdrop");

  // --- HELPERS ---
  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function format4chanDate(unixSec) {
    const d = new Date(unixSec * 1000);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const yy = String(d.getFullYear()).slice(-2);
    const day = DAYS_ES[d.getDay()];
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    return `${mm}/${dd}/${yy}(${day})${hh}:${mi}:${ss}`;
  }

  function formatPostContent(rawText) {
    const escaped = escapeHtml(rawText || "");
    const lines = escaped.split("\n").map((line) => {
      let formatted = line.replace(
        /&gt;&gt;(\d+)/g,
        '<a class="quotelink" data-ref="$1">&gt;&gt;$1</a>'
      );
      if (line.startsWith("&gt;") && !line.startsWith("&gt;&gt;")) {
        return `<span class="greentext">${formatted}</span>`;
      }
      return formatted;
    });
    return lines.join("<br>");
  }

  async function apiFetch(url, options = {}) {
    const headers = options.headers ? { ...options.headers } : {};
    if (state.token) {
      headers["Authorization"] = `Bearer ${state.token}`;
    }
    const resp = await fetch(url, { ...options, headers });
    if (resp.status === 401 || resp.status === 403) {
      let detail = "Acceso denegado.";
      try {
        const data = await resp.json();
        detail = data.detail || detail;
      } catch {}
      if (resp.status === 401 && state.token) {
        forceLogout(detail);
      }
      throw new Error(detail);
    }
    if (!resp.ok) {
      let detail = `Error HTTP ${resp.status}`;
      try {
        const data = await resp.json();
        detail = data.detail || detail;
      } catch {}
      throw new Error(detail);
    }
    return resp;
  }

  async function downloadWithAuth(url, filename) {
    const resp = await apiFetch(url);
    const blob = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objUrl);
  }

  function showAuthAlert(msg, isSuccess = false) {
    authError.classList.add("hidden");
    authSuccess.classList.add("hidden");
    const box = isSuccess ? authSuccess : authError;
    box.textContent = msg;
    box.classList.remove("hidden");
  }

  function clearAuthAlerts() {
    authError.classList.add("hidden");
    authSuccess.classList.add("hidden");
  }

  function forceLogout(reasonMsg = "") {
    localStorage.removeItem("dc_token");
    state.token = "";
    state.user = null;
    if (state.ws) {
      state.ws.close();
      state.ws = null;
    }
    clearInterval(state.hdTimerInterval);
    chatScreen.classList.add("hidden");
    authScreen.classList.remove("hidden");
    if (reasonMsg) {
      showAuthAlert(reasonMsg, false);
    }
  }

  // --- AUTH TABS & FORMS ---
  document.querySelectorAll(".auth-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      clearAuthAlerts();
      document.querySelectorAll(".auth-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      $("login-form").classList.toggle("hidden", tab !== "login");
      $("register-form").classList.toggle("hidden", tab !== "register");
      $("recover-form").classList.toggle("hidden", tab !== "recover");
    });
  });

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAuthAlerts();
    try {
      const resp = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("login-username").value.trim(),
          password: $("login-password").value,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        showAuthAlert(data.detail || "No se pudo iniciar sesión.");
        return;
      }
      state.token = data.token;
      localStorage.setItem("dc_token", state.token);
      await bootChat();
    } catch (err) {
      showAuthAlert("Error de conexión con el servidor.");
    }
  });

  $("register-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAuthAlerts();
    try {
      const resp = await fetch("/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("reg-username").value.trim(),
          alias: $("reg-alias").value.trim(),
          password: $("reg-password").value,
          avatar_color: $("reg-color").value,
          security_question: $("reg-question").value.trim(),
          security_answer: $("reg-answer").value.trim(),
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        showAuthAlert(data.detail || "No se pudo registrar.");
        return;
      }
      state.token = data.token;
      localStorage.setItem("dc_token", state.token);
      await bootChat();
    } catch (err) {
      showAuthAlert("Error de conexión al registrar.");
    }
  });

  $("btn-fetch-question").addEventListener("click", async () => {
    clearAuthAlerts();
    const uname = $("rec-username").value.trim();
    if (!uname) return;
    try {
      const resp = await fetch(`/api/recover-question/${encodeURIComponent(uname)}`);
      const data = await resp.json();
      if (!resp.ok) {
        showAuthAlert(data.detail || "No se encontró el usuario.");
        return;
      }
      $("rec-question-text").textContent = data.security_question;
      $("recover-step2").classList.remove("hidden");
    } catch {
      showAuthAlert("Error al buscar usuario.");
    }
  });

  $("recover-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAuthAlerts();
    try {
      const resp = await fetch("/api/recover-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("rec-username").value.trim(),
          security_answer: $("rec-answer").value.trim(),
          new_password: $("rec-new-password").value,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        showAuthAlert(data.detail || "Respuesta incorrecta.");
        return;
      }
      showAuthAlert("Contraseña actualizada. Ya puedes iniciar sesión.", true);
      $("recover-step2").classList.add("hidden");
      $("recover-form").reset();
    } catch {
      showAuthAlert("Error al cambiar la contraseña.");
    }
  });

  // --- BOOT CHAT ---
  async function bootChat() {
    try {
      const meResp = await apiFetch("/api/me");
      state.user = await meResp.json();
      authScreen.classList.add("hidden");
      chatScreen.classList.remove("hidden");

      myAliasLabel.textContent = state.user.alias;
      if (state.user.role === "admin") {
        btnOpenAdmin.classList.remove("hidden");
      } else {
        btnOpenAdmin.classList.add("hidden");
      }

      await Promise.all([loadChannels(), loadUsers()]);
      if (state.channels.length > 0) {
        await selectChannel(state.channels[0].id);
      }

      connectWebSocket();
      startHdCountdownLoop();
    } catch (err) {
      forceLogout(err.message);
    }
  }

  async function loadChannels() {
    const resp = await apiFetch("/api/channels");
    const data = await resp.json();
    state.channels = data.channels || [];
    renderNavigation();
  }

  async function loadUsers() {
    const resp = await apiFetch("/api/users");
    const data = await resp.json();
    state.users = data.users || [];
    state.onlineIds = new Set(state.users.filter((u) => u.online).map((u) => u.id));
    renderNavigation();
  }

  function renderNavigation() {
    // Top board bar
    topBoardLinks.innerHTML = "";
    state.channels.forEach((ch) => {
      const span = document.createElement("span");
      span.className = "board-link" + (state.activeMode === "channel" && state.activeChannelId === ch.id ? " active" : "");
      span.textContent = `/${ch.slug}/`;
      span.title = ch.name;
      span.addEventListener("click", () => selectChannel(ch.id));
      topBoardLinks.appendChild(span);
    });

    // Sidebar channels
    channelsList.innerHTML = "";
    state.channels.forEach((ch) => {
      const li = document.createElement("li");
      li.className = "nav-item" + (state.activeMode === "channel" && state.activeChannelId === ch.id ? " active" : "");
      li.innerHTML = `<div class="nav-item-main"><span class="mono-sm">/${escapeHtml(ch.slug)}/</span> <span>${escapeHtml(ch.name)}</span></div>`;
      li.addEventListener("click", () => {
        selectChannel(ch.id);
        closeMobileSidebar();
      });
      channelsList.appendChild(li);
    });

    // Sidebar users (DMs)
    const isAdmin = state.user && state.user.role === "admin";
    usersList.innerHTML = "";
    state.users.forEach((u) => {
      if (state.user && u.id === state.user.id) return;
      const isOnline = state.onlineIds.has(u.id);
      const li = document.createElement("li");
      li.className = "nav-item" + (state.activeMode === "dm" && state.activeDmUserId === u.id ? " active" : "");
      const adminUserTag = isAdmin && u.username ? ` <span class="mono-sm">(@${escapeHtml(u.username)})</span>` : "";
      li.innerHTML = `
        <div class="nav-item-main">
          <span class="status-dot ${isOnline ? "online" : ""}"></span>
          <span style="color:${escapeHtml(u.avatar_color)}">${escapeHtml(u.alias)}</span>${adminUserTag}
        </div>
      `;
      li.addEventListener("click", () => {
        selectDm(u.id);
        closeMobileSidebar();
      });
      usersList.appendChild(li);
    });
  }

  async function selectChannel(channelId) {
    state.activeMode = "channel";
    state.activeChannelId = channelId;
    state.activeDmUserId = null;
    clearReply();
    const ch = state.channels.find((c) => c.id === channelId);
    if (ch) {
      activeRoomTitle.textContent = `/${ch.slug}/ - ${ch.name}`;
      activeRoomDesc.textContent = ch.description;
    }
    renderNavigation();
    await loadCurrentMessages();
  }

  async function selectDm(friendId) {
    state.activeMode = "dm";
    state.activeDmUserId = friendId;
    state.activeChannelId = null;
    clearReply();
    const friend = state.users.find((u) => u.id === friendId);
    if (friend) {
      const extra = state.user.role === "admin" && friend.username ? ` (@${friend.username})` : "";
      activeRoomTitle.textContent = `DM Privado: ${friend.alias}${extra}`;
      activeRoomDesc.textContent = "Conversación directa 1 a 1";
    }
    renderNavigation();
    await loadCurrentMessages();
  }

  async function loadCurrentMessages() {
    const query = searchInput.value.trim();
    const qs = query ? `?search=${encodeURIComponent(query)}` : "";
    let url = "";
    if (state.activeMode === "channel" && state.activeChannelId) {
      url = `/api/messages/${state.activeChannelId}${qs}`;
    } else if (state.activeMode === "dm" && state.activeDmUserId) {
      url = `/api/dms/${state.activeDmUserId}${qs}`;
    } else {
      return;
    }
    const resp = await apiFetch(url);
    const data = await resp.json();
    state.messages = data.messages || [];
    renderMessages(true);
  }

  function renderMessages(scrollBottom = false) {
    messagesContainer.innerHTML = "";
    if (state.messages.length === 0) {
      const empty = document.createElement("div");
      empty.className = "system-notice";
      empty.textContent = ">No hay mensajes todavía en esta sala. ¡Sé el primero en postear!";
      messagesContainer.appendChild(empty);
      return;
    }
    state.messages.forEach((m) => {
      messagesContainer.appendChild(buildPostElement(m));
    });
    if (scrollBottom) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  }

  function buildPostElement(m) {
    if (m.is_system) {
      const div = document.createElement("div");
      div.className = "system-notice";
      div.id = `post-${m.id}`;
      div.innerHTML = `[${format4chanDate(m.created_at)}] ${formatPostContent(m.content)}`;
      return div;
    }

    const isAdminViewer = state.user && state.user.role === "admin";
    const canDelete = isAdminViewer || (state.user && m.user_id === state.user.id);
    const canBan = isAdminViewer && m.user_id && m.user_id !== state.user.id && m.author_role !== "admin";

    const article = document.createElement("article");
    article.className = "post";
    article.id = `post-${m.id}`;

    // Header
    const adminUserInfo = isAdminViewer && m.username
      ? `<span class="post-real-user">(@${escapeHtml(m.username)}${m.user_ip ? ` · ${escapeHtml(m.user_ip)}` : ""})</span>`
      : "";
    const modBadge = m.author_role === "admin" ? `<span class="post-admin-badge">## Admin</span>` : "";

    let actionsHtml = `<button type="button" class="btn-mini btn-quote" title="Citar mensaje">[Responder]</button>`;
    if (canDelete) {
      actionsHtml += `<button type="button" class="btn-mini btn-delete" title="Borrar mensaje">[Borrar]</button>`;
    }
    if (canBan) {
      actionsHtml += `<button type="button" class="btn-mini btn-ban-post" style="color:var(--admin-red)" title="Banear/Suspender">[Ban]</button>`;
    }

    // Reply quote preview
    let replyHtml = "";
    if (m.reply_preview) {
      const snippet = (m.reply_preview.content || m.reply_preview.image_name || "").slice(0, 70);
      replyHtml = `<div class="post-reply-ref" data-ref="${m.reply_preview.id}">&gt;&gt;${m.reply_preview.id} (${escapeHtml(m.reply_preview.alias)}): ${escapeHtml(snippet)}</div>`;
    }

    // Image / File section
    let fileHeaderHtml = "";
    let thumbHtml = "";
    if (m.is_hd && m.is_expired) {
      fileHeaderHtml = `<div class="hd-expired-box">🔒 El plazo de visualización HD de 10 minutos ha acabado — archivo eliminado para optimización.</div>`;
    } else if (m.image_url) {
      const hdBadge = m.is_hd && m.expires_at
        ? `<span class="hd-badge" data-hd-expires="${m.expires_at}">⏳ HD: --:--</span>`
        : "";
      fileHeaderHtml = `
        <div class="post-file-info">
          <span>File: <a href="${escapeHtml(m.image_url)}" download="${escapeHtml(m.image_name || "imagen.webp")}">${escapeHtml(m.image_name || "imagen.webp")}</a> (${m.image_size_kb || "?"} KB, ${escapeHtml(m.image_dims || "")})</span>
          <a href="${escapeHtml(m.image_url)}" download="${escapeHtml(m.image_name || "imagen.webp")}" class="btn-mini">[⬇ Descargar]</a>
          ${hdBadge}
        </div>
      `;
      thumbHtml = `<img src="${escapeHtml(m.image_url)}" alt="${escapeHtml(m.image_name || "Imagen")}" class="post-thumb" loading="lazy" />`;
    }

    // Reactions
    const reactionsMap = new Map();
    (m.reactions || []).forEach((r) => reactionsMap.set(r.emoji, r));
    const reactionButtonsHtml = ALLOWED_EMOJIS.map((emoji) => {
      const r = reactionsMap.get(emoji);
      const count = r ? r.count : 0;
      const isActive = r && r.me ? "active" : "";
      return `<button type="button" class="react-btn ${isActive}" data-emoji="${emoji}">${emoji}${count > 0 ? ` <span>${count}</span>` : ""}</button>`;
    }).join("");

    article.innerHTML = `
      <div class="post-header">
        <span class="post-alias" style="color:${escapeHtml(m.avatar_color)}">${escapeHtml(m.alias)}</span>
        ${modBadge}
        ${adminUserInfo}
        <span class="post-date">${format4chanDate(m.created_at)}</span>
        <span class="post-no" title="Click para citar">No.${m.id}</span>
        <div class="post-actions">${actionsHtml}</div>
      </div>
      ${replyHtml}
      ${fileHeaderHtml}
      <div class="post-body-wrap">
        ${thumbHtml}
        ${m.content ? `<div class="post-content">${formatPostContent(m.content)}</div>` : ""}
      </div>
      <div class="post-reactions">${reactionButtonsHtml}</div>
    `;

    // Event bindings inside post
    const postNoEl = article.querySelector(".post-no");
    const quoteBtn = article.querySelector(".btn-quote");
    [postNoEl, quoteBtn].forEach((el) => {
      if (el) {
        el.addEventListener("click", () => {
          setReply(m);
        });
      }
    });

    const delBtn = article.querySelector(".btn-delete");
    if (delBtn) {
      delBtn.addEventListener("click", async () => {
        if (!confirm("¿Seguro que deseas borrar este mensaje?")) return;
        const endpoint = state.activeMode === "channel" ? `/api/messages/${m.id}` : `/api/dms/${m.id}`;
        await apiFetch(endpoint, { method: "DELETE" });
      });
    }

    const banBtn = article.querySelector(".btn-ban-post");
    if (banBtn) {
      banBtn.addEventListener("click", () => {
        openBanUserModal(m.user_id, m.alias, m.username, m.user_ip);
      });
    }

    const thumbEl = article.querySelector(".post-thumb");
    if (thumbEl) {
      thumbEl.addEventListener("click", () => {
        openLightbox(m.image_url, m.image_name, m.image_size_kb, m.image_dims);
      });
    }

    article.querySelectorAll("[data-ref]").forEach((refEl) => {
      refEl.addEventListener("click", () => {
        scrollToPost(refEl.dataset.ref);
      });
    });

    article.querySelectorAll(".react-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        sendReaction(m.id, btn.dataset.emoji);
      });
    });

    return article;
  }

  function scrollToPost(postId) {
    const target = document.getElementById(`post-${postId}`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("highlighted");
    setTimeout(() => target.classList.remove("highlighted"), 1600);
  }

  function setReply(m) {
    state.replyTo = {
      id: m.id,
      alias: m.alias,
      snippet: (m.content || m.image_name || "").slice(0, 45),
    };
    replyPreviewText.textContent = `>>${m.id} (${m.alias})`;
    replyBar.classList.remove("hidden");
    const prefix = `>>${m.id}\n`;
    if (!messageInput.value.includes(`>>${m.id}`)) {
      messageInput.value = prefix + messageInput.value;
    }
    messageInput.focus();
  }

  function clearReply() {
    state.replyTo = null;
    replyBar.classList.add("hidden");
  }

  $("btn-cancel-reply").addEventListener("click", clearReply);

  // --- HD COUNTDOWN UPDATER ---
  function startHdCountdownLoop() {
    clearInterval(state.hdTimerInterval);
    const updateBadges = () => {
      const now = Date.now() / 1000;
      document.querySelectorAll("[data-hd-expires]").forEach((el) => {
        const exp = parseFloat(el.dataset.hdExpires);
        const rem = Math.max(0, Math.floor(exp - now));
        if (rem <= 0) {
          el.textContent = "⏳ HD: Finalizando...";
        } else {
          const mm = String(Math.floor(rem / 60)).padStart(2, "0");
          const ss = String(rem % 60).padStart(2, "0");
          el.textContent = `⏳ HD: ${mm}:${ss}`;
        }
      });
    };
    updateBadges();
    state.hdTimerInterval = setInterval(updateBadges, 1000);
  }

  // --- COMPOSER, IMAGES & EMOJIS ---
  COMPOSER_EMOJIS.forEach((em) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "emoji-pick";
    b.textContent = em;
    b.addEventListener("click", () => {
      messageInput.value += em;
      emojiPopover.classList.add("hidden");
      messageInput.focus();
    });
    emojiPopover.appendChild(b);
  });

  $("btn-emoji-picker").addEventListener("click", () => {
    emojiPopover.classList.toggle("hidden");
  });

  $("btn-attach").addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      alert("Solo se permiten archivos de imagen.");
      fileInput.value = "";
      return;
    }
    state.pendingFile = file;
    imgPrepName.textContent = `${file.name} (${Math.round(file.size / 1024)} KB)`;
    const reader = new FileReader();
    reader.onload = (ev) => {
      imgPrepThumb.src = ev.target.result;
      imagePreviewBar.classList.remove("hidden");
    };
    reader.readAsDataURL(file);
  });

  $("btn-cancel-image").addEventListener("click", clearPendingImage);

  function clearPendingImage() {
    state.pendingFile = null;
    fileInput.value = "";
    chkHdMode.checked = false;
    imagePreviewBar.classList.add("hidden");
  }

  async function sendMessage() {
    const content = messageInput.value.trim();
    if (!content && !state.pendingFile) return;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      alert("Reconectando al servidor, intenta en un segundo...");
      return;
    }

    let uploadedMeta = null;
    if (state.pendingFile) {
      const isHd = chkHdMode.checked;
      const maxBytes = isHd ? 15 * 1024 * 1024 : 5 * 1024 * 1024;
      if (state.pendingFile.size > maxBytes) {
        alert(`La imagen supera el límite de ${isHd ? 15 : 5} MB.`);
        return;
      }
      const fd = new FormData();
      fd.append("file", state.pendingFile);
      fd.append("is_hd", isHd ? "true" : "false");
      try {
        const resp = await apiFetch("/api/upload", { method: "POST", body: fd });
        uploadedMeta = await resp.json();
      } catch (err) {
        alert(err.message || "Error al subir la imagen.");
        return;
      }
    }

    const payload = {
      content,
      image: uploadedMeta,
      reply_to_id: state.replyTo ? state.replyTo.id : null,
    };

    if (state.activeMode === "channel") {
      payload.action = "send_channel_message";
      payload.channel_id = state.activeChannelId;
    } else {
      payload.action = "send_dm";
      payload.receiver_id = state.activeDmUserId;
    }

    state.ws.send(JSON.stringify(payload));
    messageInput.value = "";
    clearPendingImage();
    clearReply();
  }

  $("btn-send").addEventListener("click", sendMessage);
  messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  function sendReaction(targetId, emoji) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    state.ws.send(
      JSON.stringify({
        action: "toggle_reaction",
        target_type: state.activeMode === "channel" ? "channel" : "dm",
        target_id: targetId,
        emoji,
      })
    );
  }

  // --- SEARCH ---
  searchInput.addEventListener("input", () => {
    clearTimeout(state.searchTimeout);
    state.searchTimeout = setTimeout(() => {
      loadCurrentMessages();
    }, 260);
  });

  // --- WEBSOCKET ---
  function connectWebSocket() {
    if (!state.token) return;
    if (state.ws) {
      state.ws.close();
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(state.token)}`);
    state.ws = ws;

    ws.onopen = () => {
      wsStatus.className = "conn-dot online";
      wsStatus.title = "Conectado en vivo";
    };

    ws.onclose = () => {
      wsStatus.className = "conn-dot offline";
      wsStatus.title = "Desconectado — reconectando...";
      if (state.token) {
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = setTimeout(connectWebSocket, 3000);
      }
    };

    ws.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === "banned") {
        forceLogout(data.reason || "Has sido baneado del chat.");
        return;
      }

      if (data.type === "presence") {
        state.onlineIds = new Set(data.online_user_ids || []);
        renderNavigation();
        return;
      }

      if (data.type === "users_updated") {
        loadUsers();
        return;
      }

      if (data.type === "alias_updated") {
        if (state.user && data.user_id === state.user.id) {
          state.user.alias = data.alias;
          state.user.avatar_color = data.avatar_color;
          myAliasLabel.textContent = data.alias;
        }
        loadUsers();
        loadCurrentMessages();
        return;
      }

      if (data.type === "system_notice") {
        if (state.activeMode === "channel" && !searchInput.value.trim()) {
          const noticeEl = document.createElement("div");
          noticeEl.className = "system-notice";
          noticeEl.textContent = `[${format4chanDate(data.created_at)}] ${data.text}`;
          messagesContainer.appendChild(noticeEl);
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        return;
      }

      if (data.type === "new_channel_message") {
        const msg = data.message;
        if (state.activeMode === "channel" && state.activeChannelId === msg.channel_id && !searchInput.value.trim()) {
          state.messages.push(msg);
          // remove empty notice if first
          if (state.messages.length === 1) messagesContainer.innerHTML = "";
          messagesContainer.appendChild(buildPostElement(msg));
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        return;
      }

      if (data.type === "new_dm") {
        const dm = data.message;
        const otherId = dm.sender_id === state.user.id ? dm.receiver_id : dm.sender_id;
        if (state.activeMode === "dm" && state.activeDmUserId === otherId && !searchInput.value.trim()) {
          state.messages.push(dm);
          if (state.messages.length === 1) messagesContainer.innerHTML = "";
          messagesContainer.appendChild(buildPostElement(dm));
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        return;
      }

      if (data.type === "message_deleted") {
        const expectedType = state.activeMode === "channel" ? "channel" : "dm";
        if (data.target_type === expectedType) {
          state.messages = state.messages.filter((m) => m.id !== data.id);
          const el = document.getElementById(`post-${data.id}`);
          if (el) el.remove();
        }
        return;
      }

      if (data.type === "reaction_updated") {
        const expectedType = state.activeMode === "channel" ? "channel" : "dm";
        if (data.target_type === expectedType) {
          const msgObj = state.messages.find((m) => m.id === data.target_id);
          if (msgObj) {
            const oldMeMap = new Map((msgObj.reactions || []).map((r) => [r.emoji, r.me]));
            msgObj.reactions = (data.reactions || []).map((r) => {
              let me = Boolean(oldMeMap.get(r.emoji));
              if (data.actor_id === state.user.id) {
                // toggle accurate me state based on count presence or refresh
              }
              return { emoji: r.emoji, count: r.count, me };
            });
            // Re-fetch current room reactions cleanly if actor is self or update UI directly
            const postEl = document.getElementById(`post-${data.target_id}`);
            if (postEl) {
              const bar = postEl.querySelector(".post-reactions");
              if (bar) {
                const map = new Map(msgObj.reactions.map((r) => [r.emoji, r]));
                bar.querySelectorAll(".react-btn").forEach((btn) => {
                  const em = btn.dataset.emoji;
                  const info = map.get(em);
                  const cnt = info ? info.count : 0;
                  if (data.actor_id === state.user.id && btn.dataset.clicked === "1") {
                    btn.classList.toggle("active");
                    delete btn.dataset.clicked;
                  }
                  btn.innerHTML = `${em}${cnt > 0 ? ` <span>${cnt}</span>` : ""}`;
                });
              }
            }
          }
        }
        return;
      }

      if (data.type === "hd_expired") {
        const expectedType = state.activeMode === "channel" ? "channel" : "dm";
        if (data.target_type === expectedType) {
          const msgObj = state.messages.find((m) => m.id === data.id);
          if (msgObj) {
            msgObj.is_expired = true;
            msgObj.image_url = null;
            const oldEl = document.getElementById(`post-${data.id}`);
            if (oldEl) {
              oldEl.replaceWith(buildPostElement(msgObj));
            }
          }
        }
        return;
      }

      if (data.type === "chat_reset") {
        loadCurrentMessages();
      }
    };
  }

  // Mark clicked reaction button so self-toggle highlights accurately
  messagesContainer.addEventListener("click", (e) => {
    const btn = e.target.closest(".react-btn");
    if (btn) {
      btn.dataset.clicked = "1";
    }
  });

  // --- ALIAS MODAL ---
  $("btn-open-alias").addEventListener("click", () => {
    $("new-alias-input").value = state.user.alias;
    $("new-color-input").value = state.user.avatar_color;
    $("alias-modal").classList.remove("hidden");
  });

  $("alias-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await apiFetch("/api/profile/alias", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alias: $("new-alias-input").value.trim(),
          avatar_color: $("new-color-input").value,
        }),
      });
      $("alias-modal").classList.add("hidden");
    } catch (err) {
      alert(err.message);
    }
  });

  // --- ADMIN PANEL ---
  btnOpenAdmin.addEventListener("click", async () => {
    $("admin-modal").classList.remove("hidden");
    await refreshAdminData();
  });

  document.querySelectorAll(".admin-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".admin-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.dataset.atab;
      document.querySelectorAll(".admin-pane").forEach((p) => p.classList.add("hidden"));
      $(`atab-${target}`).classList.remove("hidden");
    });
  });

  async function refreshAdminData() {
    await Promise.all([loadAdminUsers(), loadAdminBans(), loadAdminBackups()]);
  }

  async function loadAdminUsers() {
    await loadUsers();
    const list = $("admin-users-list");
    list.innerHTML = "";
    state.users.forEach((u) => {
      const row = document.createElement("div");
      row.className = "admin-row";
      const isSelf = u.role === "admin";
      row.innerHTML = `
        <div>
          <strong style="color:${escapeHtml(u.avatar_color)}">${escapeHtml(u.alias)}</strong>
          <span class="mono-sm">(@${escapeHtml(u.username || "")})</span>
          ${isSelf ? `<span class="post-admin-badge">## Admin</span>` : ""}
          <div class="mono-sm">IP: ${escapeHtml(u.last_ip || "N/A")} · ${u.online ? "🟢 En línea" : "⚫ Offline"}</div>
        </div>
        <div class="admin-row-actions">
          ${!isSelf ? `<button type="button" class="btn-danger btn-ban-user">Ban / Suspender</button>` : ""}
          ${!isSelf ? `<button type="button" class="btn-secondary btn-reset-pw">Cambiar Clave</button>` : ""}
        </div>
      `;
      const banBtn = row.querySelector(".btn-ban-user");
      if (banBtn) {
        banBtn.addEventListener("click", () => {
          openBanUserModal(u.id, u.alias, u.username, u.last_ip);
        });
      }
      const pwBtn = row.querySelector(".btn-reset-pw");
      if (pwBtn) {
        pwBtn.addEventListener("click", async () => {
          const newPw = prompt(`Nueva contraseña para ${u.alias} (@${u.username}):`);
          if (!newPw || newPw.length < 4) return;
          await apiFetch("/api/admin/reset-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: u.id, new_password: newPw }),
          });
          alert("Contraseña actualizada.");
        });
      }
      list.appendChild(row);
    });
  }

  async function loadAdminBans() {
    const resp = await apiFetch("/api/admin/bans");
    const data = await resp.json();
    const list = $("admin-bans-list");
    list.innerHTML = "";
    const bans = data.bans || [];
    if (bans.length === 0) {
      list.innerHTML = `<p class="mono-sm">No hay baneos ni suspensiones activas.</p>`;
      return;
    }
    bans.forEach((b) => {
      const row = document.createElement("div");
      row.className = "admin-row";
      let timeText = "PERMANENTE";
      if (b.ban_type === "temp" && b.expires_at) {
        const remMins = Math.max(1, Math.ceil((b.expires_at - Date.now() / 1000) / 60));
        timeText = `Temporal (${remMins} min restantes)`;
      }
      row.innerHTML = `
        <div>
          <strong>${escapeHtml(b.username || "Solo IP")}</strong> · <span class="mono-sm">IP: ${escapeHtml(b.ip_address || "N/A")}</span>
          <div class="mono-sm">${timeText} · Motivo: ${escapeHtml(b.reason || "Sin motivo")}</div>
        </div>
        <button type="button" class="btn-primary btn-unban">Quitar Baneo</button>
      `;
      row.querySelector(".btn-unban").addEventListener("click", async () => {
        await apiFetch(`/api/admin/bans/${b.id}`, { method: "DELETE" });
        await loadAdminBans();
      });
      list.appendChild(row);
    });
  }

  async function loadAdminBackups() {
    const resp = await apiFetch("/api/admin/backups");
    const data = await resp.json();
    const ul = $("local-backups-list");
    ul.innerHTML = "";
    const files = data.files || [];
    if (files.length === 0) {
      ul.innerHTML = `<li class="mono-sm">Aún no hay respaldos mensuales guardados.</li>`;
      return;
    }
    files.forEach((fname) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${escapeHtml(fname)}</span><button type="button" class="btn-secondary">Descargar</button>`;
      li.querySelector("button").addEventListener("click", () => {
        downloadWithAuth(`/api/admin/backups/${encodeURIComponent(fname)}`, fname);
      });
      ul.appendChild(li);
    });
  }

  // Ban Modal controls
  function openBanUserModal(uid, alias, username, ip) {
    $("ban-target-uid").value = uid || "";
    $("ban-target-ip").value = ip || "";
    $("ban-target-label").textContent = `Objetivo: ${alias} (@${username || "?"}) · IP: ${ip || "desconocida"}`;
    $("ban-user-type").value = "permaban";
    $("ban-duration-wrap").classList.add("hidden");
    $("ban-custom-mins-wrap").classList.add("hidden");
    $("ban-user-reason").value = "";
    $("ban-user-modal").classList.remove("hidden");
  }

  $("ban-user-type").addEventListener("change", () => {
    const isTemp = $("ban-user-type").value === "temp";
    $("ban-duration-wrap").classList.toggle("hidden", !isTemp);
    if (!isTemp) $("ban-custom-mins-wrap").classList.add("hidden");
  });

  $("ban-duration-preset").addEventListener("change", () => {
    const isCustom = $("ban-duration-preset").value === "custom";
    $("ban-custom-mins-wrap").classList.toggle("hidden", !isCustom);
  });

  $("ban-user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const banType = $("ban-user-type").value;
    let mins = null;
    if (banType === "temp") {
      const preset = $("ban-duration-preset").value;
      mins = preset === "custom" ? parseInt($("ban-custom-mins").value, 10) : parseInt(preset, 10);
    }
    const uidVal = $("ban-target-uid").value;
    const ipVal = $("ban-target-ip").value;
    await apiFetch("/api/admin/bans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_user_id: uidVal ? parseInt(uidVal, 10) : null,
        ip_address: ipVal || null,
        ban_type: banType,
        duration_minutes: mins,
        reason: $("ban-user-reason").value.trim(),
      }),
    });
    $("ban-user-modal").classList.add("hidden");
    if (!$("admin-modal").classList.contains("hidden")) {
      await loadAdminBans();
    }
    alert("Sanción aplicada correctamente.");
  });

  $("direct-ip-ban-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const banType = $("ban-ip-type").value;
    await apiFetch("/api/admin/bans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_user_id: null,
        ip_address: $("ban-ip-input").value.trim(),
        ban_type: banType,
        duration_minutes: banType === "temp" ? parseInt($("ban-ip-mins").value, 10) : null,
        reason: $("ban-ip-reason").value.trim(),
      }),
    });
    $("direct-ip-ban-form").reset();
    await loadAdminBans();
    alert("Baneo por IP aplicado.");
  });

  // Backups
  $("btn-force-backup").addEventListener("click", async () => {
    if (!confirm("Esto respaldará todos los mensajes actuales (solo texto), los subirá a GitHub y REINICIARÁ el chat. ¿Continuar?")) return;
    const msgBox = $("backup-status-msg");
    msgBox.classList.add("hidden");
    try {
      const resp = await apiFetch("/api/admin/backup-now", { method: "POST" });
      const data = await resp.json();
      msgBox.classList.remove("hidden");
      if (data.github && data.github.pushed) {
        msgBox.className = "alert-box success";
        msgBox.textContent = `Respaldo ${data.filename} subido a GitHub y chat reiniciado.`;
      } else {
        msgBox.className = "alert-box";
        msgBox.textContent = `Chat reiniciado y respaldo guardado en servidor (${data.filename}). GitHub: ${data.github?.error || "no configurado"}`;
      }
      await loadAdminBackups();
    } catch (err) {
      alert(err.message);
    }
  });

  $("btn-export-snapshot").addEventListener("click", () => {
    const now = new Date();
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const dd = String(now.getDate()).padStart(2, "0");
    const yyyy = now.getFullYear();
    downloadWithAuth("/api/admin/export-snapshot", `snapshot_${mm}-${dd}-${yyyy}.txt`);
  });

  // --- LIGHTBOX ---
  function openLightbox(url, name, sizeKb, dims) {
    $("lightbox-img").src = url;
    $("lightbox-img").classList.remove("zoomed");
    $("lightbox-meta").textContent = `${name || "imagen.webp"} (${sizeKb || "?"} KB · ${dims || ""})`;
    $("lightbox-download").href = url;
    $("lightbox-download").download = name || "imagen.webp";
    $("lightbox-modal").classList.remove("hidden");
  }

  $("lightbox-img").addEventListener("click", () => {
    $("lightbox-img").classList.toggle("zoomed");
  });

  $("lightbox-close").addEventListener("click", () => {
    $("lightbox-modal").classList.add("hidden");
    $("lightbox-img").src = "";
  });

  // --- MODALS & MOBILE SIDEBAR ---
  document.querySelectorAll(".modal-close").forEach((btn) => {
    btn.addEventListener("click", () => {
      $(btn.dataset.close).classList.add("hidden");
    });
  });

  function closeMobileSidebar() {
    sidebar.classList.remove("open");
    sidebarBackdrop.classList.add("hidden");
  }

  $("btn-menu-toggle").addEventListener("click", () => {
    const willOpen = !sidebar.classList.contains("open");
    sidebar.classList.toggle("open", willOpen);
    sidebarBackdrop.classList.toggle("hidden", !willOpen);
  });

  sidebarBackdrop.addEventListener("click", closeMobileSidebar);

  $("btn-logout").addEventListener("click", async () => {
    try {
      await apiFetch("/api/logout", { method: "POST" });
    } catch {}
    forceLogout();
  });

  // --- AUTO BOOT IF TOKEN PRESENT ---
  if (state.token) {
    bootChat();
  }
})();
