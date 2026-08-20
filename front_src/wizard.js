var wizParticlesRunning = false;
var wizParticlesAnimId = null;

function startWizParticles() {
    if (wizParticlesRunning) return;
    wizParticlesRunning = true;
    var canvas = document.getElementById('wiz-particles');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var W, H;
    var particles = [];
    var PARTICLE_COUNT = 80;

    function resize() {
        W = canvas.width = window.innerWidth;
        H = canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    for (var i = 0; i < PARTICLE_COUNT; i++) {
        var p = {
            x: Math.random() * W,
            y: Math.random() * H,
            r: Math.random() * 2 + 0.6,
            vx: (Math.random() - 0.5) * 0.35,
            vy: (Math.random() - 0.5) * 0.35,
            alpha: Math.random() * 0.5 + 0.2,
            alphaDir: Math.random() > 0.5 ? 1 : -1,
            alphaSpeed: Math.random() * 0.005 + 0.002
        };
        if (i < 12) { p.r = Math.random() * 2 + 1.5; p.alpha = 0.5 + Math.random() * 0.4; }
        particles.push(p);
    }

    function draw() {
        if (!wizParticlesRunning) return;
        ctx.clearRect(0, 0, W, H);
        for (var i = 0; i < particles.length; i++) {
            for (var j = i + 1; j < particles.length; j++) {
                var dx = particles[i].x - particles[j].x;
                var dy = particles[i].y - particles[j].y;
                var dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 120) {
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.strokeStyle = 'rgba(0,122,255,' + (0.04 * (1 - dist / 120)) + ')';
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }
        }
        for (var k = 0; k < particles.length; k++) {
            var p = particles[k];
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(180,200,255,' + p.alpha + ')';
            ctx.fill();
            p.x += p.vx;
            p.y += p.vy;
            p.alpha += p.alphaDir * p.alphaSpeed;
            if (p.alpha > 0.75) { p.alphaDir = -1; }
            if (p.alpha < 0.1) { p.alphaDir = 1; }
            if (p.x < -10) p.x = W + 10;
            if (p.x > W + 10) p.x = -10;
            if (p.y < -10) p.y = H + 10;
            if (p.y > H + 10) p.y = -10;
        }
        wizParticlesAnimId = requestAnimationFrame(draw);
    }
    draw();
}

function stopWizParticles() {
    wizParticlesRunning = false;
    if (wizParticlesAnimId) {
        cancelAnimationFrame(wizParticlesAnimId);
        wizParticlesAnimId = null;
    }
}

var wizCurrentStep = 0;
var wizTotalSteps = 6;

function wizSyncContainerHeight() {
    var container = document.getElementById('wiz-card-container');
    var active = document.querySelector('.wiz-card.active');
    if (!container || !active) return;
    // 容器高度跟随当前激活卡片的内容高度（受 max-height 限制），文字多则高、少则矮
    container.style.height = active.offsetHeight + 'px';
}

function wizUpdateStepIndicators() {
    var dots = document.querySelectorAll('.wiz-step-dot');
    var lines = document.querySelectorAll('.wiz-step-line');
    dots.forEach(function(dot, i) {
        dot.classList.remove('active', 'done');
        if (i < wizCurrentStep) dot.classList.add('done');
        if (i === wizCurrentStep) dot.classList.add('active');
    });
    lines.forEach(function(line, i) {
        line.classList.toggle('done', i < wizCurrentStep);
    });
}

function wizSwitchCard(fromIdx, toIdx) {
    var cards = document.querySelectorAll('.wiz-card');
    if (fromIdx >= 0 && fromIdx < cards.length) {
        cards[fromIdx].classList.remove('active');
        cards[fromIdx].classList.add('leaving');
    }
    if (toIdx >= 0 && toIdx < cards.length) {
        cards[toIdx].classList.remove('leaving');
        void cards[toIdx].offsetWidth;
        cards[toIdx].classList.add('active');
    }
    wizUpdateStepIndicators();
    wizSyncContainerHeight();
}

function wizNextStep() {
    if (wizCurrentStep >= wizTotalSteps - 1) return;
    if (wizCurrentStep === 1) {
        var key = document.getElementById('wiz-apikey').value.trim();
        if (!key) {
            var statusEl = document.getElementById('wiz-key-status');
            statusEl.textContent = t('wizard_enter_key');
            statusEl.className = 'wiz-status err';
            document.getElementById('wiz-apikey').focus();
            wizShakeCard(1);
            return;
        }
        if (!wizKeyVerified) {
            wizVerifyAndProceed();
            return;
        }
    }
    var prev = wizCurrentStep;
    wizCurrentStep++;
    if (wizCurrentStep === 5) wizUpdateSummary();
    wizSwitchCard(prev, wizCurrentStep);
}

function wizPrevStep() {
    if (wizCurrentStep <= 0) return;
    var prev = wizCurrentStep;
    wizCurrentStep--;
    wizSwitchCard(prev, wizCurrentStep);
}

var wizKeyVerified = false;
var wizKeyVerifying = false;

async function wizVerifyKey() {
    if (wizKeyVerifying) return;
    var key = document.getElementById('wiz-apikey').value.trim();
    var baseUrl = document.getElementById('wiz-api-base').value.trim() || 'https://api.deepseek.com';
    var statusEl = document.getElementById('wiz-key-status');

    if (!key) {
        statusEl.textContent = t('wizard_enter_key');
        statusEl.className = 'wiz-status err';
        return;
    }

    wizKeyVerifying = true;
    wizKeyVerified = false;
    statusEl.textContent = t('wizard_verifying');
    statusEl.className = 'wiz-status info';
    document.getElementById('wiz-verify-key').textContent = t('wizard_verifying');
    document.getElementById('wiz-verify-key').disabled = true;

    try {
        var result = await window.pywebview.api.validate_api_key(key, baseUrl);
        if (result === 'ok') {
            wizKeyVerified = true;
            statusEl.textContent = t('wizard_key_valid');
            statusEl.className = 'wiz-status ok';
            document.getElementById('wiz-verify-key').textContent = t('wizard_verified');
        } else {
            var errMsg = result.replace('error:', '');
            statusEl.textContent = t('wizard_key_invalid');
            statusEl.className = 'wiz-status err';
            document.getElementById('wiz-verify-key').textContent = t('wizard_retry');
        }
    } catch(e) {
        statusEl.textContent = t('wizard_key_invalid');
        statusEl.className = 'wiz-status err';
        document.getElementById('wiz-verify-key').textContent = t('wizard_retry');
    }

    wizKeyVerifying = false;
    document.getElementById('wiz-verify-key').disabled = false;
}

async function wizVerifyAndProceed() {
    await wizVerifyKey();
    if (wizKeyVerified) {
        var prev = wizCurrentStep;
        wizCurrentStep++;
        if (wizCurrentStep === 5) wizUpdateSummary();
        wizSwitchCard(prev, wizCurrentStep);
    }
}

function wizShakeCard(cardIdx) {
    var card = document.querySelectorAll('.wiz-card')[cardIdx];
    if (!card) return;
    card.style.transition = 'transform 0.1s ease';
    card.style.transform = 'translateX(-6px)';
    setTimeout(function() { card.style.transform = 'translateX(6px)'; }, 100);
    setTimeout(function() { card.style.transform = 'translateX(-4px)'; }, 200);
    setTimeout(function() { card.style.transform = 'translateX(4px)'; }, 300);
    setTimeout(function() {
        card.style.transform = 'translateY(0) scale(1)';
        card.style.transition = 'all 0.55s cubic-bezier(0.4, 0, 0.2, 1)';
    }, 400);
}

function wizUpdateSummary() {
    var key = document.getElementById('wiz-apikey').value.trim();
    document.getElementById('wiz-sum-key').textContent = key ? key.substring(0, 8) + '...' + key.substring(key.length - 4) : ('(' + t('wizard_enter_key') + ')');
    document.getElementById('wiz-sum-model').textContent = document.getElementById('wiz-model').value;
    document.getElementById('wiz-sum-base').textContent = document.getElementById('wiz-api-base').value.trim() || 'https://api.deepseek.com';
    document.getElementById('wiz-sum-root').textContent = document.getElementById('wiz-project-root').value.trim() || '(default)';
    document.getElementById('wiz-sum-web-search').textContent = document.getElementById('wiz-web-search').checked ? t('wizard_on') : t('wizard_off');
    document.getElementById('wiz-sum-confirm-write').textContent = document.getElementById('wiz-confirm-write').checked ? t('wizard_on') : t('wizard_off');
    document.getElementById('wiz-sum-queue-size').textContent = document.getElementById('wiz-queue-size').value || '200';
    document.getElementById('wiz-sum-max-steps').textContent = document.getElementById('wiz-max-steps').value || '128';
    var timeout = parseInt(document.getElementById('wiz-task-timeout').value) || 0;
    document.getElementById('wiz-sum-task-timeout').textContent = timeout > 0 ? timeout + 's' : t('wizard_disabled');
}

async function wizFinish(skipped) {
    if (!skipped) {
        var apiKey = document.getElementById('wiz-apikey').value.trim();
        var apiBase = document.getElementById('wiz-api-base').value.trim() || 'https://api.deepseek.com';
        var model = document.getElementById('wiz-model').value;
        var projectRoot = document.getElementById('wiz-project-root').value.trim();
        var webSearch = document.getElementById('wiz-web-search').checked;
        var confirmWrite = document.getElementById('wiz-confirm-write').checked;
        var queueSize = parseInt(document.getElementById('wiz-queue-size').value) || 200;
        var maxSteps = parseInt(document.getElementById('wiz-max-steps').value) || 128;
        var taskTimeout = parseInt(document.getElementById('wiz-task-timeout').value) || 0;

        var baseConfig = {
            api_base: apiBase,
            model: model,
            project_root: projectRoot,
            language: config.language || 'zh_CN',
            encryption_method: config.encryption_method || 'win32crypt',
            queue_max_size: queueSize,
            max_steps: maxSteps,
            enable_web_search: webSearch,
            confirm_write_delete: confirmWrite,
            temperature: config.temperature || 1.0,
            think_level: config.think_level || '高',
            max_tokens: config.max_tokens || 32767,
            task_timeout: taskTimeout,
            memory: false,
            memory_mode: 'full',
            max_rounds: 10
        };
        try { await window.pywebview.api.save_config(baseConfig); } catch(e) {}
        if (apiKey) {
            try { await window.pywebview.api.set_api_key(apiKey); } catch(e) {}
        }
    }

    var overlay = document.getElementById('wizard-overlay');
    overlay.classList.add('fade-out');
    stopWizParticles();

    setTimeout(function() {
        overlay.classList.remove('show', 'fade-out');
        var canvas = document.getElementById('wiz-particles');
        if (canvas) { var c = canvas.getContext('2d'); c.clearRect(0, 0, canvas.width, canvas.height); }
        userInput.disabled = false;
        userInput.focus();
        if (!skipped) {
            showToast(t('welcome_setup'));
        } else {
            showToast(t('welcome_skip'));
        }
    }, 650);
}

function showWizardOverlay() {
    wizCurrentStep = 0;
    wizKeyVerified = false;
    wizKeyVerifying = false;
    var cards = document.querySelectorAll('.wiz-card');
    cards.forEach(function(card, i) {
        card.classList.remove('active', 'leaving');
        if (i === 0) card.classList.add('active');
    });
    wizUpdateStepIndicators();
    wizSyncContainerHeight();
    document.getElementById('wiz-apikey').value = '';
    document.getElementById('wiz-api-base').value = config.api_base || 'https://api.deepseek.com';
    document.getElementById('wiz-project-root').value = config.project_root || '';
    document.getElementById('wiz-web-search').checked = config.enable_web_search || false;
    document.getElementById('wiz-confirm-write').checked = config.confirm_write_delete !== false;
    document.getElementById('wiz-queue-size').value = config.queue_max_size || 200;
    document.getElementById('wiz-max-steps').value = config.max_steps || 128;
    document.getElementById('wiz-task-timeout').value = config.task_timeout || 0;
    document.getElementById('wiz-key-status').textContent = '';
    document.getElementById('wiz-key-status').className = 'wiz-status info';
    document.getElementById('wiz-verify-key').textContent = t('wizard_verify');
    document.getElementById('wiz-verify-key').disabled = false;
    // Init language selector
    var wizLang = document.getElementById('wiz-language');
    if (wizLang) wizLang.value = currentLang;
    setLanguage(currentLang);
    var overlay = document.getElementById('wizard-overlay');
    overlay.classList.add('show');
    overlay.classList.remove('fade-out');
    startWizParticles();
    document.getElementById('wiz-apikey').focus();
}

function bindWizardEvents() {
    document.getElementById('wiz-skip-btn').addEventListener('click', function() {
        document.getElementById('wiz-skip-dialog').classList.add('show');
    });
    document.getElementById('wiz-skip-cancel').addEventListener('click', function() {
        document.getElementById('wiz-skip-dialog').classList.remove('show');
    });
    document.getElementById('wiz-skip-confirm').addEventListener('click', function() {
        document.getElementById('wiz-skip-dialog').classList.remove('show');
        wizFinish(true);
    });
    document.getElementById('wiz-skip-dialog').addEventListener('click', function(e) {
        if (e.target === document.getElementById('wiz-skip-dialog')) {
            document.getElementById('wiz-skip-dialog').classList.remove('show');
        }
    });

    // Language selector in wizard — apply language & save to config
    document.getElementById('wiz-language').addEventListener('change', function() {
        var newLang = this.value;
        setLanguage(newLang);
        // Save language to config immediately so it persists
        config.language = newLang;
        try { window.pywebview.api.save_config({language: newLang}); } catch(e) {}
        // Update dynamic wizard elements
        if (document.getElementById('wiz-verify-key')) {
            if (wizKeyVerified) {
                document.getElementById('wiz-verify-key').textContent = t('wizard_verified');
            } else {
                document.getElementById('wiz-verify-key').textContent = t('wizard_verify');
            }
        }
        var statusEl = document.getElementById('wiz-key-status');
        if (statusEl) {
            // Refresh key status message in new language
            if (wizKeyVerified) {
                statusEl.textContent = t('wizard_key_valid');
                statusEl.className = 'wiz-status ok';
            }
        }
        wizUpdateSummary();
        wizSyncContainerHeight();
    });

    document.getElementById('wiz-step0-next').addEventListener('click', wizNextStep);

    document.getElementById('wiz-step1-back').addEventListener('click', wizPrevStep);
    document.getElementById('wiz-step1-next').addEventListener('click', wizNextStep);
    document.getElementById('wiz-getkey').addEventListener('click', function() {
        openExternal('https://platform.deepseek.com/');
    });
    document.getElementById('wiz-verify-key').addEventListener('click', wizVerifyKey);
    document.getElementById('wiz-apikey').addEventListener('input', function() {
        if (wizKeyVerified) {
            wizKeyVerified = false;
            document.getElementById('wiz-key-status').textContent = t('wizard_key_changed');
            document.getElementById('wiz-key-status').className = 'wiz-status info';
            document.getElementById('wiz-verify-key').textContent = t('wizard_verify');
        }
    });
    document.getElementById('wiz-api-base').addEventListener('input', function() {
        if (wizKeyVerified) {
            wizKeyVerified = false;
            document.getElementById('wiz-key-status').textContent = t('wizard_url_changed');
            document.getElementById('wiz-key-status').className = 'wiz-status info';
            document.getElementById('wiz-verify-key').textContent = t('wizard_verify');
        }
    });
    document.getElementById('wiz-apikey').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); wizNextStep(); }
    });

    document.getElementById('wiz-step2-back').addEventListener('click', wizPrevStep);
    document.getElementById('wiz-step2-next').addEventListener('click', wizNextStep);
    document.getElementById('wiz-browse').addEventListener('click', function() {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_directory) {
            window.pywebview.api.pick_directory().then(function(path) {
                if (path) document.getElementById('wiz-project-root').value = path;
            });
        } else {
            var path = prompt('Enter project directory path:');
            if (path) document.getElementById('wiz-project-root').value = path;
        }
    });

    document.getElementById('wiz-step3-back').addEventListener('click', wizPrevStep);
    document.getElementById('wiz-step3-next').addEventListener('click', wizNextStep);
    document.getElementById('wiz-step4-back').addEventListener('click', wizPrevStep);
    document.getElementById('wiz-step4-next').addEventListener('click', wizNextStep);
    document.getElementById('wiz-step5-back').addEventListener('click', wizPrevStep);
    document.getElementById('wiz-finish-btn').addEventListener('click', function() { wizFinish(false); });

    window.addEventListener('resize', function() {
        var overlay = document.getElementById('wizard-overlay');
        if (overlay.classList.contains('show')) wizSyncContainerHeight();
    });

    document.addEventListener('keydown', function(e) {
        var overlay = document.getElementById('wizard-overlay');
        if (!overlay.classList.contains('show')) return;
        var skipDlg = document.getElementById('wiz-skip-dialog');
        if (skipDlg.classList.contains('show')) {
            if (e.key === 'Escape') skipDlg.classList.remove('show');
            return;
        }
        if (e.key === 'ArrowRight' || (e.key === 'Enter' && e.ctrlKey)) {
            e.preventDefault(); wizNextStep();
        }
        if (e.key === 'ArrowLeft') {
            e.preventDefault(); wizPrevStep();
        }
        if (e.key === 'Escape' && wizCurrentStep > 0) {
            e.preventDefault(); wizPrevStep();
        }
    });
}