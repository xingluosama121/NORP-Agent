// ── 原生工具确认总开关联动：关闭时禁用子项 ──
function updateNativeConfirmVisibility() {
    var enabled = document.getElementById('cfg-native-confirm-enabled').checked;
    ['cfg-native-confirm-write', 'cfg-native-confirm-delete', 'cfg-native-confirm-exec'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.disabled = !enabled;
    });
}

async function openSettings() {
    try { config = await window.pywebview.api.get_config(); } catch(e) { config = {}; }

    var nativeConfirmMaster = document.getElementById('cfg-native-confirm-enabled');
    if (nativeConfirmMaster) {
        nativeConfirmMaster.addEventListener('change', updateNativeConfirmVisibility);
    }

    // ── Language selector (top of settings) ──
    var langSelect = document.getElementById('cfg-language');
    var savedLang = config.language || 'zh_CN';
    currentLang = savedLang;
    langSelect.value = savedLang;
    // Apply language change immediately on selection
    langSelect.addEventListener('change', function() {
        currentLang = this.value;
        setLanguage(this.value);
        // Update dynamic content that depends on language
        updateThinkMutualExclusion();
        renderPluginDirs();
        renderPluginList();
        renderSecurityAuditSummary();
        updateQueueWarning();
    });

    // ── 关闭按钮默认行为 ──
    document.getElementById('cfg-close-behavior').value = config.close_button_behavior || 'minimize_to_tray';

    document.getElementById('cfg-api-base').value = config.api_base || 'https://api.deepseek.com';
    document.getElementById('cfg-use-responses').checked = config.use_responses_api === true;
    document.getElementById('cfg-project-root').value = config.project_root || '';
    document.getElementById('cfg-queue-size').value = config.queue_max_size || 200;
    document.getElementById('cfg-max-steps').value = config.max_steps || 128;
    document.getElementById('cfg-task-timeout').value = config.task_timeout || 0;
    document.getElementById('cfg-api-request-timeout').value = String(config.api_request_timeout || 180);
    document.getElementById('cfg-web-search').checked = config.enable_web_search || false;
    document.getElementById('cfg-native-confirm-enabled').checked = config.native_confirm_enabled !== false;
    document.getElementById('cfg-native-confirm-write').checked = config.native_confirm_write !== false;
    document.getElementById('cfg-native-confirm-delete').checked = config.native_confirm_delete !== false;
    document.getElementById('cfg-native-confirm-exec').checked = config.native_confirm_exec !== false;
    updateNativeConfirmVisibility();

    var thinkLevel = config.think_level || '高';
    document.getElementById('cfg-think-level').value = thinkLevel;
    var temp = config.temperature !== undefined ? config.temperature : 1.0;
    document.getElementById('cfg-temperature').value = Math.round(temp * 10);
    document.getElementById('cfg-temperature-value').textContent = temp.toFixed(1);
    document.getElementById('cfg-max-tokens').value = config.max_tokens || 32767;
    updateThinkMutualExclusion();

    document.getElementById('cfg-memory').checked = config.memory !== false;
    document.getElementById('cfg-memory-mode').value = config.memory_mode || 'full';
    document.getElementById('cfg-max-rounds').value = config.max_rounds || 10;

    // Plugin fields（插件启用开关、目录与安全配置均已迁移至独立插件面板）
    config.plugin_dirs = config.plugin_dirs || [];

    // NORP safety system
    document.getElementById('cfg-norp-safe-enabled').checked = config.norp_safe_enabled !== false;
    updateNorpSafetyStatus();

    // Jailbreak guard
    document.getElementById('cfg-jailbreak-guard-enabled').checked = config.jailbreak_guard_enabled !== false;
    document.getElementById('cfg-jailbreak-guard-action').value = config.jailbreak_guard_action || 'block';
    updateJailbreakGuardStatus();

    // Custom system prompt
    document.getElementById('cfg-custom-prompt-enabled').checked = config.custom_system_prompt_enabled || false;
    document.getElementById('cfg-custom-prompt-source').value = config.custom_system_prompt_file ? 'file' : 'text';
    document.getElementById('cfg-custom-prompt').value = config.custom_system_prompt || '';
    document.getElementById('cfg-custom-prompt-file').value = config.custom_system_prompt_file || '';
    updateCustomPromptVisibility();
    updateCustomPromptSource();

    // Vision API
    document.getElementById('cfg-vision-enabled').checked = config.vision_enabled === true;
    document.getElementById('cfg-vision-service-url').value = config.vision_service_url || '';
    updateVisionVisibility();

    updateQueueWarning();
    setLanguage(currentLang);
    refreshRuntimeHealth();
    settingsModal.style.display = 'block';
    fetchModels(config.api_base || 'https://api.deepseek.com');
}

function updateThinkMutualExclusion() {
    var isThink = document.getElementById('cfg-think-level').value !== '关';
    var tempSlider = document.getElementById('cfg-temperature');
    tempSlider.disabled = isThink;
    var hint = document.getElementById('cfg-think-hint');
    if (hint) hint.style.display = isThink ? 'block' : 'none';
}

function updateQueueWarning() {
    var val = parseInt(document.getElementById('cfg-queue-size').value) || 200;
    document.getElementById('queue-warning').style.display = val > 300 ? 'block' : 'none';
}

// ── Custom system prompt visibility ──
function updateCustomPromptVisibility() {
    var enabled = document.getElementById('cfg-custom-prompt-enabled').checked;
    var section = document.getElementById('custom-prompt-section');
    if (section) section.style.display = enabled ? 'block' : 'none';
}

// ── Vision API visibility ──
function updateVisionVisibility() {
    var enabled = document.getElementById('cfg-vision-enabled').checked;
    var section = document.getElementById('vision-config');
    if (section) section.style.display = enabled ? 'block' : 'none';
}

// ── Custom prompt source toggle (file vs text) ──
function updateCustomPromptSource() {
    var source = document.getElementById('cfg-custom-prompt-source').value;
    var textSection = document.getElementById('custom-prompt-text-section');
    var fileSection = document.getElementById('custom-prompt-file-section');
    if (textSection) textSection.style.display = source === 'text' ? 'block' : 'none';
    if (fileSection) fileSection.style.display = source === 'file' ? 'block' : 'none';
}

// ── Browse for custom prompt file ──
async function browseCustomPromptFile() {
    try {
        var path = await window.pywebview.api.pick_file('Markdown / Text (*.md;*.txt)');
        if (path) {
            document.getElementById('cfg-custom-prompt-file').value = path;
            // Read file content
            try {
                var content = await window.pywebview.api.read_text_file(path);
                if (content) {
                    document.getElementById('cfg-custom-prompt').value = content;
                }
            } catch(e) {
                // File path is set but content couldn't be auto-loaded
            }
        }
    } catch(e) {
        // Fallback: manual path entry
        var path = prompt('请输入提示词文件路径（.md / .txt）：');
        if (path) {
            document.getElementById('cfg-custom-prompt-file').value = path;
        }
    }
}

