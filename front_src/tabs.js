//  MULTI-TAB SUPPORT — like browser tabs
// ═══════════════════════════════════════════════════════════════
//  ★ Tab persistence via localStorage — survives OOM crashes & refreshes

var tabs = [];
var activeTabId = null;
var tabIdCounter = 0;

var MAX_TABS = 16;
var TAB_STORAGE_KEY = 'norp_agent_tabs';
var _tabSaveTimer = null;
var _tabSaveInterval = 5000;  // auto-save every 5s

// ── Tab persistence ──

function saveTabsToStorage() {
    try {
        var data = [];
        for (var i = 0; i < tabs.length; i++) {
            var t = tabs[i];
            data.push({
                uiId: t.uiId,
                dbId: t.dbId,
                title: t.title,
                workspace: t.workspace || '',
                isActive: t.uiId === activeTabId
            });
        }
        localStorage.setItem(TAB_STORAGE_KEY, JSON.stringify(data));
    } catch(e) {
        // localStorage may be full or unavailable — silently ignore
    }
}

function loadTabsFromStorage() {
    try {
        var raw = localStorage.getItem(TAB_STORAGE_KEY);
        if (!raw) return [];
        var data = JSON.parse(raw);
        if (!Array.isArray(data)) return [];
        return data;
    } catch(e) {
        return [];
    }
}

function scheduleTabSave() {
    if (_tabSaveTimer) clearTimeout(_tabSaveTimer);
    _tabSaveTimer = setTimeout(function() {
        saveTabsToStorage();
        _tabSaveTimer = null;
    }, 300);  // debounce 300ms
}

// Auto-save periodically (belt-and-suspenders with debounced save)
setInterval(function() {
    saveTabsToStorage();
}, _tabSaveInterval);

// Save on page unload (before crash/refresh)
window.addEventListener('beforeunload', function() {
    saveTabsToStorage();
});

// ── Duplicate tab name check ──
function isTabNameDuplicate(name, excludeUiId) {
    name = (name || '').trim().toLowerCase();
    if (!name) return false;
    for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].uiId === excludeUiId) continue;
        if (tabs[i].title.trim().toLowerCase() === name) return true;
    }
    return false;
}

function createTabState(sessionId, title, workspace) {
    var id = 'tab_' + (++tabIdCounter);
    var st = {
        dbId: sessionId,          // backend session ID
        uiId: id,                 // frontend tab ID
        title: title || 'Tab ' + tabIdCounter,
        workspace: workspace || '', // per-tab workspace path

        // DOM elements (set after panel creation)
        panelsEl: null,           // the panels container div
        chatPanel: null,
        cmdPanel: null,
        chatContent: null,
        cmdContent: null,

        // Streaming state
        currentThinkingEl: null,
        currentReplyEl: null,
        currentCmdEl: null,
        totalTokens: { input: 0, output: 0, tool: 0 },

        // Task state
        isStreaming: false,
        isWaiting: false,
        pollingTimer: null,

        // File attachments
        selectedFiles: [],

        // Per-tab input state
        inputValue: '',

        // Pending modals — deferred until user switches to this tab
        pendingQuestion: null,   // {text: string, sessionId: string}
        pendingConfirm: null,    // {tool: string, path: string, sessionId: string}

        // Conversation messages (for display)
        messages: [],
    };
    return st;
}

function createTabPanels(tab) {
    // Clone the template panels for this tab
    var template = document.getElementById('panels-template');
    var clone = template.cloneNode(true);
    clone.id = 'panels-' + tab.uiId;
    clone.style.display = '';  // clear inline style so CSS classes can control visibility
    template.parentNode.insertBefore(clone, template);

    // Store reference to panels container
    tab.panelsEl = clone;

    // Find elements in the clone
    var chatPanel = clone.querySelector('.chat-panel');
    var cmdPanel = clone.querySelector('.cmd-panel');
    var chatContent = clone.querySelector('.panel-content');
    var cmdContent = cmdPanel ? cmdPanel.querySelector('.panel-content') : clone.querySelectorAll('.panel-content')[1];

    // Remove template IDs
    chatPanel.id = 'chat-panel-' + tab.uiId;
    if (cmdPanel) cmdPanel.id = 'cmd-panel-' + tab.uiId;
    chatContent.id = 'chat-content-' + tab.uiId;
    if (cmdContent) cmdContent.id = 'cmd-content-' + tab.uiId;

    tab.chatPanel = chatPanel;
    tab.cmdPanel = cmdPanel;
    tab.chatContent = chatContent;
    tab.cmdContent = cmdContent;

    return clone;
}

