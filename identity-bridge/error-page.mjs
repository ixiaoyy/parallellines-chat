import { createHash } from 'node:crypto';

const MAIN_URL = 'https://pingxingxian.space/';
const RETRY_URLS = new Set([
  MAIN_URL,
  'https://chat.pingxingxian.space/auth',
  'https://chat.pingxingxian.space/oauth/oidc/login',
  'https://api.pingxingxian.space/login',
]);

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
export function renderLoginFailure(retryUrl) {
  const safeRetry = typeof retryUrl === 'string' && RETRY_URLS.has(retryUrl) ? retryUrl : MAIN_URL;
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta name="robots" content="noindex, nofollow">
  <title>聊天登录未完成 · 平行线</title>
  <style>${STYLE}</style>
</head>
<body>
  <main class="page">
    <div class="shell">
      <p class="site-name">平行线</p>
      <section class="card" aria-labelledby="login-title" aria-describedby="login-description">
        <div class="status">
          <span class="status-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5M12 16.5h.01"/></svg>
          </span>
          <span>登录提示</span>
        </div>
        <h1 id="login-title">聊天登录未完成</h1>
        <p class="description" id="login-description">本次登录未能完成。你可以重新登录，或返回主站。</p>
        <a class="retry" href="${safeRetry}">重新登录<span aria-hidden="true">→</span></a>
        <a class="home" href="${MAIN_URL}">返回平行线主站</a>
      </section>
    </div>
  </main>
</body>
</html>`;
  return { html, csp: CSP };
}
