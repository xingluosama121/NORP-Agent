// ── Global state (some per-tab, some shared) ──
var isWaiting = false;
var isStreaming = false;
var config = {};
var currentThinkingEl = null;
var currentReplyEl = null;
var currentCmdEl = null;
var selectedFiles = [];

// ── 平滑思考渲染（打字机效果）──
// 后端把思考过程按 ~80 字符批量推送，前端一次性蹦出大段显得卡顿。
// 这里用 requestAnimationFrame 逐字平滑渲染，让视觉连贯。
var SMOOTH_THINK_CHARS_PER_FRAME = 3;   // 每帧渲染字符数（约 60fps → ~180 字/秒）
var SMOOTH_THINK_FAST_CHARS = 24;       // 思考块结束时加速刷完的每帧字符数

// Default content refs point to the default panel — updated by switchToTab()
var chatContent = document.getElementById('chat-content-default');
var cmdContent = document.getElementById('cmd-content-default');

var userInput = document.getElementById('user-input');
var sendBtn = document.getElementById('send-btn');
var stopBtn = document.getElementById('stop-btn');
var fileBadges = document.getElementById('file-badges');
var statusText = document.getElementById('status-text');
var tokenDisplay = document.getElementById('token-display');
var headerTokensEl = document.getElementById('header-tokens');
// Grand total tokens across all sessions (persists across tab switches)
var grandTotalTokens = { input: 0, output: 0, tool: 0 };
var settingsModal = document.getElementById('settings-modal');
var apikeyModal = document.getElementById('apikey-modal');
var aboutModal = document.getElementById('about-modal');
var firstrunModal = document.getElementById('firstrun-modal');
var securityOffWarningModal = document.getElementById('security-off-warning-modal');
var norpSafetyOffWarningModal = document.getElementById('norp-safety-off-warning-modal');
var askUserModal = document.getElementById('ask-user-modal');
var askUserQuestion = document.getElementById('ask-user-question');
var askUserReplyInput = document.getElementById('ask-user-reply-input');
var askUserReplyBtn = document.getElementById('ask-user-reply-btn');
var askUserCancelBtn = document.getElementById('ask-user-cancel-btn');
var toastContainer = document.getElementById('toast-container');

var confirmWriteModal = document.getElementById('confirm-write-modal');
var confirmWriteMessage = document.getElementById('confirm-write-message');
var confirmWriteDetail = document.getElementById('confirm-write-detail');
var confirmWriteTitle = document.getElementById('confirm-write-title');
var confirmWriteConfirmBtn = document.getElementById('confirm-write-confirm-btn');
var confirmWriteCancelBtn = document.getElementById('confirm-write-cancel-btn');
var confirmWriteNoMoreBtn = document.getElementById('confirm-write-nomore-btn');
var confirmWriteNoMoreHint = document.getElementById('confirm-write-nomore-hint');
var _pendingConfirmSessionId = '';
var _pendingAskSessionId = '';
var _pendingAskQuestionRaw = '';

// ── Message Center ──
var messageCenter = [];  // {id, type:'blocked'|'ask'|'plugin'|'crash', title, detail, tabName, time}
var _msgIdCounter = 0;

// ── Crash Recovery State ──
var _crashRetryTimer = null;
var _crashRetryCount = 0;
var _crashRetryMax = 3;
var _crashNotificationEl = null;
var _lastCrashReason = '';

// ── NORP Notification ──
function showNorpNotification(blockedType, detail, tabName) {
    var container = document.getElementById('norp-notification-container');
    var now = new Date();
    var timeStr = now.getHours().toString().padStart(2,'0') + ':' +
                  now.getMinutes().toString().padStart(2,'0') + ':' +
                  now.getSeconds().toString().padStart(2,'0');
    var div = document.createElement('div');
    div.className = 'norp-notification';
    var headerIcon = '🛡️';
    var statusText = t('norp_notif_blocked') || 'BLOCKED';
    div.innerHTML =
        '<button class="norp-notif-close" onclick="this.parentElement.remove()">✕</button>' +
        '<div class="norp-notif-header">' + headerIcon + ' NORP ' + t('norp_notif_header') + '</div>' +
        '<div class="norp-notif-body">' +
            '<div><b>' + (t('norp_notif_type') || 'Type') + ':</b> ' + escapeHtml(blockedType) + '</div>' +
            '<div><b>' + (t('norp_notif_status') || 'Status') + ':</b> <span style="color:#ff8a80;">' + statusText + '</span></div>' +
            (detail ? '<div style="margin-top:2px;font-size:11px;opacity:0.85;">' + escapeHtml(detail) + '</div>' : '') +
            (tabName ? '<div style="margin-top:2px;font-size:10px;opacity:0.7;">' + (t('norp_notif_tab') || 'From') + ': ' + escapeHtml(tabName) + '</div>' : '') +
        '</div>' +
        '<div class="norp-notif-time">' + timeStr + '</div>';
    container.appendChild(div);

    // Auto-dismiss after 7 seconds
    var timer = setTimeout(function() {
        if (div.parentNode) {
            div.classList.add('fade-out');
            setTimeout(function() { if (div.parentNode) div.remove(); }, 300);
        }
    }, 7000);

    // Store timer reference so clicking close cancels the auto-dismiss
    div._dismissTimer = timer;
    div.querySelector('.norp-notif-close').addEventListener('click', function() {
        clearTimeout(div._dismissTimer);
    });
}

// ── Crash Notification (top-right, red, 20s auto-dismiss) ──
function showCrashNotification(reason) {
    _lastCrashReason = reason || 'Unknown error';

    // Remove any existing crash notification first
    if (_crashNotificationEl && _crashNotificationEl.parentNode) {
        _crashNotificationEl.remove();
    }
    if (_crashRetryTimer) {
        clearTimeout(_crashRetryTimer);
        _crashRetryTimer = null;
    }

    var container = document.getElementById('norp-notification-container');
    var now = new Date();
    var timeStr = now.getHours().toString().padStart(2,'0') + ':' +
                  now.getMinutes().toString().padStart(2,'0') + ':' +
                  now.getSeconds().toString().padStart(2,'0');

    var div = document.createElement('div');
    div.className = 'crash-notification';

    // Build reason detail
    var reasonHtml = '';
    if (reason) {
        var shortReason = reason.length > 200 ? reason.substring(0, 200) + '…' : reason;
        reasonHtml = '<div class="crash-notif-reason">' + escapeHtml(shortReason) + '</div>';
    }

    div.innerHTML =
        '<button class="crash-notif-close" title="Dismiss">✕</button>' +
        '<div class="crash-notif-header">💥 NORP Agent Crashed</div>' +
        '<div class="crash-notif-body">' +
            '<div class="crash-notif-status">⚠️ The rendering engine encountered a fatal error</div>' +
            reasonHtml +
            '<div class="crash-notif-actions">' +
                '<button class="crash-notif-retry-btn" title="Try to recover now">🔄 Retry Now</button>' +
                '<span class="crash-notif-auto-hint">Auto-retrying in 3s…</span>' +
            '</div>' +
        '</div>' +
        '<div class="crash-notif-time">' + timeStr + '</div>';

    container.appendChild(div);
    _crashNotificationEl = div;

    // Attach close button
    div.querySelector('.crash-notif-close').addEventListener('click', function() {
        dismissCrashNotification();
    });

    // Attach retry button
    div.querySelector('.crash-notif-retry-btn').addEventListener('click', function() {
        // Cancel auto-retry and do it now
        if (_crashRetryTimer) {
            clearTimeout(_crashRetryTimer);
            _crashRetryTimer = null;
        }
        _crashRetryCount = 0;
        var hintEl = div.querySelector('.crash-notif-auto-hint');
        if (hintEl) hintEl.textContent = 'Retrying now…';
        var btnEl = div.querySelector('.crash-notif-retry-btn');
        if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Retrying…'; }
        _attemptCrashRecovery();
    });

    // Silent auto-retry after 3 seconds
    var autoHintEl = div.querySelector('.crash-notif-auto-hint');
    _crashRetryTimer = setTimeout(function() {
        _crashRetryTimer = null;
        if (autoHintEl) autoHintEl.textContent = 'Auto-retrying now…';
        var btnEl = div.querySelector('.crash-notif-retry-btn');
        if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Retrying…'; }
        _attemptCrashRecovery();
    }, 3000);

    // Auto-dismiss after 20 seconds (only if not already dismissed)
    div._autoDismissTimer = setTimeout(function() {
        if (div.parentNode) {
            div.classList.add('fade-out');
            setTimeout(function() { if (div.parentNode) div.remove(); }, 300);
        }
    }, 20000);

    // Record to message center
    addToMessageCenter('crash', 'NORP Agent Crashed', _lastCrashReason, '');
}

function dismissCrashNotification() {
    if (_crashRetryTimer) {
        clearTimeout(_crashRetryTimer);
        _crashRetryTimer = null;
    }
    if (_crashNotificationEl) {
        if (_crashNotificationEl._autoDismissTimer) {
            clearTimeout(_crashNotificationEl._autoDismissTimer);
        }
        if (_crashNotificationEl.parentNode) {
            _crashNotificationEl.classList.add('fade-out');
            var el = _crashNotificationEl;
            setTimeout(function() { if (el.parentNode) el.remove(); }, 300);
        }
        _crashNotificationEl = null;
    }
}

