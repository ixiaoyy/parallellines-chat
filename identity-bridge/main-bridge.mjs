const CLIENT = 'parallellines-chat';
const opaque = value => typeof value === 'string' && /^[A-Za-z0-9_-]{32,128}$/.test(value);
export class MainAuthorityError extends Error {
  constructor(code = 'main_authority_unavailable') { super(code); this.code = code; }
}

/** Only the trusted Main API can supply an identity, binding, or absolute expiry. */
export function validateIdentity(value, { bindingId, subject, requireName = false, now = Date.now() } = {}) {
  if (!value || typeof value !== 'object' || value.active !== true ||
      typeof value.sub !== 'string' || !value.sub || value.sub.length > 128 ||
      !opaque(value.binding_id) || !Number.isSafeInteger(value.auth_expires_at) ||
      value.auth_expires_at <= Math.floor(now / 1000) ||
      (bindingId !== undefined && value.binding_id !== bindingId) ||
      (subject !== undefined && value.sub !== subject) ||
      (requireName && (typeof value.name !== 'string' || value.name.length > 256))) {
    throw new MainAuthorityError('main_binding_inactive');
  }
  // 兼容旧主站不含角色的响应；字段一旦存在，必须通过主站角色白名单。
  if (Object.hasOwn(value, 'role') && !['user', 'moderator', 'admin'].includes(value.role)) {
    throw new MainAuthorityError('main_role_invalid');
  }
  if (Object.hasOwn(value, 'unified_browser') && typeof value.unified_browser !== 'boolean') {
    throw new MainAuthorityError('main_unified_context_invalid');
  }
  return {
    sub: value.sub, binding_id: value.binding_id, auth_expires_at: value.auth_expires_at,
    ...(typeof value.name === 'string' ? { name: value.name } : {}),
    ...(typeof value.username === 'string' ? { username: value.username } : {}),
    email_verified: value.email_verified === true,
    ...(value.email_verified === true && typeof value.email === 'string' ? { email: value.email } : {}),
    ...(Object.hasOwn(value, 'role') ? { role: value.role } : {}),
    ...(Object.hasOwn(value, 'unified_browser') ? { unified_browser: value.unified_browser } : {}),
  };
}

/** Backend-only bridge. No primary JWT, browser identity, or positive liveness cache is accepted. */
export function createMainBridge({ apiRoot, bridgeSecret, timeoutMs = 1000, fetchImpl = globalThis.fetch }) {
  const root = new URL(apiRoot);
  if (root.username || root.password || root.search || root.hash || /[\r\n]/.test(bridgeSecret || '') ||
      typeof bridgeSecret !== 'string' || bridgeSecret.length < 32) throw new MainAuthorityError('invalid_bridge_configuration');
  root.pathname = root.pathname.replace(/\/$/, '');
  async function post(suffix, payload) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(root.href.replace(/\/$/, '') + '/auth/chat-sso/' + suffix, {
        method: 'POST', redirect: 'error', cache: 'no-store', signal: controller.signal,
        headers: { Authorization: `Bearer ${bridgeSecret}`, 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ client_id: CLIENT, ...payload }),
      });
      if (!response.ok) { await response.body?.cancel(); throw new MainAuthorityError('main_binding_rejected'); }
      const reader = response.body?.getReader();
      if (!reader) throw new MainAuthorityError();
      const chunks = []; let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 16384) { await reader.cancel(); throw new MainAuthorityError(); }
          chunks.push(Buffer.from(value));
        }
      } finally { reader.releaseLock(); }
      const envelope = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!envelope || !Object.hasOwn(envelope, 'data')) throw new MainAuthorityError();
      return envelope.data;
    } catch (error) {
      throw error instanceof MainAuthorityError ? error : new MainAuthorityError();
    } finally { clearTimeout(timer); }
  }
  return Object.freeze({
    async exchange({ interactionId, approvalCode }) {
      return validateIdentity(await post('exchange', { interaction_id: interactionId, approval_code: approvalCode }), { requireName: true });
    },
    async sessionState(identity) {
      return validateIdentity(await post('session-state', { binding_id: identity.binding_id }), {
        bindingId: identity.binding_id, subject: identity.sub,
      });
    },
    /** 新 RP 复核只把明确 active:false 当退出；异常资料、网络和权限故障均不可用。 */
    async bindingState(identity) {
      const data = await post('session-state', { binding_id: identity.binding_id });
      if (data && typeof data === 'object' && data.active === false) return { active: false };
      try {
        return { active: true, ...validateIdentity(data, { bindingId: identity.binding_id, subject: identity.sub }) };
      } catch { throw new MainAuthorityError(); }
    },
    /** 仅为已核主站绑定申请一次短期退出票据，不将主站服务密钥交给 RP。 */
    async logoutTicket(identity, returnUri) {
      const data = await post('unified-logout-ticket', { binding_id: identity.binding_id, return_uri: returnUri });
      const expected = new URL(root.origin + '/auth/unified/logout');
      let target;
      try { target = new URL(data?.logout_url); } catch { throw new MainAuthorityError(); }
      if (target.origin !== expected.origin || target.pathname !== expected.pathname || target.hash ||
          target.username || target.password || data?.expires_in !== 60 ||
          target.searchParams.getAll('ticket').length !== 1 || [...target.searchParams.keys()].some(key => key !== 'ticket') || !opaque(target.searchParams.get('ticket'))) throw new MainAuthorityError();
      return { logout_url: target.href, expires_in: 60 };
    },
  });
}
