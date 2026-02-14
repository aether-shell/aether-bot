/* Nanobot Web — Chat App */
(function () {
    'use strict';

    const API = '';
    let token = localStorage.getItem('nanobot_token') || '';
    let chatId = localStorage.getItem('nanobot_chat_id') || '';
    let currentSessionId = '';
    let eventSource = null;
    // Streaming state: streamId -> { el, content }
    const streams = {};
    // Delivery ACK tracking: avoid ACK storms on reconnect/replay
    const ackedEventIds = new Set();
    // Pending file uploads: [{file, path, file_id, filename, content_type}]
    let pendingAttachments = [];
    // Track message_ids sent from this client to deduplicate user_message SSE events
    const sentMessageIds = new Set();
    // Guard: prevent catchUpMessages from running concurrently or during SSE message processing
    let catchUpRunning = false;
    let catchUpScheduled = false;
    // Flag: set while loadSessionsAndHistory is running to suppress catch-up
    let initialLoadInProgress = false;

    // === DOM ===
    const loginView = document.getElementById('login-view');
    const chatView = document.getElementById('chat-view');
    const loginForm = document.getElementById('login-form');
    const inviteInput = document.getElementById('invite-code');
    const loginError = document.getElementById('login-error');
    const messagesEl = document.getElementById('messages');
    const messageForm = document.getElementById('message-form');
    const messageInput = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebarClose = document.getElementById('sidebar-close');
    const sessionList = document.getElementById('session-list');
    const newChatBtn = document.getElementById('new-chat-btn');
    const logoutBtn = document.getElementById('logout-btn');
    const chatTitle = document.getElementById('chat-title');
    const attachBtn = document.getElementById('attach-btn');
    const fileInput = document.getElementById('file-input');
    const attachmentPreview = document.getElementById('attachment-preview');
    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const lightboxClose = document.getElementById('lightbox-close');
    const contextBar = document.getElementById('context-bar');

    const DEFAULT_PRODUCT_NAME = 'Nanobot';
    const DEFAULT_ASSISTANT_NAME = 'Nanobot';
    const DEFAULT_ASSISTANT_AVATAR_URL = 'https://lh3.googleusercontent.com/aida-public/AB6AXuAnTgNMSWaolorX1KbBnPvmYBhCltmdngCLe1-_mc3ZOtO6me-1HJfZsDr6MFEcrtCvHifvaHr6lEDGiRfmVfJ2rKecaU8sSFPrbJorycVKulM7iR4TqaSlxfVfq9dQxji_Gbx82L-b5W7SIVMnLhVIil_VZTQmQdg8TV1YvKGfRsD8hF-6Qn7TY6355PpBUka3JP0_M9ppdmVOvha_3SAUofzcs1gS3o147DcMrreGHN9c2vdYL6bMT1g1V7HPHO7_JDwO-yEmTgU';
    const DEFAULT_NEW_CHAT_LABEL = 'New Chat';
    const DEFAULT_USER_NAME = 'You';
    const DEFAULT_USER_AVATAR_ICON = 'person';

    function getBrand() {
        return window.NANOBOT_BRAND || {};
    }

    function getBrandProductName() {
        var brand = getBrand();
        return brand.productName || DEFAULT_PRODUCT_NAME;
    }

    function getAssistantName() {
        var brand = getBrand();
        return brand.assistantName || brand.productName || DEFAULT_ASSISTANT_NAME;
    }

    function getAssistantAvatarUrl() {
        var brand = getBrand();
        return brand.assistantAvatarUrl || DEFAULT_ASSISTANT_AVATAR_URL;
    }

    function getNewChatLabel() {
        var brand = getBrand();
        return brand.newChatLabel || DEFAULT_NEW_CHAT_LABEL;
    }

    function getUserName() {
        var brand = getBrand();
        return brand.userName || DEFAULT_USER_NAME;
    }

    function getUserAvatarUrl() {
        var brand = getBrand();
        return brand.userAvatarUrl || '';
    }

    function getUserAvatarIcon() {
        var brand = getBrand();
        return brand.userAvatarIcon || DEFAULT_USER_AVATAR_ICON;
    }

    // === Init ===
    async function init() {
        if (window.NANOBOT_BRAND_READY) {
            try {
                await window.NANOBOT_BRAND_READY;
            } catch (e) { /* ignore */ }
        }
        if (token) {
            const valid = await checkAuth();
            if (valid) {
                showChat();
                return;
            }
            clearAuth();
        }
        showLogin();
    }

    // === Auth ===
    async function checkAuth() {
        try {
            const res = await fetch(API + '/api/auth/check', {
                headers: { 'Authorization': 'Bearer ' + token }
            });
            if (res.ok) {
                const data = await res.json();
                chatId = data.chat_id;
                return true;
            }
        } catch (e) { /* ignore */ }
        return false;
    }

    function clearAuth() {
        token = '';
        chatId = '';
        lastEventId = '';
        ackedEventIds.clear();
        localStorage.removeItem('nanobot_token');
        localStorage.removeItem('nanobot_chat_id');
    }

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        loginError.textContent = '';
        const code = inviteInput.value.trim();
        if (!code) return;

        try {
            const res = await fetch(API + '/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ invite_code: code })
            });
            const data = await res.json();
            if (res.ok && data.token) {
                token = data.token;
                chatId = data.chat_id;
                localStorage.setItem('nanobot_token', token);
                localStorage.setItem('nanobot_chat_id', chatId);
                showChat();
            } else {
                loginError.textContent = data.error || 'Login failed';
            }
        } catch (err) {
            loginError.textContent = 'Network error';
        }
    });

    logoutBtn.addEventListener('click', () => {
        clearAuth();
        closeSidebar();
        if (eventSource) { eventSource.close(); eventSource = null; }
        messagesEl.innerHTML = '';
        showLogin();
    });

    // === Views ===
    function showLogin() {
        loginView.classList.remove('hidden');
        chatView.classList.add('hidden');
        inviteInput.value = '';
        inviteInput.focus();
    }

    function showChat() {
        loginView.classList.add('hidden');
        chatView.classList.remove('hidden');
        messageInput.focus();
        connectSSE();
        loadSessionsAndHistory();
    }

    // Load sessions, find active one, then load its history
    async function loadSessionsAndHistory() {
        initialLoadInProgress = true;
        try {
            const res = await fetch(API + '/api/sessions', {
                headers: { 'Authorization': 'Bearer ' + token }
            });
            if (!res.ok) return;
            const data = await res.json();
            var sessions = data.sessions || [];
            var defaultTitle = getBrandProductName();

            // Sync currentSessionId before rendering so highlight is correct
            var active = sessions.find(function (s) { return s.active; });
            if (active) {
                currentSessionId = active.session_id;
                chatTitle.textContent = active.title || defaultTitle;
            } else if (sessions.length > 0) {
                currentSessionId = sessions[0].session_id;
                chatTitle.textContent = sessions[0].title || defaultTitle;
            } else {
                chatTitle.textContent = defaultTitle;
            }

            renderSessions(sessions);

            // Load history for the active session
            if (currentSessionId) {
                try {
                    var msgRes = await fetch(API + '/api/sessions/' + encodeURIComponent(currentSessionId) + '/messages', {
                        headers: { 'Authorization': 'Bearer ' + token }
                    });
                    if (msgRes.ok) {
                        var msgData = await msgRes.json();
                        var msgs = msgData.messages || [];
                        msgs.forEach(function (m) {
                            var el = appendMessage(m.role, '', false, m.timestamp, { history: true });
                            var contentEl = el.querySelector('.msg-content');
                            contentEl.innerHTML = renderMarkdown(m.content);
                            if (m.media && m.media.length > 0) {
                                renderMediaAttachments(el, m.media);
                            }
                        });
                        scrollToBottom();
                    }
                } catch (e) { /* ignore */ }
            }
        } catch (e) { /* ignore */ }
        finally { initialLoadInProgress = false; }
    }

    // === SSE ===
    var sseReconnectTimer = null;
    var lastEventId = '';

    function shouldHandleSessionEvent(eventSessionId, eventType) {
        if (!eventSessionId) return true;
        if (!currentSessionId) {
            currentSessionId = eventSessionId;
            return true;
        }
        if (eventSessionId === currentSessionId) return true;

        // Compatibility: treat "default" and "default#timestamp" as the same
        // session lineage and upgrade local id to the concrete one.
        if (!currentSessionId.includes('#') && eventSessionId.indexOf(currentSessionId + '#') === 0) {
            console.info('Session id upgraded from SSE ' + eventType + ':', currentSessionId, '->', eventSessionId);
            currentSessionId = eventSessionId;
            return true;
        }
        if (!eventSessionId.includes('#') && currentSessionId.indexOf(eventSessionId + '#') === 0) {
            return true;
        }
        return false;
    }

    function connectSSE() {
        if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
        if (eventSource) { eventSource.close(); eventSource = null; }
        if (!lastEventId) {
            ackedEventIds.clear();
        }
        var url = API + '/api/messages/stream?token=' + encodeURIComponent(token);
        if (lastEventId) {
            url += '&lastEventId=' + encodeURIComponent(lastEventId);
        }
        eventSource = new EventSource(url);

        eventSource.addEventListener('connected', (e) => {
            console.log('SSE connected', JSON.parse(e.data));
            // Delay catch-up slightly so any replayed SSE events (via
            // Last-Event-ID) are processed first, avoiding count mismatch.
            setTimeout(function () { catchUpMessages(); }, 500);
        });

        eventSource.addEventListener('delta', (e) => {
            if (e.lastEventId) lastEventId = e.lastEventId;
            const data = JSON.parse(e.data);
            handleDelta(data);
        });

        eventSource.addEventListener('message', (e) => {
            var prevEventId = lastEventId;
            if (e.lastEventId) lastEventId = e.lastEventId;
            const data = JSON.parse(e.data);
            handleMessage(data, lastEventId, prevEventId);
        });

        eventSource.addEventListener('user_message', (e) => {
            if (e.lastEventId) lastEventId = e.lastEventId;
            const data = JSON.parse(e.data);
            // Skip if this client sent the message (already rendered in DOM)
            if (data.message_id && sentMessageIds.has(data.message_id)) {
                sentMessageIds.delete(data.message_id);
                return;
            }
            // Verify session_id matches current session
            if (!shouldHandleSessionEvent(data.session_id, 'user_message')) {
                console.debug('Ignoring user_message for different session:', data.session_id);
                return;
            }
            // Render user message from another device
            var crossEl = appendMessage('user', data.content || '', true, data.timestamp);
            setDelivered(crossEl);
            scrollToBottom();
        });

        eventSource.onerror = () => {
            console.warn('SSE error, reconnecting in 3s...');
            if (eventSource) { eventSource.close(); eventSource = null; }
            sseReconnectTimer = setTimeout(connectSSE, 3000);
        };
    }

    function schedulePostSendCatchup() {
        // Fallback for cases where SSE is temporarily disconnected:
        // poll history shortly after sending so assistant replies still appear.
        setTimeout(function () { catchUpMessages(); }, 700);
        setTimeout(function () { catchUpMessages(); }, 2200);
        setTimeout(function () { catchUpMessages(); }, 5000);
    }

    // === Visibility change: reconnect SSE + catch up missed messages ===
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible' && token) {
            // Reconnect SSE immediately if it's dead
            if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
                connectSSE();
            }
            // Catch up any messages we missed while the tab was hidden.
            // Delay slightly so replayed SSE events arrive first.
            setTimeout(function () { catchUpMessages(); }, 600);
        }
    });

    // === Network recovery: reconnect SSE when back online ===
    window.addEventListener('online', function () {
        if (token) {
            console.log('Network back online, reconnecting SSE...');
            connectSSE();
        }
    });

    async function catchUpMessages() {
        if (!currentSessionId) return;
        // Skip if initial history load is in progress
        if (initialLoadInProgress) return;
        if (catchUpRunning) {
            catchUpScheduled = true;
            return;
        }
        catchUpRunning = true;
        catchUpScheduled = false;
        try {
            var res = await fetch(API + '/api/sessions/' + encodeURIComponent(currentSessionId) + '/messages', {
                headers: { 'Authorization': 'Bearer ' + token }
            });
            if (!res.ok) return;
            var data = await res.json();
            var serverMsgs = data.messages || [];
            if (serverMsgs.length === 0) return;

            // Count existing non-streaming messages in DOM
            var existingEls = messagesEl.querySelectorAll('.message:not(.streaming)');
            var existingCount = existingEls.length;

            // Only reload when server has MORE messages than DOM (we missed
            // messages while disconnected).  When DOM has more than server it
            // means the user just sent a message that the agent hasn't
            // persisted yet — don't wipe those pending messages.
            if (serverMsgs.length > existingCount) {
                // Remove all non-streaming messages and re-render from server
                Array.from(existingEls).forEach(function (el) { el.remove(); });
                serverMsgs.forEach(function (m) {
                    var el = appendMessage(m.role, '', false, m.timestamp, { history: true });
                    var contentEl = el.querySelector('.msg-content');
                    contentEl.innerHTML = renderMarkdown(m.content);
                    // Re-render media attachments if present
                    if (m.media && m.media.length > 0) {
                        renderMediaAttachments(el, m.media);
                    }
                });
                scrollToBottom();
            }
        } catch (e) {
            console.warn('catchUpMessages error:', e);
        } finally {
            catchUpRunning = false;
            if (catchUpScheduled) {
                catchUpScheduled = false;
                catchUpMessages();
            }
        }
    }

    function handleDelta(data) {
        // Verify session_id matches current session
        if (!shouldHandleSessionEvent(data.session_id, 'delta')) {
            console.debug('Ignoring delta for different session:', data.session_id);
            return;
        }

        const sid = data.stream_id || 'default_stream';
        if (!streams[sid]) {
            // Create a new streaming bubble
            const el = appendMessage('assistant', '', false, data.timestamp);
            el.classList.add('streaming');
            streams[sid] = { el: el, content: '' };
        }
        streams[sid].content = data.content;
        const contentEl = streams[sid].el.querySelector('.msg-content') || streams[sid].el;
        contentEl.innerHTML = renderMarkdown(streams[sid].content) + '<span class="cursor"></span>';
        scrollToBottom();
    }

    function handleMessage(data, eventId, prevEventId) {
        // Verify session_id matches current session
        if (!shouldHandleSessionEvent(data.session_id, 'message')) {
            console.debug('Ignoring message for different session:', data.session_id);
            // Still refresh session list (titles may have changed)
            loadSessions();
            return;
        }

        const sid = data.stream_id;
        var msgEl;
        if (sid && streams[sid]) {
            // Finalize the streaming bubble
            const s = streams[sid];
            const contentEl = s.el.querySelector('.msg-content') || s.el;
            contentEl.innerHTML = renderMarkdown(data.content);
            s.el.classList.remove('streaming');
            msgEl = s.el;
            delete streams[sid];
        } else {
            // Full message (no prior stream)
            msgEl = appendMessage(data.role || 'assistant', '', false, data.timestamp);
            const contentEl = msgEl.querySelector('.msg-content') || msgEl;
            contentEl.innerHTML = renderMarkdown(data.content);
        }

        updateMessageTime(msgEl, data.timestamp);

        // Send ACK to server (fire-and-forget, no UI badge on bot messages)
        tryAckDelivered(data, msgEl, eventId, prevEventId);

        // Render media attachments (bot -> user)
        if (data.media && data.media.length > 0) {
            renderMediaAttachments(msgEl, data.media);
        }

        // Update fixed context status bar
        if (data.context || data.timing) {
            updateContextBar(data.context, data.timing);
        }

        scrollToBottom();

        // Refresh session list (new messages may change titles / add new sessions)
        loadSessions();
    }

    // === Media rendering (bot -> user) ===
    function renderMediaAttachments(msgEl, mediaList) {
        var container = document.createElement('div');
        container.className = 'msg-media';
        mediaList.forEach(function (item) {
            var mediaUrl = API + '/api/media/' + item.file_id + '?token=' + encodeURIComponent(token);
            if (item.is_image) {
                var img = document.createElement('img');
                img.src = mediaUrl;
                img.alt = item.filename;
                img.className = 'msg-image';
                img.loading = 'lazy';
                img.addEventListener('click', function () { openLightbox(mediaUrl); });
                container.appendChild(img);
            } else {
                var a = document.createElement('a');
                a.href = mediaUrl;
                a.download = item.filename;
                a.className = 'msg-file';
                a.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> ' + item.filename;
                container.appendChild(a);
            }
        });
        appendToMessageBody(msgEl, container);
    }

    // === Context status bar (fixed position, updates on each message) ===
    // Format aligned with Feishu channel: Chinese labels, full-width pipe separator
    var MODE_LABELS = {
        'native': '模型连续',
        'reset': '重新绑定',
        'stateless': '本地拼接'
    };

    function updateContextBar(context, timing) {
        var parts = [];
        if (context) {
            // 会话模式
            if (context.mode) {
                var label = MODE_LABELS[context.mode] || '未知';
                parts.push('会话模式：' + label);
            }
            // LLM会话压缩
            parts.push('LLM会话压缩：' + (context.summarized ? '是' : '否'));
            // 同步重置
            if (context.synced_reset != null) {
                parts.push('同步重置：' + (context.synced_reset ? '是' : '否'));
            }
            // 数据来源
            if (context.source) {
                parts.push('数据来源：' + (context.source === 'usage' ? 'API' : '估算'));
            }
            // 估算 Tokens
            if (context.est_tokens != null) {
                parts.push('估算Tokens：' + context.est_tokens);
            }
            // LLM Context 比例
            if (context.est_ratio != null) {
                parts.push('LLM Context：' + (context.est_ratio * 100).toFixed(2) + '%');
            }
        }
        if (timing) {
            if (timing.llm_s != null) parts.push('LLM耗时：' + timing.llm_s.toFixed(1) + 's');
            if (timing.total_s != null) parts.push('总耗时：' + timing.total_s.toFixed(1) + 's');
        }
        if (parts.length === 0) {
            contextBar.classList.add('hidden');
            return;
        }
        contextBar.innerHTML = parts.map(function (p) {
            return '<span class="ctx-item">' + p + '</span>';
        }).join('');
        contextBar.classList.remove('hidden');
    }

    // === Lightbox ===
    function openLightbox(src) {
        lightboxImg.src = src;
        lightbox.classList.remove('hidden');
    }

    function closeLightboxFn() {
        lightbox.classList.add('hidden');
        lightboxImg.src = '';
    }

    lightboxClose.addEventListener('click', closeLightboxFn);
    lightbox.addEventListener('click', function (e) {
        if (e.target === lightbox) closeLightboxFn();
    });

    // === Messages ===
    messageForm.addEventListener('submit', (e) => {
        e.preventDefault();
        sendMessage();
    });

    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize textarea
    messageInput.addEventListener('input', () => {
        messageInput.style.height = 'auto';
        messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
    });

    async function sendMessage() {
        const content = messageInput.value.trim();
        if (!content && pendingAttachments.length === 0) return;

        // Intercept /new command — same behavior as clicking "+" button
        if (content === '/new') {
            console.debug('[session] intercepted /new command, creating session via HTTP');
            messageInput.value = '';
            messageInput.style.height = 'auto';
            createNewSession();
            return;
        }

        messageInput.value = '';
        messageInput.style.height = 'auto';
        sendBtn.disabled = true;

        // Generate dedup message_id
        var messageId = Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        sentMessageIds.add(messageId);
        // Cap the set size to avoid unbounded growth
        if (sentMessageIds.size > 200) {
            var first = sentMessageIds.values().next().value;
            sentMessageIds.delete(first);
        }

        // Collect media paths from pending attachments
        var mediaPaths = pendingAttachments.map(function (a) { return a.path; }).filter(Boolean);

        // Show user message immediately (with attachment thumbnails)
        var userEl = appendMessage('user', content || '', true, Date.now());
        setSending(userEl);
        if (pendingAttachments.length > 0) {
            renderUserAttachments(userEl, pendingAttachments);
        }
        scrollToBottom();

        // Clear attachments
        pendingAttachments = [];
        updateAttachmentPreview();

        var body = {
            content: content,
            session_id: currentSessionId,
            message_id: messageId
        };
        if (mediaPaths.length > 0) {
            body.media = mediaPaths;
        }

        var maxRetries = 3;
        var attempt = 0;
        var sent = false;

        while (attempt < maxRetries && !sent) {
            try {
                const res = await fetch(API + '/api/messages', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + token
                    },
                    body: JSON.stringify(body)
                });

                if (res.status === 401) {
                    clearAuth();
                    showLogin();
                    return;
                }

                if (res.status === 429) {
                    // Rate limited — wait and retry
                    attempt++;
                    if (attempt < maxRetries) {
                        await new Promise(function (r) { setTimeout(r, 1000 * Math.pow(2, attempt)); });
                        continue;
                    }
                    setSendFailed(userEl);
                    appendMessage('assistant', 'Rate limited. Please wait a moment.', false, Date.now());
                    scrollToBottom();
                    break;
                }

                if (res.ok) {
                    sent = true;
                    setDelivered(userEl);
                    try {
                        var ack = await res.json();
                        if (ack && ack.session_id) {
                            currentSessionId = ack.session_id;
                        }
                    } catch (e) { /* ignore */ }
                    if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
                        connectSSE();
                    }
                    schedulePostSendCatchup();
                } else {
                    // Server error — retry
                    attempt++;
                    if (attempt < maxRetries) {
                        await new Promise(function (r) { setTimeout(r, 1000 * Math.pow(2, attempt)); });
                        continue;
                    }
                    setSendFailed(userEl);
                    appendMessage('assistant', 'Failed to send message (server error). Please try again.', false, Date.now());
                    scrollToBottom();
                }
            } catch (err) {
                attempt++;
                if (attempt < maxRetries) {
                    await new Promise(function (r) { setTimeout(r, 1000 * Math.pow(2, attempt)); });
                    continue;
                }
                setSendFailed(userEl);
                appendMessage('assistant', 'Failed to send message. Check your connection.', false, Date.now());
                scrollToBottom();
            }
        }

        sendBtn.disabled = false;
        messageInput.focus();
    }

    function renderUserAttachments(msgEl, attachments) {
        var container = document.createElement('div');
        container.className = 'msg-media';
        attachments.forEach(function (att) {
            if (att.content_type && att.content_type.startsWith('image/') && att.previewUrl) {
                var img = document.createElement('img');
                img.src = att.previewUrl;
                img.alt = att.filename;
                img.className = 'msg-image';
                container.appendChild(img);
            } else {
                var span = document.createElement('span');
                span.className = 'msg-file';
                span.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> ' + (att.filename || 'file');
                container.appendChild(span);
            }
        });
        appendToMessageBody(msgEl, container);
    }

    function getMessageBody(msgEl) {
        return msgEl.querySelector('.msg-body') || msgEl;
    }

    function appendToMessageBody(msgEl, node) {
        var body = getMessageBody(msgEl);
        var timeEl = body.querySelector('.msg-time');
        if (timeEl) {
            body.insertBefore(node, timeEl);
        } else {
            body.appendChild(node);
        }
    }

    function formatMessageTime(timestamp) {
        var date = null;
        if (timestamp instanceof Date) {
            date = timestamp;
        } else if (typeof timestamp === 'number') {
            date = new Date(timestamp);
        } else if (typeof timestamp === 'string' && timestamp) {
            date = new Date(timestamp);
        }
        if (!date || isNaN(date.getTime())) {
            date = new Date();
        }
        var now = new Date();
        var sameDay = date.toDateString() === now.toDateString();
        var options = sameDay
            ? { hour: '2-digit', minute: '2-digit' }
            : { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' };
        return date.toLocaleString(undefined, options);
    }

    function updateMessageTime(msgEl, timestamp) {
        var body = getMessageBody(msgEl);
        var timeEl = body.querySelector('.msg-time');
        if (!timeEl) {
            timeEl = document.createElement('div');
            timeEl.className = 'msg-time';
            body.appendChild(timeEl);
        }
        timeEl.textContent = formatMessageTime(timestamp);
    }

    function ensureDeliveryBadge(msgEl) {
        if (!msgEl) return null;
        // Only user messages show delivery status
        if (!msgEl.classList.contains('user')) return null;
        var body = getMessageBody(msgEl);
        var timeEl = body.querySelector('.msg-time');
        if (!timeEl) return null;

        var badge = timeEl.querySelector('.msg-delivery');
        if (badge) return badge;

        badge = document.createElement('span');
        badge.className = 'msg-delivery';
        badge.setAttribute('aria-label', '');
        timeEl.appendChild(badge);
        return badge;
    }

    function setDelivered(msgEl) {
        var badge = ensureDeliveryBadge(msgEl);
        if (!badge) return;
        badge.classList.remove('sending', 'send-failed');
        badge.classList.add('delivered');
        badge.textContent = '\u2713';
        badge.setAttribute('aria-label', 'delivered');
    }

    function setSending(msgEl) {
        var badge = ensureDeliveryBadge(msgEl);
        if (!badge) return;
        badge.classList.add('sending');
        badge.textContent = '\u2022\u2022\u2022';
    }

    function setSendFailed(msgEl) {
        var badge = ensureDeliveryBadge(msgEl);
        if (!badge) return;
        badge.classList.remove('sending');
        badge.classList.add('send-failed');
        badge.textContent = '!';
        badge.setAttribute('aria-label', 'send failed');
    }


    async function tryAckDelivered(data, msgEl, eventId, prevEventId) {
        // Optimistic: mark delivered immediately (client received the message)
        setDelivered(msgEl);

        if (!eventSource) return;
        if (!eventId) return;
        if (!data || data.type !== 'message') return;
        if ((data.role || 'assistant') !== 'assistant') return;

        var eid = eventId;
        var currentId = parseInt(eid, 10);
        var previousId = parseInt(prevEventId || '', 10);
        if (!Number.isNaN(currentId) && !Number.isNaN(previousId) && currentId < previousId) {
            // Server likely restarted/reset event_id — clear dedupe set.
            ackedEventIds.clear();
        }
        if (ackedEventIds.has(eid)) return;
        ackedEventIds.add(eid);
        // Evict oldest entry when Set exceeds limit.
        if (ackedEventIds.size > 2000) {
            var first = ackedEventIds.values().next().value;
            ackedEventIds.delete(first);
        }

        // Fire-and-forget ACK POST
        fetch(API + '/api/messages/ack', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token
            },
            body: JSON.stringify({
                event_id: eid,
                session_id: data.session_id || currentSessionId || '',
                stream_id: data.stream_id || ''
            })
        }).catch(function () { /* ignore */ });
    }

    function appendMessage(role, content, isPlainText, timestamp, opts) {
        const safeRole = role === 'user' ? 'user' : 'assistant';
        const assistantName = getAssistantName();
        const userName = getUserName();
        const div = document.createElement('div');
        div.className = 'message ' + safeRole;

        const avatar = document.createElement('div');
        avatar.className = 'msg-avatar ' + safeRole;
        if (safeRole === 'assistant') {
            const img = document.createElement('img');
            img.src = getAssistantAvatarUrl();
            img.alt = assistantName + ' avatar';
            avatar.appendChild(img);
        } else {
            const userAvatarUrl = getUserAvatarUrl();
            if (userAvatarUrl) {
                const img = document.createElement('img');
                img.src = userAvatarUrl;
                img.alt = userName + ' avatar';
                avatar.appendChild(img);
            } else {
                const icon = document.createElement('span');
                icon.className = 'material-symbols-outlined';
                icon.textContent = getUserAvatarIcon();
                avatar.appendChild(icon);
            }
        }

        const body = document.createElement('div');
        body.className = 'msg-body';

        const name = document.createElement('div');
        name.className = 'msg-name';
        name.textContent = safeRole === 'assistant' ? assistantName : userName;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'msg-content';
        if (isPlainText) {
            contentDiv.textContent = content;
        } else {
            contentDiv.innerHTML = renderMarkdown(content);
        }

        body.appendChild(name);
        body.appendChild(contentDiv);
        updateMessageTime(body, timestamp);
        div.appendChild(avatar);
        div.appendChild(body);

        messagesEl.appendChild(div);
        // Auto-mark user messages as delivered when loading history
        if (opts && opts.history && safeRole === 'user') {
            setDelivered(div);
        }
        return div;
    }

    function scrollToBottom() {
        requestAnimationFrame(() => {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        });
    }

    // === File Upload ===
    attachBtn.addEventListener('click', function () {
        fileInput.click();
    });

    fileInput.addEventListener('change', async function () {
        var files = Array.from(fileInput.files || []);
        fileInput.value = '';
        if (files.length === 0) return;

        for (var i = 0; i < files.length; i++) {
            var file = files[i];
            var formData = new FormData();
            formData.append('file', file, file.name);

            try {
                var res = await fetch(API + '/api/upload', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token },
                    body: formData
                });
                if (!res.ok) {
                    var err = await res.json().catch(function () { return {}; });
                    console.warn('Upload failed:', err.error || res.status);
                    continue;
                }
                var data = await res.json();
                var uploaded = data.files && data.files[0];
                if (uploaded) {
                    var att = {
                        file: file,
                        path: uploaded.path,
                        file_id: uploaded.file_id,
                        filename: uploaded.filename,
                        content_type: uploaded.content_type,
                        previewUrl: null
                    };
                    if (file.type.startsWith('image/')) {
                        att.previewUrl = URL.createObjectURL(file);
                    }
                    pendingAttachments.push(att);
                }
            } catch (e) {
                console.warn('Upload error:', e);
            }
        }
        updateAttachmentPreview();
    });

    function updateAttachmentPreview() {
        if (pendingAttachments.length === 0) {
            attachmentPreview.classList.add('hidden');
            attachmentPreview.innerHTML = '';
            return;
        }
        attachmentPreview.classList.remove('hidden');
        attachmentPreview.innerHTML = '';
        pendingAttachments.forEach(function (att, idx) {
            var item = document.createElement('div');
            item.className = 'attachment-thumb';

            if (att.previewUrl) {
                var img = document.createElement('img');
                img.src = att.previewUrl;
                img.alt = att.filename;
                item.appendChild(img);
            } else {
                var nameSpan = document.createElement('span');
                nameSpan.className = 'attachment-name';
                nameSpan.textContent = att.filename;
                item.appendChild(nameSpan);
            }

            var removeBtn = document.createElement('button');
            removeBtn.className = 'attachment-remove';
            removeBtn.textContent = '\u00d7';
            removeBtn.addEventListener('click', function () {
                if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
                pendingAttachments.splice(idx, 1);
                updateAttachmentPreview();
            });
            item.appendChild(removeBtn);
            attachmentPreview.appendChild(item);
        });
    }

    // === Sessions (disk-based) ===
    async function loadSessions() {
        try {
            const res = await fetch(API + '/api/sessions', {
                headers: { 'Authorization': 'Bearer ' + token }
            });
            if (!res.ok) return;
            const data = await res.json();
            var sessions = data.sessions || [];
            var defaultTitle = getBrandProductName();

            // Sync currentSessionId with backend's active marker and update title
            if (sessions.length > 0) {
                var active = sessions.find(function (s) { return s.active; });
                if (active) {
                    currentSessionId = active.session_id;
                    chatTitle.textContent = active.title || defaultTitle;
                } else if (!currentSessionId) {
                    // No active marker, use first (most recent)
                    currentSessionId = sessions[0].session_id;
                    chatTitle.textContent = sessions[0].title || defaultTitle;
                } else {
                    // currentSessionId already set — sync title from session list
                    var current = sessions.find(function (s) { return s.session_id === currentSessionId; });
                    if (current) {
                        chatTitle.textContent = current.title || defaultTitle;
                    }
                }
            }

            renderSessions(sessions);
        } catch (e) { /* ignore */ }
    }

    function renderSessions(sessions) {
        sessionList.innerHTML = '';
        sessions.forEach(function (s) {
            var li = document.createElement('li');

            var icon = document.createElement('span');
            icon.className = 'session-icon';

            var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('aria-hidden', 'true');
            svg.setAttribute('focusable', 'false');

            var bubble = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            bubble.setAttribute('d', 'M7 6.5h10a3 3 0 0 1 3 3V14a3 3 0 0 1-3 3H11l-4 3v-3H7a3 3 0 0 1-3-3V9.5a3 3 0 0 1 3-3z');
            bubble.setAttribute('stroke-linejoin', 'round');

            var sparkle = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            sparkle.setAttribute('d', 'M15.5 9.3l.6 1.2 1.2.6-1.2.6-.6 1.2-.6-1.2-1.2-.6 1.2-.6z');
            sparkle.setAttribute('class', 'sparkle');
            sparkle.setAttribute('stroke-linecap', 'round');
            sparkle.setAttribute('stroke-linejoin', 'round');

            svg.appendChild(bubble);
            svg.appendChild(sparkle);
            icon.appendChild(svg);
            li.appendChild(icon);

            var info = document.createElement('div');
            info.className = 'session-info';

            var titleSpan = document.createElement('span');
            titleSpan.className = 'session-title';
            titleSpan.textContent = s.title || getNewChatLabel();
            info.appendChild(titleSpan);

            var metaSpan = document.createElement('span');
            metaSpan.className = 'session-meta';
            var parts = [];
            if (s.message_count) parts.push(s.message_count + ' msgs');
            if (s.updated_at) {
                try {
                    var d = new Date(s.updated_at);
                    parts.push(d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
                } catch (e) { /* ignore */ }
            }
            metaSpan.textContent = parts.join(' · ');
            info.appendChild(metaSpan);

            li.appendChild(info);

            li.dataset.id = s.session_id;
            if (s.session_id === currentSessionId) li.classList.add('active');

            li.addEventListener('click', function () {
                switchSession(s.session_id, s.title, sessions);
            });
            sessionList.appendChild(li);
        });
    }

    async function switchSession(sessionId, title, allSessions) {
        if (sessionId === currentSessionId) {
            closeSidebar();
            return;
        }
        currentSessionId = sessionId;
        chatTitle.textContent = title || getBrandProductName();
        messagesEl.innerHTML = '';
        contextBar.classList.add('hidden');
        closeSidebar();

        // Tell backend to switch active pointer
        try {
            await fetch(API + '/api/sessions/switch', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token
                },
                body: JSON.stringify({ session_id: sessionId })
            });
        } catch (e) { /* ignore */ }

        // Load history messages
        try {
            var res = await fetch(API + '/api/sessions/' + encodeURIComponent(sessionId) + '/messages', {
                headers: { 'Authorization': 'Bearer ' + token }
            });
            if (res.ok) {
                var data = await res.json();
                var msgs = data.messages || [];
                msgs.forEach(function (m) {
                    var el = appendMessage(m.role, '', false, m.timestamp, { history: true });
                    var contentEl = el.querySelector('.msg-content');
                    contentEl.innerHTML = renderMarkdown(m.content);
                    if (m.media && m.media.length > 0) {
                        renderMediaAttachments(el, m.media);
                    }
                });
                scrollToBottom();
            }
        } catch (e) { /* ignore */ }

        // Re-render sidebar to update active highlight
        if (allSessions) renderSessions(allSessions);
    }

    async function createNewSession() {
        try {
            const res = await fetch(API + '/api/sessions/new', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token
                }
            });
            if (res.ok) {
                const data = await res.json();
                console.debug('[session] /api/sessions/new response', data && data.session ? data.session : data);
                currentSessionId = data.session.session_id;
                chatTitle.textContent = data.session.title || getBrandProductName();
                messagesEl.innerHTML = '';
                contextBar.classList.add('hidden');
                // Show the greeting from the HTTP response (not SSE)
                if (data.session.greeting) {
                    var el = appendMessage('assistant', '', false, Date.now());
                    var contentEl = el.querySelector('.msg-content') || el;
                    contentEl.innerHTML = renderMarkdown(data.session.greeting);
                    scrollToBottom();
                    console.debug('[session] rendered HTTP greeting', { session_id: currentSessionId });
                }
                closeSidebar();
                loadSessions();
            }
        } catch (e) { /* ignore */ }
    }

    newChatBtn.addEventListener('click', createNewSession);

    // === Sidebar ===
    sidebarToggle.addEventListener('click', openSidebar);
    sidebarClose.addEventListener('click', closeSidebar);
    sidebarOverlay.addEventListener('click', closeSidebar);

    function openSidebar() {
        sidebar.classList.add('open');
        sidebarOverlay.classList.add('open');
    }
    function closeSidebar() {
        sidebar.classList.remove('open');
        sidebarOverlay.classList.remove('open');
    }

    // === Markdown Renderer ===
    function renderMarkdown(text) {
        if (!text) return '';

        // Escape HTML
        let html = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        // Code blocks: ```lang\n...\n```
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
            const highlighted = lang ? highlightCode(code.trim(), lang) : escapeCodeContent(code.trim());
            return '<pre><code' + (lang ? ' class="lang-' + lang + '"' : '') + '>' + highlighted + '</code></pre>';
        });

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Italic
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Headers
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

        // Links
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

        // Tables: header | sep | rows
        html = html.replace(/((?:^\|.+\|[ \t]*\n)+)/gm, function (tableBlock) {
            var lines = tableBlock.trim().split('\n');
            if (lines.length < 2) return tableBlock;
            // Check if second line is separator
            var sepLine = lines[1].trim();
            if (!/^\|[\s:|-]+\|$/.test(sepLine)) return tableBlock;
            var parseRow = function (line) {
                return line.replace(/^\|/, '').replace(/\|$/, '').split('|').map(function (c) { return c.trim(); });
            };
            var headers = parseRow(lines[0]);
            var result = '<table><thead><tr>';
            headers.forEach(function (h) { result += '<th>' + h + '</th>'; });
            result += '</tr></thead><tbody>';
            for (var i = 2; i < lines.length; i++) {
                var cells = parseRow(lines[i]);
                result += '<tr>';
                cells.forEach(function (c) { result += '<td>' + c + '</td>'; });
                result += '</tr>';
            }
            result += '</tbody></table>';
            return result;
        });

        // Blockquotes: merge adjacent > lines
        html = html.replace(/(^&gt; .+$(\n&gt; .+$)*)/gm, function (block) {
            var content = block.replace(/^&gt; /gm, '');
            return '<blockquote>' + content + '</blockquote>';
        });

        // Unordered lists
        html = html.replace(/^[*-] (.+)$/gm, '<li>$1</li>');
        html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, function (match) {
            return '<ul>' + match + '</ul>';
        });

        // Ordered lists: match consecutive numbered lines and wrap in <ol>
        html = html.replace(/((?:^\d+\. .+$\n?)+)/gm, function (block) {
            var items = block.trim().replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
            return '<ol>' + items + '</ol>';
        });

        // Paragraphs: split on double newlines
        html = html.split(/\n\n+/).map(block => {
            block = block.trim();
            if (!block) return '';
            if (block.startsWith('<h') || block.startsWith('<pre') ||
                block.startsWith('<ul') || block.startsWith('<ol') ||
                block.startsWith('<li') || block.startsWith('<table') ||
                block.startsWith('<blockquote')) {
                return block;
            }
            return '<p>' + block.replace(/\n/g, '<br>') + '</p>';
        }).join('\n');

        return html;
    }

    function escapeCodeContent(code) {
        return code;
    }

    // Lightweight syntax highlighting for common languages
    function highlightCode(code, lang) {
        lang = lang.toLowerCase();
        var rules = getSyntaxRules(lang);
        if (!rules) return code;

        // Tokenize: walk through code, match rules in priority order
        var result = '';
        var i = 0;
        while (i < code.length) {
            var matched = false;
            for (var r = 0; r < rules.length; r++) {
                var rule = rules[r];
                rule.pattern.lastIndex = i;
                var m = rule.pattern.exec(code);
                if (m && m.index === i) {
                    result += '<span class="' + rule.cls + '">' + m[0] + '</span>';
                    i += m[0].length;
                    matched = true;
                    break;
                }
            }
            if (!matched) {
                result += code[i];
                i++;
            }
        }
        return result;
    }

    function getSyntaxRules(lang) {
        var kwPython = 'def|class|if|elif|else|for|while|return|import|from|as|try|except|finally|with|raise|pass|break|continue|and|or|not|in|is|None|True|False|self|yield|async|await|lambda';
        var kwJS = 'function|var|let|const|if|else|for|while|do|return|switch|case|break|continue|new|this|class|extends|import|export|from|default|try|catch|finally|throw|typeof|instanceof|async|await|yield|null|undefined|true|false|of|in';
        var kwSQL = 'SELECT|FROM|WHERE|AND|OR|NOT|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|ALTER|DROP|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|ASC|DESC|LIMIT|OFFSET|HAVING|UNION|AS|DISTINCT|COUNT|SUM|AVG|MIN|MAX|NULL|IS|LIKE|BETWEEN|IN|EXISTS|CASE|WHEN|THEN|ELSE|END';
        var kwBash = 'if|then|else|elif|fi|for|while|do|done|case|esac|function|return|exit|echo|export|source|local|readonly|shift|set|unset|trap|eval|exec|cd|pwd|true|false';

        var commentSingle = { pattern: /#[^\n]*/g, cls: 'cmt' };
        var commentSlash = { pattern: /\/\/[^\n]*/g, cls: 'cmt' };
        var commentDash = { pattern: /--[^\n]*/g, cls: 'cmt' };
        var stringDouble = { pattern: /"(?:[^"\\]|\\.)*"/g, cls: 'str' };
        var stringSingle = { pattern: /'(?:[^'\\]|\\.)*'/g, cls: 'str' };
        var stringBacktick = { pattern: /`(?:[^`\\]|\\.)*`/g, cls: 'str' };
        var tripleDouble = { pattern: /"""[\s\S]*?"""/g, cls: 'str' };
        var tripleSingle = { pattern: /'''[\s\S]*?'''/g, cls: 'str' };
        var number = { pattern: /\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b/g, cls: 'num' };

        switch (lang) {
            case 'python':
            case 'py':
                return [
                    tripleDouble, tripleSingle, commentSingle,
                    stringDouble, stringSingle,
                    { pattern: new RegExp('\\b(?:' + kwPython + ')\\b', 'g'), cls: 'kw' },
                    number
                ];
            case 'javascript':
            case 'js':
            case 'typescript':
            case 'ts':
                return [
                    commentSlash,
                    stringBacktick, stringDouble, stringSingle,
                    { pattern: new RegExp('\\b(?:' + kwJS + ')\\b', 'g'), cls: 'kw' },
                    number
                ];
            case 'json':
                return [
                    stringDouble,
                    { pattern: /\b(?:true|false|null)\b/g, cls: 'kw' },
                    number
                ];
            case 'bash':
            case 'sh':
            case 'shell':
                return [
                    commentSingle,
                    stringDouble, stringSingle,
                    { pattern: new RegExp('\\b(?:' + kwBash + ')\\b', 'g'), cls: 'kw' },
                    { pattern: /\$\w+/g, cls: 'num' },
                    number
                ];
            case 'sql':
                return [
                    commentDash, commentSlash,
                    stringSingle, stringDouble,
                    { pattern: new RegExp('\\b(?:' + kwSQL + ')\\b', 'gi'), cls: 'kw' },
                    number
                ];
            default:
                return null;
        }
    }

    // === SW Registration ===
    // Unregister any old Service Workers and clear their caches
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.getRegistrations().then(function (regs) {
            regs.forEach(function (r) { r.unregister(); });
        });
        if ('caches' in window) {
            caches.keys().then(function (keys) {
                keys.forEach(function (k) { caches.delete(k); });
            });
        }
    }

    // === Start ===
    init();
})();
