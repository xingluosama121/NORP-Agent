window.addEventListener('pywebviewready', async function() {
    // ── Step 1: 等待后端初始化（loading_ready 做所有耗时操作）──
    var initResult;
    try {
        initResult = await window.pywebview.api.loading_ready();
    } catch(e) {
        showLoadingError('启动失败', 1, '无法连接到后端进程。\n\n' + (e.message || e.toString()));
        return;
    }

    if (initResult && initResult.status === 'error') {
        showLoadingError(initResult.title, initResult.fatal_count, initResult.details);
        return;  // 停止，不继续初始化
    }

    // ── Step 2: 后端就绪，正常初始化前端 ──
    // dismiss_splash 已在 loading_ready 中调用，这里兜底
    try { await window.pywebview.api.dismiss_splash(); } catch(e) {}

    try {
        updateLoadingProgress("正在加载配置...");
        config = await window.pywebview.api.get_config();
        // Apply saved language
        currentLang = config.language || 'zh_CN';
        setLanguage(currentLang);

        // Sync NORP checkbox with config before checking warning bar visibility
        var norpCb = document.getElementById('cfg-norp-safe-enabled');
        if (norpCb) {
            norpCb.checked = config.norp_safe_enabled !== false;
        }
        // Show NORP safety warning bar if safety is disabled
        updateNorpSafetyWarningBar();

        // ── Initialize first tab ──
        // Create backend session and set up the frontend tab
        updateLoadingProgress("正在创建会话...");
        var firstSessionId = await window.pywebview.api.create_session(config.project_root || '');
        var firstTab = createTab(firstSessionId, 'Tab 1', true, true, config.project_root || '');
        // Make default panels visible
        var defaultPanels = document.getElementById('panels-container');
        if (defaultPanels) defaultPanels.classList.add('visible');
        // Update global refs
        chatContent = firstTab.chatContent;
        cmdContent = firstTab.cmdContent;
        activeTabId = firstTab.uiId;

        var isFirst = await window.pywebview.api.is_first_run();
        if (isFirst) {
            bindWizardEvents();
            setTimeout(function() { showWizardOverlay(); }, 200);
        } else {
            var hasKey = await window.pywebview.api.has_api_key();
            if (!hasKey) {
                setTimeout(function() { showApiKeyModal(); }, 300);
            }
            updateLoadingProgress("正在加载历史记录...");
            await loadHistory();
            userInput.disabled = false;
            userInput.focus();
        }

        // ── Tab keyboard shortcuts ──
        document.addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.key === 't') {
                e.preventDefault();
                createNewTab();
            }
            if (e.ctrlKey && e.key === 'w') {
                e.preventDefault();
                var activeTab = getActiveTab();
                if (activeTab && tabs.length > 1) {
                    closeTab(activeTab.uiId);
                }
            }
            // Ctrl+Tab / Ctrl+Shift+Tab for tab switching
            if (e.ctrlKey && e.key === 'Tab') {
                e.preventDefault();
                if (e.shiftKey) {
                    switchToPrevTab();
                } else {
                    switchToNextTab();
                }
            }
        });

        // New tab button
        document.getElementById('new-tab-btn').addEventListener('click', createNewTab);

        // ── Initialize header token display from backend ──
        try {
            var totalUsage = await window.pywebview.api.get_total_usage(firstSessionId);
            if (totalUsage && (totalUsage.input_tokens || totalUsage.output_tokens || totalUsage.tool_call_tokens)) {
                // Sync Tab 1's totalTokens so recalcGrandTotalTokens() won't zero out the header
                firstTab.totalTokens.input = totalUsage.input_tokens || 0;
                firstTab.totalTokens.output = totalUsage.output_tokens || 0;
                firstTab.totalTokens.tool = totalUsage.tool_call_tokens || 0;
                recalcGrandTotalTokens();
                updateHeaderTokensDisplay();
            }
        } catch(e) {
            // Silently ignore — token display is non-critical
        }

        // ── ★ Restore tabs after crash/refresh ──
        try {
            await restoreTabsAfterCrash(firstTab);
        } catch(e) {
            console.warn('Tab restoration failed:', e);
        }

        // ── Hide loading overlay ──
        updateLoadingProgress(t('ready'), true);
        setTimeout(function() {
            dismissLoadingOverlay();
        }, 700);

    } catch(e) {
        config = {};
        userInput.disabled = false;
        userInput.focus();
        // ── Hide loading overlay even on error ──
        dismissLoadingOverlay();
    }
});

