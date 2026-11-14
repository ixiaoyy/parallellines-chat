import { createHash, timingSafeEqual } from 'node:crypto';

const opaque = value => typeof value === 'string' && /^[A-Za-z0-9_-]{32,128}$/.test(value);
const equalSecret = (left, right) => timingSafeEqual(createHash('sha256').update(left).digest(), createHash('sha256').update(right).digest());

/** 后台专用 RP 会话接口；独立 Basic 凭据和 grant 所有权共同限定当前浏览器 Main 绑定。 */
export function createRelyingPartySessions({ config, store, bridge }) {
  const endpoints = new Set(['/auth/oidc/main-session-state', '/auth/oidc/unified-logout-ticket']);
  const clients = new Map(config.clients.map(client => [client.clientId, client]));
  const send = (res, status, value) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff' }); res.end(JSON.stringify(value)); };
  return async (req, res, url) => {
    if (!endpoints.has(url.pathname)) return false;
    if (!config.unifiedAuthEnabled) { send(res, 404, { error: 'not_found' }); return true; }
    if (req.method !== 'POST' || req.headers.origin) { send(res, 403, { error: 'rp_server_only' }); return true; }
    let client;
    try {
      const header = req.headers.authorization || '';
      if (!/^Basic [A-Za-z0-9+/=]{1,4096}$/.test(header)) throw new Error();
      const basic = Buffer.from(header.slice(6), 'base64').toString('utf8');
      const colon = basic.indexOf(':');
      if (colon < 1) throw new Error();
      const id = basic.slice(0, colon);
      const secret = basic.slice(colon + 1);
      client = clients.get(id);
      if (!client || !equalSecret(secret, client.clientSecret)) throw new Error();
    } catch { send(res, 401, { error: 'invalid_client' }); return true; }
    let input;
    try {
      if ((req.headers['content-type'] || '').split(';')[0] !== 'application/json') throw new Error();
      // 请求体总限时三秒、最多 2 KiB；慢请求不占用后台会话复核入口。
      const chunks = []; let size = 0;
      let timer; let cleanup;
      const body = new Promise((resolve, reject) => {
        const data = chunk => { size += chunk.length; if (size > 2048) reject(new Error('body_too_large')); else chunks.push(chunk); };
        const end = () => resolve(Buffer.concat(chunks).toString('utf8'));
        const error = () => reject(new Error('body_unavailable'));
        req.on('data', data); req.once('end', end); req.once('error', error); req.once('aborted', error);
        timer = setTimeout(() => reject(new Error('body_timeout')), 3000);
        cleanup = () => { clearTimeout(timer); req.off('data', data); req.off('end', end); req.off('error', error); req.off('aborted', error); };
      });
      try { input = JSON.parse(await body); } finally { cleanup?.(); }
      if (!input || typeof input !== 'object' || Array.isArray(input) ||
          Object.keys(input).some(key => !['binding_id', 'subject'].includes(key)) || !opaque(input.binding_id) ||
          typeof input.subject !== 'string' || !input.subject || input.subject.length > 128) throw new Error();
    } catch { send(res, 400, { error: 'invalid_request' }); return true; }
    const identity = store.getClientBinding(input.binding_id, client.clientId);
    if (!identity || identity.sub !== input.subject) { send(res, 200, { active: false }); return true; }
    try {
      const live = await bridge.bindingState(identity);
      // 旧 bearer 审批绑定保持原合同，但绝不能升级为可信中央浏览器会话。
      if (!live.active || live.unified_browser !== true) { send(res, 200, { active: false }); return true; }
      if (url.pathname.endsWith('unified-logout-ticket')) {
        // 返回地址来自已登记 RP，不接受浏览器或调用方任意指定跳转。
        let ticket;
        try {
          ticket = await bridge.logoutTicket(identity, new URL(client.callbackUri).origin + '/');
        } catch (error) {
          // 复核与出票之间可并发退出；只再读一次权威状态，不重试出票，也不把 4xx/故障猜成匿名。
          const current = await bridge.bindingState(identity);
          if (!current.active || current.unified_browser !== true) { send(res, 200, { active: false }); return true; }
          throw error;
        }
        send(res, 200, { active: true, ...ticket });
      } else {
        send(res, 200, { active: true, sub: identity.sub, binding_id: identity.binding_id,
          auth_expires_at: Math.min(identity.auth_expires_at, live.auth_expires_at), unified_browser: live.unified_browser === true });
      }
    } catch { send(res, 503, { error: 'main_authority_unavailable' }); }
    return true;
  };
}