document.addEventListener('DOMContentLoaded', function() {
    var thinkSelect = document.getElementById('cfg-think-level');
    if (thinkSelect) {
        thinkSelect.addEventListener('change', updateThinkMutualExclusion);
    }

    var tempSlider = document.getElementById('cfg-temperature');
    if (tempSlider) {
        tempSlider.addEventListener('input', function() {
            document.getElementById('cfg-temperature-value').textContent = (this.value / 10).toFixed(1);
        });
    }

    var baseInput = document.getElementById('cfg-api-base');
    if (baseInput) {
        baseInput.addEventListener('change', function() {
            var url = baseInput.value.trim() || 'https://api.deepseek.com';
            fetchModels(url);
        });
    }
    document.getElementById('cfg-refresh-models').addEventListener('click', function() {
        var url = document.getElementById('cfg-api-base').value.trim() || 'https://api.deepseek.com';
        fetchModels(url);
    });

    // Custom system prompt toggle
    var customPromptToggle = document.getElementById('cfg-custom-prompt-enabled');
    if (customPromptToggle) {
        customPromptToggle.addEventListener('change', updateCustomPromptVisibility);
    }
    // Custom prompt source toggle
    var customPromptSource = document.getElementById('cfg-custom-prompt-source');
    if (customPromptSource) {
        customPromptSource.addEventListener('change', updateCustomPromptSource);
    }
    // Browse button for custom prompt file
    var browseBtn = document.getElementById('cfg-custom-prompt-browse');
    if (browseBtn) {
        browseBtn.addEventListener('click', browseCustomPromptFile);
    }
    // Vision API toggle
    var visionToggle = document.getElementById('cfg-vision-enabled');
    if (visionToggle) {
        visionToggle.addEventListener('change', updateVisionVisibility);
    }
});