function _attemptCrashRecovery() {
    _crashRetryCount++;
    var hintEl = _crashNotificationEl ? _crashNotificationEl.querySelector('.crash-notif-auto-hint') : null;
    var btnEl = _crashNotificationEl ? _crashNotificationEl.querySelector('.crash-notif-retry-btn') : null;

    try {
        // Step 1: Save tab state to localStorage
        try { saveTabsToStorage(); } catch(e) {}

        // Step 2: Stop all polling timers on all tabs
        for (var i = 0; i < tabs.length; i++) {
            if (tabs[i].pollingTimer) {
                clearInterval(tabs[i].pollingTimer);
                tabs[i].pollingTimer = null;
            }
            tabs[i].isStreaming = false;
            tabs[i].isWaiting = false;
        }
        isStreaming = false;
        isWaiting = false;

        // Step 3: Update UI
        if (sendBtn) sendBtn.style.display = 'inline-block';
        if (stopBtn) stopBtn.style.display = 'none';
        if (statusText) statusText.textContent = t('ready') || 'Ready';
        updateTabBar();

        // Step 4: Try to re-establish backend connectivity
        if (window.pywebview && window.pywebview.api && window.pywebview.api.log_frontend_error) {
            try {
                window.pywebview.api.log_frontend_error('[CrashRecovery] Attempt #' + _crashRetryCount + ' — reason: ' + _lastCrashReason);
            } catch(e) {}
        }

        // Step 5: Attempt to restore tabs if possible
        if (tabs.length === 0 || !tabs[0].dbId) {
            // Critical: no tabs exist — need full page reload
            if (_crashRetryCount < _crashRetryMax) {
                if (hintEl) hintEl.textContent = 'Critical state — reloading page in 2s…';
                _crashRetryTimer = setTimeout(function() {
                    _crashRetryTimer = null;
                    location.reload();
                }, 2000);
                return;
            }
        }

        // Step 6: Try to restore tabs from localStorage
        try {
            var savedTabs = loadTabsFromStorage();
            if (savedTabs && savedTabs.length > 0) {
                // Re-attempt recovery via restoreTabsAfterCrash
                var firstTab = tabs.length > 0 ? tabs[0] : null;
                if (firstTab) {
                    restoreTabsAfterCrash(firstTab).then(function() {
                        _onRecoverySuccess();
                    }).catch(function(e) {
                        _onRecoveryFail(e);
                    });
                } else {
                    _onRecoverySuccess();
                }
            } else {
                _onRecoverySuccess();
            }
        } catch(e) {
            _onRecoveryFail(e);
        }
    } catch(e) {
        _onRecoveryFail(e);
    }
}

function _onRecoverySuccess() {
    _crashRetryCount = 0;
    dismissCrashNotification();
    showToast('✅ Agent recovered successfully', 3000);
    addToMessageCenter('crash', 'NORP Agent Recovered', 'Recovery succeeded after crash', '');
    userInput.disabled = false;
    userInput.focus();
}

function _onRecoveryFail(err) {
    if (_crashRetryCount < _crashRetryMax) {
        var delay = _crashRetryCount * 2000;
        var hintEl = _crashNotificationEl ? _crashNotificationEl.querySelector('.crash-notif-auto-hint') : null;
        if (hintEl) hintEl.textContent = 'Recovery failed (attempt ' + _crashRetryCount + '/' + _crashRetryMax + ') — retrying in ' + (delay/1000) + 's…';
        var btnEl = _crashNotificationEl ? _crashNotificationEl.querySelector('.crash-notif-retry-btn') : null;
        if (btnEl) { btnEl.disabled = false; btnEl.textContent = '🔄 Retry Now'; }
        _crashRetryTimer = setTimeout(function() {
            _crashRetryTimer = null;
            var b = _crashNotificationEl ? _crashNotificationEl.querySelector('.crash-notif-retry-btn') : null;
            if (b) { b.disabled = true; b.textContent = '⏳ Retrying…'; }
            _attemptCrashRecovery();
        }, delay);
    } else {
        // Max retries reached — suggest page reload
        if (_crashNotificationEl) {
            var body = _crashNotificationEl.querySelector('.crash-notif-body');
            if (body) {
                body.innerHTML =
                    '<div class="crash-notif-status" style="color:#ff8a80;">❌ Recovery failed after ' + _crashRetryMax + ' attempts</div>' +
                    '<div class="crash-notif-reason">' + escapeHtml(err ? (err.message || String(err)) : 'Unknown error') + '</div>' +
                    '<div class="crash-notif-actions">' +
                        '<button class="crash-notif-reload-btn" onclick="location.reload()">🔁 Reload Page</button>' +
                    '</div>';
            }
            // Keep notification visible (don't auto-dismiss)
            if (_crashNotificationEl._autoDismissTimer) {
                clearTimeout(_crashNotificationEl._autoDismissTimer);
            }
            _crashNotificationEl._autoDismissTimer = setTimeout(function() {
                if (_crashNotificationEl && _crashNotificationEl.parentNode) {
                    _crashNotificationEl.classList.add('fade-out');
                    setTimeout(function() { if (_crashNotificationEl && _crashNotificationEl.parentNode) _crashNotificationEl.remove(); }, 300);
                }
            }, 60000); // 60s for final failure state
        }
        addToMessageCenter('crash', 'NORP Agent — Recovery Failed', 'All ' + _crashRetryMax + ' recovery attempts failed. Page reload recommended.', '');
    }
}

// ── Message Center Functions ──
function addToMessageCenter(type, title, detail, tabName) {
    _msgIdCounter++;
    var now = new Date();
    var timeStr = now.getHours().toString().padStart(2,'0') + ':' +
                  now.getMinutes().toString().padStart(2,'0') + ':' +
                  now.getSeconds().toString().padStart(2,'0');
    var msg = {
        id: _msgIdCounter,
        type: type,  // 'blocked', 'ask', 'plugin'
        title: title,
        detail: detail || '',
        tabName: tabName || '',
        time: timeStr
    };
    messageCenter.unshift(msg);
    updateMsgBadge();
    // If message center is open, re-render
    var mcModal = document.getElementById('message-center-modal');
    if (mcModal && mcModal.style.display === 'block') {
        renderMessageCenter();
    }
}

function removeMessage(id) {
    messageCenter = messageCenter.filter(function(m) { return m.id !== id; });
    updateMsgBadge();
    renderMessageCenter();
}

function clearAllMessages() {
    messageCenter = [];
    updateMsgBadge();
    renderMessageCenter();
}

function updateMsgBadge() {
    var badge = document.getElementById('msg-center-badge');
    if (!badge) return;
    var count = messageCenter.length;
    if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = 'block';
    } else {
        badge.style.display = 'none';
    }
}

function renderMessageCenter() {
    var list = document.getElementById('message-center-list');
    var empty = document.getElementById('message-center-empty');
    if (messageCenter.length === 0) {
        list.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';
    var html = '';
    for (var i = 0; i < messageCenter.length; i++) {
        var m = messageCenter[i];
        var cssClass = '';
        var icon = '';
        if (m.type === 'blocked') { cssClass = 'msg-blocked'; icon = '🛡️❌'; }
        else if (m.type === 'ask') { cssClass = 'msg-ask'; icon = '🤖'; }
        else if (m.type === 'plugin') { cssClass = 'msg-plugin'; icon = '⚠️'; }
        else if (m.type === 'crash') { cssClass = 'msg-crash'; icon = '💥'; }
        html += '<div class="msg-item ' + cssClass + '">' +
            '<button class="msg-close" onclick="removeMessage(' + m.id + ')" title="' + (t('msg_delete') || 'Delete') + '">✕</button>' +
            '<div class="msg-title">' + icon + ' ' + escapeHtml(m.title) + '</div>' +
            (m.detail ? '<div class="msg-detail">' + escapeHtml(m.detail) + '</div>' : '') +
            '<div class="msg-tab">' + (t('norp_notif_tab') || 'From') + ': ' + escapeHtml(m.tabName || '—') + '</div>' +
            '<div class="msg-time">' + m.time + '</div>' +
        '</div>';
    }
    list.innerHTML = html;
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg, duration) {
    duration = duration || 2000;
    var div = document.createElement('div');
    div.className = 'toast';
    div.textContent = msg;
    toastContainer.appendChild(div);
    setTimeout(function() { div.classList.add('show'); }, 10);
    setTimeout(function() { div.classList.remove('show'); setTimeout(function() { div.remove(); }, 300); }, duration);
}

function updateTokenDisplay(usage, optTab) {
    var tab = optTab || getActiveTab();
    if (!tab) return;
    if (usage) {
        if (usage.input_tokens !== undefined) tab.totalTokens.input = usage.input_tokens;
        if (usage.output_tokens !== undefined) tab.totalTokens.output = usage.output_tokens;
        if (usage.tool_call_tokens !== undefined) tab.totalTokens.tool = usage.tool_call_tokens;
    }

    // Update header grand total — sum of ALL tabs' totalTokens
    if (usage) {
        var prevInput = grandTotalTokens.input;
        var prevOutput = grandTotalTokens.output;
        var prevTool = grandTotalTokens.tool;
        recalcGrandTotalTokens();
        if (grandTotalTokens.input !== prevInput || grandTotalTokens.output !== prevOutput || grandTotalTokens.tool !== prevTool) {
            updateHeaderTokensDisplay();
        }
    }

    // Only update the global token display if this is the active tab
    if (tab.uiId !== activeTabId) return;

    var grandTotal = tab.totalTokens.input + tab.totalTokens.output + tab.totalTokens.tool;
    if (grandTotal <= 0) return;

    var parts = [];
    if (tab.totalTokens.input > 0)
        parts.push('<span class="tok-in">' + tab.totalTokens.input.toLocaleString() + '</span> in');
    if (tab.totalTokens.output > 0)
        parts.push('<span class="tok-out">' + tab.totalTokens.output.toLocaleString() + '</span> out');
    if (tab.totalTokens.tool > 0)
        parts.push('<span class="tok-tool">' + tab.totalTokens.tool.toLocaleString() + '</span> tool');
    parts.push('= <span class="tok-total">' + grandTotal.toLocaleString() + '</span>');
    tokenDisplay.innerHTML = 'Tokens: ' + parts.join(' + ');
}

function updateHeaderTokensDisplay() {
    if (!headerTokensEl) return;
    var total = grandTotalTokens.input + grandTotalTokens.output + grandTotalTokens.tool;
    if (total <= 0) {
        headerTokensEl.textContent = '';
        return;
    }
    headerTokensEl.textContent = t('total_tokens') + ': ' + total.toLocaleString();
}

// Recalculate grand total tokens by summing all tabs' per-session totals
function recalcGrandTotalTokens() {
    var input = 0, output = 0, tool = 0;
    for (var i = 0; i < tabs.length; i++) {
        var tt = tabs[i].totalTokens;
        if (tt) {
            input += tt.input || 0;
            output += tt.output || 0;
            tool += tt.tool || 0;
        }
    }
    grandTotalTokens.input = input;
    grandTotalTokens.output = output;
    grandTotalTokens.tool = tool;
}

function updateWorkspaceDisplay(workspace) {
    var el = document.getElementById('workspace-display');
    if (!el) return;
    if (workspace) {
        el.textContent = '📁 ' + workspace;
        el.style.display = 'inline';
    } else {
        el.textContent = '';
        el.style.display = 'none';
    }
}

function resetTokenDisplay() {
    var tab = getActiveTab();
    if (tab) tab.totalTokens = { input: 0, output: 0, tool: 0 };
    tokenDisplay.innerHTML = '';
    recalcGrandTotalTokens();
    updateHeaderTokensDisplay();
}

var SCROLL_THRESHOLD = 80;

function isNearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_THRESHOLD;
}