function dismissLoadingOverlay() {
    var overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    overlay.classList.add('fade-out');
    setTimeout(function() {
        overlay.style.display = 'none';
    }, 350);
}

function showLoadingError(title, fatalCount, details) {
    var overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    overlay.classList.add('error');
    var statusText = document.getElementById('loadingStatusText');
    if (statusText) statusText.textContent = title || t('loading_failed');
    var badge = document.getElementById('loadingFatalBadge');
    if (badge) {
        badge.textContent = tf('loading_fatal_issues', currentLang, fatalCount);
    }
    var errorDetails = document.getElementById('loadingErrorDetails');
    if (errorDetails) {
        errorDetails.textContent = details;
    }
    // Wire exit button
    var exitBtn = document.getElementById('loadingExitBtn');
    if (exitBtn) {
        exitBtn.textContent = t('loading_exit');
        exitBtn.onclick = function(e) {
            e.stopPropagation();
            try {
                window.pywebview.api.quit_app();
            } catch(_) {
                window.close();
            }
        };
    }
}

var _loadingQueue = [];
var _loadingTimer = null;

function updateLoadingProgress(msg, done) {
    _loadingQueue.push({msg: msg, done: done});
    if (!_loadingTimer) {
        processLoadingQueue();
    }
}

function processLoadingQueue() {
    if (_loadingQueue.length === 0) {
        _loadingTimer = null;
        return;
    }
    var item = _loadingQueue.shift();
    var el = document.getElementById('loadingProgress');
    if (el) {
        var div = document.createElement('div');
        div.className = 'loading-progress-item' + (item.done ? ' done' : '');
        div.textContent = (item.done ? '✓ ' : '→ ') + item.msg;
        el.appendChild(div);
        el.scrollTop = el.scrollHeight;
        if (item.done) {
            // Replace spinner with green checkmark + "一切就绪"
            var overlay = document.getElementById('loading-overlay');
            if (overlay) overlay.classList.add('done');
            var statusText = document.getElementById('loadingStatusText');
            if (statusText) statusText.textContent = t('loading_all_ready');
        }
    }
    _loadingTimer = setTimeout(processLoadingQueue, 300);
}

async function createNewTab() {
    if (tabs.length >= MAX_TABS) {
        showToast('⚠️ Maximum ' + MAX_TABS + ' tabs reached');
        return;
    }
    var sid;
    try {
        // Use current config's project_root as default workspace
        var defaultWs = config.project_root || '';
        sid = await window.pywebview.api.create_session(defaultWs);
        if (sid && sid.indexOf('error:') === 0) {
            showToast(sid.replace('error:', ''));
            return;
        }
    } catch(e) {
        sid = 'session_' + Date.now();
    }
    var idx = tabs.length + 1;
    var baseTitle = 'Tab ' + idx;
    // Ensure unique tab name
    var title = baseTitle;
    var counter = 1;
    while (isTabNameDuplicate(title, null)) {
        counter++;
        title = baseTitle + ' (' + counter + ')';
    }
    var defaultWs = config.project_root || '';
    createTab(sid, title, true, false, defaultWs);
    // Reset for new tab
    updateFileBadges();
    tokenDisplay.innerHTML = '';
    userInput.value = '';
    resetTextareaHeight();
    userInput.focus();
}

function switchToNextTab() {
    if (tabs.length < 2) return;
    var idx = -1;
    for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].uiId === activeTabId) { idx = i; break; }
    }
    var next = (idx + 1) % tabs.length;
    switchToTab(tabs[next].uiId);
}

function switchToPrevTab() {
    if (tabs.length < 2) return;
    var idx = -1;
    for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].uiId === activeTabId) { idx = i; break; }
    }
    var prev = (idx - 1 + tabs.length) % tabs.length;
    switchToTab(tabs[prev].uiId);
}

