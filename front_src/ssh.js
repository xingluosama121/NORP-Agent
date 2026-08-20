// ═══════════════════════════════════════════════════════════════
//  SSH — 远程运维面板
//  主机管理 / 执行 / 传输 / 隧道 / 集群
// ═══════════════════════════════════════════════════════════════

function sshEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function sshApi(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    return window.pywebview.api[name].apply(window.pywebview.api, args);
}

function openSsh() {
    document.getElementById('ssh-modal').style.display = 'block';
    sshSwitchTab('hosts');
}

function closeSsh() {
    document.getElementById('ssh-modal').style.display = 'none';
}

function sshSwitchTab(tab) {
    document.querySelectorAll('.ssh-tab').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-tab') === tab);
    });
    ['hosts', 'exec', 'transfer', 'tunnel', 'cluster', 'terminal'].forEach(function (name) {
        var el = document.getElementById('ssh-panel-' + name);
        if (el) el.style.display = (name === tab) ? 'block' : 'none';
    });
    if (tab === 'hosts') sshRefreshHosts();
    if (tab === 'exec' || tab === 'transfer' || tab === 'tunnel' || tab === 'terminal') sshPopulateHostSelects();
    if (tab === 'tunnel') sshTunnelList();
    if (tab === 'terminal') sshTermEnsure();
}