function scrollChatToBottom() {
    if (isNearBottom(chatContent)) {
        chatContent.scrollTop = chatContent.scrollHeight;
    }
}
function scrollCmdToBottom() {
    if (isNearBottom(cmdContent)) {
        cmdContent.scrollTop = cmdContent.scrollHeight;
    }
}

function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function safeKatex(latex, displayMode) {
    try {
        return katex.renderToString(latex, {
            displayMode: displayMode,
            throwOnError: false,
            trust: true
        });
    } catch (err) {
        var escaped = latex.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return '<code style="color:#dc3545;background:#fff5f5;padding:2px 6px;border-radius:3px;font-size:0.85em;" title="KaTeX render error: ' + err.message.replace(/"/g, '&quot;') + '">' + escaped + '</code>';
    }
}

var TABLE_LINE_RE = /^\|.+\|.+\|/;
var TABLE_SEP_RE  = /^\|[\s\-:]+\|[\s\-:]+\|/;

function fixTableNewlines(text) {
    var lines = text.split('\n');
    var out = [];
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var isTableLine = TABLE_LINE_RE.test(line);
        var prevLine = i > 0 ? lines[i - 1] : '';
        var isPrevTable = TABLE_LINE_RE.test(prevLine);
        var isPrevSep = TABLE_SEP_RE.test(prevLine);
        var isPrevBlank = prevLine.trim() === '';

        if (isTableLine && !isPrevTable && !isPrevSep && !isPrevBlank && i > 0) {
            out.push('');
        }
        out.push(line);
    }
    return out.join('\n');
}

function renderContent(text) {
    if (!text) return '';
    if (typeof marked === 'undefined') {
        return escapeHtml(text);
    }

    try {
        var processed = fixTableNewlines(text);
        processed = processed.replace(/\u200B/g, '').replace(/\uFEFF/g, '');

        var lines = processed.split('\n');
        var newLines = [];
        var fencedBlocks = [];
        var inCode = false;
        var lang = '';
        var codeContent = [];

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            var startMatch = line.match(/^```(\s*)(\w*)/);
            var endMatch = line.match(/^```\s*$/);

            if (!inCode) {
                if (startMatch) {
                    inCode = true;
                    lang = startMatch[2] || '';
                    codeContent = [];
                    var idx = fencedBlocks.length;
                    fencedBlocks.push({ lang: lang, code: '', unclosed: false });
                    newLines.push('\u00A7\u00A7FENCED_' + idx + '\u00A7\u00A7');
                } else {
                    newLines.push(line);
                }
            } else {
                if (endMatch) {
                    var code = codeContent.join('\n');
                    var lastBlock = fencedBlocks[fencedBlocks.length - 1];
                    lastBlock.code = code;
                    lastBlock.unclosed = false;
                    inCode = false;
                    lang = '';
                    codeContent = [];
                } else {
                    codeContent.push(line);
                }
            }
        }

        if (inCode) {
            var code = codeContent.join('\n');
            var lastBlock = fencedBlocks[fencedBlocks.length - 1];
            lastBlock.code = code;
            lastBlock.unclosed = true;
        }

        processed = newLines.join('\n');

        var inlineCodes = [];
        processed = processed.replace(/(`+)([^`]+?)\1/g, function(match, ticks, code) {
            inlineCodes.push(code);
            return '\u00A7\u00A7INLINE_' + (inlineCodes.length - 1) + '\u00A7\u00A7';
        });

        var displayMaths = [];
        var inlineMaths = [];

        processed = processed.replace(/\$\$([\s\S]*?)\$\$/g, function(match, math) {
            displayMaths.push(math.trim());
            return '\u00A7\u00A7MATH_DISPLAY_' + (displayMaths.length - 1) + '\u00A7\u00A7';
        });
        processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, function(match, math) {
            displayMaths.push(math.trim());
            return '\u00A7\u00A7MATH_DISPLAY_' + (displayMaths.length - 1) + '\u00A7\u00A7';
        });
        processed = processed.replace(/\\\((.*?)\\\)/g, function(match, math) {
            inlineMaths.push(math.trim());
            return '\u00A7\u00A7MATH_INLINE_' + (inlineMaths.length - 1) + '\u00A7\u00A7';
        });
        processed = processed.replace(/(^|[^\w\$])\$(?!\$)([^$\n]+?)\$(?![a-zA-Z0-9])/g, function(match, before, math) {
            inlineMaths.push(math.trim());
            return before + '\u00A7\u00A7MATH_INLINE_' + (inlineMaths.length - 1) + '\u00A7\u00A7';
        });

        var html = marked.parse(processed);

        html = html.replace(/\u00A7\u00A7INLINE_(\d+)\u00A7\u00A7/g, function(match, idx) {
            var code = inlineCodes[parseInt(idx)] || '';
            var escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            return '<code>' + escaped + '</code>';
        });

        html = html.replace(/\u00A7\u00A7FENCED_(\d+)\u00A7\u00A7/g, function(match, idx) {
            var block = fencedBlocks[parseInt(idx)] || { lang: '', code: '', unclosed: false };
            var rawCode = block.code.trim();
            rawCode = rawCode.replace(/\n{3,}/g, '\n\n');
            var displayCode = rawCode.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            var encodedRaw = encodeURIComponent(rawCode);
            var langClass = block.lang ? ' class="language-' + block.lang + '"' : '';

            var unclosedLabel = block.unclosed
                ? ' <span style="font-weight:normal;color:#999;font-size:10px;">(unclosed)</span>'
                : '';

            return '<div class="code-block-wrapper">' +
                '<div class="code-header">' +
                    '<span class="code-language">' + (block.lang || 'text') + unclosedLabel + '</span>' +
                    '<div><button class="copy-btn" data-code="' + encodedRaw + '">Copy</button></div>' +
                '</div>' +
                '<pre><code' + langClass + '>' + displayCode + '</code></pre>' +
            '</div>';
        });

        if (typeof katex !== 'undefined') {
            html = html.replace(/<p>\s*\u00A7\u00A7MATH_DISPLAY_(\d+)\u00A7\u00A7\s*<\/p>/g, function(match, idx) {
                var latex = displayMaths[parseInt(idx)] || '';
                var rendered = safeKatex(latex, true);
                var encodedLatex = encodeURIComponent(latex);
                return '<div class="math-display-wrapper"><div class="math-display" style="position:relative;display:inline-block;" data-latex="' + encodedLatex + '">' + rendered + '<button class="math-copy-btn" data-latex="' + encodedLatex + '">Copy LaTeX</button></div></div>';
            });
            html = html.replace(/\u00A7\u00A7MATH_DISPLAY_(\d+)\u00A7\u00A7/g, function(match, idx) {
                var latex = displayMaths[parseInt(idx)] || '';
                var rendered = safeKatex(latex, true);
                var encodedLatex = encodeURIComponent(latex);
                return '<div class="math-display-wrapper"><div class="math-display" style="position:relative;display:inline-block;" data-latex="' + encodedLatex + '">' + rendered + '<button class="math-copy-btn" data-latex="' + encodedLatex + '">Copy LaTeX</button></div></div>';
            });
            html = html.replace(/\u00A7\u00A7MATH_INLINE_(\d+)\u00A7\u00A7/g, function(match, idx) {
                var latex = inlineMaths[parseInt(idx)] || '';
                var rendered = safeKatex(latex, false);
                var encodedLatex = encodeURIComponent(latex);
                return '<span class="math-inline-wrapper"><span class="math-inline" data-latex="' + encodedLatex + '">' + rendered + '</span><button class="math-copy-btn" data-latex="' + encodedLatex + '">Copy LaTeX</button></span>';
            });
        } else {
            html = html.replace(/\u00A7\u00A7MATH_DISPLAY_(\d+)\u00A7\u00A7/g, function(m, i) { return '$$' + (displayMaths[parseInt(i)] || '') + '$$'; });
            html = html.replace(/\u00A7\u00A7MATH_INLINE_(\d+)\u00A7\u00A7/g, function(m, i) { return '\\(' + (inlineMaths[parseInt(i)] || '') + '\\)'; });
        }

        html = html.replace(/<table([^>]*)>/g, function(match, attrs) {
            if (/class="[^"]*\bmarkdown-table\b[^"]*"/.test(match)) return match;
            if (/class="/.test(match)) return '<table' + attrs.replace(/class="([^"]*)"/, 'class="$1 markdown-table"') + '>';
            return '<table class="markdown-table"' + attrs + '>';
        });

        return html;
    } catch (e) {
        try { window.pywebview.api.log_frontend_error('[renderContent] ' + e.message); } catch (_) {}
        return escapeHtml(text);
    }
}