async function saveSettings() {
    var modelVal = document.getElementById('cfg-model').value;
    if (!modelVal || modelVal.trim() === '' || modelVal.trim() === '.') {
        showToast(t('select_valid_model'));
        return;
    }
    var newConfig = {
        language: document.getElementById('cfg-language').value,
        close_button_behavior: document.getElementById('cfg-close-behavior').value,
        model: modelVal.trim(),
        api_base: document.getElementById('cfg-api-base').value.trim(),
        use_responses_api: document.getElementById('cfg-use-responses').checked,
        project_root: document.getElementById('cfg-project-root').value.trim(),
        queue_max_size: parseInt(document.getElementById('cfg-queue-size').value) || 200,
        max_steps: parseInt(document.getElementById('cfg-max-steps').value) || 128,
        task_timeout: parseInt(document.getElementById('cfg-task-timeout').value) || 0,
        api_request_timeout: parseInt(document.getElementById('cfg-api-request-timeout').value) || 180,
        enable_web_search: document.getElementById('cfg-web-search').checked,
        native_confirm_enabled: document.getElementById('cfg-native-confirm-enabled').checked,
        native_confirm_write: document.getElementById('cfg-native-confirm-write').checked,
        native_confirm_delete: document.getElementById('cfg-native-confirm-delete').checked,
        native_confirm_exec: document.getElementById('cfg-native-confirm-exec').checked,
        think_level: document.getElementById('cfg-think-level').value,
        temperature: parseFloat(document.getElementById('cfg-temperature-value').textContent) || 1.0,
        max_tokens: parseInt(document.getElementById('cfg-max-tokens').value) || 32767,
        memory: document.getElementById('cfg-memory').checked,
        memory_mode: document.getElementById('cfg-memory-mode').value,
        max_rounds: parseInt(document.getElementById('cfg-max-rounds').value) || 10,
        plugins_enabled: config.plugins_enabled !== false,
        plugin_dirs: config.plugin_dirs || [],
        custom_system_prompt_enabled: document.getElementById('cfg-custom-prompt-enabled').checked,
        custom_system_prompt: document.getElementById('cfg-custom-prompt').value,
        custom_system_prompt_file: document.getElementById('cfg-custom-prompt-file').value,
        norp_safe_enabled: document.getElementById('cfg-norp-safe-enabled').checked,
        jailbreak_guard_enabled: document.getElementById('cfg-jailbreak-guard-enabled').checked,
        jailbreak_guard_action: document.getElementById('cfg-jailbreak-guard-action').value,
        vision_enabled: document.getElementById('cfg-vision-enabled').checked,
        vision_service_url: document.getElementById('cfg-vision-service-url').value.trim(),
    };

    try {
        await window.pywebview.api.save_config(newConfig);
        config = newConfig;
        currentLang = newConfig.language || 'zh_CN';
        settingsModal.style.display = 'none';
        showToast(t('settings_saved'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + e.message);
    }
}

document.getElementById('browse-project-root').addEventListener('click', function() {
    window.pywebview.api.pick_directory().then(function(path) {
        if (path) document.getElementById('cfg-project-root').value = path;
    }).catch(function() {
        var path = prompt('Enter project directory path:');
        if (path) document.getElementById('cfg-project-root').value = path;
    });
});

async function restoreDefaults() {
    if (!confirm(t('restore_confirm'))) {
        return;
    }
    try {
        config = await window.pywebview.api.reset_config();
        document.getElementById('cfg-language').value = config.language || 'zh_CN';
        currentLang = config.language || 'zh_CN';
        setLanguage(currentLang);
        document.getElementById('cfg-close-behavior').value = config.close_button_behavior || 'minimize_to_tray';
        document.getElementById('cfg-api-base').value = config.api_base || 'https://api.deepseek.com';
        document.getElementById('cfg-use-responses').checked = config.use_responses_api === true;
        document.getElementById('cfg-project-root').value = config.project_root || '';
        document.getElementById('cfg-queue-size').value = config.queue_max_size || 200;
        document.getElementById('cfg-max-steps').value = config.max_steps || 128;
        document.getElementById('cfg-task-timeout').value = config.task_timeout || 0;
        document.getElementById('cfg-api-request-timeout').value = String(config.api_request_timeout || 180);
        document.getElementById('cfg-web-search').checked = config.enable_web_search || false;
        document.getElementById('cfg-native-confirm-enabled').checked = config.native_confirm_enabled !== false;
        document.getElementById('cfg-native-confirm-write').checked = config.native_confirm_write !== false;
        document.getElementById('cfg-native-confirm-delete').checked = config.native_confirm_delete !== false;
        document.getElementById('cfg-native-confirm-exec').checked = config.native_confirm_exec !== false;
        updateNativeConfirmVisibility();
        document.getElementById('cfg-think-level').value = config.think_level || '高';
        var temp = config.temperature !== undefined ? config.temperature : 1.0;
        document.getElementById('cfg-temperature').value = Math.round(temp * 10);
        document.getElementById('cfg-temperature-value').textContent = temp.toFixed(1);
        document.getElementById('cfg-max-tokens').value = config.max_tokens || 32767;
        document.getElementById('cfg-memory').checked = config.memory !== false;
        document.getElementById('cfg-memory-mode').value = config.memory_mode || 'full';
        document.getElementById('cfg-max-rounds').value = config.max_rounds || 10;
        config.plugin_dirs = config.plugin_dirs || [];
        renderPluginDirs();
        renderPluginList();

        document.getElementById('cfg-security-audit').value = config.plugin_security_audit || 'block';
        document.getElementById('cfg-security-import-restrict').value = config.plugin_security_import_restrict || 'strict';
        document.getElementById('cfg-security-permissions').checked = config.plugin_security_require_permissions !== false;
        document.getElementById('cfg-security-resource-limit').checked = config.plugin_security_resource_limit === true;
        renderSecurityAuditSummary();

        // NORP safety system reset
        document.getElementById('cfg-norp-safe-enabled').checked = true;
        updateNorpSafetyStatus();

        // Jailbreak guard reset
        document.getElementById('cfg-jailbreak-guard-enabled').checked = true;
        document.getElementById('cfg-jailbreak-guard-action').value = 'block';
        updateJailbreakGuardStatus();

        // Custom system prompt reset
        document.getElementById('cfg-custom-prompt-enabled').checked = false;
        document.getElementById('cfg-custom-prompt').value = '';
        document.getElementById('cfg-custom-prompt-file').value = '';
        document.getElementById('cfg-custom-prompt-source').value = 'text';
        updateCustomPromptVisibility();
        updateCustomPromptSource();

        // Vision API reset
        document.getElementById('cfg-vision-enabled').checked = false;
        document.getElementById('cfg-vision-service-url').value = '';
        updateVisionVisibility();

        updateThinkMutualExclusion();
        updateQueueWarning();
        ensureModelOptions(document.getElementById('cfg-model'), config.model);
        showToast(t('restored'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + e.message);
    }
}

// Plugin management UI

function renderPluginDirs() {
    var list = document.getElementById('plugin-dirs-list');
    var dirs = config.plugin_dirs || [];
    if (dirs.length === 0) {
        list.innerHTML = '<span style="font-size:11px;color:#888;">' + t('no_plugin_dirs') + '</span>';
        return;
    }
    var html = '';
    for (var i = 0; i < dirs.length; i++) {
        html += '<div style="display:flex;align-items:center;gap:4px;margin-bottom:3px;font-size:12px;">';
        html += '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:#f0f0f0;padding:2px 6px;border-radius:3px;">' + escapeHtml(dirs[i]) + '</span>';
        html += '<button class="btn-small" style="padding:1px 6px;font-size:10px;" onclick="removePluginDir(' + i + ')">x</button>';
        html += '</div>';
    }
    list.innerHTML = html;
}

async function renderPluginList() {
    var list = document.getElementById('plugin-list');
    try {
        var plugins = await window.pywebview.api.get_plugins();
        if (!plugins || plugins.length === 0) {
            list.innerHTML = '<span style="font-size:11px;color:#888;">' + t('no_plugins_found') + '</span>';
            renderSecurityAuditSummary();
            return;
        }
        var html = '';
        for (var i = 0; i < plugins.length; i++) {
            var p = plugins[i];
            var icon = p.enabled ? '✅' : '⚠️';
            var err = p.error ? ' <span style="color:#dc3545;">(' + escapeHtml(p.error) + ')</span>' : '';

            // Security audit badges
            var auditBadge = '';
            if (p.audit_critical > 0) {
                auditBadge += ' <span style="background:#dc3545;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;">🔴' + p.audit_critical + '</span>';
            }
            if (p.audit_warning > 0) {
                auditBadge += ' <span style="background:#e08f3a;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;">🟠' + p.audit_warning + '</span>';
            }

            // Signature / isolation badges（P0-1 / P0-5）
            var sigBadge = '';
            if (p.signature_status === 'trusted') {
                sigBadge += ' <span style="background:#2e7d32;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;" title="' + t('signature_trusted') + '">🔏已签名</span>';
            } else if (p.signature_status === 'unsigned') {
                sigBadge += ' <span style="background:#f9a825;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;" title="' + t('signature_unsigned') + '">未签名</span>';
            } else if (p.signature_status === 'invalid') {
                sigBadge += ' <span style="background:#dc3545;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;" title="' + t('signature_invalid') + '">签名无效</span>';
            } else if (p.signature_status === 'untrusted') {
                sigBadge += ' <span style="background:#e08f3a;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;" title="' + t('signature_untrusted') + '">未信任</span>';
            }
            if (p.isolation === 'process') {
                sigBadge += ' <span style="background:#5c6bc0;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;">🧩隔离</span>';
            } else if (p.isolation === 'inprocess') {
                sigBadge += ' <span style="background:#b71c1c;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;">⚠️进程内</span>';
            }

            html += '<div style="padding:3px 0;border-bottom:1px solid #eee;">';
            html += icon + ' <b>' + escapeHtml(p.name) + '</b> v' + escapeHtml(p.version);
            if (p.publisher) html += ' <span style="font-size:10px;color:#888;">by ' + escapeHtml(p.publisher) + '</span>';
            html += ' — tools:' + p.tool_count + ' hooks:' + p.hook_count;
            html += auditBadge;
            html += sigBadge;
            if (p.description) html += '<br><span style="font-size:10px;color:#888;">' + escapeHtml(p.description) + '</span>';
            html += err;
            html += '</div>';
        }
        list.innerHTML = html;
        renderSecurityAuditSummary();
    } catch(e) {
        list.innerHTML = '<span style="color:#dc3545;">Failed to load plugins: ' + escapeHtml(e.message || e) + '</span>';
    }
}

async function renderSecurityAuditSummary() {
    var el = document.getElementById('plugin-security-audit-summary');
    try {
        var plugins = await window.pywebview.api.get_plugins();
        if (!plugins || plugins.length === 0) {
            el.style.display = 'none';
            return;
        }
        var totalCritical = 0, totalWarning = 0, totalInfo = 0;
        var blockedPlugins = [];
        for (var i = 0; i < plugins.length; i++) {
            var p = plugins[i];
            if (p.audit_critical) totalCritical += p.audit_critical;
            if (p.audit_warning) totalWarning += p.audit_warning;
            if (p.audit_info) totalInfo += p.audit_info;
            if (!p.enabled && p.error && p.error.indexOf('Security audit blocked') >= 0) {
                blockedPlugins.push(p.name);
            }
        }

        if (totalCritical === 0 && totalWarning === 0 && totalInfo === 0 && blockedPlugins.length === 0) {
            el.style.display = 'none';
            return;
        }

        el.style.display = 'block';
        var html = '<b>' + t('audit_summary_label') + ':</b> ';
        var parts = [];
        if (totalCritical > 0) parts.push('<span style="color:#dc3545;">🔴 ' + totalCritical + ' ' + t('audit_critical') + '</span>');
        if (totalWarning > 0) parts.push('<span style="color:#e08f3a;">🟠 ' + totalWarning + ' ' + t('audit_warning') + '</span>');
        if (totalInfo > 0) parts.push('ℹ️ ' + totalInfo + ' ' + t('audit_info'));
        html += parts.join(', ') || 'No issues found';

        if (blockedPlugins.length > 0) {
            html += '<br><span style="color:#dc3545;">' + t('audit_blocked') + ': ' + blockedPlugins.join(', ') + '</span>';
            html += '<br><span style="font-size:10px;color:#888;">' + t('audit_blocked_hint') + '</span>';
            // Add to message center (deduplicated by plugin name)
            for (var b = 0; b < blockedPlugins.length; b++) {
                var alreadyExists = false;
                for (var m = 0; m < messageCenter.length; m++) {
                    if (messageCenter[m].type === 'plugin' && messageCenter[m].detail.indexOf(blockedPlugins[b]) !== -1) {
                        alreadyExists = true;
                        break;
                    }
                }
                if (!alreadyExists) {
                    addToMessageCenter('plugin', t('audit_blocked'), blockedPlugins[b], 'Plugins');
                }
            }
        }
        el.innerHTML = html;
    } catch(e) {
        el.style.display = 'none';
    }
}

// ── 插件控制面板（P0 改造：独立面板）──

async function openPluginPanel() {
    try {
        config = await window.pywebview.api.get_config();
        var sec = await window.pywebview.api.get_plugin_security_config();
        if (sec) {
            config.plugins_enabled = sec.plugins_enabled !== undefined ? sec.plugins_enabled : config.plugins_enabled;
            config.plugin_security_audit = sec.audit;
            config.plugin_security_import_restrict = sec.import_restrict;
            config.plugin_security_require_permissions = sec.require_permissions;
            config.plugin_security_resource_limit = sec.resource_limit;
            config.plugin_isolation = sec.isolation;
            config.plugin_signature_verify = sec.signature_verify;
            config.plugin_trusted_keys = sec.trusted_keys || [];
            config.plugin_network_policy = sec.network_policy;
            config.plugin_network_url_allowlist = sec.network_url_allowlist || [];
            config.plugin_network_domain_allowlist = sec.network_domain_allowlist || [];
            config.approval_enabled = sec.approval_enabled;
        }
    } catch(e) {
        config = config || {};
    }

    document.getElementById('cfg-security-audit').value = config.plugin_security_audit || 'block';
    _prevSecurityAudit = document.getElementById('cfg-security-audit').value;
    document.getElementById('cfg-security-import-restrict').value = config.plugin_security_import_restrict || 'strict';
    document.getElementById('cfg-security-permissions').checked = config.plugin_security_require_permissions !== false;
    document.getElementById('cfg-security-resource-limit').checked = config.plugin_security_resource_limit === true;

    document.getElementById('pp-isolation').value = config.plugin_isolation || 'process';
    document.getElementById('pp-signature-verify').checked = config.plugin_signature_verify !== false;
    document.getElementById('pp-network-policy').value = config.plugin_network_policy || 'deny';
    document.getElementById('pp-network-url-allowlist').value = (config.plugin_network_url_allowlist || []).join(', ');
    document.getElementById('pp-network-domain-allowlist').value = (config.plugin_network_domain_allowlist || []).join(', ');
    document.getElementById('pp-approval-enabled').checked = config.approval_enabled !== false;
    document.getElementById('pp-plugins-enabled').checked = config.plugins_enabled !== false;

    renderPluginDirs();
    renderPluginList();
    document.getElementById('plugin-panel-modal').style.display = 'block';
}

async function savePluginPanel() {
    var urlAllowlist = document.getElementById('pp-network-url-allowlist').value.split(',')
        .map(function(s){return s.trim();}).filter(function(s){return s.length > 0;});
    var domainAllowlist = document.getElementById('pp-network-domain-allowlist').value.split(',')
        .map(function(s){return s.trim();}).filter(function(s){return s.length > 0;});
    try {
        await window.pywebview.api.set_plugin_security_config(
            document.getElementById('cfg-security-audit').value,
            document.getElementById('cfg-security-import-restrict').value,
            document.getElementById('cfg-security-permissions').checked,
            document.getElementById('cfg-security-resource-limit').checked,
            document.getElementById('pp-isolation').value,
            document.getElementById('pp-signature-verify').checked,
            (config.plugin_trusted_keys || []),
            document.getElementById('pp-network-policy').value,
            urlAllowlist,
            domainAllowlist,
            document.getElementById('pp-approval-enabled').checked,
            document.getElementById('pp-plugins-enabled').checked
        );
        document.getElementById('plugin-panel-modal').style.display = 'none';
        showToast(t('settings_saved'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + e.message);
    }
}

// ── NORP 安全系统 UI ──

function updateNorpSafetyStatus() {
    var checkbox = document.getElementById('cfg-norp-safe-enabled');
    var statusEl = document.getElementById('norp-safety-status');

    if (!checkbox || !statusEl) return;

    if (checkbox.checked) {
        statusEl.style.background = '#e8f5e9';
        statusEl.style.color = '#2e7d32';
        statusEl.setAttribute('data-i18n', 'norp_safety_enabled_status');
        statusEl.textContent = t('norp_safety_enabled_status');
    } else {
        statusEl.style.background = '#fff3e0';
        statusEl.style.color = '#e65100';
        statusEl.setAttribute('data-i18n', 'norp_safety_disabled_status');
        statusEl.textContent = t('norp_safety_disabled_status');
    }
    // Also update the top warning bar
    updateNorpSafetyWarningBar();
}

// ── 顶部 NORP 安全警告条 ──
function updateNorpSafetyWarningBar() {
    var bar = document.getElementById('norp-safety-warning-bar');
    if (!bar) return;
    // Always sync i18n text before showing, in case language was switched
    var key = bar.getAttribute('data-i18n');
    if (key) {
        var val = t(key, currentLang);
        if (/<[a-zA-Z][^>]*>/.test(val)) {
            bar.innerHTML = val;
        } else {
            bar.textContent = val;
        }
    }
    var checkbox = document.getElementById('cfg-norp-safe-enabled');
    if (checkbox) {
        // 优先使用 checkbox 当前状态（反映用户最新操作）
        bar.style.display = checkbox.checked ? 'none' : 'block';
        return;
    }
    // fallback: 从 config 读取
    bar.style.display = (config.norp_safe_enabled !== false) ? 'none' : 'block';
}

// NORP safety toggle: custom warning modal (like security-off-warning-modal)
document.addEventListener('DOMContentLoaded', function() {
    var checkbox = document.getElementById('cfg-norp-safe-enabled');
    if (checkbox) {
        checkbox.addEventListener('change', function() {
            if (!this.checked) {
                // Show custom warning modal instead of confirm()
                this.checked = true; // revert for now
                norpSafetyOffWarningModal.style.display = 'block';
            } else {
                config.norp_safe_enabled = true;
                updateNorpSafetyStatus();
                updateNorpSafetyWarningBar();
                try {
                    window.pywebview.api.set_norp_safe_enabled(true);
                } catch(e) {}
            }
        });
    }
});

// NORP safety off modal buttons
document.addEventListener('DOMContentLoaded', function() {
    var confirmBtn = document.getElementById('norp-safety-off-confirm-btn');
    var cancelBtn = document.getElementById('norp-safety-off-cancel-btn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', function() {
            var cb = document.getElementById('cfg-norp-safe-enabled');
            if (cb) cb.checked = false;
            config.norp_safe_enabled = false;
            norpSafetyOffWarningModal.style.display = 'none';
            updateNorpSafetyStatus();
            updateNorpSafetyWarningBar();
            try {
                window.pywebview.api.set_norp_safe_enabled(false);
            } catch(e) {}
        });
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', function() {
            norpSafetyOffWarningModal.style.display = 'none';
            // Checkbox was already reverted to checked in the change handler
        });
    }
});

