import { createHash } from 'node:crypto';

const MAIN_URL = 'https://pingxingxian.space/';
const RECOVERY_TARGETS = { main: MAIN_URL, chat: 'https://chat.pingxingxian.space/oauth/oidc/login',
  'chat-auth': 'https://chat.pingxingxian.space/auth', api: 'https://api.pingxingxian.space/login',
  tavern: 'https://tavern.pingxingxian.space/' };
const RETRY_URLS = new Set(Object.values(RECOVERY_TARGETS));
const COPY = {
  'zh-CN': { site: '平行线', title: '聊天登录未完成', status: '登录提示', description: '本次登录未能完成。你可以重新登录，或返回主站。', retry: '重新登录', home: '返回平行线主站', language: '语言' },
  'en-US': { site: 'ParallelLines', title: 'Sign-in did not complete', status: 'Sign-in notice', description: 'Sign-in could not be completed. You can sign in again or return to the main site.', retry: 'Sign in again', home: 'Back to ParallelLines', language: 'Language' },
};

/** 将固定展示目标转成重试 URL；未知值返回主站，不接收任意 redirect 或认证状态。 */
export function loginFailureTarget(target) { return typeof target === 'string' && Object.hasOwn(RECOVERY_TARGETS, target) ? RECOVERY_TARGETS[target] : MAIN_URL; }

/** 从显式中英代码、Cookie、Accept-Language 依次返回语言代码；无匹配保留中文默认。 */
export function loginFailureLanguage(headers = {}, explicit) {
  if (typeof explicit === 'string' && Object.hasOwn(COPY, explicit)) return explicit;
  const stored = String(headers.cookie || '').match(/(?:^|;\s*)pl_oidc_locale=(zh-CN|en-US)(?:;|$)/)?.[1];
  if (stored) return stored;
  const candidates = String(headers['accept-language'] || '').slice(0, 2048).split(',').map((part, index) => {
    const [code, ...parameters] = part.trim().split(';');
    const quality = parameters.find(value => value.trim().startsWith('q='));
    return { code, index, q: quality === undefined ? 1 : Number(quality.trim().slice(2)) };
  }).filter(item => Number.isFinite(item.q) && item.q > 0 && item.q <= 1).sort((a, b) => b.q - a.q || a.index - b.index);
  for (const { code } of candidates) {
    if (/^en(?:-|$)/i.test(code)) return 'en-US';
    if (/^zh(?:-|$)/i.test(code)) return 'zh-CN';
  }
  return 'zh-CN';
}

// Fixed bytes: the CSP hash applies to this exact stylesheet, including newlines.
const STYLE = `
:root {
  color-scheme: light;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  color: #334155;
  background: #f8fafc;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
}
* { box-sizing: border-box; }
body { margin: 0; min-width: 0; }
.page {
  min-height: 100vh;
  min-height: 100svh;
  display: grid;
  place-items: center;
  padding: 40px 24px;
}
.shell { width: 100%; max-width: 448px; }
.site-name {
  margin: 0 0 20px;
  color: #475569;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: .08em;
}
.language { display: flex; justify-content: flex-end; gap: 14px; margin-bottom: 16px; font-size: 14px; }
.language a { color: #475569; text-underline-offset: 4px; min-height: 44px; display: inline-flex; align-items: center; }
.language a[aria-current="true"] { font-weight: 700; color: #1e293b; }
.card {
  padding: 36px;
  border: 1px solid #e2e8f0;
  border-radius: 20px;
  background: #ffffff;
  box-shadow: 0 12px 36px -20px #33415530;
}
.status {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 22px;
  color: #475569;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}
.status-icon {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: #eff6ff;
  color: #2878c9;
}
.status-icon svg { width: 20px; height: 20px; }
h1 {
  margin: 0;
  color: #334155;
  font-size: clamp(24px, 5.5vw, 28px);
  font-weight: 700;
  line-height: 1.4;
  letter-spacing: -.025em;
  overflow-wrap: anywhere;
}
.description {
  margin: 14px 0 28px;
  color: #475569;
  font-size: 15px;
  line-height: 1.8;
}
a { -webkit-tap-highlight-color: transparent; }
.retry {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 48px;
  padding: 12px 18px;
  border: 1px solid #409EFF;
  border-radius: 10px;
  background: #409EFF;
  color: #ffffff;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.5;
  text-decoration: none;
  transition: background-color .15s, border-color .15s;
}
.retry:hover { background: #66b1ff; border-color: #66b1ff; }
.retry:active { background: #3a8ee6; border-color: #3a8ee6; }
.home {
  display: flex;
  align-items: center;
  justify-content: center;
  width: fit-content;
  max-width: 100%;
  min-height: 44px;
  margin: 12px auto 0;
  padding: 8px 12px;
  color: #475569;
  font-size: 14px;
  line-height: 1.5;
  text-decoration: underline;
  text-underline-offset: 4px;
  text-decoration-color: #cbd5e1;
}
.home:hover { color: #1e293b; text-decoration-color: currentColor; }
a:focus-visible { outline: 3px solid #1d4ed8; outline-offset: 4px; }
@media (max-width: 380px) {
  .page { padding: 24px 16px; }
  .card { padding: 28px 22px; border-radius: 16px; }
  .site-name { margin-bottom: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .retry { transition: none; }
}
`;

const STYLE_HASH = createHash('sha256').update(STYLE, 'utf8').digest('base64');
const CSP = `default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; script-src 'none'; style-src 'sha256-${STYLE_HASH}'; object-src 'none'`;

/** Render a self-contained recovery page; untrusted arguments are never reflected. */
export function renderLoginFailure(retryUrl, language = 'zh-CN') {
  const safeRetry = typeof retryUrl === 'string' && RETRY_URLS.has(retryUrl) ? retryUrl : MAIN_URL;
  const locale = typeof language === 'string' && Object.hasOwn(COPY, language) ? language : 'zh-CN';
  const copy = COPY[locale];
  const target = Object.keys(RECOVERY_TARGETS).find(key => RECOVERY_TARGETS[key] === safeRetry);
  // 切换仅打开只读错误展示页，不重交审批码或重复原 POST。
  const languageHref = code => `/auth/oidc/login-failure?target=${target}&amp;lang=${code}`;
  const html = `<!doctype html>
<html lang="${locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta name="robots" content="noindex, nofollow">
  <title>${copy.title} · ${copy.site}</title>
  <style>${STYLE}</style>
</head>
<body>
  <main class="page">
    <div class="shell">
      <nav class="language" aria-label="${copy.language}"><a href="${languageHref('zh-CN')}" lang="zh-CN" aria-current="${locale === 'zh-CN'}">简体中文</a><a href="${languageHref('en-US')}" lang="en" aria-current="${locale === 'en-US'}">English</a></nav>
      <p class="site-name">${copy.site}</p>
      <section class="card" aria-labelledby="login-title" aria-describedby="login-description">
        <div class="status">
          <span class="status-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5M12 16.5h.01"/></svg>
          </span>
          <span>${copy.status}</span>
        </div>
        <h1 id="login-title">${copy.title}</h1>
        <p class="description" id="login-description">${copy.description}</p>
        <a class="retry" href="${safeRetry}">${copy.retry}<span aria-hidden="true">→</span></a>
        <a class="home" href="${MAIN_URL}">${copy.home}</a>
      </section>
    </div>
  </main>
</body>
</html>`;
  return { html, csp: CSP };
}