function createTab(sessionId, title, makeActive, useDefaultPanels, workspace) {
    if (makeActive === undefined) makeActive = true;
    var tab = createTabState(sessionId, title, workspace);
    tabs.push(tab);

    if (useDefaultPanels) {
        // Reuse the existing default panels (for first tab)
        tab.panelsEl = document.getElementById('panels-container');
        tab.chatPanel = document.getElementById('chat-panel-default');
        tab.cmdPanel = document.getElementById('cmd-panel-default');
        tab.chatContent = document.getElementById('chat-content-default');
        tab.cmdContent = document.getElementById('cmd-content-default');
    } else {
        createTabPanels(tab);
    }

    if (makeActive || !activeTabId) {
        switchToTab(tab.uiId);
    }

    updateTabBar();
    scheduleTabSave();
    return tab;
}

function getTab(uiId) {
    for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].uiId === uiId) return tabs[i];
    }
    return null;
}

function getActiveTab() {
    return getTab(activeTabId);
}

function switchToTab(uiId) {
    var newTab = getTab(uiId);
    if (!newTab) return;
    if (activeTabId === uiId) return; // already active

    // Save current tab state
    if (activeTabId) {
        var oldTab = getTab(activeTabId);
        if (oldTab) {
            oldTab.currentThinkingEl = currentThinkingEl;
            oldTab.currentReplyEl = currentReplyEl;
            oldTab.currentCmdEl = currentCmdEl;
            oldTab.selectedFiles = selectedFiles;
            oldTab.inputValue = userInput.value;
            // Polling is per-tab — never paused, each tab manages its own timer

            // Save ask_user modal state if it belongs to this tab
            if (askUserModal.style.display === 'block' && _pendingAskSessionId === oldTab.dbId) {
                oldTab.pendingQuestion = {
                    text: _pendingAskQuestionRaw || '',
                    sessionId: _pendingAskSessionId
                };
                hideAskUserModal();
            }

            // Save confirm-write modal state if it belongs to this tab
            if (confirmWriteModal.style.display === 'block' && _pendingConfirmSessionId === oldTab.dbId) {
                oldTab.pendingConfirm = {
                    tool: confirmWriteMessage.textContent.includes('delete') ? 'delete_file' :
                          confirmWriteMessage.textContent.includes('modify') ? 'replace_in_file' : 'write_file',
                    path: confirmWriteMessage.textContent.split(':').pop().trim() || '',
                    sessionId: _pendingConfirmSessionId
                };
                hideConfirmWriteModal();
            }
        }
        // Hide old panels using stored reference
        var oldTabRef = getTab(activeTabId);
        if (oldTabRef && oldTabRef.panelsEl) {
            oldTabRef.panelsEl.classList.remove('visible');
        }
    }

    // Show new tab's panels using stored reference
    if (newTab.panelsEl) {
        newTab.panelsEl.classList.add('visible');
    }

    // Update global references
    activeTabId = uiId;
    chatContent = newTab.chatContent;
    cmdContent = newTab.cmdContent;
    currentThinkingEl = newTab.currentThinkingEl;
    currentReplyEl = newTab.currentReplyEl;
    currentCmdEl = newTab.currentCmdEl;
    selectedFiles = newTab.selectedFiles || [];
    userInput.value = newTab.inputValue || '';
    resetTextareaHeight();
    updateFileBadges();

    // Update token display for the newly active tab
    updateTokenDisplay(newTab.totalTokens);
    // Recalculate header grand total (sum of all tabs)
    recalcGrandTotalTokens();
    updateHeaderTokensDisplay();

    // Update status bar and workspace display
    updateWorkspaceDisplay(newTab.workspace);

    // Update global UI to reflect new tab's streaming state
    // (each tab's polling runs independently — we just update the display)
    if (newTab.isStreaming) {
        isStreaming = true;
        isWaiting = newTab.isWaiting;
        sendBtn.style.display = 'none';
        stopBtn.style.display = 'inline-block';
        statusText.textContent = newTab.isWaiting ? t('waiting_reply') : t('running');
    } else {
        isStreaming = false;
        isWaiting = false;
        sendBtn.style.display = 'inline-block';
        stopBtn.style.display = 'none';
        statusText.textContent = t('ready');
    }

    updateTabBar();

    // Restore pending ask_user modal for the newly active tab
    if (newTab.pendingQuestion) {
        var pq = newTab.pendingQuestion;
        newTab.pendingQuestion = null;
        // Use setTimeout to let the DOM settle before showing modal
        setTimeout(function() {
            showAskUserModal(pq.text, pq.sessionId);
        }, 50);
    }

    // Restore pending confirm-write modal for the newly active tab
    if (newTab.pendingConfirm) {
        var pc = newTab.pendingConfirm;
        newTab.pendingConfirm = null;
        setTimeout(function() {
            showConfirmWriteModal(pc.tool, pc.path, pc.sessionId, pc.isPlugin);
        }, 50);
    }

    // Focus input
    setTimeout(function() { userInput.focus(); }, 50);

    // ★ Lazy history load for restored tabs
    if (newTab._needsHistoryLoad) {
        newTab._needsHistoryLoad = false;
        loadHistoryForTab(newTab);
    }

    scheduleTabSave();
}