// ── 越狱防护状态更新 ──
function updateJailbreakGuardStatus() {
    var statusEl = document.getElementById('jailbreak-guard-status');
    var actionEl = document.getElementById('cfg-jailbreak-guard-action');
    var enabled = document.getElementById('cfg-jailbreak-guard-enabled').checked;
    if (!statusEl) return;
    if (enabled) {
        statusEl.style.background = '#e8f5e9';
        statusEl.style.color = '#2e7d32';
        statusEl.setAttribute('data-i18n', 'jailbreak_guard_enabled_status');
        statusEl.textContent = t('jailbreak_guard_enabled_status');
        if (actionEl) actionEl.disabled = false;
    } else {
        statusEl.style.background = '#fff3e0';
        statusEl.style.color = '#e65100';
        statusEl.setAttribute('data-i18n', 'jailbreak_guard_disabled_status');
        statusEl.textContent = t('jailbreak_guard_disabled_status');
        if (actionEl) actionEl.disabled = true;
    }
}

// Jailbreak guard toggle
document.addEventListener('DOMContentLoaded', function() {
    var checkbox = document.getElementById('cfg-jailbreak-guard-enabled');
    if (checkbox) {
        checkbox.addEventListener('change', function() {
            updateJailbreakGuardStatus();
        });
    }
});