document.addEventListener('click', function(e) {
    // Only handle clicks inside panel-content areas
    if (!e.target.closest('.panel-content')) return;

    var copyBtn = e.target.closest('.copy-btn');
    if (copyBtn) {
        e.preventDefault(); e.stopPropagation();
        // Read code directly from the <code> element so copying works
        // as soon as the code block is closed, even if the rest of the
        // reply is still streaming.
        var wrapper = copyBtn.closest('.code-block-wrapper');
        var codeEl = wrapper ? wrapper.querySelector('pre code') : null;
        var code = codeEl ? codeEl.textContent : '';
        if (!code) {
            // Fallback to data-code for backward compatibility
            code = copyBtn.dataset.code || '';
            if (code) code = decodeURIComponent(code);
        }
        if (code) fallbackCopy(code);
        return;
    }

    var mathCopyBtn = e.target.closest('.math-copy-btn');
    if (mathCopyBtn) {
        e.preventDefault(); e.stopPropagation();
        var encodedLatex = mathCopyBtn.dataset.latex || '';
        if (encodedLatex) fallbackCopy(decodeURIComponent(encodedLatex));
        return;
    }

    var target = e.target.closest('a');
    if (target && target.href) {
        if (target.href.indexOf('http://') === 0 || target.href.indexOf('https://') === 0) {
            e.preventDefault(); e.stopPropagation();
            openExternal(target.href);
        }
        return;
    }

    var crBtn = e.target.closest('.copy-reply-btn');
    if (crBtn) {
        e.preventDefault(); e.stopPropagation();
        var parentText = crBtn.closest('.assistant-text');
        if (parentText) {
            var rawText = parentText.dataset.raw || parentText.textContent || '';
            fallbackCopy(rawText);
        }
        return;
    }
});

function fallbackCopy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast(t('copied'));
        }).catch(function() {
            _fallbackCopyExec(text);
        });
    } else {
        _fallbackCopyExec(text);
    }
}

function _fallbackCopyExec(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); showToast(t('copied')); } catch(e) { showToast(t('copy_failed')); }
    document.body.removeChild(ta);
}

function openExternal(url) {
    showExternalViewer(url);
}

function showExternalViewer(url) {
    var viewer = document.getElementById('external-viewer');
    if (!viewer) return;
    var iframe = document.getElementById('external-iframe');
    var openBtn = document.getElementById('external-open-btn');
    var urlEl = document.getElementById('external-viewer-url');
    if (iframe) iframe.src = url;
    if (openBtn) openBtn.dataset.url = url;
    if (urlEl) urlEl.value = url;
    viewer.classList.add('show');
}

function closeExternalViewer() {
    var viewer = document.getElementById('external-viewer');
    if (!viewer) return;
    viewer.classList.remove('show');
    var iframe = document.getElementById('external-iframe');
    if (iframe) iframe.src = 'about:blank';
}

function navigateExternalViewer() {
    var urlEl = document.getElementById('external-viewer-url');
    var iframe = document.getElementById('external-iframe');
    var openBtn = document.getElementById('external-open-btn');
    if (!urlEl) return;
    var url = urlEl.value.trim();
    if (!url) return;
    if (!/^(https?:|data:|about:|file:)/i.test(url)) {
        url = 'https://' + url;
    }
    if (iframe) iframe.src = url;
    if (openBtn) openBtn.dataset.url = url;
}

var urlInput = document.getElementById('external-viewer-url');
if (urlInput) {
    urlInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            navigateExternalViewer();
            this.blur();
        }
    });
}

document.getElementById('external-back-btn').addEventListener('click', closeExternalViewer);

var extOpenBtn = document.getElementById('external-open-btn');
if (extOpenBtn) {
    extOpenBtn.addEventListener('click', function() {
        var urlEl = document.getElementById('external-viewer-url');
        var url = (urlEl && urlEl.value.trim()) || this.dataset.url;
        if (!url) return;
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_url) {
            window.pywebview.api.open_url(url);
        } else {
            window.open(url, '_blank');
        }
    });
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var viewer = document.getElementById('external-viewer');
        if (viewer && viewer.classList.contains('show')) closeExternalViewer();
    }
});

function openHTMLContent(htmlContent) {
    if (!htmlContent || !htmlContent.trim()) return;
    var fullHtml = htmlContent;
    if (htmlContent.toLowerCase().indexOf('<!doctype') === -1 && htmlContent.toLowerCase().indexOf('<html') === -1) {
        fullHtml = '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>HTML Preview</title>\n</head>\n<body>\n' + htmlContent + '\n</body>\n</html>';
    }
    var encoded = encodeURIComponent(fullHtml);
    var dataUri = 'data:text/html;charset=utf-8,' + encoded;
    openExternal(dataUri);
}

function createUserMessageDOM(text) {
    var div = document.createElement('div');
    div.className = 'message user-msg';
    var inner = document.createElement('div');
    inner.className = 'user-text';
    inner.textContent = text;
    div.appendChild(inner);
    return div;
}

function appendError(msg) {
    var div = document.createElement('div');
    div.className = 'error-text';
    div.textContent = translateErrorMsg(msg);
    chatContent.appendChild(div);
    scrollChatToBottom();
}

// 前端轻量错误转义：把英文技术错误 / 堆栈转成用户能看懂的提示。
// 后端 error_i18n.py 已做主要转义，这里兜底处理前端本地捕获的异常。
function translateErrorMsg(msg) {
    if (!msg) return msg;
    var s = String(msg);
    var low = s.toLowerCase();
    // 堆栈信息：提取最后一行真正的错误描述
    if (low.indexOf('traceback') !== -1 || s.indexOf('  File ') !== -1) {
        var lines = s.split('\n');
        for (var i = lines.length - 1; i >= 0; i--) {
            var line = lines[i].trim();
            if (line && line.indexOf('File ') !== 0 && line.indexOf('Traceback') !== 0) {
                s = line;
                low = s.toLowerCase();
                break;
            }
        }
    }
    var map = [
        ['failed to fetch', '无法连接到后端服务，请检查网络连接。'],
        ['networkerror', '网络错误，请检查网络连接。'],
        ['connection refused', '连接被拒绝，后端服务可能未启动。'],
        ['timed out', '请求超时，请稍后重试。'],
        ['timeout', '请求超时，请稍后重试。'],
        ['unicode decode', '文件编码无法识别，可能是二进制文件（图片/视频等）。'],
        ['unsupported format', '不支持的文件格式。'],
        ['file too large', '文件过大。'],
        ['permission denied', '没有权限执行此操作。'],
        ['not found', '找不到目标资源。'],
        ['polling error', '轮询后端事件失败'],
        ['send failed', '发送失败'],
        ['invalid api key', 'API 密钥无效。'],
    ];
    for (var i = 0; i < map.length; i++) {
        if (low.indexOf(map[i][0]) !== -1) {
            return map[i][1];
        }
    }
    return s;
}

function appendAskUser(question) {
    var div = document.createElement('div');
    div.className = 'question-bubble';
    div.innerHTML = renderContent(question);
    chatContent.appendChild(div);
    scrollChatToBottom();
}

function showAskUserModal(question, sessionId) {
    hideConfirmWriteModal();
    _pendingAskQuestionRaw = question;
    askUserQuestion.innerHTML = renderContent(question);
    askUserReplyInput.value = '';
    askUserReplyInput.style.height = 'auto';
    askUserModal.style.display = 'block';
    askUserReplyInput.focus();
    statusText.textContent = t('waiting_reply');
    // Use explicit sessionId if provided (from background tab), otherwise active tab
    _pendingAskSessionId = sessionId || (getActiveTab() ? getActiveTab().dbId : '');
}

