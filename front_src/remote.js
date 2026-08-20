// ═══════════════════════════════════════════════════════════════
//  移动端远程控制 —— 桌面端按钮 + 二维码/链接
// ═══════════════════════════════════════════════════════════════

function remoteEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function openRemote() {
    document.getElementById('remote-modal').style.display = 'block';
    refreshRemote();
}

function closeRemote() {
    document.getElementById('remote-modal').style.display = 'none';
}

async function refreshRemote() {
    var body = document.getElementById('remote-body');
    var st = {};
    try { st = await window.pywebview.api.get_remote_status(); } catch (e) { st = {}; }

    if (!st.enabled) {
        body.innerHTML = '<div style="background:#fff3cd;color:#664d03;padding:10px 12px;border-radius:8px;font-size:13px;line-height:1.8;">' +
            '此功能需要先<b>启用远程访问</b>才能让手机连上。<br>' +
            '当前服务未启用，手机无法访问。<br>' +
            '请前往 <b>设置 → 📱 移动端远程控制</b>：勾选「启用远程访问」，把绑定地址设为 <b>0.0.0.0</b>（或填写内网穿透的公网地址），然后<b>重启 NORP</b>。</div>';
        return;
    }
    if (!st.running) {
        body.innerHTML = '<div style="background:#fff3cd;color:#664d03;padding:10px 12px;border-radius:8px;font-size:13px;line-height:1.8;">' +
            '⚠️ 远程服务<b>启动失败</b>，很可能是端口被占用（例如 DeepSeek Harness 占用了 3080）。<br>' +
            '请在 <b>设置 → 📱 移动端远程控制</b> 中换一个端口（如 <b>8090</b>），保存后<b>重启 NORP</b>。</div>';
        return;
    }
    if (!st.lan_accessible) {
        body.innerHTML = '<div style="background:#fff3cd;color:#664d03;padding:10px 12px;border-radius:8px;font-size:13px;line-height:1.8;">' +
            '当前服务仅绑定 <b>' + remoteEsc(st.host || '127.0.0.1') + '</b>，手机无法访问。<br>' +
            '请在 <b>设置 → 📱 移动端远程控制</b> 中把绑定地址改为 <b>0.0.0.0</b> 并重启。</div>';
        return;
    }

    var ips = st.lan_ips || [];
    if (!ips.length) ips = ['127.0.0.1'];

    var html = '<div style="font-size:13px;color:#888;">选择二维码指向的网络：</div>' +
        '<select id="remote-net" style="width:100%;margin:6px 0;background:#1e232d;color:#e6e6e6;border:1px solid #333;border-radius:6px;padding:6px;">';
    ips.forEach(function (ip) {
        html += '<option value="' + remoteEsc(ip) + '">局域网 ' + remoteEsc(ip) + '</option>';
    });
    html += '</select>' +
        '<div style="text-align:center;margin:12px 0;"><img id="remote-qr-img" alt="二维码" style="background:#fff;padding:8px;border-radius:8px;width:200px;height:200px;"></div>' +
        '<div class="flex-row" style="gap:6px;">' +
        '<input type="text" id="remote-link-input" readonly style="flex:1;background:#1e232d;color:#e6e6e6;border:1px solid #333;border-radius:6px;padding:6px 8px;font-size:12px;">' +
        '<button class="btn-small" id="remote-copy">复制</button>' +
        '</div>' +
        '<div style="font-size:11px;color:#888;margin-top:6px;">手机与电脑在同一网络时，扫码或打开链接即可。</div>';
    body.innerHTML = html;

    function renderQr() {
        var ip = document.getElementById('remote-net').value;
        var url = 'http://' + ip + ':' + (st.port || 8090) + '/';
        document.getElementById('remote-link-input').value = url;
        window.pywebview.api.get_remote_qr(url).then(function (dataUrl) {
            if (dataUrl) document.getElementById('remote-qr-img').src = dataUrl;
        });
    }
    document.getElementById('remote-net').addEventListener('change', renderQr);
    document.getElementById('remote-copy').addEventListener('click', function () {
        var el = document.getElementById('remote-link-input');
        el.select();
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(el.value).then(function () {
                document.getElementById('remote-copy').textContent = '已复制';
            });
        }
    });
    renderQr();
}

document.getElementById('remote-btn').addEventListener('click', openRemote);
document.getElementById('remote-close').addEventListener('click', closeRemote);