async function addPluginDir() {
    try {
        var path = prompt('请输入插件目录路径:');
        if (!path) return;
        await window.pywebview.api.add_plugin_dir(path);
        renderPluginDirs();
    } catch(e) {
        showToast('添加插件目录失败: ' + e.message);
    }
}

// ═══════════════════════════════════════════════════════════════
//  运行时健康检查
// ═══════════════════════════════════════════════════════════════

async function refreshRuntimeHealth() {
    var statusEl = document.getElementById('runtime-health-status');
    var textEl = document.getElementById('runtime-health-text');
    var detailEl = document.getElementById('runtime-health-detail');

    if (!statusEl || !textEl) return;

    try {
        var health = await window.pywebview.api.get_runtime_health();
        if (!health) {
            textEl.textContent = t('runtime_health_no_data');
            statusEl.style.background = '#f0f0f0';
            return;
        }

        var overallHealthy = health.overall_healthy;
        var fatalCount = health.fatal_count || 0;
        var errorCount = health.error_count || 0;
        var warningCount = health.warning_count || 0;
        var envType = health.environment_type || 'unknown';

        // ── 状态栏 ──
        var icon, bgColor, statusText;
        if (fatalCount > 0) {
            icon = '🔴';
            bgColor = '#fce4e4';
            statusText = t('runtime_health_fatal', null).replace('{count}', fatalCount);
        } else if (errorCount > 0) {
            icon = '🟠';
            bgColor = '#fff3e0';
            statusText = t('runtime_health_error', null).replace('{count}', errorCount);
        } else if (warningCount > 0) {
            icon = '🟡';
            bgColor = '#fffde7';
            statusText = t('runtime_health_warning', null).replace('{count}', warningCount);
        } else {
            icon = '✅';
            bgColor = '#e8f5e9';
            statusText = t('runtime_health_ok');
        }

        // 附加环境类型
        var envLabels = {
            'windows_sandbox': t('runtime_env_sandbox'),
            'docker': t('runtime_env_docker'),
            'vm': t('runtime_env_vm'),
            'wine': t('runtime_env_wine'),
            'normal': '',
            'unknown': ''
        };
        statusText += (envLabels[envType] || '');

        statusEl.style.background = bgColor;
        statusEl.querySelector('span').textContent = icon;
        textEl.textContent = statusText;

        // ── 详细信息 ──
        var checks = health.checks || [];
        var detailHtml = '';
        for (var i = 0; i < checks.length; i++) {
            var c = checks[i];
            var sevIcon = {fatal: '🔴', error: '🟠', warning: '🟡', info: 'ℹ️'}[c.severity] || '•';
            var passedIcon = c.passed ? '✓' : '✗';
            var color = c.passed ? '#2e7d32' : (c.severity === 'fatal' ? '#c62828' : c.severity === 'error' ? '#e65100' : '#f57f17');
            detailHtml += '<div style="color:' + color + ';margin-bottom:4px;">';
            detailHtml += sevIcon + ' <b>' + escHtml(c.name) + '</b> ' + passedIcon;
            detailHtml += '<br><span style="color:#666;">' + escHtml(c.message) + '</span>';
            if (c.detail) {
                detailHtml += '<br><span style="color:#888;font-size:10px;">' + escHtml(c.detail).replace(/\n/g, '<br>') + '</span>';
            }
            if (c.suggestion) {
                detailHtml += '<br><span style="color:#1565c0;font-size:10px;">💡 ' + escHtml(c.suggestion) + '</span>';
            }
            detailHtml += '</div>';
        }
        detailEl.innerHTML = detailHtml;

        // 根据 toggle 按钮状态决定是否展开详情
        var toggleBtn = document.getElementById('runtime-health-toggle');
        if (toggleBtn && toggleBtn.textContent === '▼') {
            detailEl.style.display = 'block';
        }

    } catch(e) {
        textEl.textContent = t('runtime_health_failed').replace('{error}', e.message);
        statusEl.style.background = '#f0f0f0';
    }
}

function escHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function browsePluginDir() {
    try {
        var path = await window.pywebview.api.pick_plugin_dir();
        if (!path) return;
        await window.pywebview.api.add_plugin_dir(path);
        config.plugin_dirs = await window.pywebview.api.get_plugin_dirs();
        renderPluginDirs();
        renderPluginList();
        showToast(t('plugin_dir_added'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + (e.message || e));
    }
}

async function removePluginDir(index) {
    var dirs = config.plugin_dirs || [];
    if (index < 0 || index >= dirs.length) return;
    var path = dirs[index];
    try {
        await window.pywebview.api.remove_plugin_dir(path);
        config.plugin_dirs = await window.pywebview.api.get_plugin_dirs();
        renderPluginDirs();
        renderPluginList();
        showToast(t('plugin_dir_removed'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + (e.message || e));
    }
}

async function reloadPlugins() {
    try {
        await window.pywebview.api.reload_plugins();
        config.plugin_dirs = await window.pywebview.api.get_plugin_dirs();
        renderPluginDirs();
        renderPluginList();
        showToast(t('plugins_reloaded'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + (e.message || e));
    }
}

// Attach plugin event handlers when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    var addBtn = document.getElementById('add-plugin-dir');
    if (addBtn) addBtn.addEventListener('click', addPluginDir);
    var reloadBtn = document.getElementById('reload-plugins-btn');
    if (reloadBtn) reloadBtn.addEventListener('click', reloadPlugins);
});

function showApiKeyModal() {
    document.getElementById('apikey-input').value = '';
    apikeyModal.style.display = 'block';
    document.getElementById('apikey-input').focus();
}

async function confirmApiKey() {
    var key = document.getElementById('apikey-input').value.trim();
    if (!key) return;
    try {
        var result = await window.pywebview.api.set_api_key(key);
        if (result === 'ok') {
            apikeyModal.style.display = 'none';
            showToast(t('apikey_updated'));
        } else {
            showToast(t('invalid_apikey'));
            document.getElementById('apikey-input').value = '';
            document.getElementById('apikey-input').focus();
        }
    } catch(e) { showToast(t('error_prefix') + ': ' + e.message); }
}

async function showBalance() {
    try {
        var data = await window.pywebview.api.get_balance();
        if (data.error) {
            showToast(t('balance_query_failed') + ': ' + data.error);
        } else if (data.balance_infos && data.balance_infos.length > 0) {
            var info = data.balance_infos[0];
            var msg = t('balance_format').replace('{total}', info.total_balance).replace('{granted}', info.granted_balance).replace('{topped}', info.topped_up_balance);
            alert(msg);
        } else {
            showToast(t('balance_unavailable'));
        }
    } catch(e) { showToast(t('balance_query_failed') + ': ' + e.message); }
}

sendBtn.addEventListener('click', handleSend);
stopBtn.addEventListener('click', handleStop);
document.getElementById('key-btn').addEventListener('click', showApiKeyModal);
document.getElementById('settings-btn').addEventListener('click', openSettings);
document.getElementById('settings-save-btn').addEventListener('click', saveSettings);
document.getElementById('runtime-health-refresh').addEventListener('click', refreshRuntimeHealth);
document.getElementById('runtime-health-toggle').addEventListener('click', function() {
    var detailEl = document.getElementById('runtime-health-detail');
    var isHidden = detailEl.style.display === 'none' || !detailEl.style.display;
    if (isHidden) {
        detailEl.style.display = 'block';
        this.textContent = '▼';
        this.title = t('runtime_health_toggle_collapse');
    } else {
        detailEl.style.display = 'none';
        this.textContent = '▶';
        this.title = t('runtime_health_toggle_expand');
    }
});

// ── 插件控制面板 ──
document.getElementById('plugins-btn').addEventListener('click', function() {
    var modal = document.getElementById('plugin-panel-modal');
    if (modal.style.display === 'block') {
        modal.style.display = 'none';
    } else {
        openPluginPanel();
    }
});
document.getElementById('plugin-panel-close').addEventListener('click', function() {
    document.getElementById('plugin-panel-modal').style.display = 'none';
});
document.getElementById('plugin-panel-save').addEventListener('click', savePluginPanel);
document.getElementById('plugin-panel-refresh').addEventListener('click', function() {
    openPluginPanel();
});
var openPanelBtn = document.getElementById('open-plugin-panel-btn');
if (openPanelBtn) {
    openPanelBtn.addEventListener('click', function() {
        document.getElementById('settings-modal').style.display = 'none';
        openPluginPanel();
    });
}

// ── Message Center ──
document.getElementById('msg-center-btn').addEventListener('click', function() {
    var modal = document.getElementById('message-center-modal');
    if (modal.style.display === 'block') {
        modal.style.display = 'none';
    } else {
        renderMessageCenter();
        modal.style.display = 'block';
        updateMsgBadge();
    }
});
document.getElementById('msg-center-close').addEventListener('click', function() {
    document.getElementById('message-center-modal').style.display = 'none';
});
document.getElementById('msg-center-clear-all').addEventListener('click', function() {
    clearAllMessages();
});

// Security audit off warning

var _prevSecurityAudit = document.getElementById("cfg-security-audit").value;
document.getElementById("cfg-security-audit").addEventListener("change", function() {
    var newVal = this.value;
    if (newVal === "off" && _prevSecurityAudit !== "off") {
        securityOffWarningModal.style.display = "block";
        this.value = _prevSecurityAudit;
    } else {
        _prevSecurityAudit = newVal;
    }
});
document.getElementById("security-off-confirm-btn").addEventListener("click", function() {
    document.getElementById("cfg-security-audit").value = "off";
    _prevSecurityAudit = "off";
    securityOffWarningModal.style.display = "none";
});
document.getElementById("security-off-cancel-btn").addEventListener("click", function() {
    securityOffWarningModal.style.display = "none";
});
document.getElementById('settings-cancel').addEventListener('click', function() { settingsModal.style.display = 'none'; });
document.getElementById('settings-restore-defaults').addEventListener('click', restoreDefaults);

document.getElementById('clear-memory-btn').addEventListener('click', async function() {
    if (!confirm('Clear all saved conversation history? This cannot be undone.')) {
        return;
    }
    try {
        var tab = getActiveTab();
        var sid = tab ? tab.dbId : '';
        var result = await window.pywebview.api.clear_memory(sid);
        if (result) {
            document.getElementById('memory-status').textContent = t('memory_cleared');
            showToast(t('memory_cleared'));
        } else {
            document.getElementById('memory-status').textContent = t('no_memory');
            showToast(t('no_memory'));
        }
    } catch(e) {
        showToast(t('save_failed') + ': ' + e.message);
    }
});
document.getElementById('cfg-queue-size').addEventListener('input', updateQueueWarning);
document.getElementById('balance-btn').addEventListener('click', showBalance);
document.getElementById('about-btn').addEventListener('click', function() { aboutModal.style.display = 'block'; });
document.getElementById('about-close-btn').addEventListener('click', function() { aboutModal.style.display = 'none'; });

document.getElementById('apikey-confirm-btn').addEventListener('click', confirmApiKey);
document.getElementById('apikey-cancel-btn').addEventListener('click', function() { apikeyModal.style.display = 'none'; });
document.getElementById('apikey-get-btn').addEventListener('click', function() {
    openExternal('https://platform.deepseek.com/');
});

userInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    } else if (e.key === 'Enter' && (e.ctrlKey || e.shiftKey)) {
        e.preventDefault();
        insertNewlineAtCursor();
    }
});
userInput.addEventListener('input', autoResizeTextarea);

apikeyModal.addEventListener('click', function(e) { if (e.target === apikeyModal) apikeyModal.style.display = 'none'; });
aboutModal.addEventListener('click', function(e) { if (e.target === aboutModal) aboutModal.style.display = 'none'; });

function checkCDN() {
    var missing = [];
    if (typeof marked === 'undefined') missing.push('marked');
    if (typeof katex === 'undefined') missing.push('katex');
    if (missing.length > 0) {
        reloadCDN();
    }
}

function reloadCDN() {
    if (typeof marked === 'undefined') {
        var script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/marked/marked.min.js';
        document.head.appendChild(script);
    }
    if (typeof katex === 'undefined') {
        if (!document.querySelector('link[href*="katex.min.css"]')) {
            var link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css';
            document.head.appendChild(link);
        }
        var script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js';
        document.head.appendChild(script);
    }
}

setInterval(function() {
    if (navigator.onLine) {
        if (typeof marked === 'undefined' || typeof katex === 'undefined') {
            checkCDN();
        }
    }
}, 3000);