function hideAskUserModal() {
    askUserModal.style.display = 'none';
    askUserQuestion.innerHTML = '';
    askUserReplyInput.value = '';
}

async function handleAskUserReply() {
    var text = askUserReplyInput.value.trim();
    if (!text) return;

    var userDiv = createUserMessageDOM(text);
    chatContent.appendChild(userDiv);
    scrollChatToBottom();

    hideAskUserModal();
    _pendingAskQuestionRaw = '';
    updateTabBar();

    try {
        var sid = _pendingAskSessionId || (getActiveTab() ? getActiveTab().dbId : '');
        _pendingAskSessionId = '';
        await window.pywebview.api.provide_user_input(sid, text);
    } catch(e) {
        appendError(t('reply') + ' ' + t('save_failed') + ': ' + e.message);
        finishStream();
    }
}

async function handleAskUserCancel() {
    hideAskUserModal();
    _pendingAskQuestionRaw = '';
    updateTabBar();
    appendError(t('task_cancelled'));
    var sid = _pendingAskSessionId || (getActiveTab() ? getActiveTab().dbId : '');
    _pendingAskSessionId = '';
    try { await window.pywebview.api.stop_task(sid); } catch(e) {}
    // Clear the per-tab polling timer
    var tab = getActiveTab();
    if (tab && tab.pollingTimer) {
        clearInterval(tab.pollingTimer);
        tab.pollingTimer = null;
    }
    finishStream();
}

var TOOL_NAMES_EN = {
    'write_file': 'Write',
    'delete_file': 'Delete',
    'replace_in_file': 'Modify'
};

function showConfirmWriteModal(tool, path, sessionId, isPlugin) {
    hideAskUserModal();
    var action = TOOL_NAMES_EN[tool] || tool;
    confirmWriteMessage.textContent = tf('confirm_write_msg', null, action.toLowerCase(), path);
    confirmWriteDetail.textContent = tool === 'delete_file'
        ? t('confirm_delete_warn')
        : t('confirm_overwrite_warn');
    if (confirmWriteTitle) {
        confirmWriteTitle.textContent = t('confirm_operation');
    }
    // 「不再显示」按钮仅对原生工具确认弹窗可见；插件工具审批弹窗不提供
    var showNoMore = !isPlugin;
    if (confirmWriteNoMoreBtn) {
        confirmWriteNoMoreBtn.style.display = showNoMore ? '' : 'none';
    }
    if (confirmWriteNoMoreHint) {
        confirmWriteNoMoreHint.style.display = showNoMore ? '' : 'none';
    }
    confirmWriteModal.style.display = 'block';
    statusText.textContent = t('waiting_confirm');
    _pendingConfirmSessionId = sessionId || (getActiveTab() ? getActiveTab().dbId : '');
}
function hideConfirmWriteModal() {
    confirmWriteModal.style.display = 'none';
    confirmWriteMessage.textContent = '';
    confirmWriteDetail.textContent = '';
}

async function handleConfirmWriteConfirm() {
    hideConfirmWriteModal();
    statusText.textContent = t('running');
    try {
        var sid = _pendingConfirmSessionId || (getActiveTab() ? getActiveTab().dbId : '');
        _pendingConfirmSessionId = '';
        await window.pywebview.api.provide_user_input(sid, '__confirm__');
    } catch(e) {
        appendError(t('confirm_btn') + ' ' + t('save_failed') + ': ' + e.message);
        finishStream();
    }
}

async function handleConfirmWriteNoMore() {
    // 「不再显示」：本次放行 + 后端持久化关闭原生工具确认
    hideConfirmWriteModal();
    statusText.textContent = t('running');
    try {
        var sid = _pendingConfirmSessionId || (getActiveTab() ? getActiveTab().dbId : '');
        _pendingConfirmSessionId = '';
        await window.pywebview.api.provide_user_input(sid, '__confirm_no_more__');
    } catch(e) {
        appendError(t('confirm_btn') + ' ' + t('save_failed') + ': ' + e.message);
        finishStream();
    }
}

async function handleConfirmWriteCancel() {
    hideConfirmWriteModal();
    statusText.textContent = t('running');
    try {
        var sid = _pendingConfirmSessionId || (getActiveTab() ? getActiveTab().dbId : '');
        _pendingConfirmSessionId = '';
        await window.pywebview.api.provide_user_input(sid, '__cancel__');
    } catch(e) {
        appendError(t('cancel') + ' ' + t('save_failed') + ': ' + e.message);
        finishStream();
    }
}

function appendOrAccumulateThinking(text) {
    if (!currentThinkingEl) {
        var details = document.createElement('details');
        details.className = 'reasoning-details';
        details.open = true;
        details.style.marginBottom = '8px';

        var summary = document.createElement('summary');
        summary.textContent = t('thinking');
        summary.style.cssText = 'cursor:pointer; font-size:0.9em; color:#555; padding:4px 0; user-select:none;';

        var content = document.createElement('div');
        content.className = 'reasoning-box';
        content.style.marginBottom = '0';
        content.dataset.raw = text;      // 完整文本（真相源），复制/保存依赖它
        content.textContent = '';        // 显示内容由平滑渲染器逐字填充

        details.appendChild(summary);
        details.appendChild(content);
        chatContent.appendChild(details);

        currentThinkingEl = details;
        currentThinkingEl._content = content;
    } else {
        var c = currentThinkingEl._content;
        c.dataset.raw += text;           // 只更新真相源，显示进度由渲染器推进
    }
    _startSmoothThinking(currentThinkingEl._content);
    scrollChatToBottom();
}

// 启动（或恢复）思考内容的平滑渲染循环
function _startSmoothThinking(contentEl) {
    if (!contentEl || contentEl._rafId) return;
    contentEl._rafId = requestAnimationFrame(function() { _tickSmoothThinking(contentEl); });
}

// 每帧把思考内容向完整文本推进若干字符，实现逐字平滑显示
function _tickSmoothThinking(contentEl) {
    if (!contentEl || !contentEl.parentNode) {
        contentEl._rafId = null;
        return;
    }
    var raw = contentEl.dataset.raw || '';
    var shown = contentEl.textContent || '';
    if (shown.length < raw.length) {
        var perFrame = contentEl._fast ? SMOOTH_THINK_FAST_CHARS : SMOOTH_THINK_CHARS_PER_FRAME;
        var target = Math.min(raw.length, shown.length + perFrame);
        contentEl.textContent = raw.substring(0, target);
        // 滚动所属面板到底部（用 closest 精确定位，避免跨 tab 误滚动）
        var scroller = contentEl.closest('.panel-content');
        if (scroller && isNearBottom(scroller)) {
            scroller.scrollTop = scroller.scrollHeight;
        }
    }
    if ((contentEl.textContent || '').length < raw.length) {
        contentEl._rafId = requestAnimationFrame(function() { _tickSmoothThinking(contentEl); });
    } else {
        contentEl._rafId = null;
    }
}

function appendOrAccumulateReply(text) {
    if (!currentReplyEl) {
        var wrapper = document.createElement('div');
        wrapper.className = 'message assistant-msg';

        var inner = document.createElement('div');
        inner.className = 'assistant-text';
        inner.dataset.raw = text;
        inner.innerHTML = renderContent(text);

        var copyBtn = document.createElement('button');
        copyBtn.className = 'copy-reply-btn';
        copyBtn.textContent = t('copy');
        inner.appendChild(copyBtn);

        wrapper.appendChild(inner);
        chatContent.appendChild(wrapper);

        currentReplyEl = wrapper;
        currentReplyEl._inner = inner;
        currentReplyEl._copyBtn = copyBtn;
    } else {
        var inner = currentReplyEl._inner;
        inner.dataset.raw += text;
        inner.innerHTML = renderContent(inner.dataset.raw);
        var existingBtn = inner.querySelector('.copy-reply-btn');
        if (!existingBtn) {
            var copyBtn = document.createElement('button');
            copyBtn.className = 'copy-reply-btn';
            copyBtn.textContent = 'Copy';
            inner.appendChild(copyBtn);
            currentReplyEl._copyBtn = copyBtn;
        }
    }
    scrollChatToBottom();
}

function appendOrAccumulateCommand(text) {
    // Format JSON tool-call info for human-readable display.
    // JSON strings from the backend have \n \t \r escaped, which
    // textContent would show as literal characters — so we must
    // parse & reformat (or at least unescape) before display.
    var displayText = _formatCommandText(text);

    if (!currentCmdEl) {
        currentCmdEl = document.createElement('div');
        currentCmdEl.className = 'reasoning-box';
        currentCmdEl.style.fontFamily = 'monospace';
        currentCmdEl.style.fontSize = '11px';
        currentCmdEl.dataset.raw = text;
        currentCmdEl.textContent = displayText;
        cmdContent.appendChild(currentCmdEl);
    } else {
        currentCmdEl.dataset.raw += '\n' + text;
        currentCmdEl.textContent += '\n' + displayText;
    }
    scrollCmdToBottom();
}