function closeTab(uiId) {
    if (tabs.length <= 1) return; // Can't close last tab

    var tab = getTab(uiId);
    if (!tab) return;

    // Check if this tab has a running agent task
    if (tab.isStreaming) {
        if (!confirm(t('agent_running_confirm') || 'Agent正在运行，确定关闭吗？')) {
            return;
        }
    }

    // Stop any running task on this tab
    if (tab.dbId) {
        try { window.pywebview.api.stop_task(tab.dbId); } catch(e) {}
    }
    if (tab.pollingTimer) {
        clearInterval(tab.pollingTimer);
        tab.pollingTimer = null;
    }

    // Remove panels from DOM using stored reference
    if (tab.panelsEl) {
        tab.panelsEl.remove();
    }

    // Remove from tabs array
    var idx = -1;
    for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].uiId === uiId) { idx = i; break; }
    }
    if (idx >= 0) tabs.splice(idx, 1);

    // Close backend session
    if (tab.dbId) {
        try { window.pywebview.api.close_session(tab.dbId); } catch(e) {}
    }

    // Switch to another tab if closing the active one
    if (activeTabId === uiId) {
        switchToTab(tabs[0].uiId);
    }

    updateTabBar();

    // Recalculate header token total after tab removal
    recalcGrandTotalTokens();
    updateHeaderTokensDisplay();
    scheduleTabSave();
}

function updateTabBar() {
    var list = document.getElementById('tab-list');
    if (!list) return;

    var html = '';
    for (var i = 0; i < tabs.length; i++) {
        var t = tabs[i];
        var isActive = t.uiId === activeTabId;
        var cls = isActive ? 'tab-btn active' : 'tab-btn';
        var tooltip = t.title;
        if (t.workspace) {
            tooltip += ' \u2014 ' + t.workspace;
        }
        html += '<div class="' + cls + '" data-tab="' + t.uiId + '" title="' + escapeHtml(tooltip) + '">';
        if (t.isStreaming) {
            html += '<span class="tab-running-dot"></span>';
        }
        if (t.pendingQuestion) {
            html += '<span class="tab-ask-dot" title="Agent is asking a question — click to view"></span>';
        }
        if (t.pendingConfirm) {
            html += '<span class="tab-confirm-dot" title="Agent needs write confirmation — click to view"></span>';
        }
        // Show workspace icon if set
        if (t.workspace) {
            html += '<span class="tab-ws-icon" title="' + escapeHtml(t.workspace) + '">📁</span>';
        }
        html += '<span class="tab-title">' + escapeHtml(t.title) + '</span>';
        if (tabs.length > 1) {
            html += '<span class="tab-close" data-close="' + t.uiId + '">×</span>';
        }
        html += '</div>';
    }
    list.innerHTML = html;

    // Attach event listeners
    var tabBtns = list.querySelectorAll('.tab-btn');
    for (var j = 0; j < tabBtns.length; j++) {
        tabBtns[j].addEventListener('click', function(e) {
            var closeBtn = e.target.closest('.tab-close');
            if (closeBtn) {
                e.stopPropagation();
                closeTab(closeBtn.dataset.close);
                return;
            }
            var tabId = this.dataset.tab;
            if (tabId !== activeTabId) {
                switchToTab(tabId);
            }
        });

        // Double-click to rename
        tabBtns[j].addEventListener('dblclick', function(e) {
            var closeBtn = e.target.closest('.tab-close');
            if (closeBtn) return;
            var tabId = this.dataset.tab;
            var tab = getTab(tabId);
            if (!tab) return;
            var newTitle = prompt('Rename tab:', tab.title);
            if (newTitle && newTitle.trim()) {
                newTitle = newTitle.trim();
                if (isTabNameDuplicate(newTitle, tab.uiId)) {
                    showToast(t('tab_name_duplicate') || 'A tab with this name already exists');
                    return;
                }
                tab.title = newTitle;
                scheduleTabSave();
                updateTabBar();
                if (tab.dbId) {
                    try { window.pywebview.api.set_session_title(tab.dbId, tab.title); } catch(e) {}
                }
            }
        });

        // Right-click context menu for workspace
        tabBtns[j].addEventListener('contextmenu', function(e) {
            e.preventDefault();
            var tabId = this.dataset.tab;
            var tab = getTab(tabId);
            if (!tab) return;
            showTabContextMenu(e, tab);
        });
    }
}