setTimeout(function() {
    if (navigator.onLine) {
        if (typeof marked === 'undefined' || typeof katex === 'undefined') {
            checkCDN();
        }
    }
}, 1000);

// ═══════════════════════════════════════════════════════════════
//  🛠️ 调试面板（Agent 运行诊断）
// ═══════════════════════════════════════════════════════════════

function fmtJson(obj) {
    if (obj === null || obj === undefined) return '';
    try {
        if (typeof obj === 'string') return obj;
        return JSON.stringify(obj, null, 2);
    } catch (e) {
        return String(obj);
    }
}

async function openDebugPanel() {
    var modal = document.getElementById('debug-modal');
    if (modal) modal.style.display = 'block';
    await refreshDebugPanel();
}

async function refreshDebugPanel() {
    var content = document.getElementById('debug-content');
    if (content) content.innerHTML = '<div class="debug-empty">⏳ 正在加载调试数据...</div>';
    try {
        var data = await window.pywebview.api.get_debug_data();
        renderDebugPanel(data || {});
    } catch (e) {
        if (content) content.innerHTML = '<div class="debug-empty">加载失败: ' + escHtml((e && e.message) || e) + '</div>';
    }
}

function renderDebugPanel(data) {
    var summary = document.getElementById('debug-summary');
    var content = document.getElementById('debug-content');
    if (!content) return;

    // 顶部摘要
    var parts = [];
    if (data.task_id) parts.push('任务: ' + escHtml(data.task_id));
    if (data.started_at) parts.push('开始: ' + escHtml(data.started_at));
    if (data.finished_at) parts.push('结束: ' + escHtml(data.finished_at));
    if (data.user_message) parts.push('提问: ' + escHtml(String(data.user_message).slice(0, 40)));
    if (data.log_dir) parts.push('日志目录: ' + escHtml(data.log_dir));
    if (summary) summary.innerHTML = parts.length ? parts.join(' &nbsp;|&nbsp; ') : '暂无任务记录';

    var html = '';
    html += renderReactTimeline(data.react_steps);
    html += renderToolCalls(data.tool_calls);
    html += renderSecurityEvents(data.security_events);
    html += renderHookEvents(data.hook_events);
    html += renderSnapshot(data.snapshot);
    content.innerHTML = html;
}

// ── 调试面板区块折叠/展开（默认折叠）──
function toggleDebugSection(headerEl) {
    if (!headerEl) return;
    var body = headerEl.nextElementSibling;
    if (!body) return;
    var caret = headerEl.querySelector('.debug-caret');
    var isCollapsed = body.style.display === 'none';
    body.style.display = isCollapsed ? 'block' : 'none';
    if (caret) caret.textContent = isCollapsed ? '▼' : '▶';
}

// ── 模块 1：ReAct 循环时间线 ──
function renderReactTimeline(steps) {
    steps = steps || [];
    var html = '<div class="debug-section">';
    html += '<div class="debug-section-header debug-collapse" onclick="toggleDebugSection(this)" title="点击展开/折叠">';
    html += '<span class="debug-caret">▶</span> 📜 ReAct 循环时间线 <span class="debug-count">' + steps.length + ' 步</span>';
    html += '</div>';
    html += '<div class="debug-section-body" style="display:none">';
    if (!steps.length) {
        html += '<div class="debug-empty">暂无 ReAct 循环记录（尚未运行任务）</div>';
    } else {
        for (var i = 0; i < steps.length; i++) {
            var s = steps[i];
            var elapsed = s.elapsed_ms !== undefined ? (s.elapsed_ms / 1000).toFixed(2) + 's' : '-';
            html += '<div class="debug-card">';
            html += '<div class="debug-card-title">步骤 #' + s.step + ' &nbsp;<span class="debug-meta">' + escHtml(s.timestamp || '') + ' · 累计耗时 ' + elapsed + '</span></div>';

            if (s.reasoning) {
                html += '<div class="debug-label">🧠 推理过程（思考链）</div>';
                html += '<div class="debug-pre">' + escHtml(s.reasoning) + '</div>';
            }

            if (s.tool_calls && s.tool_calls.length) {
                html += '<div class="debug-label">🔧 工具调用</div>';
                for (var j = 0; j < s.tool_calls.length; j++) {
                    var tc = s.tool_calls[j];
                    html += '<div class="debug-pre">' + escHtml(tc.name || '') + '(' + escHtml(tc.arguments || '') + ')</div>';
                }
            }

            if (s.observations && s.observations.length) {
                html += '<div class="debug-label">👁️ 观察结果</div>';
                for (var k = 0; k < s.observations.length; k++) {
                    var o = s.observations[k];
                    html += '<div class="debug-pre">[' + escHtml(o.tool || '') + '] ' + escHtml(o.result || '') + '</div>';
                }
            }
            html += '</div>';
        }
    }
    html += '</div></div>';
    return html;
}

// ── 模块 2：工具调用详情 ──
function renderToolCalls(calls) {
    calls = calls || [];
    var html = '<div class="debug-section">';
    html += '<div class="debug-section-header">🛠️ 工具调用详情 <span class="debug-count">' + calls.length + ' 次</span></div>';
    html += '<div class="debug-section-body">';
    if (!calls.length) {
        html += '<div class="debug-empty">暂无工具调用记录</div>';
    } else {
        for (var i = 0; i < calls.length; i++) {
            var c = calls[i];
            var blockedTag = c.blocked ? '<span class="debug-tag blocked">🚫 拦截</span>' : '';
            html += '<div class="debug-card">';
            html += '<div class="debug-card-title">' + escHtml(c.tool || '') + ' ' + blockedTag + ' <span class="debug-meta">步骤 #' + (c.step || '-') + ' · ' + escHtml(c.timestamp || '') + ' · 耗时 ' + (c.elapsed_ms !== undefined ? c.elapsed_ms + ' ms' : '-') + '</span></div>';

            if (c.blocked && c.blocked_reason) {
                html += '<div class="debug-label">拦截原因</div><div class="debug-pre">' + escHtml(c.blocked_reason) + '</div>';
            }

            if (c.args !== undefined && c.args !== null) {
                html += '<div class="debug-label">📥 入参</div>';
                html += '<div class="debug-pre">' + escHtml(fmtJson(c.args)) + '</div>';
            }
            if (c.result) {
                html += '<div class="debug-label">📤 出参</div>';
                html += '<div class="debug-pre">' + escHtml(fmtJson(c.result)) + '</div>';
            }
            if (c.sandbox_paths && Object.keys(c.sandbox_paths).length) {
                html += '<div class="debug-label">🗺️ 沙箱路径映射</div>';
                html += '<div class="debug-pre">' + escHtml(fmtJson(c.sandbox_paths)) + '</div>';
            }
            html += '</div>';
        }
    }
    html += '</div></div>';
    return html;
}