window.onerror = function(message, source, lineno, colno, error) {
    // ★ Save tab state before the error potentially crashes the page
    try { saveTabsToStorage(); } catch(e) {}
    var msg = 'JS Error: ' + message + ' at ' + source + ':' + lineno + ':' + colno;
    try { window.pywebview.api.log_frontend_error(msg); } catch(e) {}

    // Detect OOM / crash-level errors
    var isOOM = false;
    var reason = message || '';
    if (typeof message === 'string') {
        var lower = message.toLowerCase();
        if (lower.indexOf('out of memory') !== -1 ||
            lower.indexOf('memory') !== -1 ||
            lower.indexOf('allocation failed') !== -1 ||
            lower.indexOf('heap') !== -1 ||
            lower.indexOf('stack overflow') !== -1 ||
            lower.indexOf('cannot allocate') !== -1 ||
            lower.indexOf('insufficient memory') !== -1 ||
            lower.indexOf('webview2') !== -1 ||
            lower.indexOf('renderer') !== -1 ||
            lower.indexOf('crash') !== -1) {
            isOOM = true;
        }
    }
    // Also treat RangeError (often from huge allocations) as OOM
    if (error && error instanceof RangeError) {
        isOOM = true;
    }

    if (isOOM) {
        // Disable input while recovering
        userInput.disabled = true;
        // Show crash notification
        try { showCrashNotification(reason || 'JavaScript heap limit exceeded — possible OOM'); } catch(e) {}
    }
    return true;
};

// ── Unhandled Promise Rejection — catch OOM from async operations ──
window.addEventListener('unhandledrejection', function(event) {
    var reason = event.reason;
    var reasonStr = '';
    try {
        if (reason instanceof Error) {
            reasonStr = reason.message || reason.toString();
        } else if (typeof reason === 'string') {
            reasonStr = reason;
        } else {
            reasonStr = JSON.stringify(reason);
        }
    } catch(e) {
        reasonStr = String(reason);
    }

    // Log to backend
    try {
        window.pywebview.api.log_frontend_error('[UnhandledRejection] ' + reasonStr);
    } catch(e) {}

    // Detect OOM patterns
    var lower = reasonStr.toLowerCase();
    if (lower.indexOf('out of memory') !== -1 ||
        lower.indexOf('memory') !== -1 ||
        lower.indexOf('heap') !== -1 ||
        lower.indexOf('allocation') !== -1 ||
        lower.indexOf('quota exceeded') !== -1) {
        try { saveTabsToStorage(); } catch(e) {}
        userInput.disabled = true;
        try { showCrashNotification(reasonStr || 'Unhandled async rejection — likely memory exhaustion'); } catch(e) {}
    }
});

// ── Memory pressure monitoring (WebView2 Chromium) ──
(function initMemoryMonitor() {
    // Only works in Chromium-based WebView2
    if (!window.performance || !performance.memory) return;

    var CHECK_INTERVAL = 15000; // every 15s
    var WARN_RATIO = 0.85;      // 85% of heap limit → warn
    var CRITICAL_RATIO = 0.95;  // 95% → crash notification

    setInterval(function() {
        try {
            var mem = performance.memory;
            var used = mem.usedJSHeapSize;
            var limit = mem.jsHeapSizeLimit;
            var total = mem.totalJSHeapSize;

            if (limit <= 0) return; // not available

            var ratio = used / limit;

            if (ratio >= CRITICAL_RATIO) {
                // Critical — trigger crash notification
                var mbUsed = (used / 1048576).toFixed(1);
                var mbLimit = (limit / 1048576).toFixed(1);
                try { saveTabsToStorage(); } catch(e) {}
                userInput.disabled = true;
                try {
                    showCrashNotification(
                        'JS heap at ' + (ratio * 100).toFixed(0) + '% (' +
                        mbUsed + ' MB / ' + mbLimit + ' MB limit). ' +
                        'Imminent OOM crash detected.'
                    );
                } catch(e) {}
            } else if (ratio >= WARN_RATIO) {
                // Warning — log but don't show crash notification yet
                var mbUsedW = (used / 1048576).toFixed(1);
                var mbLimitW = (limit / 1048576).toFixed(1);
                try {
                    window.pywebview.api.log_frontend_error(
                        '[MemoryWarn] Heap at ' + (ratio * 100).toFixed(0) + '% — ' +
                        mbUsedW + ' MB / ' + mbLimitW + ' MB'
                    );
                } catch(e) {}
            }
        } catch(e) {
            // performance.memory might throw in some contexts
        }
    }, CHECK_INTERVAL);
})();