function _formatCommandText(text) {
    try {
        var parsed = JSON.parse(text);
        var toolName = parsed.tool || '';
        var args = parsed.args || {};
        var lines = ['▶ ' + toolName];
        for (var key in args) {
            if (!args.hasOwnProperty(key)) continue;
            var val = args[key];
            var valStr = (typeof val === 'string') ? val : JSON.stringify(val);
            // Truncate very long values to keep the panel readable
            if (valStr.length > 300) {
                valStr = valStr.substring(0, 300) + '…';
            }
            // Indent each line of multi-line values for alignment
            var keyLabel = '  ' + key + ': ';
            var firstLine = true;
            valStr.split('\n').forEach(function(line) {
                if (firstLine) {
                    lines.push(keyLabel + line);
                    firstLine = false;
                } else {
                    lines.push('    ' + line);
                }
            });
        }
        return lines.join('\n');
    } catch(e) {
        // Fallback for non-JSON text: unescape common JSON escape sequences
        return text.replace(/\\n/g, '\n').replace(/\\t/g, '\t').replace(/\\r/g, '\r');
    }
}

function flushAccumulated() {
    finalizeCurrentThinking();
    if (currentReplyEl && currentReplyEl._inner) {
        if (currentReplyEl._copyBtn) {
            currentReplyEl._copyBtn.style.display = 'inline-block';
        }
    }
    currentThinkingEl = null;
    currentReplyEl = null;
    currentCmdEl = null;
}

function finalizeCurrentThinking() {
    // Keep thinking expanded by default so user can always see reasoning.
    if (currentThinkingEl) {
        // Update summary to indicate this phase is complete
        var sum = currentThinkingEl.querySelector('summary');
        if (sum) sum.textContent = '\u2705 ' + t('thinking');
        // 切换到快速模式，让尚未渲染完的思考内容平滑地加速刷完，
        // 而不是瞬间跳变（思考块结束通常紧接着回复开始，视觉更自然）
        var content = currentThinkingEl._content;
        if (content && (content.textContent || '').length < (content.dataset.raw || '').length) {
            content._fast = true;
            _startSmoothThinking(content);
            // 兜底：窗口隐藏时 RAF 会被节流/暂停，超时后强制补全，确保内容不残缺
            var raw = content.dataset.raw || '';
            setTimeout(function() {
                if (content.parentNode && (content.textContent || '').length < raw.length) {
                    content.textContent = raw;
                    if (content._rafId) { cancelAnimationFrame(content._rafId); content._rafId = null; }
                }
            }, 300);
        }
        currentThinkingEl = null;
    }
}

function handleEvent(event, tab) {
    // tab is optional — when called from handleEventForTab (background tab),
    // it provides the correct tab context for token display and modals.
    if (event.indexOf('T:') === 0) {
        // If reply already started and new thinking arrives,
        // finalize the *previous* thinking block first.
        if (currentReplyEl && currentThinkingEl) {
            finalizeCurrentThinking();
        }
        appendOrAccumulateThinking(event.slice(2));
    } else if (event.indexOf('R:') === 0) {
        // First output chunk → finalize the preceding thinking block
        if (!currentReplyEl) {
            finalizeCurrentThinking();
        }
        var replyText = event.slice(2);
        appendOrAccumulateReply(replyText);
        // NORP Safety Block Detection (from command return string)
        if (replyText.indexOf('NORP安全系统拦截') !== -1 || replyText.indexOf('NORP safety') !== -1) {
            _handleNorpBlock(replyText, tab);
        }
    } else if (event.indexOf('C:') === 0) {
        flushAccumulated();
        appendOrAccumulateCommand(event.slice(2));
    } else if (event.indexOf('U:') === 0) {
        try {
            var usage = JSON.parse(event.slice(2));
            updateTokenDisplay(usage, tab);
        } catch(e) {}
    } else if (event.indexOf('E:') === 0) {
        flushAccumulated();
        var errMsg = event.slice(2);
        appendError(errMsg);
        // NORP Safety Block Detection (from ValueError)
        if (errMsg.indexOf('NORP安全系统拦截') !== -1 || errMsg.indexOf('NORP safety') !== -1) {
            _handleNorpBlock(errMsg, tab);
        }
    } else if (event.indexOf('F:') === 0) {
        // Backend requests explicit flush of current thinking block
        finalizeCurrentThinking();
    } else if (event.indexOf('WC:') === 0) {
        flushAccumulated();
        var confirmData;
        try {
            confirmData = JSON.parse(event.slice(3));
        } catch(e) {
            confirmData = { tool: 'write_file', path: '' };
        }
        var wcSessionId = tab ? tab.dbId : (getActiveTab() ? getActiveTab().dbId : '');
        // Only show modal if this is the active tab; otherwise defer until user switches
        if (tab && tab.uiId === activeTabId) {
            showConfirmWriteModal(confirmData.tool, confirmData.path, wcSessionId, confirmData.is_plugin);
        } else if (tab) {
            // Background tab — store confirm request and show indicator
            tab.pendingConfirm = { tool: confirmData.tool, path: confirmData.path, sessionId: wcSessionId, isPlugin: confirmData.is_plugin };
            tab.isWaiting = true;
            updateTabBar();
        }
    } else if (event.indexOf('Q:') === 0) {
        flushAccumulated();
        var question;
        try {
            question = JSON.parse(event.slice(2));
        } catch(e) {
            question = event.slice(2);
        }
        appendAskUser(question);
        var qSessionId = tab ? tab.dbId : (getActiveTab() ? getActiveTab().dbId : '');
        // Record to message center
        var tabName = tab ? tab.title : (getActiveTab() ? getActiveTab().title : '');
        addToMessageCenter('ask', 'Agent Question', typeof question === 'string' ? question.substring(0, 120) : '', tabName);
        // Only show modal if this is the active tab; otherwise defer until user switches
        if (tab && tab.uiId === activeTabId) {
            showAskUserModal(question, qSessionId);
        } else if (tab) {
            // Background tab — store question and show blue indicator
            tab.pendingQuestion = { text: question, sessionId: qSessionId };
            tab.isWaiting = true;
            updateTabBar();
        }
    } else if (event.indexOf('D:') === 0) {
        finalizeCurrentThinking();
        var directText = event.slice(2);
        if (currentReplyEl && currentReplyEl._inner) {
            if (currentReplyEl._copyBtn) {
                currentReplyEl._copyBtn.style.display = 'inline-block';
            }
            currentThinkingEl = null;
            currentReplyEl = null;
            currentCmdEl = null;
        } else {
            flushAccumulated();
            var wrapper = document.createElement('div');
            wrapper.className = 'message assistant-msg';
            var inner = document.createElement('div');
            inner.className = 'assistant-text';
            inner.dataset.raw = directText;
            inner.innerHTML = renderContent(directText);
            var copyBtn = document.createElement('button');
            copyBtn.className = 'copy-reply-btn';
            copyBtn.textContent = t('copy');
            copyBtn.style.display = 'inline-block';
            inner.appendChild(copyBtn);
            wrapper.appendChild(inner);
            chatContent.appendChild(wrapper);
        }
        scrollChatToBottom();
    }
}

// ── NORP Safety Block Detection ──
function _handleNorpBlock(rawMsg, tab) {
    // Parse blocked type from message: "NORP安全系统拦截: {reason}（威胁等级: {level}）"
    var blockedType = 'unknown';
    var detail = rawMsg;

    // Try to extract the tool/command type
    var reasonMatch = rawMsg.match(/NORP安全系统拦截:\s*(.+?)（威胁等级/);
    if (reasonMatch) {
        detail = reasonMatch[1].trim();
    } else {
        reasonMatch = rawMsg.match(/NORP安全系统拦截:\s*(.+)/);
        if (reasonMatch) detail = reasonMatch[1].trim();
    }

    // Determine blocked type from the reason
    if (rawMsg.indexOf('危险命令') !== -1 || rawMsg.indexOf('dangerous') !== -1) {
        blockedType = 'exec_cmd (危险命令)';
    } else if (rawMsg.indexOf('UAC') !== -1 || rawMsg.indexOf('提权') !== -1) {
        blockedType = 'exec_cmd (UAC提权)';
    } else if (rawMsg.indexOf('路径越界') !== -1 || rawMsg.indexOf('path traversal') !== -1) {
        blockedType = 'path (路径越界)';
    } else if (rawMsg.indexOf('路径') !== -1) {
        blockedType = 'path';
    } else if (rawMsg.indexOf('命令') !== -1 || rawMsg.indexOf('command') !== -1) {
        blockedType = 'exec_cmd';
    }

    var tabName = tab ? tab.title : (getActiveTab() ? getActiveTab().title : '');
    showNorpNotification(blockedType, detail, tabName);
    addToMessageCenter('blocked', blockedType, detail, tabName);
}

