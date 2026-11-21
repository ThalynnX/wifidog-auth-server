/* WiFidog Unified Console helpers · v2.0.1 */
(function () {
  'use strict';

  const esc = value => (value === undefined || value === null ? '' : String(value))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const money = cents => '¥' + ((Number(cents) || 0) / 100).toFixed(2);

  function bytes(value) {
    const n = Number(value) || 0;
    if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
    if (n >= 1048576) return (n / 1048576).toFixed(2) + ' MB';
    if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
    return n + ' B';
  }

  function toast(message, type = 'ok', duration = 2800) {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      stack.setAttribute('aria-live', 'polite');
      document.body.appendChild(stack);
    }
    const item = document.createElement('div');
    item.className = 'toast' + (type === 'error' ? ' error' : type === 'warn' ? ' warn' : '');
    item.textContent = message || '操作完成';
    stack.appendChild(item);
    const timer = setTimeout(() => item.remove(), duration);
    item.onclick = () => { clearTimeout(timer); item.remove(); };
    return item;
  }

  function safeReturnUrl(raw, gatewayAddress) {
    if (!raw) return '';
    let target;
    try {
      target = new URL(String(raw).trim(), location.origin);
    } catch (_) {
      return '';
    }
    if (!['http:', 'https:'].includes(target.protocol)) return '';
    const host = String(target.hostname || '').toLowerCase().replace(/^\[|\]$/g, '');
    if (!host) return '';

    let gateway = '';
    if (gatewayAddress) {
      try {
        const value = String(gatewayAddress).trim();
        const gatewayUrl = /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : 'http://' + value;
        gateway = new URL(gatewayUrl).hostname.toLowerCase().replace(/^\[|\]$/g, '');
      } catch (_) {}
    }
    if (gateway && host === gateway) return '';
    if (host === 'localhost' || host === '0.0.0.0' || host === '::1' || host.endsWith('.local')) return '';

    // 网关管理页、内网地址和系统联网检测页不应作为“原始页面”回跳。
    if (/^(0|10|127)\./.test(host) || /^192\.168\./.test(host) ||
        /^172\.(1[6-9]|2\d|3[01])\./.test(host) || /^169\.254\./.test(host)) return '';
    if (/^fe[89ab]/i.test(host) || /^f[cd]/i.test(host)) return '';
    if (['connectivitycheck.gstatic.com', 'connectivitycheck.vivo.com.cn',
         'connectivitycheck.android.com', 'www.msftconnecttest.com',
         'captive.apple.com'].includes(host)) return '';

    return target.href;
  }

  function busy(button, loading, loadingText) {
    if (!button) return;
    if (loading) {
      if (button.dataset.busy === '1') return;
      button.dataset.busy = '1';
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = loadingText || '处理中…';
    } else {
      if (button.dataset.originalHtml !== undefined) button.innerHTML = button.dataset.originalHtml;
      button.disabled = false;
      button.dataset.busy = '0';
      button.removeAttribute('aria-busy');
    }
  }

  async function api(url, options = {}) {
    const opts = Object.assign({ credentials: 'same-origin' }, options);
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || 15000);
    opts.signal = controller.signal;
    let loadingLine;
    try {
      if (!options.silent) {
        loadingLine = document.createElement('div');
        loadingLine.className = 'loading-line';
        document.body.appendChild(loadingLine);
      }
      const response = await fetch(url, opts);
      if (response.status === 401 && url.startsWith('/admin/api') && !url.endsWith('/login')) {
        location.href = '/admin/login?expired=1';
        return { ok: false, msg: '登录已过期，请重新登录', unauthorized: true };
      }
      const raw = await response.text();
      let data;
      try { data = raw ? JSON.parse(raw) : {}; }
      catch (_) { return { ok: false, msg: '服务返回格式异常（HTTP ' + response.status + '）', httpStatus: response.status }; }
      if (!response.ok && data.ok !== false) {
        const detail = typeof data.detail === 'string' ? data.detail : '请求失败（HTTP ' + response.status + '）';
        return { ok: false, msg: detail, httpStatus: response.status };
      }
      if (data && data.httpStatus === undefined) data.httpStatus = response.status;
      return data;
    } catch (error) {
      const msg = error && error.name === 'AbortError' ? '请求超时，请检查网络后重试' : '网络连接失败，请稍后重试';
      return { ok: false, msg, networkError: true };
    } finally {
      clearTimeout(timeout);
      if (loadingLine) loadingLine.remove();
    }
  }

  const post = (url, body, options = {}) => api(url, Object.assign({}, options, {
    method: 'POST', body: JSON.stringify(body || {})
  }));

  const del = (url, options = {}) => api(url, Object.assign({}, options, { method: 'DELETE' }));

  function closeModal(layer) {
    const target = typeof layer === 'string' ? document.querySelector(layer) : layer;
    if (target) target.remove();
  }

  function confirmDialog({ title = '确认操作', text = '确认继续吗？', okText = '确定', cancelText = '取消', danger = false } = {}) {
    return new Promise(resolve => {
      const layer = document.createElement('div');
      layer.className = 'modal-layer';
      layer.innerHTML = '<div class="modal-box small" role="dialog" aria-modal="true">' +
        '<div class="modal-head"><div class="modal-title">' + esc(title) + '</div>' +
        '<button type="button" class="modal-close" data-close aria-label="关闭">×</button></div>' +
        '<div class="modal-body" style="color:var(--muted);line-height:1.8">' + esc(text) + '</div>' +
        '<div class="modal-foot"><button type="button" class="btn ghost" data-close>' + esc(cancelText) + '</button>' +
        '<button type="button" class="btn ' + (danger ? 'danger' : '') + '" data-ok>' + esc(okText) + '</button></div></div>';
      document.body.appendChild(layer);
      const done = value => { closeModal(layer); resolve(value); };
      layer.querySelectorAll('[data-close]').forEach(btn => btn.onclick = () => done(false));
      layer.querySelector('[data-ok]').onclick = () => done(true);
      layer.onclick = event => { if (event.target === layer) done(false); };
      layer.querySelector('[data-ok]').focus();
    });
  }

  function table(columns, rows, emptyText) {
    if (!rows || !rows.length) {
      return '<div class="empty"><strong>暂无数据</strong>' + esc(emptyText || '当前条件下没有记录') + '</div>';
    }
    let html = '<div class="table-wrap"><table><thead><tr>';
    columns.forEach(col => { html += '<th>' + esc(col) + '</th>'; });
    html += '</tr></thead><tbody>';
    rows.forEach(row => {
      html += '<tr>';
      row.forEach(cell => { html += '<td>' + (cell === undefined || cell === null ? '-' : cell) + '</td>'; });
      html += '</tr>';
    });
    return html + '</tbody></table></div>';
  }

  function pager(id, page, size, total, handlerName) {
    const pages = Math.max(1, Math.ceil((Number(total) || 0) / size));
    const current = Math.min(page, pages);
    return '<div class="pager"><span>共 ' + (Number(total) || 0) + ' 条 · 第 ' + current + '/' + pages + ' 页</span>' +
      '<button type="button" class="btn small ghost" ' + (current <= 1 ? 'disabled' : '') + ' onclick="' + handlerName + '(' + (current - 1) + ')">上一页</button>' +
      '<button type="button" class="btn small ghost" ' + (current >= pages ? 'disabled' : '') + ' onclick="' + handlerName + '(' + (current + 1) + ')">下一页</button></div>';
  }

  window.UI = { esc, money, bytes, safeReturnUrl, toast, busy, api, post, del, table, pager, confirm: confirmDialog, closeModal };

  window.addEventListener('error', event => {
    if (document.querySelector('.error-banner')) return;
    const banner = document.createElement('div');
    banner.className = 'error-banner';
    banner.textContent = '页面脚本错误：' + (event.message || '未知错误') + '，请刷新重试';
    document.body.appendChild(banner);
  });

  window.addEventListener('unhandledrejection', event => {
    console.error('Unhandled promise rejection:', event.reason);
    toast('操作未完成：' + ((event.reason && event.reason.message) || '未知错误'), 'error', 5000);
  });
})();