// ── 主机管理 ────────────────────────────────────────────────────
async function sshRefreshHosts() {
    var list = document.getElementById('ssh-host-list');
    var q = document.getElementById('ssh-host-search').value || '';
    var hosts = [];
    try { hosts = await sshApi('ssh_list_hosts', q); } catch (e) { hosts = []; }
    if (!hosts || !hosts.length) {
        list.innerHTML = '<div style="color:#888;padding:8px;">暂无主机。用下方表单添加，或点「导入 ~/.ssh/config」。</div>';
        return;
    }
    var html = '<table class="ssh-host-table"><thead><tr>' +
        '<th>别名</th><th>主机</th><th>用户</th><th>端口</th><th>认证</th><th>密钥</th><th>标签</th><th>操作</th>' +
        '</tr></thead><tbody>';
    hosts.forEach(function (h) {
        var auth = h.auth === 'password' ? '🔒密码' : '🔑密钥';
        var key = (h.auth !== 'password' && h.identity_file) ? (h.key_ready ? '✅' : '⚠️') : '—';
        var tags = (h.tags || []).join(',');
        html += '<tr>' +
            '<td><b>' + sshEsc(h.alias) + '</b></td>' +
            '<td>' + sshEsc(h.host) + '</td>' +
            '<td>' + sshEsc(h.user) + '</td>' +
            '<td>' + h.port + '</td>' +
            '<td>' + auth + '</td>' +
            '<td>' + key + '</td>' +
            '<td>' + sshEsc(tags) + '</td>' +
            '<td>' +
                '<button class="btn-small" data-test="' + sshEsc(h.alias) + '">测</button> ' +
                '<button class="btn-small btn-danger" data-del="' + sshEsc(h.alias) + '">删</button>' +
            '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    list.innerHTML = html;
    list.querySelectorAll('[data-test]').forEach(function (b) {
        b.onclick = function () { sshTestHost(b.getAttribute('data-test')); };
    });
    list.querySelectorAll('[data-del]').forEach(function (b) {
        b.onclick = function () { sshDelHost(b.getAttribute('data-del')); };
    });
}

async function sshAddHost() {
    var g = function (id) { return document.getElementById(id).value.trim(); };
    var entry = {
        alias: g('ssh-f-alias'), host: g('ssh-f-host'), user: g('ssh-f-user'),
        port: parseInt(g('ssh-f-port') || '22', 10),
        auth: document.getElementById('ssh-f-auth').value,
        identity_file: g('ssh-f-identity'), password: document.getElementById('ssh-f-password').value,
        jump: g('ssh-f-jump'), tags: g('ssh-f-tags')
    };
    if (!entry.alias || !entry.host) { alert('别名 和 主机 为必填'); return; }
    var r = await sshApi('ssh_add_host', entry);
    alert(r.message || (r.ok ? '已保存' : '保存失败'));
    sshRefreshHosts();
}

async function sshDelHost(alias) {
    if (!confirm('确认删除主机 ' + alias + ' ？')) return;
    var r = await sshApi('ssh_remove_host', alias);
    alert(r.message || '');
    sshRefreshHosts();
}

async function sshTestHost(alias) {
    var r = await sshApi('ssh_test_host', alias, 12);
    alert((r.ok ? '✅ ' : '❌ ') + (r.message || ''));
}

async function sshImportConfig() {
    var r = await sshApi('ssh_import_config');
    alert(r.message || '');
    sshRefreshHosts();
}

// ── 主机下拉框（执行/传输/隧道共用）─────────────────────────────
async function sshPopulateHostSelects() {
    var hosts = [];
    try { hosts = await sshApi('ssh_list_hosts', ''); } catch (e) { hosts = []; }
    ['ssh-exec-host', 'ssh-xfer-host', 'ssh-tun-host', 'ssh-term-host'].forEach(function (id) {
        var sel = document.getElementById(id);
        if (!sel) return;
        var cur = sel.value;
        sel.innerHTML = '';
        (hosts || []).forEach(function (h) {
            var o = document.createElement('option');
            o.value = h.alias;
            o.textContent = h.alias + ' (' + h.host + ')';
            sel.appendChild(o);
        });
        if (cur) sel.value = cur;
    });
}

// ── 执行 ────────────────────────────────────────────────────────
async function sshExecRun() {
    var host = document.getElementById('ssh-exec-host').value;
    var cmd = document.getElementById('ssh-exec-cmd').value;
    var timeout = parseInt(document.getElementById('ssh-exec-timeout').value) || 60;
    if (!host || !cmd) { alert('请选主机并输入命令'); return; }
    var out = document.getElementById('ssh-exec-output');
    out.style.display = 'block';
    out.textContent = '执行中…';
    var r = await sshApi('ssh_exec_cmd', host, cmd, timeout);
    out.textContent = (r.stdout ? r.stdout : '') +
        (r.stderr ? '\n[stderr] ' + r.stderr : '') +
        '\n[exit ' + r.rc + ']';
}

// ── 传输 ────────────────────────────────────────────────────────
async function sshUpload() {
    var host = document.getElementById('ssh-xfer-host').value;
    var local = document.getElementById('ssh-up-local').value.trim();
    var remote = document.getElementById('ssh-up-remote').value.trim();
    if (!host || !local || !remote) { alert('请选主机并填写路径'); return; }
    var r = await sshApi('ssh_upload_file', host, local, remote);
    alert(r.message || (r.ok ? '上传成功' : '上传失败'));
}

async function sshDownload() {
    var host = document.getElementById('ssh-xfer-host').value;
    var remote = document.getElementById('ssh-down-remote').value.trim();
    var local = document.getElementById('ssh-down-local').value.trim();
    if (!host || !remote || !local) { alert('请选主机并填写路径'); return; }
    var r = await sshApi('ssh_download_file', host, remote, local);
    alert(r.message || (r.ok ? '下载成功' : '下载失败'));
}

async function sshPickUpload() {
    try { var p = await sshApi('pick_open_file'); if (typeof p === 'string' && p) document.getElementById('ssh-up-local').value = p; } catch (e) {}
}

async function sshPickDownload() {
    try { var p = await sshApi('pick_save_file'); if (typeof p === 'string' && p) document.getElementById('ssh-down-local').value = p; } catch (e) {}
}

// ── 隧道 ────────────────────────────────────────────────────────
async function sshTunnelStart() {
    var host = document.getElementById('ssh-tun-host').value;
    var rp = parseInt(document.getElementById('ssh-tun-remote-port').value);
    var rh = document.getElementById('ssh-tun-remote-host').value.trim() || '127.0.0.1';
    if (!host || !rp) { alert('请选主机并填远程端口'); return; }
    var r = await sshApi('ssh_tunnel_start', host, rp, rh, 0);
    alert(r.message || '');
    sshTunnelList();
}

async function sshTunnelList() {
    var list = document.getElementById('ssh-tun-list');
    var ts = [];
    try { ts = await sshApi('ssh_tunnel_list'); } catch (e) { ts = []; }
    if (!ts || !ts.length) { list.innerHTML = '<div style="color:#888;padding:6px;">没有活动隧道</div>'; return; }
    var html = '';
    ts.forEach(function (t) {
        html += '<div class="flex-row" style="gap:6px;align-items:center;padding:6px 0;border-bottom:1px solid #2a2a2a;">' +
            '<span>' + (t.alive ? '🟢' : '🔴') + ' ' + sshEsc(t.id) + '</span>' +
            '<span style="color:#888;font-size:12px;">127.0.0.1:' + t.local_port + ' → ' + sshEsc(t.remote) + '</span>' +
            '<span style="flex:1;"></span>' +
            '<button class="btn-small btn-danger" data-tstop="' + sshEsc(t.id) + '">关闭</button>' +
            '</div>';
    });
    list.innerHTML = html;
    list.querySelectorAll('[data-tstop]').forEach(function (b) {
        b.onclick = function () { sshTunnelStop(b.getAttribute('data-tstop')); };
    });
}

async function sshTunnelStop(id) {
    var r = await sshApi('ssh_tunnel_stop', id);
    alert(r.message || '');
    sshTunnelList();
}

async function sshTunnelStopAll() {
    var r = await sshApi('ssh_tunnel_stop_all');
    alert(r.message || '');
    sshTunnelList();
}

// ── 集群 ────────────────────────────────────────────────────────
async function sshClusterRun() {
    var cmd = document.getElementById('ssh-cl-cmd').value;
    if (!cmd) { alert('请输入命令'); return; }
    var aliases = document.getElementById('ssh-cl-aliases').value.trim().split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    var tags = document.getElementById('ssh-cl-tags').value.trim().split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    var env = document.getElementById('ssh-cl-env').value.trim();
    var res = [];
    try { res = await sshApi('ssh_cluster_run', cmd, aliases, env, tags, 60, 8); } catch (e) { res = []; }
    var el = document.getElementById('ssh-cl-result');
    if (!res || !res.length) { el.innerHTML = '<div style="color:#888;">没有匹配的主机</div>'; return; }
    var html = '';
    res.forEach(function (r) {
        html += '<div style="padding:6px 0;border-bottom:1px solid #2a2a2a;">' +
            '<b>' + (r.rc === 0 ? '✅' : '❌') + ' ' + sshEsc(r.alias) + '</b> (exit ' + r.rc + ')' +
            '<pre style="margin:4px 0 0;white-space:pre-wrap;font-size:12px;color:#ccc;">' + sshEsc(r.output) + '</pre></div>';
    });
    el.innerHTML = html;
}

// ── 终端 ────────────────────────────────────────────────────────
var sshTermId = null;
var sshTermTimer = null;

function sshStripAnsi(s) {
    return String(s || '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
}

async function sshTermOpen() {
    var host = document.getElementById('ssh-term-host').value;
    if (!host) { alert('请先选主机'); return; }
    var r = await sshApi('ssh_terminal_open', host);
    if (!r || !r.ok) { alert((r && r.message) || '连接失败'); return; }
    sshTermId = r.terminal_id;
    document.getElementById('ssh-term-output').textContent = '';
    sshTermPoll();
    if (sshTermTimer) clearInterval(sshTermTimer);
    sshTermTimer = setInterval(sshTermPoll, 300);
}

async function sshTermPoll() {
    if (!sshTermId) return;
    var r = await sshApi('ssh_terminal_read', sshTermId);
    var out = document.getElementById('ssh-term-output');
    if (r && r.data) {
        out.textContent += sshStripAnsi(r.data);
        out.scrollTop = out.scrollHeight;
    }
    if (r && r.closed) {
        out.textContent += '\n[连接已关闭]';
        sshTermStop();
    }
}

async function sshTermSend() {
    var input = document.getElementById('ssh-term-input');
    var text = input.value;
    if (!sshTermId) { alert('未连接'); return; }
    input.value = '';
    await sshApi('ssh_terminal_write', sshTermId, text + '\n');
    sshTermPoll();
}

async function sshTermClose() {
    if (sshTermId) { await sshApi('ssh_terminal_close', sshTermId); }
    sshTermStop();
    document.getElementById('ssh-term-output').textContent += '\n[已断开]';
}

function sshTermStop() {
    sshTermId = null;
    if (sshTermTimer) { clearInterval(sshTermTimer); sshTermTimer = null; }
}

function sshTermEnsure() {
    sshPopulateHostSelects();
    if (sshTermId && !sshTermTimer) sshTermTimer = setInterval(sshTermPoll, 300);
}

// ── 事件绑定 ────────────────────────────────────────────────────
document.getElementById('ssh-btn').addEventListener('click', openSsh);
document.getElementById('ssh-close').addEventListener('click', closeSsh);
document.getElementById('ssh-host-add').addEventListener('click', sshAddHost);
document.getElementById('ssh-host-refresh').addEventListener('click', sshRefreshHosts);
document.getElementById('ssh-host-import').addEventListener('click', sshImportConfig);
document.getElementById('ssh-host-search').addEventListener('input', sshRefreshHosts);
document.getElementById('ssh-exec-run').addEventListener('click', sshExecRun);
document.getElementById('ssh-up-go').addEventListener('click', sshUpload);
document.getElementById('ssh-down-go').addEventListener('click', sshDownload);
document.getElementById('ssh-up-browse').addEventListener('click', sshPickUpload);
document.getElementById('ssh-down-browse').addEventListener('click', sshPickDownload);
document.getElementById('ssh-tun-start').addEventListener('click', sshTunnelStart);
document.getElementById('ssh-tun-stop-all').addEventListener('click', sshTunnelStopAll);
document.getElementById('ssh-cl-run').addEventListener('click', sshClusterRun);
document.getElementById('ssh-term-open').addEventListener('click', sshTermOpen);
document.getElementById('ssh-term-close').addEventListener('click', sshTermClose);
document.getElementById('ssh-term-send').addEventListener('click', sshTermSend);
document.getElementById('ssh-term-input').addEventListener('keydown', function (e) { if (e.key === 'Enter') sshTermSend(); });
document.querySelectorAll('.ssh-tab').forEach(function (b) {
    b.addEventListener('click', function () { sshSwitchTab(b.getAttribute('data-tab')); });
});