// ── Per-tab event handler: swaps globals so background tabs can render ──
function handleEventForTab(tab, event) {
    // Save current global context
    var savedChat = chatContent;
    var savedCmd = cmdContent;
    var savedThink = currentThinkingEl;
    var savedReply = currentReplyEl;
    var savedCmdEl = currentCmdEl;

    // Switch to tab's DOM context
    chatContent = tab.chatContent;
    cmdContent = tab.cmdContent;
    currentThinkingEl = tab.currentThinkingEl || null;
    currentReplyEl = tab.currentReplyEl || null;
    currentCmdEl = tab.currentCmdEl || null;

    // Process the event with the tab's context
    handleEvent(event, tab);

    // Save back tab's streaming state
    tab.currentThinkingEl = currentThinkingEl;
    tab.currentReplyEl = currentReplyEl;
    tab.currentCmdEl = currentCmdEl;

    // Restore active tab's global context
    var active = getActiveTab();
    if (active && active.uiId !== tab.uiId) {
        chatContent = savedChat;
        cmdContent = savedCmd;
        currentThinkingEl = savedThink;
        currentReplyEl = savedReply;
        currentCmdEl = savedCmdEl;
    }
    // If the processed tab IS the active tab, leave the new globals in place
}

function startPolling(tab) {
    if (!tab) return;
    // Clear any existing timer for this tab first
    if (tab.pollingTimer) clearInterval(tab.pollingTimer);

    resetTokenDisplay();

    var tabId = tab.uiId;
    var sid = tab.dbId;

    tab.pollingTimer = setInterval(async function() {
        try {
            var t = getTab(tabId);
            // Tab was closed — clean up
            if (!t || !t.dbId) {
                clearInterval(tab.pollingTimer);
                return;
            }

            var event = await window.pywebview.api.get_next_event(sid);
            if (event === null) {
                // Stream finished
                clearInterval(t.pollingTimer);
                t.pollingTimer = null;
                t.isStreaming = false;
                t.isWaiting = false;

                if (getActiveTab() && getActiveTab().uiId === tabId) {
                    finishStream();
                }
                updateTabBar();
                return;
            }
            if (event === 'WAIT') return;

            // ALWAYS process events — even for background tabs.
            // handleEventForTab swaps DOM context so rendering goes to the right panel.
            handleEventForTab(t, event);
        } catch(e) {
            var t2 = getTab(tabId);
            if (t2) {
                clearInterval(t2.pollingTimer);
                t2.pollingTimer = null;
                t2.isStreaming = false;
                t2.isWaiting = false;

                if (getActiveTab() && getActiveTab().uiId === tabId) {
                    finishStream();
                    appendError('Polling error: ' + e.message);
                }
            }
        }
    }, 50);
}

function finishStream() {
    flushAccumulated();
    hideAskUserModal();
    hideConfirmWriteModal();
    isStreaming = false;
    isWaiting = false;
    var tab = getActiveTab();
    if (tab) {
        tab.isStreaming = false;
        tab.isWaiting = false;
        // ★ 结块修复：用户手动停止后，必须同步清空 tab 持有的思考/回复/命令块引用。
        // 否则 handleEventForTab 下次会从 tab.currentThinkingEl 恢复旧引用，
        // 导致新消息的思考内容追加到旧的思考块里。
        tab.currentThinkingEl = null;
        tab.currentReplyEl = null;
        tab.currentCmdEl = null;
        // Clear this tab's polling timer if still running
        if (tab.pollingTimer) {
            clearInterval(tab.pollingTimer);
            tab.pollingTimer = null;
        }
    }
    stopBtn.style.display = 'none';
    sendBtn.style.display = 'inline-block';
    statusText.textContent = t('ready');
    resetTextareaHeight();
    updateTabBar();
    userInput.focus();
}

function autoResizeTextarea() {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
}

function resetTextareaHeight() {
    userInput.style.height = '';
}

function resetTextarea() {
    userInput.style.height = '';
    userInput.value = '';
}

function insertNewlineAtCursor() {
    var start = userInput.selectionStart;
    var end = userInput.selectionEnd;
    var value = userInput.value;
    userInput.value = value.substring(0, start) + '\n' + value.substring(end);
    userInput.selectionStart = userInput.selectionEnd = start + 1;
    autoResizeTextarea();
}

var ALLOWED_EXTS = [
    'txt', 'py', 'json', 'csv', 'css', 'html', 'md', 'js',
    'ts', 'tsx', 'jsx', 'yaml', 'yml', 'toml', 'xml',
    'sh', 'bat', 'ps1', 'ini', 'cfg', 'log', 'sql',
    'rs', 'go', 'c', 'cpp', 'h', 'java', 'kt', 'swift',
    'rb', 'php', 'lua', 'r', 'm', 'mm',
    'pdf', 'docx', 'xlsx'
];

// 视觉文件扩展名（图片 / 视频）—— 仅在设置中开启「视觉 API」后允许上传，
// 交由开发者接入的多模态视觉模型处理。
var VISUAL_IMAGE_EXTS = [
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico', 'tiff', 'tif'
];
var VISUAL_VIDEO_EXTS = [
    'mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv', 'm4v', 'mpg', 'mpeg'
];
var VISUAL_EXTS = VISUAL_IMAGE_EXTS.concat(VISUAL_VIDEO_EXTS);

function isVisualExt(ext) {
    return VISUAL_EXTS.indexOf(ext) !== -1;
}