// ── Tab context menu (right-click) ──
var tabContextMenu = null;

function createTabContextMenu() {
    if (tabContextMenu) return;
    tabContextMenu = document.createElement('div');
    tabContextMenu.id = 'tab-context-menu';
    tabContextMenu.style.cssText = 'display:none;position:fixed;z-index:5000;background:#fff;border:1px solid #ccc;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,0.15);padding:4px 0;min-width:180px;font-size:12px;';
    tabContextMenu.innerHTML = '<div class="ctx-item" data-action="workspace" style="padding:6px 14px;cursor:pointer;color:#333;">📁 Set Workspace...</div>' +
        '<div class="ctx-item" data-action="rename" style="padding:6px 14px;cursor:pointer;color:#333;">✏️ Rename Tab</div>' +
        '<div class="ctx-item" data-action="close" style="padding:6px 14px;cursor:pointer;color:#dc3545;">✕ Close Tab</div>';
    document.body.appendChild(tabContextMenu);

    tabContextMenu.addEventListener('click', function(e) {
        var item = e.target.closest('.ctx-item');
        if (!item) return;
        var action = item.dataset.action;
        var tabId = tabContextMenu._targetTab;
        hideTabContextMenu();
        if (!tabId) return;
        var tab = getTab(tabId);
        if (!tab) return;

        if (action === 'workspace') {
            promptTabWorkspace(tab);
        } else if (action === 'rename') {
            var newTitle = prompt('Rename tab:', tab.title);
            if (newTitle && newTitle.trim()) {
                newTitle = newTitle.trim();
                if (isTabNameDuplicate(newTitle, tab.uiId)) {
                    showToast(t('tab_name_duplicate') || 'A tab with this name already exists');
                    return;
                }
                tab.title = newTitle;
                scheduleTabSave();
                updateTabBar();
                if (tab.dbId) {
                    try { window.pywebview.api.set_session_title(tab.dbId, tab.title); } catch(e) {}
                }
            }
        } else if (action === 'close') {
            if (tabs.length > 1) closeTab(tab.uiId);
        }
    });

    // Close on outside click
    document.addEventListener('click', function(e) {
        if (tabContextMenu && !tabContextMenu.contains(e.target)) {
            hideTabContextMenu();
        }
    });
}

function showTabContextMenu(e, tab) {
    createTabContextMenu();
    tabContextMenu._targetTab = tab.uiId;
    tabContextMenu.style.display = 'block';
    tabContextMenu.style.left = e.clientX + 'px';
    tabContextMenu.style.top = e.clientY + 'px';
    // Make sure it's in viewport
    var rect = tabContextMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) tabContextMenu.style.left = (e.clientX - rect.width) + 'px';
    if (rect.bottom > window.innerHeight) tabContextMenu.style.top = (e.clientY - rect.height) + 'px';
}

function hideTabContextMenu() {
    if (tabContextMenu) {
        tabContextMenu.style.display = 'none';
        tabContextMenu._targetTab = null;
    }
}

// ── Per-tab workspace ──
async function promptTabWorkspace(tab) {
    var currentWs = tab.workspace || config.project_root || '';
    var newWs = null;
    
    // Try native directory picker first (acts as "Browse" button)
    if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_directory) {
        try {
            newWs = await window.pywebview.api.pick_directory();
            if (newWs) newWs = newWs.trim();
        } catch(e) {
            console.warn('pick_directory failed:', e);
            newWs = null;
        }
    }
    
    // Fall back to text prompt if native picker unavailable or cancelled
    if (!newWs) {
        var hint = currentWs ? ' (current: ' + currentWs + ')' : '';
        newWs = prompt('Enter workspace path for this tab' + hint + ':', currentWs);
        if (newWs === null) return; // cancelled
        newWs = (newWs || '').trim();
    }
    
    tab.workspace = newWs;
    updateTabBar();
    // Update status bar immediately if this is the active tab
    if (tab === getActiveTab()) {
        updateWorkspaceDisplay(newWs);
    }
    
    if (tab.dbId) {
        try {
            await window.pywebview.api.set_session_workspace(tab.dbId, newWs);
        } catch(e) {
            console.warn('Failed to set workspace:', e);
        }
    }
    if (newWs) {
        showToast('📁 Workspace: ' + newWs);
    }
    scheduleTabSave();
}

