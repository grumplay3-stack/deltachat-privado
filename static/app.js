(() => {
  const ALLOWED_EMOJIS = ["👍", "❤️", "😂", "🔥", "😮"];
  const COMPOSER_EMOJIS = ["😀", "😂", "😎", "🔥", "❤️", "👍", "👀", "💀", "👾", "🎉", "😭", "🤡"];
  const DAYS_ES = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];

  // Isolated impersonation token support for ?imp=...
  const urlParams = new URLSearchParams(window.location.search);
  const impTokenFromUrl = urlParams.get("imp");
  if (impTokenFromUrl) {
    sessionStorage.setItem("dc_imp_token", impTokenFromUrl);
    window.history.replaceState({}, "", window.location.pathname);
  }
  const IS_IMP_TAB = Boolean(sessionStorage.getItem("dc_imp_token"));

  function saveSessionToken(tok) {
    state.token = tok;
    if (IS_IMP_TAB) {
      sessionStorage.setItem("dc_imp_token", tok);
    } else {
      localStorage.setItem("dc_token", tok);
    }
  }

  const state = {
    token: sessionStorage.getItem("dc_imp_token") || localStorage.getItem("dc_token") || "",
    user: null,
    channels: [],
    users: [],
    adminUsers: [],
    dmSummary: [],
    friendsData: { friends: [], incoming_requests: [], outgoing_requests: [], blocked: [], blocked_by_ids: [] },
    requestsCollapsed: false,
    onlineIds: new Set(),
    activeMode: "channel", // "channel" | "dm"
    activeChannelId: null,
    activeDmUserId: null,
    activeDmMeta: null,
    messages: [],
    replyTo: null, // { id, alias, snippet }
    pendingFile: null,
    ws: null,
    reconnectTimer: null,
    searchTimeout: null,
    hdTimerInterval: null,
    xatRosterTab: "visitors", // "visitors" | "friends"
  };

  // --- CATÁLOGO DE EMOJIS ICONICOS XAT (3D GLOSS) ---
  const XAT_SMILEYS = {
    smile: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><ellipse cx="11.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="11" cy="12.5" r="0.8" fill="#ffffff"/><ellipse cx="20.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="20" cy="12.5" r="0.8" fill="#ffffff"/><path d="M10 18.5 Q16 25 22 18.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/>`,
    biggrin: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><path d="M9.5 13.5 Q12 11 14.5 13.5" fill="none" stroke="#682a00" stroke-width="2.2" stroke-linecap="round"/><path d="M17.5 13.5 Q20 11 22.5 13.5" fill="none" stroke="#682a00" stroke-width="2.2" stroke-linecap="round"/><path d="M8.5 17 Q16 19 23.5 17 C22.5 25.5 9.5 25.5 8.5 17 Z" fill="#ffffff" stroke="#682a00" stroke-width="1.6" stroke-linejoin="round"/><path d="M8.5 17 Q16 18.8 23.5 17" fill="none" stroke="#682a00" stroke-width="1.2"/><path d="M16 18.2 L16 24.2" stroke="#682a00" stroke-width="0.9" opacity="0.6"/>`,
    wink: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><path d="M9 13.5 Q11.5 11 14 13.5" fill="none" stroke="#682a00" stroke-width="2.2" stroke-linecap="round"/><ellipse cx="20.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="20" cy="12.5" r="0.8" fill="#ffffff"/><path d="M11 18.5 Q16 24.5 22 18.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/>`,
    tongue: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><ellipse cx="11.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="11" cy="12.5" r="0.8" fill="#ffffff"/><ellipse cx="20.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="20" cy="12.5" r="0.8" fill="#ffffff"/><path d="M9.5 17.5 Q16 19.5 22.5 17.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/><path d="M12.5 18 C12.5 24 19.5 24 19.5 18 Z" fill="#e91e63" stroke="#880e4f" stroke-width="1.2"/><path d="M16 18.5 L16 22.5" stroke="#880e4f" stroke-width="0.8"/>`,
    cool: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><path d="M6.5 11.5 L15 11.5 C15.5 16.5 9 17 6.5 13.5 Z" fill="#14171c" stroke="#000" stroke-width="1"/><path d="M17 11.5 L25.5 11.5 C25.5 13.5 23 17 16.5 16.5 Z" fill="#14171c" stroke="#000" stroke-width="1"/><path d="M14 12 L18 12" stroke="#14171c" stroke-width="2"/><path d="M8.5 12.5 L12 15.5" stroke="#ffffff" stroke-width="1.2" stroke-linecap="round" opacity="0.8"/><path d="M19 12.5 L22.5 15.5" stroke="#ffffff" stroke-width="1.2" stroke-linecap="round" opacity="0.8"/><path d="M12 21 Q16.5 21.5 21 19.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/>`,
    mad: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-red)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.4" transform="rotate(-18 12 8.5)"/><path d="M7.5 11 L14 14" stroke="#1a0505" stroke-width="2.4" stroke-linecap="round"/><path d="M24.5 11 L18 14" stroke="#1a0505" stroke-width="2.4" stroke-linecap="round"/><ellipse cx="11.5" cy="15.5" rx="1.6" ry="2" fill="#1a0505"/><circle cx="12" cy="15" r="0.6" fill="#ffffff"/><ellipse cx="20.5" cy="15.5" rx="1.6" ry="2" fill="#1a0505"/><circle cx="20" cy="15" r="0.6" fill="#ffffff"/><path d="M10 23.5 Q16 17.5 22 23.5" fill="none" stroke="#1a0505" stroke-width="2.2" stroke-linecap="round"/>`,
    redface: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><ellipse cx="11.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="11" cy="12.5" r="0.8" fill="#ffffff"/><ellipse cx="20.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="20" cy="12.5" r="0.8" fill="#ffffff"/><circle cx="8" cy="18" r="3.5" fill="#e91e63" fill-opacity="0.45" filter="url(#xat-blur-blush)"/><circle cx="24" cy="18" r="3.5" fill="#e91e63" fill-opacity="0.45" filter="url(#xat-blur-blush)"/><path d="M12 19.5 Q16 23.5 20 19.5" fill="none" stroke="#682a00" stroke-width="1.8" stroke-linecap="round"/>`,
    confused: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><ellipse cx="11" cy="13" rx="2" ry="2.8" fill="#1a1a1a"/><circle cx="10.5" cy="12" r="0.8" fill="#ffffff"/><ellipse cx="21" cy="14" rx="1.4" ry="2.2" fill="#1a1a1a"/><circle cx="20.8" cy="13.2" r="0.6" fill="#ffffff"/><path d="M10 20.5 Q13 17 16 20.5 T22 20" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/>`,
    cry: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><ellipse cx="11.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="11" cy="12.5" r="0.8" fill="#ffffff"/><ellipse cx="20.5" cy="13.5" rx="1.8" ry="2.6" fill="#1a1a1a"/><circle cx="20" cy="12.5" r="0.8" fill="#ffffff"/><path d="M11 22 Q16 17.5 21 22" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/><path d="M22 17 C22 17 24.5 20.5 24.5 22.2 C24.5 23.8 23.2 25 21.8 25 C20.3 25 19 23.8 19 22.2 C19 20.5 22 17 22 17 Z" fill="#00b0ff" stroke="#0277bd" stroke-width="0.8"/>`,
    eek: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><path d="M8.5 9 Q11.5 7 14.5 9" fill="none" stroke="#682a00" stroke-width="1.8" stroke-linecap="round"/><path d="M17.5 9 Q20.5 7 23.5 9" fill="none" stroke="#682a00" stroke-width="1.8" stroke-linecap="round"/><circle cx="11.5" cy="13.5" r="2.5" fill="#1a1a1a"/><circle cx="11" cy="12.5" r="1" fill="#ffffff"/><circle cx="20.5" cy="13.5" r="2.5" fill="#1a1a1a"/><circle cx="20" cy="12.5" r="1" fill="#ffffff"/><ellipse cx="16" cy="21.5" rx="4" ry="5.5" fill="#2d1300" stroke="#682a00" stroke-width="1.5"/>`,
    "8ball": `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-black)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.35" transform="rotate(-18 12 8.5)"/><circle cx="16" cy="16" r="6" fill="#ffffff"/><text x="16" y="19" font-family="Arial, sans-serif" font-weight="900" font-size="9" fill="#000000" text-anchor="middle">8</text>`,
    party: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><polygon points="6,9 13,2 17,10" fill="#e91e63" stroke="#880e4f" stroke-width="0.8"/><path d="M8 8 L15 3.5" stroke="#ffeb3b" stroke-width="1.2"/><path d="M10 10 L16 5.5" stroke="#00e676" stroke-width="1.2"/><circle cx="13" cy="2" r="1.5" fill="#ffd700"/><path d="M9.5 14 Q12 12 14.5 14" fill="none" stroke="#682a00" stroke-width="1.8" stroke-linecap="round"/><path d="M17.5 14 Q20 12 22.5 14" fill="none" stroke="#682a00" stroke-width="1.8" stroke-linecap="round"/><path d="M14 20 L27 18 L27 21 L14 21 Z" fill="#ff9100" stroke="#e65100" stroke-width="0.8"/><circle cx="27" cy="19.5" r="2.2" fill="#00e5ff"/>`,
    crying: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><path d="M9 13 Q11.5 15.5 14 13" fill="none" stroke="#682a00" stroke-width="2.2" stroke-linecap="round"/><path d="M18 13 Q20.5 15.5 23 13" fill="none" stroke="#682a00" stroke-width="2.2" stroke-linecap="round"/><path d="M10 14.5 L10 27" stroke="#00b0ff" stroke-width="2.6" stroke-linecap="round"/><path d="M22 14.5 L22 27" stroke="#00b0ff" stroke-width="2.6" stroke-linecap="round"/><path d="M11 20 Q16 18 21 20 C20 26 12 26 11 20 Z" fill="#2d1300" stroke="#682a00" stroke-width="1"/>`,
    roll: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><circle cx="11.5" cy="12.5" r="3" fill="#ffffff" stroke="#682a00" stroke-width="0.8"/><circle cx="11.5" cy="10.8" r="1.4" fill="#1a1a1a"/><circle cx="20.5" cy="12.5" r="3" fill="#ffffff" stroke="#682a00" stroke-width="0.8"/><circle cx="20.5" cy="10.8" r="1.4" fill="#1a1a1a"/><path d="M11 20 L21 19.5" stroke="#682a00" stroke-width="2" stroke-linecap="round"/>`,
    sleepy: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><path d="M9 13.5 Q11.5 16 14 13.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/><path d="M18 13.5 Q20.5 16 23 13.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/><ellipse cx="16" cy="20" rx="2.5" ry="3" fill="#2d1300" stroke="#682a00" stroke-width="1"/><text x="21" y="9" font-family="Arial, sans-serif" font-weight="900" font-size="7" fill="#00b0ff">Z</text><text x="25" y="6" font-family="Arial, sans-serif" font-weight="900" font-size="5" fill="#40c4ff">z</text>`,
    kiss: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><path d="M9.5 13.5 Q12 11 14.5 13.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/><path d="M17.5 13.5 Q20 11 22.5 13.5" fill="none" stroke="#682a00" stroke-width="2" stroke-linecap="round"/><path d="M14 18.5 Q16 17 18 18.5 Q16 20 14 18.5 Z" fill="#e91e63" stroke="#880e4f" stroke-width="0.9"/><path d="M24 10 C24 8.5 22.5 8.5 22.5 10 C22.5 8.5 21 8.5 21 10 C21 11.5 22.5 13 22.5 13 C22.5 13 24 11.5 24 10 Z" fill="#ff1744"/>`,
    angel: `<ellipse cx="16" cy="4" rx="8" ry="2.2" fill="none" stroke="#ffd700" stroke-width="2"/><circle cx="16" cy="17" r="13" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="10" rx="5" ry="2.8" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 10)"/><ellipse cx="11.5" cy="14.5" rx="1.6" ry="2.4" fill="#1a1a1a"/><circle cx="11" cy="13.5" r="0.7" fill="#ffffff"/><ellipse cx="20.5" cy="14.5" rx="1.6" ry="2.4" fill="#1a1a1a"/><circle cx="20" cy="13.5" r="0.7" fill="#ffffff"/><path d="M11 19.5 Q16 24.5 21 19.5" fill="none" stroke="#682a00" stroke-width="1.8" stroke-linecap="round"/>`,
    evil: `<polygon points="7,10 4,2 10,5" fill="#d50000" stroke="#b71c1c" stroke-width="0.8"/><polygon points="25,10 28,2 22,5" fill="#d50000" stroke="#b71c1c" stroke-width="0.8"/><circle cx="16" cy="16" r="14" fill="url(#xat-grad-purple)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.35" transform="rotate(-18 12 8.5)"/><path d="M8 11.5 L14 14.5" stroke="#100020" stroke-width="2.2" stroke-linecap="round"/><path d="M24 11.5 L18 14.5" stroke="#100020" stroke-width="2.2" stroke-linecap="round"/><ellipse cx="11.5" cy="15.5" rx="1.6" ry="2.2" fill="#100020"/><circle cx="12" cy="15" r="0.6" fill="#ffeb3b"/><ellipse cx="20.5" cy="15.5" rx="1.6" ry="2.2" fill="#100020"/><circle cx="20" cy="15" r="0.6" fill="#ffeb3b"/><path d="M10 20 Q16 26 22 20" fill="none" stroke="#100020" stroke-width="2.2" stroke-linecap="round"/>`,
    heart: `<path d="M16 27 C16 27, 4 19, 4 10 C4 5.5, 8 2.5, 12.5 2.5 C14.5 2.5, 16 4, 16 4 C16 4, 17.5 2.5, 19.5 2.5 C24 2.5, 28 5.5, 28 10 C28 19, 16 27, 16 27 Z" fill="url(#xat-grad-heart)"/><path d="M7 8 C7 5.5, 9.5 4.5, 12 5" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" opacity="0.75"/>`,
    fire: `<path d="M16 2 C18 6 25 11 25 19 C25 25 21 29 16 29 C11 29 7 25 7 19 C7 11 14 6 16 2 Z" fill="url(#xat-grad-fire)"/><path d="M16 12 C18 15 21 17 21 21 C21 24.5 19 27 16 27 C13 27 11 24.5 11 21 C11 17 14 15 16 12 Z" fill="#ffff8d"/>`,
    music: `<path d="M10 8 L24 4 L24 17 C22.5 16 20 16.5 19 18 C17.8 19.5 18.5 22 20.5 22.8 C22.5 23.5 25 22 25 19.5 L25 2 L9 6 L9 20 C7.5 19 5 19.5 4 21 C2.8 22.5 3.5 25 5.5 25.8 C7.5 26.5 10 25 10 22.5 Z" fill="url(#xat-grad-music)"/>`,
    nerd: `<circle cx="16" cy="16" r="14" fill="url(#xat-grad-yellow)"/><ellipse cx="12" cy="8.5" rx="5.5" ry="3" fill="#ffffff" fill-opacity="0.6" transform="rotate(-18 12 8.5)"/><circle cx="11" cy="13.5" r="4.2" fill="#ffffff" stroke="#1a1a1a" stroke-width="1.4"/><circle cx="21" cy="13.5" r="4.2" fill="#ffffff" stroke="#1a1a1a" stroke-width="1.4"/><circle cx="11.5" cy="13.5" r="1.6" fill="#1a1a1a"/><circle cx="21.5" cy="13.5" r="1.6" fill="#1a1a1a"/><rect x="14.5" y="12.5" width="3" height="2" fill="#ffffff" stroke="#1a1a1a" stroke-width="0.8"/><path d="M11 20 Q16 23.5 21 20" fill="none" stroke="#682a00" stroke-width="1.8"/><rect x="14" y="21" width="1.8" height="2.5" fill="#ffffff" stroke="#682a00" stroke-width="0.6"/><rect x="16.2" y="21" width="1.8" height="2.5" fill="#ffffff" stroke="#682a00" stroke-width="0.6"/>`
  };

  const XAT_PAWNS = {
    admin: `<polygon points="6,5 8,1 12,4 16,1 18,5" fill="#ffe082" stroke="#ff8f00" stroke-width="0.8"/><circle cx="12" cy="7.5" r="4.8" fill="url(#pawn-gold-grad)" stroke="#3e2723" stroke-width="0.8"/><ellipse cx="12" cy="12.5" rx="4.5" ry="1.4" fill="url(#pawn-gold-grad)" stroke="#3e2723" stroke-width="0.8"/><path d="M9 13.5 C9 17 6.5 20.5 5.5 22.5 L18.5 22.5 C17.5 20.5 15 17 15 13.5 Z" fill="url(#pawn-gold-grad)" stroke="#3e2723" stroke-width="0.8"/><path d="M4 23 C4 22 20 22 20 23 L21 26 C21 27 3 27 3 26 Z" fill="url(#pawn-gold-grad)" stroke="#3e2723" stroke-width="0.8"/>`,
    green: `<circle cx="12" cy="7.5" r="4.8" fill="url(#pawn-green-grad)" stroke="#003311" stroke-width="0.8"/><ellipse cx="12" cy="12.5" rx="4.5" ry="1.4" fill="url(#pawn-green-grad)" stroke="#003311" stroke-width="0.8"/><path d="M9 13.5 C9 17 6.5 20.5 5.5 22.5 L18.5 22.5 C17.5 20.5 15 17 15 13.5 Z" fill="url(#pawn-green-grad)" stroke="#003311" stroke-width="0.8"/><path d="M4 23 C4 22 20 22 20 23 L21 26 C21 27 3 27 3 26 Z" fill="url(#pawn-green-grad)" stroke="#003311" stroke-width="0.8"/>`,
    blue: `<circle cx="12" cy="7.5" r="4.8" fill="url(#pawn-blue-grad)" stroke="#001a33" stroke-width="0.8"/><ellipse cx="12" cy="12.5" rx="4.5" ry="1.4" fill="url(#pawn-blue-grad)" stroke="#001a33" stroke-width="0.8"/><path d="M9 13.5 C9 17 6.5 20.5 5.5 22.5 L18.5 22.5 C17.5 20.5 15 17 15 13.5 Z" fill="url(#pawn-blue-grad)" stroke="#001a33" stroke-width="0.8"/><path d="M4 23 C4 22 20 22 20 23 L21 26 C21 27 3 27 3 26 Z" fill="url(#pawn-blue-grad)" stroke="#001a33" stroke-width="0.8"/>`,
    red: `<circle cx="12" cy="7.5" r="4.8" fill="url(#pawn-red-grad)" stroke="#330000" stroke-width="0.8"/><ellipse cx="12" cy="12.5" rx="4.5" ry="1.4" fill="url(#pawn-red-grad)" stroke="#330000" stroke-width="0.8"/><path d="M9 13.5 C9 17 6.5 20.5 5.5 22.5 L18.5 22.5 C17.5 20.5 15 17 15 13.5 Z" fill="url(#pawn-red-grad)" stroke="#330000" stroke-width="0.8"/><path d="M4 23 C4 22 20 22 20 23 L21 26 C21 27 3 27 3 26 Z" fill="url(#pawn-red-grad)" stroke="#330000" stroke-width="0.8"/>`,
    white: `<circle cx="12" cy="7.5" r="4.8" fill="url(#pawn-gray-grad)" stroke="#1a202c" stroke-width="0.8"/><ellipse cx="12" cy="12.5" rx="4.5" ry="1.4" fill="url(#pawn-gray-grad)" stroke="#1a202c" stroke-width="0.8"/><path d="M9 13.5 C9 17 6.5 20.5 5.5 22.5 L18.5 22.5 C17.5 20.5 15 17 15 13.5 Z" fill="url(#pawn-gray-grad)" stroke="#1a202c" stroke-width="0.8"/><path d="M4 23 C4 22 20 22 20 23 L21 26 C21 27 3 27 3 26 Z" fill="url(#pawn-gray-grad)" stroke="#1a202c" stroke-width="0.8"/>`
  };

  function renderXatSmiley(code) {
    const body = XAT_SMILEYS[code] || XAT_SMILEYS.smile;
    return `<svg viewBox="0 0 32 32" class="xat-smiley" alt="(${code})" title="(${code})">${body}</svg>`;
  }

  function renderXatPawn(type) {
    const body = XAT_PAWNS[type] || XAT_PAWNS.green;
    return `<svg viewBox="0 0 24 28" class="xat-pawn" title="${type}">${body}</svg>`;
  }

  function getUserPawn(userId, role, username) {
    if (role === "admin" || username === "dabo777") return renderXatPawn("admin");
    const isFriend = (state.friendsData.friends || []).some((f) => f.user_id === userId);
    if (isFriend) return renderXatPawn("blue");
    if (userId) return renderXatPawn("green");
    return renderXatPawn("red");
  }

  function parseXatSmileys(escapedText) {
    if (!escapedText) return "";
    let str = escapedText;
    // Emoticones textuales clásicos -> etiquetas xat
    str = str
      .replace(/(^|[\s(]):-?D(?=[\s).,!?;]|$)/g, '$1(biggrin)')
      .replace(/(^|[\s(]):-?\)(?=[\s).,!?;]|$)/g, '$1(smile)')
      .replace(/(^|[\s(]);-?\)(?=[\s).,!?;]|$)/g, '$1(wink)')
      .replace(/(^|[\s(]):-?[pP](?=[\s).,!?;]|$)/g, '$1(tongue)')
      .replace(/(^|[\s(])(?:B|8)-?\)(?=[\s).,!?;]|$)/g, '$1(cool)')
      .replace(/(^|[\s(])&gt;:-?\((?=[\s).,!?;]|$)/g, '$1(mad)')
      .replace(/(^|[\s(]):-?\$|:\$(?=[\s).,!?;]|$)/g, '$1(redface)')
      .replace(/(^|[\s(]):-?[sS](?=[\s).,!?;]|$)/g, '$1(confused)')
      .replace(/(^|[\s(]):-?['’]\((?=[\s).,!?;]|$)/g, '$1(cry)')
      .replace(/(^|[\s(]):-?[oO](?=[\s).,!?;]|$)/g, '$1(eek)')
      .replace(/(^|[\s(]):-?\*(?=[\s).,!?;]|$)/g, '$1(kiss)')
      .replace(/(^|[\s(])&lt;3(?=[\s).,!?;]|$)/g, '$1(heart)');

    // Etiquetas xat (smile), (cool), etc. -> SVG
    str = str.replace(/\((smile|biggrin|wink|tongue|cool|mad|redface|confused|cry|eek|8ball|party|crying|roll|sleepy|kiss|angel|evil|heart|fire|music|nerd)\)/gi, (match, tag) => {
      const lower = tag.toLowerCase();
      if (XAT_SMILEYS[lower]) {
        return renderXatSmiley(lower);
      }
      return match;
    });

    return str;
  }

  function formatXatDate(unixSec) {
    const d = new Date(unixSec * 1000);
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mi}`;
  }

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
        return `<span class="greentext">${parseXatSmileys(formatted)}</span>`;
      }
      return parseXatSmileys(formatted);
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
    if (IS_IMP_TAB) {
      sessionStorage.removeItem("dc_imp_token");
    } else {
      localStorage.removeItem("dc_token");
    }
    state.token = "";
    state.user = null;
    if (state.ws) {
      state.ws.close();
      state.ws = null;
    }
    clearInterval(state.hdTimerInterval);
    $("forced-pw-modal").classList.add("hidden");
    chatScreen.classList.add("hidden");
    authScreen.classList.remove("hidden");
    if (reasonMsg) {
      showAuthAlert(reasonMsg, false);
    }
  }

  function resolveQuestionValue(selectId, customInputId) {
    const sel = $(selectId);
    if (!sel) return $(customInputId).value.trim();
    return sel.value === "__custom__" ? $(customInputId).value.trim() : sel.value;
  }

  function bindQuestionPreset(selectId, customWrapId, customInputId) {
    const sel = $(selectId);
    const wrap = $(customWrapId);
    const inp = $(customInputId);
    if (!sel || !wrap || !inp) return;
    const sync = () => {
      const isCustom = sel.value === "__custom__";
      wrap.classList.toggle("hidden", !isCustom);
      inp.required = isCustom;
    };
    sel.addEventListener("change", sync);
    sync();
  }

  bindQuestionPreset("reg-question-preset", "reg-question-custom-wrap", "reg-question");
  bindQuestionPreset("sec-question-preset", "sec-question-custom-wrap", "sec-question");

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
      saveSessionToken(data.token);
      await bootChat();
    } catch (err) {
      showAuthAlert("Error de conexión con el servidor.");
    }
  });

  $("register-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAuthAlerts();
    const secQuestion = resolveQuestionValue("reg-question-preset", "reg-question");
    if (secQuestion.length < 3) {
      showAuthAlert("La pregunta secreta debe tener al menos 3 caracteres.");
      return;
    }
    try {
      const resp = await fetch("/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("reg-username").value.trim(),
          alias: $("reg-alias").value.trim(),
          password: $("reg-password").value,
          avatar_color: $("reg-color").value,
          security_question: secQuestion,
          security_answer: $("reg-answer").value.trim(),
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        showAuthAlert(data.detail || "No se pudo registrar.");
        return;
      }
      saveSessionToken(data.token);
      await bootChat();
    } catch (err) {
      showAuthAlert("Error de conexión al registrar.");
    }
  });

  $("rec-username").addEventListener("input", () => {
    $("recover-step2").classList.add("hidden");
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
      $("rec-answer").focus();
    } catch {
      showAuthAlert("Error al buscar usuario.");
    }
  });

  $("recover-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAuthAlerts();
    if ($("recover-step2").classList.contains("hidden")) {
      $("btn-fetch-question").click();
      return;
    }
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
      $("recover-step2").classList.add("hidden");
      $("recover-form").reset();
      if (data.token) {
        saveSessionToken(data.token);
        await bootChat();
      } else {
        showAuthAlert("Contraseña actualizada. Ya puedes iniciar sesión.", true);
      }
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

      if (state.user.must_change_password) {
        $("forced-pw-modal").classList.remove("hidden");
      } else {
        $("forced-pw-modal").classList.add("hidden");
      }

      await Promise.all([loadChannels(), loadUsers(), loadSidebarSocial()]);
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

  async function loadSidebarSocial() {
    try {
      const [frResp, sumResp] = await Promise.all([apiFetch("/api/friends"), apiFetch("/api/dms/summary")]);
      state.friendsData = await frResp.json();
      const sumData = await sumResp.json();
      state.dmSummary = sumData.conversations || [];
      renderNavigation();
    } catch {}
  }

  $("btn-toggle-requests").addEventListener("click", () => {
    state.requestsCollapsed = !state.requestsCollapsed;
    $("friend-requests-list").classList.toggle("hidden", state.requestsCollapsed);
    $("requests-arrow").textContent = state.requestsCollapsed ? "▸" : "▾";
  });

  $("friend-add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("friend-search-input").value.trim();
    if (!q) return;
    try {
      await apiFetch("/api/friends/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      $("friend-search-input").value = "";
      await loadSidebarSocial();
      alert("Solicitud de amistad enviada.");
    } catch (err) {
      alert(err.message);
    }
  });

  async function respondFriendAction(targetUserId, action) {
    await apiFetch("/api/friends/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_user_id: targetUserId, action }),
    });
    await loadSidebarSocial();
    if (state.activeMode === "dm" && state.activeDmUserId === targetUserId) {
      if (action === "block" || action === "reject") {
        if (state.channels.length > 0) await selectChannel(state.channels[0].id);
      } else {
        await loadCurrentMessages();
      }
    }
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

    // Incoming friend requests
    const reqList = $("friend-requests-list");
    const incoming = state.friendsData.incoming_requests || [];
    $("requests-count").textContent = String(incoming.length);
    reqList.innerHTML = "";
    incoming.forEach((r) => {
      const li = document.createElement("li");
      li.className = "req-item";
      li.innerHTML = `
        <div><strong style="color:${escapeHtml(r.avatar_color)}">${escapeHtml(r.alias)}</strong></div>
        <div class="req-actions">
          <button type="button" class="btn-primary btn-sm req-acc">✓ Aceptar</button>
          <button type="button" class="btn-secondary btn-sm req-rej">✕</button>
          <button type="button" class="btn-danger btn-sm req-blk">⛔</button>
        </div>
      `;
      li.querySelector(".req-acc").addEventListener("click", () => respondFriendAction(r.user_id, "accept"));
      li.querySelector(".req-rej").addEventListener("click", () => respondFriendAction(r.user_id, "reject"));
      li.querySelector(".req-blk").addEventListener("click", () => respondFriendAction(r.user_id, "block"));
      reqList.appendChild(li);
    });

    // Sidebar DMs sorted by most recent conversation + remaining users
    const isAdmin = state.user && state.user.role === "admin";
    const blockedIds = new Set([
      ...(state.friendsData.blocked || []).map((b) => b.user_id),
      ...(state.friendsData.blocked_by_ids || []),
    ]);

    const orderedEntries = [];
    const seenIds = new Set();
    (state.dmSummary || []).forEach((conv) => {
      if (state.user && conv.user_id === state.user.id) return;
      seenIds.add(conv.user_id);
      orderedEntries.push({
        user_id: conv.user_id,
        alias: conv.alias,
        username: conv.username,
        avatar_color: conv.avatar_color,
        online: state.onlineIds.has(conv.user_id) || conv.online,
        last_message: conv.last_message || "",
        unread_count: conv.unread_count || 0,
        is_incoming_request: Boolean(conv.is_incoming_request),
      });
    });

    // Include accepted friends without recent messages yet, plus other members
    const friendsFirst = (state.friendsData.friends || []).map((f) => ({
      id: f.user_id,
      alias: f.alias,
      avatar_color: f.avatar_color,
      online: state.onlineIds.has(f.user_id),
    }));
    [...friendsFirst, ...state.users].forEach((u) => {
      if (!u || (state.user && u.id === state.user.id) || seenIds.has(u.id) || blockedIds.has(u.id)) return;
      seenIds.add(u.id);
      orderedEntries.push({
        user_id: u.id,
        alias: u.alias,
        username: u.username,
        avatar_color: u.avatar_color,
        online: state.onlineIds.has(u.id),
        last_message: "",
        unread_count: 0,
        is_incoming_request: false,
      });
    });

    usersList.innerHTML = "";
    orderedEntries.forEach((entry) => {
      const isOnline = state.onlineIds.has(entry.user_id) || entry.online;
      const li = document.createElement("li");
      li.className = "nav-item dm-row-btn" + (state.activeMode === "dm" && state.activeDmUserId === entry.user_id ? " active" : "");
      const adminUserTag = isAdmin && entry.username ? ` <span class="mono-sm">(@${escapeHtml(entry.username)})</span>` : "";
      const badge = entry.unread_count > 0 ? `<span class="unread-badge">${entry.unread_count}</span>` : "";
      const reqPill = entry.is_incoming_request ? `<span class="req-pill">Solicitud</span>` : "";
      const previewHtml = entry.last_message ? `<div class="dm-row-preview">${escapeHtml(entry.last_message)}</div>` : "";
      li.innerHTML = `
        <div class="dm-row-top">
          <div class="nav-item-main">
            <span class="status-dot ${isOnline ? "online" : ""}"></span>
            <span style="color:${escapeHtml(entry.avatar_color)}">${escapeHtml(entry.alias)}</span>${adminUserTag}
          </div>
          <div style="display:flex;gap:4px;align-items:center;">${reqPill}${badge}</div>
        </div>
        ${previewHtml}
      `;
      li.addEventListener("click", () => {
        selectDm(entry.user_id);
        closeMobileSidebar();
      });
      usersList.appendChild(li);
    });
    renderXatRoster();
  }

  function updateDmBanners() {
    const reqBanner = $("dm-request-banner");
    const blkBanner = $("dm-blocked-banner");
    const msgInput = messageInput;
    const btnSend = $("btn-send");

    if (state.activeMode !== "dm" || !state.activeDmMeta) {
      reqBanner.classList.add("hidden");
      blkBanner.classList.add("hidden");
      msgInput.disabled = false;
      btnSend.disabled = false;
      return;
    }

    const meta = state.activeDmMeta;
    reqBanner.classList.toggle("hidden", !meta.is_incoming_request);
    if (!meta.can_send && !meta.is_incoming_request) {
      $("dm-blocked-text").textContent = meta.block_reason || "No puedes enviar mensajes en esta conversación.";
      $("btn-dm-readd").classList.toggle("hidden", meta.friendship_status !== "unfriended");
      blkBanner.classList.remove("hidden");
    } else {
      blkBanner.classList.add("hidden");
    }

    const lockComposer = !meta.can_send;
    msgInput.disabled = lockComposer;
    btnSend.disabled = lockComposer;
  }

  $("btn-dm-accept").addEventListener("click", () => {
    if (state.activeDmUserId) respondFriendAction(state.activeDmUserId, "accept");
  });
  $("btn-dm-reject").addEventListener("click", () => {
    if (state.activeDmUserId) respondFriendAction(state.activeDmUserId, "reject");
  });
  $("btn-dm-block").addEventListener("click", () => {
    if (state.activeDmUserId && confirm("¿Bloquear a este usuario? No podrá volver a escribirte.")) {
      respondFriendAction(state.activeDmUserId, "block");
    }
  });
  $("btn-dm-readd").addEventListener("click", async () => {
    if (!state.activeDmUserId) return;
    await apiFetch("/api/friends/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_user_id: state.activeDmUserId }),
    });
    await loadSidebarSocial();
    await loadCurrentMessages();
  });

  async function selectChannel(channelId) {
    state.activeMode = "channel";
    state.activeChannelId = channelId;
    state.activeDmUserId = null;
    state.activeDmMeta = null;
    clearReply();
    const ch = state.channels.find((c) => c.id === channelId);
    if (ch) {
      activeRoomTitle.textContent = `/${ch.slug}/ - ${ch.name}`;
      activeRoomDesc.textContent = ch.description;
    }
    updateDmBanners();
    renderNavigation();
    await loadCurrentMessages();
  }

  async function selectDm(friendId) {
    state.activeMode = "dm";
    state.activeDmUserId = friendId;
    state.activeChannelId = null;
    clearReply();
    const friend = state.users.find((u) => u.id === friendId) || state.dmSummary.find((d) => d.user_id === friendId);
    if (friend) {
      const extra = state.user.role === "admin" && friend.username ? ` (@${friend.username})` : "";
      activeRoomTitle.textContent = `DM Privado: ${friend.alias}${extra}`;
      activeRoomDesc.textContent = "Conversación directa 1 a 1";
    }
    renderNavigation();
    await loadCurrentMessages();
    await loadSidebarSocial();
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
    let msgs = data.messages || [];
    if (onlyMediaFilter) {
      msgs = msgs.filter((m) => Boolean(m.image_url));
    }
    state.messages = msgs;

    // Handle channel pinned message
    const pinnedBar = $("channel-pinned-bar");
    const pinnedText = $("channel-pinned-text");
    const unpinBtn = $("btn-unpin-msg");
    const chInfo = data.channel;
    if (chInfo && chInfo.pinned_message_id) {
      const pinMsg = (data.messages || []).find((x) => x.id === chInfo.pinned_message_id);
      if (pinMsg) {
        pinnedText.textContent = `No.${pinMsg.id} (${pinMsg.alias}): ${pinMsg.content || pinMsg.image_name || "Foto"}`;
        pinnedBar.classList.remove("hidden");
        unpinBtn.classList.toggle("hidden", state.user && state.user.role !== "admin");
        unpinBtn.onclick = async () => {
          await apiFetch(`/api/admin/channels/${chInfo.id}/pin`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message_id: null }),
          });
          loadCurrentMessages();
        };
      } else {
        pinnedBar.classList.add("hidden");
      }
    } else {
      pinnedBar.classList.add("hidden");
    }

    if (state.activeMode === "dm" && data.friend) {
      state.activeDmMeta = data.friend;
    } else {
      state.activeDmMeta = null;
    }
    updateDmBanners();
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
    article.className = "post xat-post";
    article.id = `post-${m.id}`;

    // Header
    const adminUserInfo = isAdminViewer && m.username
      ? `<span class="post-real-user">(@${escapeHtml(m.username)}${m.user_ip ? ` · ${escapeHtml(m.user_ip)}` : ""})</span>`
      : "";
    const modBadge = m.author_role === "admin" ? `<span class="post-admin-badge">## Admin</span>` : "";

    let actionsHtml = `<button type="button" class="btn-mini btn-quote" title="Citar mensaje">[Responder]</button>`;
    if (isAdminViewer && state.activeMode === "channel") {
      actionsHtml += `<button type="button" class="btn-mini btn-pin-post" title="Fijar en el tablón">[📌 Fijar]</button>`;
    }
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
      const nsfwBadge = m.is_nsfw
        ? `<span class="hd-badge" style="background:#5a2323;color:#ff9e9e;border-color:#8e3838;">🔞 +18 / NSFW</span>`
        : "";
      fileHeaderHtml = `
        <div class="post-file-info">
          <span>File: <a href="${escapeHtml(m.image_url)}" download="${escapeHtml(m.image_name || "imagen.webp")}">${escapeHtml(m.image_name || "imagen.webp")}</a> (${m.image_size_kb || "?"} KB, ${escapeHtml(m.image_dims || "")})</span>
          <a href="${escapeHtml(m.image_url)}" download="${escapeHtml(m.image_name || "imagen.webp")}" class="btn-mini">[⬇ Descargar]</a>
          ${hdBadge}
          ${nsfwBadge}
        </div>
      `;

      if (m.is_nsfw) {
        thumbHtml = `
          <div class="nsfw-wrap" title="Haz click para revelar contenido +18">
            <img src="${escapeHtml(m.image_url)}" alt="${escapeHtml(m.image_name || "Imagen")}" class="post-thumb" loading="lazy" />
            <div class="nsfw-overlay">
              <span>🔞 CONTENIDO +18</span>
              <span class="mono-sm">[ Click para ver ]</span>
            </div>
          </div>
        `;
      } else {
        thumbHtml = `<img src="${escapeHtml(m.image_url)}" alt="${escapeHtml(m.image_name || "Imagen")}" class="post-thumb" loading="lazy" />`;
      }
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

    const canClickAuthor = Boolean(m.user_id && state.user && m.user_id !== state.user.id);
    const authorPawnSvg = getUserPawn(m.user_id, m.author_role, m.username);
    const authorTagHtml = canClickAuthor
      ? `<button type="button" class="post-alias post-author-btn xat-post-author" style="color:${escapeHtml(m.avatar_color)}" title="Opciones de amistad / DM">${authorPawnSvg}<span>${escapeHtml(m.alias)}</span></button>`
      : `<span class="post-alias xat-post-author" style="color:${escapeHtml(m.avatar_color)}">${authorPawnSvg}<span>${escapeHtml(m.alias)}</span></span>`;

    const shortTime = formatXatDate(m.created_at);
    const fullDate = format4chanDate(m.created_at);

    article.innerHTML = `
      <div class="xat-post-avatar-col">
        <div class="xat-post-avatar-img" style="display:flex;align-items:center;justify-content:center;background:#151820;border-color:${escapeHtml(m.avatar_color || '#363d4f')}">
          ${authorPawnSvg}
        </div>
      </div>
      <div class="xat-post-content-col">
        <div class="xat-post-header">
          ${authorTagHtml}
          ${modBadge}
          ${adminUserInfo}
          <span class="xat-post-time" title="${fullDate}">${shortTime}</span>
          <span class="xat-post-no post-no" title="Click para citar">No.${m.id}</span>
          <div class="xat-post-actions post-actions">${actionsHtml}</div>
        </div>
        ${replyHtml}
        ${fileHeaderHtml}
        <div class="xat-post-body">
          ${thumbHtml}
          ${m.content ? `<div class="post-content">${formatPostContent(m.content)}</div>` : ""}
        </div>
        <div class="post-reactions">${reactionButtonsHtml}</div>
      </div>
    `;

    // Event bindings inside post
    const authorBtn = article.querySelector(".post-author-btn");
    if (authorBtn) {
      authorBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openAuthorPopover(e.currentTarget, m);
      });
    }
    const postNoEl = article.querySelector(".post-no");
    const quoteBtn = article.querySelector(".btn-quote");
    [postNoEl, quoteBtn].forEach((el) => {
      if (el) {
        el.addEventListener("click", () => {
          setReply(m);
        });
      }
    });

    const pinBtn = article.querySelector(".btn-pin-post");
    if (pinBtn) {
      pinBtn.addEventListener("click", async () => {
        if (!state.activeChannelId) return;
        await apiFetch(`/api/admin/channels/${state.activeChannelId}/pin`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message_id: m.id }),
        });
        loadCurrentMessages();
      });
    }

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

    const nsfwWrap = article.querySelector(".nsfw-wrap");
    if (nsfwWrap) {
      nsfwWrap.addEventListener("click", (e) => {
        if (!nsfwWrap.classList.contains("revealed")) {
          e.stopPropagation();
          nsfwWrap.classList.add("revealed");
        }
      });
    }

    const thumbEl = article.querySelector(".post-thumb");
    if (thumbEl) {
      thumbEl.addEventListener("click", (e) => {
        if (nsfwWrap && !nsfwWrap.classList.contains("revealed")) return;
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

  function openAuthorPopover(anchorEl, msg) {
    const pop = $("author-popover");
    const uid = msg.user_id;
    const isFriend = (state.friendsData.friends || []).some((f) => f.user_id === uid);
    const isPendingOut = (state.friendsData.outgoing_requests || []).some((f) => f.user_id === uid);
    const isAdminViewer = state.user && state.user.role === "admin";

    pop.innerHTML = `
      <div class="author-popover-title" style="color:${escapeHtml(msg.avatar_color)}">${escapeHtml(msg.alias)}</div>
      <button type="button" id="pop-dm-btn">💬 Enviar DM</button>
      ${
        isFriend
          ? `<button type="button" id="pop-unfriend-btn">✕ Quitar Amigo</button>`
          : isPendingOut
          ? `<button type="button" disabled>⏳ Solicitud enviada</button>`
          : `<button type="button" id="pop-add-btn">+ Agregar Amigo</button>`
      }
      <button type="button" id="pop-block-btn" style="color:var(--warn-red)">⛔ Bloquear</button>
      ${isAdminViewer ? `<button type="button" id="pop-admin-btn">🛠 Ver en Base Admin</button>` : ""}
    `;

    const rect = anchorEl.getBoundingClientRect();
    pop.style.top = `${Math.min(window.innerHeight - 160, rect.bottom + 4)}px`;
    pop.style.left = `${Math.min(window.innerWidth - 210, rect.left)}px`;
    pop.classList.remove("hidden");

    $("pop-dm-btn").addEventListener("click", () => {
      pop.classList.add("hidden");
      selectDm(uid);
    });
    const addBtn = $("pop-add-btn");
    if (addBtn) {
      addBtn.addEventListener("click", async () => {
        pop.classList.add("hidden");
        try {
          await apiFetch("/api/friends/request", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target_user_id: uid }),
          });
          await loadSidebarSocial();
        } catch (err) {
          alert(err.message);
        }
      });
    }
    const unfBtn = $("pop-unfriend-btn");
    if (unfBtn) {
      unfBtn.addEventListener("click", () => {
        pop.classList.add("hidden");
        respondFriendAction(uid, "unfriend");
      });
    }
    $("pop-block-btn").addEventListener("click", () => {
      pop.classList.add("hidden");
      if (confirm(`¿Bloquear a ${msg.alias}?`)) {
        respondFriendAction(uid, "block");
      }
    });
    const admBtn = $("pop-admin-btn");
    if (admBtn) {
      admBtn.addEventListener("click", async () => {
        pop.classList.add("hidden");
        $("admin-modal").classList.remove("hidden");
        await refreshAdminData();
        openAdminEditUser(uid);
      });
    }
  }

  document.addEventListener("click", (e) => {
    const pop = $("author-popover");
    if (pop && !pop.classList.contains("hidden") && !pop.contains(e.target) && !e.target.closest(".post-author-btn")) {
      pop.classList.add("hidden");
    }
  });

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
    $("chk-nsfw-mode").checked = false;
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
      const isNsfw = $("chk-nsfw-mode").checked;
      const maxBytes = isHd ? 15 * 1024 * 1024 : 5 * 1024 * 1024;
      if (state.pendingFile.size > maxBytes) {
        alert(`La imagen supera el límite de ${isHd ? 15 : 5} MB.`);
        return;
      }
      const fd = new FormData();
      fd.append("file", state.pendingFile);
      fd.append("is_hd", isHd ? "true" : "false");
      fd.append("is_nsfw", isNsfw ? "true" : "false");
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

  // --- SEARCH & FILTER ---
  let onlyMediaFilter = false;
  $("btn-filter-media").addEventListener("click", () => {
    onlyMediaFilter = !onlyMediaFilter;
    $("btn-filter-media").classList.toggle("active", onlyMediaFilter);
    loadCurrentMessages();
  });

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

      if (data.type === "error") {
        alert(data.detail || "No se pudo completar la acción.");
        return;
      }

      if (data.type === "users_updated") {
        loadUsers();
        loadSidebarSocial();
        return;
      }

      if (data.type === "channels_updated") {
        loadChannels();
        if (state.activeMode === "channel") {
          loadCurrentMessages();
        }
        return;
      }

      if (data.type === "friends_updated") {
        loadSidebarSocial();
        if (state.activeMode === "dm") {
          loadCurrentMessages();
        }
        return;
      }

      if (data.type === "alias_updated") {
        if (state.user && data.user_id === state.user.id) {
          state.user.alias = data.alias;
          state.user.avatar_color = data.avatar_color;
          myAliasLabel.textContent = data.alias;
        }
        loadUsers();
        loadSidebarSocial();
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
          apiFetch(`/api/dms/${otherId}/read`, { method: "POST" }).catch(() => {});
        }
        loadSidebarSocial();
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

  // --- PERFIL, ALIAS, SEGURIDAD Y CAMBIO FORZADO DE CLAVE ---
  $("btn-open-alias").addEventListener("click", () => {
    $("new-alias-input").value = state.user.alias;
    $("new-color-input").value = state.user.avatar_color;
    $("current-sec-question").textContent = state.user.security_question || "—";
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

  $("security-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const secQ = resolveQuestionValue("sec-question-preset", "sec-question");
    if (secQ.length < 3) {
      alert("La pregunta secreta debe tener al menos 3 caracteres.");
      return;
    }
    try {
      await apiFetch("/api/profile/security", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: $("sec-current-pw").value,
          security_question: secQ,
          security_answer: $("sec-answer").value.trim(),
        }),
      });
      state.user.security_question = secQ;
      $("current-sec-question").textContent = secQ;
      $("security-form").reset();
      bindQuestionPreset("sec-question-preset", "sec-question-custom-wrap", "sec-question");
      alert("Pregunta y respuesta de recuperación actualizadas.");
    } catch (err) {
      alert(err.message);
    }
  });

  $("forced-pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = $("forced-pw-error");
    errBox.classList.add("hidden");
    const pw1 = $("forced-new-pw").value;
    const pw2 = $("forced-confirm-pw").value;
    if (pw1 !== pw2) {
      errBox.textContent = "Las contraseñas no coinciden.";
      errBox.classList.remove("hidden");
      return;
    }
    try {
      await apiFetch("/api/profile/change-forced-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: pw1 }),
      });
      state.user.must_change_password = false;
      $("forced-pw-form").reset();
      $("forced-pw-modal").classList.add("hidden");
    } catch (err) {
      errBox.textContent = err.message;
      errBox.classList.remove("hidden");
    }
  });

  // --- ADMIN PANEL (BASE DE DATOS, IMPERSONAR, AUDITORÍA, DMs, RESPALDOS) ---
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
      if (target === "channels") loadAdminChannelsSettings();
      if (target === "dms") loadAdminDmThreads();
      if (target === "audit") loadAdminAudit();
      if (target === "backups") {
        loadAdminBackups();
        loadAdminUsersBackups();
      }
    });
  });

  // --- ADMIN CANALES & VISIBILIDAD ---
  let adminAllChannels = [];
  async function loadAdminChannelsSettings() {
    const [chResp, uResp] = await Promise.all([
      apiFetch("/api/admin/channels"),
      apiFetch("/api/admin/database/users"),
    ]);
    const chData = await chResp.json();
    const uData = await uResp.json();
    adminAllChannels = chData.channels || [];
    const allUsers = (uData.users || []).filter((u) => u.role !== "admin");

    const sel = $("admin-channel-select");
    sel.innerHTML = "";
    adminAllChannels.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = String(c.id);
      opt.textContent = `/${c.slug}/ - ${c.name} (${c.access_type || "public"})`;
      sel.appendChild(opt);
    });

    const updateFormForChannel = () => {
      const cid = parseInt(sel.value, 10);
      const ch = adminAllChannels.find((x) => x.id === cid);
      if (!ch) return;
      $("admin-channel-access-type").value = ch.access_type || "public";
      $("admin-channel-announcement").checked = Boolean(ch.is_announcement);

      const permsMap = new Map((ch.permissions || []).map((p) => [p.user_id, p.permission_type]));
      const checklist = $("admin-channel-users-checklist");
      checklist.innerHTML = "";
      if (allUsers.length === 0) {
        checklist.innerHTML = `<span class="mono-sm">No hay usuarios miembros registrados.</span>`;
        return;
      }
      allUsers.forEach((u) => {
        const lbl = document.createElement("label");
        lbl.className = "user-check-item";
        const isChecked = permsMap.has(u.id);
        lbl.innerHTML = `
          <input type="checkbox" value="${u.id}" ${isChecked ? "checked" : ""} />
          <span>${escapeHtml(u.alias)} (@${escapeHtml(u.username)})</span>
        `;
        checklist.appendChild(lbl);
      });
    };

    sel.onchange = updateFormForChannel;
    updateFormForChannel();
  }

  $("admin-channel-perm-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sel = $("admin-channel-select");
    const cid = parseInt(sel.value, 10);
    const accessType = $("admin-channel-access-type").value;
    const isAnnounce = $("admin-channel-announcement").checked;

    const checkedBoxes = $("admin-channel-users-checklist").querySelectorAll("input[type='checkbox']:checked");
    const userIds = Array.from(checkedBoxes).map((cb) => parseInt(cb.value, 10));
    const permType = accessType === "blacklist" ? "deny" : "allow";

    try {
      await apiFetch(`/api/admin/channels/${cid}/visibility`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          access_type: accessType,
          user_ids: userIds,
          permission_type: permType,
          is_announcement: isAnnounce,
        }),
      });
      alert("Configuración de visibilidad del tablón guardada.");
      await loadChannels();
      await loadAdminChannelsSettings();
    } catch (err) {
      alert(err.message);
    }
  });

  $("admin-db-search").addEventListener("input", () => {
    renderAdminUsersTable();
  });

  async function refreshAdminData() {
    await Promise.all([loadAdminUsers(), loadAdminBans(), loadAdminBackups(), loadAdminUsersBackups()]);
  }

  async function loadAdminUsers() {
    const resp = await apiFetch("/api/admin/database/users");
    const data = await resp.json();
    state.adminUsers = data.users || [];
    renderAdminUsersTable();
  }

  function renderAdminUsersTable() {
    const q = ($("admin-db-search").value || "").trim().toLowerCase();
    const filtered = state.adminUsers.filter((u) => {
      if (!q) return true;
      return (
        (u.username || "").toLowerCase().includes(q) ||
        (u.alias || "").toLowerCase().includes(q) ||
        (u.last_ip || "").toLowerCase().includes(q)
      );
    });
    $("admin-db-count").textContent = `${filtered.length} de ${state.adminUsers.length} cuentas`;
    const wrap = $("admin-users-list");
    wrap.innerHTML = "";

    const table = document.createElement("table");
    table.className = "admin-db-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th>ID</th>
          <th>Usuario / Alias</th>
          <th>Clave (Admin)</th>
          <th>IP / Dispositivo</th>
          <th>Notas / Estado</th>
          <th>Acciones</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector("tbody");

    filtered.forEach((u) => {
      const tr = document.createElement("tr");
      const isSelf = u.role === "admin";
      tr.innerHTML = `
        <td>#${u.id}</td>
        <td>
          <strong>@${escapeHtml(u.username)}</strong>
          <div><span style="color:${escapeHtml(u.avatar_color)}">${escapeHtml(u.alias)}</span></div>
          ${isSelf ? `<span class="post-admin-badge">## Admin</span>` : ""}
        </td>
        <td>
          <span class="pw-pill">${escapeHtml(u.password || "")}</span>
          ${u.must_change_password ? `<div class="mono-sm" style="color:var(--warn-amber)">Cambio obligatorio</div>` : ""}
        </td>
        <td>
          <div>${escapeHtml(u.last_ip || "N/A")} ${u.online ? "🟢" : "⚫"}</div>
          <div class="mono-sm">${escapeHtml(u.last_device || "—")}</div>
        </td>
        <td>
          <div class="mono-sm">${escapeHtml(u.admin_notes || "Sin notas")}</div>
        </td>
        <td>
          <div class="admin-row-actions">
            <button type="button" class="btn-secondary btn-sm btn-edit-u">Editar</button>
            <button type="button" class="btn-secondary btn-sm btn-logs-u">Logs/IPs</button>
            ${!isSelf ? `<button type="button" class="btn-secondary btn-sm btn-tmp-u">Clave Temp</button>` : ""}
            ${!isSelf ? `<button type="button" class="btn-primary btn-sm btn-imp-u">Entrar</button>` : ""}
            ${!isSelf ? `<button type="button" class="btn-danger btn-sm btn-ban-u">Ban</button>` : ""}
          </div>
        </td>
      `;

      tr.querySelector(".btn-edit-u").addEventListener("click", () => openAdminEditUser(u.id));
      tr.querySelector(".btn-logs-u").addEventListener("click", () => openUserLogs(u.id, u.username));

      const tmpBtn = tr.querySelector(".btn-tmp-u");
      if (tmpBtn) {
        tmpBtn.addEventListener("click", async () => {
          if (!confirm(`¿Generar contraseña temporal aleatoria para @${u.username}? Cerrará sus sesiones activas.`)) return;
          const res = await apiFetch(`/api/admin/generate-temp-password/${u.id}`, { method: "POST" });
          const info = await res.json();
          window.prompt(`Clave temporal generada para @${info.username} (cópiala):`, info.temp_password);
          await loadAdminUsers();
        });
      }

      const impBtn = tr.querySelector(".btn-imp-u");
      if (impBtn) {
        impBtn.addEventListener("click", async () => {
          const res = await apiFetch(`/api/admin/impersonate/${u.id}`, { method: "POST" });
          const info = await res.json();
          window.open(`/?imp=${encodeURIComponent(info.token)}`, "_blank");
        });
      }

      const banBtn = tr.querySelector(".btn-ban-u");
      if (banBtn) {
        banBtn.addEventListener("click", () => openBanUserModal(u.id, u.alias, u.username, u.last_ip));
      }

      tbody.appendChild(tr);
    });

    wrap.appendChild(table);
  }

  function openAdminEditUser(userId) {
    const u = state.adminUsers.find((x) => x.id === userId);
    if (!u) return;
    $("edit-uid").value = String(u.id);
    $("edit-username").value = u.username || "";
    $("edit-password").value = u.password || "";
    $("edit-alias").value = u.alias || "";
    $("edit-sec-q").value = u.security_question || "";
    $("edit-sec-a").value = u.security_answer || "";
    $("edit-admin-notes").value = u.admin_notes || "";
    $("edit-must-change").checked = Boolean(u.must_change_password);
    $("admin-edit-user-modal").classList.remove("hidden");
  }

  $("admin-edit-user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const uid = parseInt($("edit-uid").value, 10);
    const payload = {
      username: $("edit-username").value.trim(),
      alias: $("edit-alias").value.trim(),
      security_question: $("edit-sec-q").value.trim(),
      security_answer: $("edit-sec-a").value.trim(),
      admin_notes: $("edit-admin-notes").value,
      must_change_password: $("edit-must-change").checked,
    };
    const pwVal = $("edit-password").value;
    if (pwVal) payload.password = pwVal;
    try {
      await apiFetch(`/api/admin/database/users/${uid}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      $("admin-edit-user-modal").classList.add("hidden");
      await loadAdminUsers();
      await loadUsers();
      alert("Ficha actualizada en auth_users.db.");
    } catch (err) {
      alert(err.message);
    }
  });

  async function openUserLogs(userId, username) {
    $("logs-modal-title").textContent = `Logs, IPs e Historial de @${username}`;
    const body = $("admin-user-logs-body");
    body.innerHTML = `<p class="mono-sm">Cargando...</p>`;
    $("admin-user-logs-modal").classList.remove("hidden");

    const resp = await apiFetch(`/api/admin/database/users/${userId}/logs`);
    const data = await resp.json();

    const ipsHtml = (data.known_ips || []).map((ip) => `<li>${escapeHtml(ip.ip_address)} (visto ${format4chanDate(ip.last_seen)})</li>`).join("") || "<li>Sin IPs</li>";
    const loginsHtml = (data.logins || []).slice(0, 20).map((l) => `
      <tr>
        <td>${format4chanDate(l.created_at)}</td>
        <td>${escapeHtml(l.ip_address)}</td>
        <td>${escapeHtml(l.device_summary || "")}</td>
        <td>${l.success ? "✓ OK" : `✕ ${escapeHtml(l.failure_reason || "Fallo")}`}</td>
      </tr>
    `).join("");
    const changesHtml = (data.changes || []).slice(0, 20).map((c) => `
      <tr>
        <td>${format4chanDate(c.created_at)}</td>
        <td>${escapeHtml(c.field_name)}</td>
        <td>${escapeHtml(c.old_value)} → ${escapeHtml(c.new_value)}</td>
        <td>${escapeHtml(c.changed_by_role || "")}</td>
      </tr>
    `).join("");

    body.innerHTML = `
      <h4>IPs Conocidas</h4>
      <ul class="backup-files-list">${ipsHtml}</ul>
      <hr class="sep" />
      <h4>Últimos Intentos de Login</h4>
      <div class="admin-table-wrap"><table class="admin-db-table"><thead><tr><th>Fecha</th><th>IP</th><th>Dispositivo</th><th>Resultado</th></tr></thead><tbody>${loginsHtml}</tbody></table></div>
      <hr class="sep" />
      <h4>Historial de Cambios</h4>
      <div class="admin-table-wrap"><table class="admin-db-table"><thead><tr><th>Fecha</th><th>Campo</th><th>Cambio</th><th>Actor</th></tr></thead><tbody>${changesHtml}</tbody></table></div>
    `;
  }

  async function loadAdminDmThreads() {
    const resp = await apiFetch("/api/admin/dms/inspect");
    const data = await resp.json();
    const list = $("admin-dm-threads");
    const preview = $("admin-dm-preview");
    list.innerHTML = "";
    const threads = data.threads || [];
    if (threads.length === 0) {
      list.innerHTML = `<p class="mono-sm">No hay mensajes privados registrados.</p>`;
      return;
    }
    threads.forEach((t) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "admin-dm-thread-btn";
      btn.innerHTML = `
        <strong>${escapeHtml(t.user_a_alias)} (@${escapeHtml(t.user_a_username)}) ↔ ${escapeHtml(t.user_b_alias)} (@${escapeHtml(t.user_b_username)})</strong>
        <div class="mono-sm">${t.message_count} msgs · ${escapeHtml((t.last_message || "").slice(0, 40))}</div>
      `;
      btn.addEventListener("click", async () => {
        list.querySelectorAll(".admin-dm-thread-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const r = await apiFetch(`/api/admin/dms/inspect/${t.user_a_id}/${t.user_b_id}`);
        const d = await r.json();
        preview.innerHTML = (d.messages || []).map((m) => `
          <div style="margin-bottom:6px;border-bottom:1px solid var(--border-color);padding-bottom:4px;">
            <span class="mono-sm">[${format4chanDate(m.created_at)}]</span>
            <strong style="color:${escapeHtml(m.avatar_color)}">${escapeHtml(m.alias)}</strong>:
            <span>${escapeHtml(m.content || (m.image_name ? `[Imagen: ${m.image_name}]` : ""))}</span>
          </div>
        `).join("") || `<p class="mono-sm">Sin mensajes.</p>`;
      });
      list.appendChild(btn);
    });
  }

  async function loadAdminAudit() {
    const resp = await apiFetch("/api/admin/audit-logs");
    const data = await resp.json();
    const list = $("admin-audit-list");
    list.innerHTML = "";
    const logs = data.logs || [];
    if (logs.length === 0) {
      list.innerHTML = `<p class="mono-sm">Sin eventos de auditoría todavía.</p>`;
      return;
    }
    logs.forEach((l) => {
      const row = document.createElement("div");
      row.className = "admin-row";
      row.innerHTML = `
        <div>
          <strong>[${escapeHtml(l.event_type)}]</strong> @${escapeHtml(l.username || "")}
          <div class="mono-sm">${escapeHtml(l.details || "")}</div>
        </div>
        <span class="mono-sm">${format4chanDate(l.created_at)}</span>
      `;
      list.appendChild(row);
    });
  }

  async function loadAdminUsersBackups() {
    const resp = await apiFetch("/api/admin/database/backups");
    const data = await resp.json();
    const ul = $("users-backups-list");
    ul.innerHTML = "";
    const files = data.files || [];
    if (files.length === 0) {
      ul.innerHTML = `<li class="mono-sm">Aún no hay respaldos de usuarios (cada 3 días).</li>`;
      return;
    }
    files.forEach((fname) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${escapeHtml(fname)}</span><button type="button" class="btn-secondary btn-sm">Descargar</button>`;
      li.querySelector("button").addEventListener("click", () => {
        downloadWithAuth(`/api/admin/database/backups/${encodeURIComponent(fname)}`, fname);
      });
      ul.appendChild(li);
    });
  }

  $("btn-users-backup-now").addEventListener("click", async () => {
    await apiFetch("/api/admin/database/backup-now", { method: "POST" });
    await loadAdminUsersBackups();
    alert("Respaldo de auth_users.db creado.");
  });

  $("btn-export-users-json").addEventListener("click", () => {
    downloadWithAuth("/api/admin/database/export?format=json", "auth_users_export.json");
  });

  $("btn-export-users-sqlite").addEventListener("click", () => {
    downloadWithAuth("/api/admin/database/export?format=db", "auth_users_export.db");
  });

  $("restore-db-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("restore-db-file").files[0];
    if (!file) return;
    if (!confirm(`¿Restaurar la base de datos de usuarios desde "${file.name}"? Sobrescribirá los registros actuales.`)) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const resp = await apiFetch("/api/admin/database/restore", { method: "POST", body: fd });
      const data = await resp.json();
      $("restore-db-form").reset();
      await loadAdminUsers();
      await loadUsers();
      alert(`Base de datos restaurada (${data.restored_users} usuarios).`);
    } catch (err) {
      alert(err.message);
    }
  });

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

  // --- FUNCIONES XAT DARK (ROSTER, RIBBON Y CONTROLES) ---
  const XAT_RIBBON_SMILEYS = [
    "smile", "biggrin", "wink", "tongue", "cool",
    "mad", "redface", "confused", "cry", "eek",
    "8ball", "party", "crying", "heart", "fire", "music"
  ];

  function insertSmileyAtCursor(code) {
    const tag = `(${code}) `;
    const el = messageInput;
    el.focus();
    const start = el.selectionStart || el.value.length;
    const end = el.selectionEnd || el.value.length;
    el.value = el.value.substring(0, start) + tag + el.value.substring(end);
    el.selectionStart = el.selectionEnd = start + tag.length;
  }

  function renderXatRoster() {
    const rosterList = $("xat-roster-list");
    const countEl = $("xat-online-count");
    const mobileCountEl = $("mobile-online-count");
    const headingEl = $("xat-roster-heading");
    if (!rosterList) return;

    rosterList.innerHTML = "";
    const isFriendsTab = state.xatRosterTab === "friends";

    if (isFriendsTab) {
      const friends = state.friendsData.friends || [];
      const onlineFriends = friends.filter((f) => state.onlineIds.has(f.user_id));
      if (headingEl) headingEl.innerHTML = `Friends (<span>${friends.length}</span>)`;
      if (countEl) countEl.textContent = String(onlineFriends.length);
      if (mobileCountEl) mobileCountEl.textContent = String(onlineFriends.length);

      if (friends.length === 0) {
        rosterList.innerHTML = `<div class="mono-sm" style="padding:10px;text-align:center;color:var(--xat-text-muted);">Sin amigos aún. Agrega uno en la barra lateral.</div>`;
        return;
      }

      friends.forEach((f) => {
        const isOnline = state.onlineIds.has(f.user_id);
        const pawnType = isOnline ? "blue" : "white";
        const row = document.createElement("div");
        row.className = "xat-roster-item" + (state.activeMode === "dm" && state.activeDmUserId === f.user_id ? " active" : "");
        row.innerHTML = `
          ${renderXatPawn(pawnType)}
          <div class="xat-roster-item-info">
            <span class="xat-roster-item-name" style="color:${escapeHtml(f.avatar_color || '#e2e8f0')}">${escapeHtml(f.alias)}</span>
            <span class="xat-roster-item-sub">${isOnline ? "🟢 Online" : "⚫ Offline"}</span>
          </div>
        `;
        row.addEventListener("click", (e) => {
          e.stopPropagation();
          openAuthorPopover(row, { user_id: f.user_id, alias: f.alias, avatar_color: f.avatar_color, username: f.username });
        });
        rosterList.appendChild(row);
      });
      return;
    }

    // Pestaña Visitors: lista los usuarios ordenando online primero
    const users = state.users || [];
    const onlineList = [];
    const offlineList = [];
    users.forEach((u) => {
      if (state.onlineIds.has(u.id) || u.online) {
        onlineList.push(u);
      } else {
        offlineList.push(u);
      }
    });

    const totalOnline = onlineList.length;
    if (headingEl) headingEl.innerHTML = `Visitors (<span>${totalOnline}</span>)`;
    if (countEl) countEl.textContent = String(totalOnline);
    if (mobileCountEl) mobileCountEl.textContent = String(totalOnline);

    const fullList = [...onlineList, ...offlineList];
    if (fullList.length === 0) {
      rosterList.innerHTML = `<div class="mono-sm" style="padding:10px;text-align:center;color:var(--xat-text-muted);">Sin visitantes.</div>`;
      return;
    }

    const friendsSet = new Set((state.friendsData.friends || []).map((f) => f.user_id));

    fullList.forEach((u) => {
      const isOnline = state.onlineIds.has(u.id) || u.online;
      let pawnType = "red";
      if (u.role === "admin" || u.username === "dabo777") {
        pawnType = "admin";
      } else if (friendsSet.has(u.id)) {
        pawnType = isOnline ? "blue" : "white";
      } else if (isOnline) {
        pawnType = "green";
      } else {
        pawnType = "white";
      }

      const row = document.createElement("div");
      row.className = "xat-roster-item" + (state.activeMode === "dm" && state.activeDmUserId === u.id ? " active" : "");
      row.innerHTML = `
        ${renderXatPawn(pawnType)}
        <div class="xat-roster-item-info">
          <span class="xat-roster-item-name" style="color:${escapeHtml(u.avatar_color || '#e2e8f0')}">${escapeHtml(u.alias || u.username)}</span>
          <span class="xat-roster-item-sub">${isOnline ? "🟢 Online" : "⚫ Offline"}${u.role === "admin" ? " · 👑 Admin" : ""}</span>
        </div>
      `;
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        openAuthorPopover(row, { user_id: u.id, alias: u.alias, avatar_color: u.avatar_color, username: u.username, author_role: u.role });
      });
      rosterList.appendChild(row);
    });
  }

  function initXatSmileysRibbon() {
    const ribbon = $("xat-smileys-ribbon");
    if (!ribbon) return;
    ribbon.innerHTML = "";
    XAT_RIBBON_SMILEYS.forEach((code) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "xat-ribbon-btn";
      btn.title = `(${code})`;
      btn.innerHTML = renderXatSmiley(code);
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        insertSmileyAtCursor(code);
      });
      ribbon.appendChild(btn);
    });

    const popover = $("emoji-popover");
    if (popover) {
      popover.innerHTML = "";
      Object.keys(XAT_SMILEYS).forEach((code) => {
        const item = document.createElement("div");
        item.className = "xat-palette-item";
        item.title = `(${code})`;
        item.innerHTML = renderXatSmiley(code);
        item.addEventListener("click", (e) => {
          e.preventDefault();
          insertSmileyAtCursor(code);
          popover.classList.add("hidden");
        });
        popover.appendChild(item);
      });
    }
  }

  function initXatControls() {
    const btnTabVisitors = $("xat-tab-visitors");
    const btnTabFriends = $("xat-tab-friends");
    if (btnTabVisitors && btnTabFriends) {
      btnTabVisitors.addEventListener("click", () => {
        state.xatRosterTab = "visitors";
        btnTabVisitors.classList.add("active");
        btnTabFriends.classList.remove("active");
        renderXatRoster();
      });
      btnTabFriends.addEventListener("click", () => {
        state.xatRosterTab = "friends";
        btnTabFriends.classList.add("active");
        btnTabVisitors.classList.remove("active");
        renderXatRoster();
      });
    }

    const btnBoards = $("btn-xat-boards");
    if (btnBoards) {
      btnBoards.addEventListener("click", () => {
        const willOpen = !sidebar.classList.contains("open");
        sidebar.classList.toggle("open", willOpen);
        sidebarBackdrop.classList.toggle("hidden", !willOpen);
      });
    }

    const btnXatLogout = $("btn-xat-logout");
    if (btnXatLogout) {
      btnXatLogout.addEventListener("click", async () => {
        try {
          await apiFetch("/api/logout", { method: "POST" });
        } catch {}
        forceLogout();
      });
    }

    const btnToggleRoster = $("btn-toggle-xat-roster");
    const rosterPane = $("xat-roster-pane");
    if (btnToggleRoster && rosterPane) {
      btnToggleRoster.addEventListener("click", () => {
        rosterPane.classList.toggle("open");
      });
    }

    const btnMoreSmileys = $("btn-emoji-picker");
    const popover = $("emoji-popover");
    if (btnMoreSmileys && popover) {
      btnMoreSmileys.addEventListener("click", (e) => {
        e.stopPropagation();
        popover.classList.toggle("hidden");
      });
      document.addEventListener("click", (e) => {
        if (!popover.contains(e.target) && e.target !== btnMoreSmileys) {
          popover.classList.add("hidden");
        }
      });
    }
  }

  // Inicializar componentes xat
  initXatSmileysRibbon();
  initXatControls();

  // --- AUTO BOOT IF TOKEN PRESENT ---
  if (state.token) {
    bootChat();
  }
})();