// ── 模块 3：安全拦截日志 ──
function renderSecurityEvents(events) {
    events = events || [];
    var html = '<div class="debug-section">';
    html += '<div class="debug-section-header debug-collapse" onclick="toggleDebugSection(this)" title="点击展开/折叠">';
    html += '<span class="debug-caret">▶</span> 🛡️ 安全拦截日志 <span class="debug-count">' + events.length + ' 条</span>';
    html += '</div>';
    html += '<div class="debug-section-body" style="display:none">';
    if (!events.length) {
        html += '<div class="debug-empty">暂无安全拦截事件（✅ 安全系统运行正常）</div>';
    } else {
        for (var i = events.length - 1; i >= 0; i--) {
            var e = events[i];
            var actionTag = '';
            if (e.action === 'blocked') actionTag = '<span class="debug-tag blocked">🚫 拦截</span>';
            else if (e.action === 'allowed') actionTag = '<span class="debug-tag allowed">✅ 放行</span>';
            else actionTag = '<span class="debug-tag">' + escHtml(e.action || '未知') + '</span>';
            var level = escHtml(e.threat_level || '');
            html += '<div class="debug-card">';
            html += '<div class="debug-card-title">' + actionTag + ' <span class="debug-meta">' + escHtml(e.timestamp || '') + ' · 威胁等级: ' + level + ' · ' + escHtml(e.event_type || '') + '</span></div>';
            if (e.reason) html += '<div class="debug-label">规则命中</div><div class="debug-pre">' + escHtml(e.reason) + '</div>';
            if (e.details) html += '<div class="debug-label">详情</div><div class="debug-pre">' + escHtml(fmtJson(e.details)) + '</div>';
            html += '</div>';
        }
    }
    html += '</div></div>';
    return html;
}

// ── 模块 4：插件钩子触发记录 ──
function renderHookEvents(events) {
    events = events || [];
    var html = '<div class="debug-section">';
    html += '<div class="debug-section-header">🪝 插件钩子触发记录 <span class="debug-count">' + events.length + ' 次</span></div>';
    html += '<div class="debug-section-body">';
    if (!events.length) {
        html += '<div class="debug-empty">暂无插件钩子触发（未加载插件或插件未注册钩子）</div>';
    } else {
        for (var i = 0; i < events.length; i++) {
            var h = events[i];
            var layer = (h.layer || '?').toLowerCase();
            var layerTag = '<span class="debug-tag ' + layer + '">' + escHtml(h.layer || '?') + '</span>';
            var actionTag = '';
            if (h.action === 'mutated') actionTag = '<span class="debug-tag mutated">✏️ 修改数据</span>';
            else if (h.action === 'blocked') actionTag = '<span class="debug-tag blocked">🚫 拦截</span>';
            else actionTag = '<span class="debug-tag">🔔 触发</span>';
            html += '<div class="debug-card">';
            html += '<div class="debug-card-title">' + layerTag + ' ' + escHtml(h.hook || '') + ' ' + actionTag + ' <span class="debug-meta">' + escHtml(h.plugin || '') + ' · 步骤 #' + (h.step || '-') + ' · ' + escHtml(h.timestamp || '') + '</span></div>';
            if (h.before !== undefined && h.before !== null) {
                html += '<div class="debug-label">变形前</div><div class="debug-pre">' + escHtml(fmtJson(h.before)) + '</div>';
            }
            if (h.after !== undefined && h.after !== null) {
                html += '<div class="debug-label">变形后</div><div class="debug-pre">' + escHtml(fmtJson(h.after)) + '</div>';
            }
            html += '</div>';
        }
    }
    html += '</div></div>';
    return html;
}

// ── 模块 5：性能与状态快照 ──
function renderSnapshot(snapshot) {
    snapshot = snapshot || {};
    var tokens = snapshot.tokens || {};
    var pool = snapshot.sandbox_pool || {};
    var fio = snapshot.file_io_queue || {};

    var html = '<div class="debug-section">';
    html += '<div class="debug-section-header">⚡ 性能与状态快照</div>';
    html += '<div class="debug-section-body">';

    // Token 计量
    html += '<div class="debug-label" style="margin-bottom:6px;">🔢 Token 实时计量</div>';
    html += '<div class="debug-grid">';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (tokens.input_tokens || 0).toLocaleString() + '</div><div class="debug-stat-label">输入 Token</div></div>';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (tokens.output_tokens || 0).toLocaleString() + '</div><div class="debug-stat-label">输出 Token</div></div>';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (tokens.tool_call_tokens || 0).toLocaleString() + '</div><div class="debug-stat-label">工具调用 Token</div></div>';
    html += '</div>';

    // 沙箱池
    html += '<div class="debug-label" style="margin:10px 0 6px;">🏖️ 沙箱池（Sandbox Pool）</div>';
    html += '<div class="debug-grid">';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (pool.total !== undefined ? pool.total : '-') + '</div><div class="debug-stat-label">总沙箱数</div></div>';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (pool.available !== undefined ? pool.available : '-') + '</div><div class="debug-stat-label">可用</div></div>';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (pool.in_use !== undefined ? pool.in_use : '-') + '</div><div class="debug-stat-label">占用中</div></div>';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (pool.created !== undefined ? pool.created : '-') + '</div><div class="debug-stat-label">已创建</div></div>';
    html += '</div>';

    // 文件 I/O 队列
    html += '<div class="debug-label" style="margin:10px 0 6px;">📁 文件 I/O 队列</div>';
    html += '<div class="debug-grid">';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (fio.active_files !== undefined ? fio.active_files : '-') + '</div><div class="debug-stat-label">活跃文件数</div></div>';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (fio.total_tracked_files !== undefined ? fio.total_tracked_files : '-') + '</div><div class="debug-stat-label">跟踪文件总数</div></div>';
    html += '</div>';

    // 事件队列
    html += '<div class="debug-label" style="margin:10px 0 6px;">📨 事件队列（event_queue）</div>';
    html += '<div class="debug-grid">';
    html += '<div class="debug-stat"><div class="debug-stat-value">' + (snapshot.event_queue_size !== undefined ? snapshot.event_queue_size : '-') + '</div><div class="debug-stat-label">当前队列长度</div></div>';
    html += '</div>';

    html += '</div></div>';
    return html;
}

// ── 事件绑定 ──
document.addEventListener('DOMContentLoaded', function() {
    var debugBtn = document.getElementById('debug-btn');
    if (debugBtn) debugBtn.addEventListener('click', openDebugPanel);

    var debugClose = document.getElementById('debug-close-btn');
    if (debugClose) debugClose.addEventListener('click', function() {
        var modal = document.getElementById('debug-modal');
        if (modal) modal.style.display = 'none';
    });

    var debugRefresh = document.getElementById('debug-refresh-btn');
    if (debugRefresh) debugRefresh.addEventListener('click', refreshDebugPanel);

    var debugOpenLog = document.getElementById('debug-open-log-btn');
    if (debugOpenLog) debugOpenLog.addEventListener('click', async function() {
        try {
            var r = await window.pywebview.api.open_debug_log_dir();
            if (r && r.indexOf('error:') === 0) showToast('打开失败: ' + r.replace('error:', ''));
        } catch (e) { showToast('打开失败: ' + (e.message || e)); }
    });
});

// 点击调试面板遮罩关闭
(function() {
    var modal = document.getElementById('debug-modal');
    if (modal) modal.addEventListener('click', function(e) {
        if (e.target === modal) modal.style.display = 'none';
    });
})();