// ── Tab restoration after crash/refresh ──
// Called from main.js after the first tab is created.
// Reconciles localStorage tab metadata with backend sessions.

async function restoreTabsAfterCrash(firstTab) {
    var savedTabs = loadTabsFromStorage();
    if (!savedTabs || savedTabs.length <= 1) {
        // Nothing to restore, or only one tab (the one we just created)
        return;
    }

    // Get all backend sessions
    var backendSessions = [];
    try {
        backendSessions = await window.pywebview.api.get_sessions();
        if (!backendSessions || !backendSessions.length) return;
    } catch(e) {
        console.warn('Failed to get backend sessions for tab restore:', e);
        return;
    }

    // Build a map of backend session IDs
    var backendMap = {};
    for (var i = 0; i < backendSessions.length; i++) {
        backendMap[backendSessions[i].id] = backendSessions[i];
    }

    // The first tab already exists — update its title/workspace from saved state
    for (var j = 0; j < savedTabs.length; j++) {
        if (savedTabs[j].dbId === firstTab.dbId) {
            if (savedTabs[j].title && savedTabs[j].title !== 'Tab 1') {
                firstTab.title = savedTabs[j].title;
            }
            if (savedTabs[j].workspace) {
                firstTab.workspace = savedTabs[j].workspace;
                updateWorkspaceDisplay(firstTab.workspace);
            }
            break;
        }
    }

    // Restore additional tabs that have valid backend sessions
    for (var k = 0; k < savedTabs.length; k++) {
        var st = savedTabs[k];
        // Skip the first tab (already exists)
        if (st.dbId === firstTab.dbId) continue;

        // Check if backend session still exists
        if (!backendMap[st.dbId]) continue;

        // Check we don't exceed max tabs
        if (tabs.length >= MAX_TABS) break;

        // Create the tab shell (reuse existing backend session)
        var restoredTab = createTabState(st.dbId, st.title || 'Tab', st.workspace || '');
        // Override uiId to match saved state
        restoredTab.uiId = st.uiId;
        // Ensure tabIdCounter is ahead of any restored IDs
        var numPart = parseInt(st.uiId.replace('tab_', ''));
        if (!isNaN(numPart) && numPart >= tabIdCounter) {
            tabIdCounter = numPart;
        }
        tabs.push(restoredTab);
        createTabPanels(restoredTab);

        // Mark that messages need loading when this tab is first activated
        restoredTab._needsHistoryLoad = true;

        // Start polling if there's a running task on this session
        if (backendMap[st.dbId] && backendMap[st.dbId].has_task) {
            restoredTab.isStreaming = true;
            restoredTab.isWaiting = false;
            startPolling(restoredTab);
        }
    }

    // Switch to the previously active tab
    for (var m = 0; m < savedTabs.length; m++) {
        if (savedTabs[m].isActive) {
            var targetTab = getTab(savedTabs[m].uiId);
            if (targetTab && targetTab.uiId !== firstTab.uiId) {
                switchToTab(targetTab.uiId);
            }
            break;
        }
    }

    updateTabBar();
}

// Lazy-load history for a restored tab when first activated
async function loadHistoryForTab(tab) {
    if (!tab || !tab.dbId || !tab.chatContent) return;
    try {
        var msgs = await window.pywebview.api.get_initial_messages(tab.dbId);
        if (!msgs || !msgs.length) return;
        tab.messages = msgs;

        // Render messages using the same pattern as loadHistory()
        var savedChat = chatContent;
        chatContent = tab.chatContent;
        for (var i = 0; i < msgs.length; i++) {
            var msg = msgs[i];
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
            tab.chatContent.appendChild(div);
        }
        tab.chatContent.scrollTop = tab.chatContent.scrollHeight;
        chatContent = savedChat;
    } catch(e) {
        console.warn('Failed to load history for tab:', tab.dbId, e);
    }
}