function visionEnabled() {
    return !!(typeof config !== 'undefined' && config && config.vision_enabled);
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function getFileIcon(ext) {
    var icons = {
        'py':'🐍', 'js':'📜', 'ts':'📘', 'tsx':'📘', 'jsx':'📜',
        'html':'🌐', 'css':'🎨', 'json':'📋', 'csv':'📊',
        'txt':'📄', 'md':'📝', 'pdf':'📕', 'docx':'📘', 'xlsx':'📗',
        'yaml':'⚙️', 'yml':'⚙️', 'toml':'⚙️',
        'rs':'🦀', 'go':'🐹', 'java':'☕', 'kt':'📱', 'swift':'🍎',
        'c':'⚡', 'cpp':'⚡', 'h':'⚡',
        'sh':'💻', 'bat':'💻', 'ps1':'💻',
        'sql':'🗄️', 'rb':'💎', 'php':'🐘', 'lua':'🌙', 'r':'📊',
        'xml':'📋', 'ini':'⚙️', 'cfg':'⚙️', 'log':'📄'
    };
    return icons[ext] || '📎';
}

async function readFileAsDataURL(file) {
    return new Promise(function(resolve, reject) {
        var reader = new FileReader();
        reader.onload = function(e) {
            var full = e.target.result;
            var commaIdx = full.indexOf(',');
            resolve(commaIdx >= 0 ? full.substring(commaIdx + 1) : full);
        };
        reader.onerror = function(e) { reject(e.target.error); };
        reader.readAsDataURL(file);
    });
}

async function handleFiles(fileList) {
    var added = 0;
    var tab = getActiveTab();
    if (!tab) return;
    for (var i = 0; i < fileList.length; i++) {
        var file = fileList[i];
        if (file.size > 10 * 1024 * 1024) {
            showToast(t('file_too_large') + ': ' + file.name + ' (max 10MB)');
            continue;
        }
        var ext = file.name.split('.').pop().toLowerCase();
        if (isVisualExt(ext)) {
            // 视觉文件（图片/视频）：需开启「视觉 API」才接收
            if (!visionEnabled()) {
                showToast(t('vision_required_hint') + ': ' + file.name + ' (.' + ext + ')');
                continue;
            }
        } else if (ALLOWED_EXTS.indexOf(ext) === -1) {
            showToast(t('unsupported_format') + ': ' + file.name + ' (.' + ext + ')');
            continue;
        }
        try {
            var data = await readFileAsDataURL(file);
            if (!tab.selectedFiles) tab.selectedFiles = [];
            tab.selectedFiles.push({ name: file.name, size: file.size, type: ext, data: data });
            added++;
        } catch(err) {
            showToast(t('read_error') + ': ' + file.name + ' - ' + err.message);
        }
    }
    if (added > 0) {
        updateFileBadges();
        showToast(t('file_added') + ' ' + added);
        userInput.focus();
    }
}

function updateFileBadges() {
    var tab = getActiveTab();
    var files = tab ? (tab.selectedFiles || []) : [];
    if (files.length === 0) {
        fileBadges.innerHTML = '';
        return;
    }
    var html = '';
    for (var i = 0; i < files.length; i++) {
        var f = files[i];
        html += '<span class="file-badge"><span class="badge-remove-row"><span class="remove" data-index="' + i + '" title="Remove">✕</span></span><span class="badge-name">📄 ' + f.name + '</span><span class="badge-size">' + formatSize(f.size) + '</span></span>';
    }
    fileBadges.innerHTML = html;

    var removes = fileBadges.querySelectorAll('.remove');
    for (var j = 0; j < removes.length; j++) {
        removes[j].addEventListener('click', function(e) {
            e.stopPropagation();
            var idx = parseInt(this.dataset.index);
            var t = getActiveTab();
            if (t && t.selectedFiles) {
                t.selectedFiles.splice(idx, 1);
                updateFileBadges();
                showToast(t('file_removed'));
            }
        });
    }
}

document.addEventListener('dragover', function(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    var inputArea = document.querySelector('.input-area');
    if (inputArea) inputArea.style.background = '#e8f0fe';
});

document.addEventListener('dragleave', function(e) {
    e.preventDefault();
    var inputArea = document.querySelector('.input-area');
    if (inputArea) inputArea.style.background = '';
});

document.addEventListener('drop', function(e) {
    e.preventDefault();
    var inputArea = document.querySelector('.input-area');
    if (inputArea) inputArea.style.background = '';
    if (e.dataTransfer && e.dataTransfer.files.length > 0) {
        handleFiles(e.dataTransfer.files);
    }
});

document.addEventListener('paste', function(e) {
    var files = [];
    for (var i = 0; i < e.clipboardData.items.length; i++) {
        var item = e.clipboardData.items[i];
        if (item.kind === 'file') files.push(item.getAsFile());
    }
    if (files.length > 0) {
        e.preventDefault();
        e.stopPropagation();
        handleFiles(files);
    }
});

async function loadHistory() {
    try {
        var tab = getActiveTab();
        if (!tab) return;
        var history = await window.pywebview.api.get_initial_messages(tab.dbId);
        if (history && history.length) {
            for (var i = 0; i < history.length; i++) {
                var msg = history[i];
                var div = document.createElement('div');
                div.className = 'message';
                if (msg.role === 'user') {
                    div.className += ' user-msg';
                    var userInner = document.createElement('div');
                    userInner.className = 'user-text';
                    userInner.style.whiteSpace = 'pre-wrap';
                    userInner.textContent = msg.content || '';
                    div.appendChild(userInner);
                } else {
                    div.className += ' assistant-msg';
                    var inner = document.createElement('div');
                    inner.className = 'assistant-text';
                    inner.dataset.raw = msg.content || '';
                    inner.innerHTML = renderContent(msg.content || '');
                    var copyBtn = document.createElement('button');
                    copyBtn.className = 'copy-reply-btn';
                    copyBtn.textContent = 'Copy';
                    copyBtn.style.display = 'inline-block';
                    inner.appendChild(copyBtn);
                    div.appendChild(inner);
                }
                chatContent.appendChild(div);
            }
            scrollChatToBottom();
        }
        try {
            var memoryContent = await window.pywebview.api.get_memory_content(tab.dbId);
            if (memoryContent) {
                var memDiv = document.createElement('div');
                memDiv.className = 'message assistant-msg';
                var memInner = document.createElement('div');
                memInner.className = 'assistant-text';
                memInner.style.cssText = 'background:#f0f0f0;color:#666;font-size:0.9em;padding:8px 12px;';
                memInner.textContent = t('memory_loaded');
                memDiv.appendChild(memInner);
                chatContent.appendChild(memDiv);
                scrollChatToBottom();
            }
        } catch(e) {
            console.warn('Failed to check memory:', e);
        }
    } catch(e) {
        console.warn('Failed to load history:', e);
    }
}

async function handleSend() {
    var text = userInput.value.trim();
    var tab = getActiveTab();
    if (!tab) return;
    var currentFiles = tab.selectedFiles || [];
    if (currentFiles.length === 0 && !text) {
        showToast(t('enter_text_or_file'));
        return;
    }

    if (askUserModal.style.display === 'block') {
        showToast(t('please_reply_first'));
        return;
    }

    if (confirmWriteModal.style.display === 'block') {
        showToast(t('please_confirm_first'));
        return;
    }

    if (isStreaming) {
        showToast(t('agent_responding'));
        return;
    }

    var userDiv = document.createElement('div');
    userDiv.className = 'message user-msg';
    var userInner = document.createElement('div');
    userInner.className = 'user-text';
    var userHtml = '';

    if (currentFiles.length > 0) {
        userHtml += '<div class="file-list">';
        for (var i = 0; i < currentFiles.length; i++) {
            var f = currentFiles[i];
            var icon = getFileIcon(f.type);
            userHtml += '<div class="file-card"><div class="file-name"><span class="file-icon">' + icon + '</span>' + f.name + '</div><div class="file-meta">' + formatSize(f.size) + ' <span style="background:#e8e8e8;padding:0 4px;border-radius:2px;font-size:10px;">.' + f.type + '</span></div></div>';
        }
        userHtml += '</div>';
    }
    if (text) {
        userHtml += '<div style="margin-top:3px;white-space:pre-wrap;">' + escapeHtml(text) + '</div>';
    }
    userInner.innerHTML = userHtml;
    userDiv.appendChild(userInner);
    chatContent.appendChild(userDiv);
    scrollChatToBottom();

    var filesToSend = currentFiles.map(function(f) {
        return { name: f.name, size: f.size, type: f.type, data: f.data };
    });
    tab.selectedFiles = [];
    updateFileBadges();
    userInput.value = '';
    tab.inputValue = '';
    resetTextareaHeight();

    isWaiting = true;
    isStreaming = true;
    tab.isWaiting = true;
    tab.isStreaming = true;
    sendBtn.style.display = 'none';
    stopBtn.style.display = 'inline-block';
    statusText.textContent = t('running');
    updateTabBar();

    try {
        var uploadedFiles = [];
        if (filesToSend.length > 0) {
            uploadedFiles = await window.pywebview.api.upload_files(filesToSend);
        }

        var fullMessage = text;
        if (uploadedFiles.length > 0) {
            var fileContents = uploadedFiles.map(function(uf) {
                if (uf.error) {
                    return '[File: ' + uf.name + '] Error: ' + uf.error;
                }
                return '[File: ' + uf.name + ' (' + uf.type + ')]\n' + uf.content;
            }).join('\n\n---\n\n');
            if (fullMessage) {
                fullMessage = fullMessage + '\n\n--- Attached Files ---\n' + fileContents;
            } else {
                fullMessage = '--- Attached Files ---\n' + fileContents;
            }
        }

        var sid = tab.dbId;
        var result = await window.pywebview.api.send_message(sid, fullMessage);
        if (result && result.indexOf('error:') === 0) {
            appendError(result.slice(6));
            finishStream();
            return;
        }
        startPolling(tab);
    } catch(e) {
        appendError(t('send_failed') + ': ' + e.message);
        finishStream();
    }
}

async function handleStop() {
    try {
        var tab = getActiveTab();
        var sid = tab ? tab.dbId : '';
        await window.pywebview.api.stop_task(sid);
        hideAskUserModal();
        hideConfirmWriteModal();
        if (tab && tab.pollingTimer) {
            clearInterval(tab.pollingTimer);
            tab.pollingTimer = null;
        }
        finishStream();
        showToast(t('task_stopped'));
    } catch(e) {
        showToast(t('stop_failed') + ': ' + e.message);
    }
}

askUserReplyBtn.addEventListener('click', handleAskUserReply);
askUserCancelBtn.addEventListener('click', handleAskUserCancel);

askUserReplyInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        handleAskUserReply();
    } else if (e.key === 'Enter' && (e.ctrlKey || e.shiftKey)) {
        e.preventDefault();
        var start = askUserReplyInput.selectionStart;
        var end = askUserReplyInput.selectionEnd;
        var value = askUserReplyInput.value;
        askUserReplyInput.value = value.substring(0, start) + '\n' + value.substring(end);
        askUserReplyInput.selectionStart = askUserReplyInput.selectionEnd = start + 1;
    } else if (e.key === 'Escape') {
        e.preventDefault();
        handleAskUserCancel();
    }
});

confirmWriteConfirmBtn.addEventListener('click', handleConfirmWriteConfirm);
confirmWriteCancelBtn.addEventListener('click', handleConfirmWriteCancel);
if (confirmWriteNoMoreBtn) {
    confirmWriteNoMoreBtn.addEventListener('click', handleConfirmWriteNoMore);
}

document.addEventListener('keydown', function(e) {
    if (confirmWriteModal.style.display === 'block') {
        if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey) {
            e.preventDefault();
            handleConfirmWriteConfirm();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            handleConfirmWriteCancel();
        }
    }
});

var DEFAULT_MODELS = ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner'];

function ensureModelOptions(select, currentModel) {
    if (select.options.length === 0) {
        for (var i = 0; i < DEFAULT_MODELS.length; i++) {
            var opt = document.createElement('option');
            opt.value = DEFAULT_MODELS[i];
            opt.textContent = DEFAULT_MODELS[i];
            select.appendChild(opt);
        }
    }
    var validModel = currentModel;
    if (!validModel || validModel === '.' || validModel.trim() === '') {
        validModel = DEFAULT_MODELS[0];
    }
    var found = false;
    for (var j = 0; j < select.options.length; j++) {
        if (select.options[j].value === validModel) { found = true; break; }
    }
    if (!found) {
        select.value = select.options[0].value;
    } else {
        select.value = validModel;
    }
}

async function fetchModels(baseUrl) {
    var statusEl = document.getElementById('cfg-models-status');
    if (statusEl) statusEl.textContent = t('models_loading');
    try {
        var result = await window.pywebview.api.get_models_with_base(baseUrl);
        if (result && result.error) {
            if (statusEl) statusEl.textContent = result.error;
            ensureModelOptions(document.getElementById('cfg-model'), config.model);
            return;
        }
        var select = document.getElementById('cfg-model');
        if (!select) return;
        select.innerHTML = '';
        var models = Array.isArray(result) ? result : [];
        for (var i = 0; i < models.length; i++) {
            var m = models[i];
            var id = typeof m === 'string' ? m : (m.id || m);
            var opt = document.createElement('option');
            opt.value = id;
            opt.textContent = id;
            select.appendChild(opt);
        }
        ensureModelOptions(select, config.model);
        if (statusEl) statusEl.textContent = t('models_fetched').replace('{count}', models.length);
    } catch(e) {
        if (statusEl) statusEl.textContent = t('models_failed') + ': ' + (e.message || e);
        ensureModelOptions(document.getElementById('cfg-model'), config.model);
    }
}