import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { renderLoginFailure } from '../error-page.mjs';

const MAIN_URL = 'https://pingxingxian.space/';
const retryHref = html => html.match(/<a class="retry" href="([^"]+)"/)?.[1];

test('recovery allows only the five explicit product entry points', () => {
  for (const url of [
    MAIN_URL,
    'https://pingxingxian.space/chat/',
    'https://chat.pingxingxian.space/auth',
    'https://chat.pingxingxian.space/oauth/oidc/login',
    'https://api.pingxingxian.space/login',
  ]) {
    const { html } = renderLoginFailure(url);
    assert.equal(retryHref(html), url);
    assert.ok(html.includes(`<a class="home" href="${MAIN_URL}">`));
  }
});

test('untrusted URLs and non-string inputs fall back without reflecting any input', () => {
  const fallback = renderLoginFailure(MAIN_URL);
  const rejected = [
    undefined, null, 0, false, [], {}, new URL(MAIN_URL),
    { toString() { throw new Error('Untrusted coercion must not run'); } },
    '', '/chat/', '//chat.pingxingxian.space/auth',
    'http://chat.pingxingxian.space/auth',
    'https://chat.pingxingxian.space:443/auth',
    'https://CHAT.pingxingxian.space/auth',
    'https://chat.pingxingxian.space/auth/',
    'https://chat.pingxingxian.space/auth?redirect=https://evil.invalid',
    'https://chat.pingxingxian.space/auth#account=private@example.invalid',
    'https://chat.pingxingxian.space/auth?error=internal-secret',
    'https://chat.pingxingxian.space/%61uth',
    'https://chat.pingxingxian.space/a/../auth',
    'https://chat.pingxingxian.space/oauth/oidc/callback',
    'https://chat.pingxingxian.space/oauth/oidc/login/callback',
    'https://chat.pingxingxian.space.evil.invalid/auth',
    'https://chat.pingxingxian.space@evil.invalid/auth',
    'https://user:password@chat.pingxingxian.space/auth',
    'https://api.pingxingxian.space/admin',
    'https://pingxingxian.space/chat',
    'https://pingxingxian.space/other',
    ' https://chat.pingxingxian.space/auth',
    'https://chat.pingxingxian.space/auth\n',
    'javascript:alert(document.cookie)',
    'data:text/html,<script>alert(1)</script>',
    '"><script>alert(1)</script><a href="',
    'https://chat.pingxingxian.space/auth" onmouseover="alert(1)',
  ];
  for (const input of rejected) {
    assert.deepEqual(renderLoginFailure(input), fallback);
  }
});

test('CSP authorizes the exact fixed stylesheet and prohibits scripts and external resources', () => {
  const { html, csp } = renderLoginFailure('https://chat.pingxingxian.space/auth');
  const styles = [...html.matchAll(/<style>([\s\S]*?)<\/style>/g)];
  assert.equal(styles.length, 1);
  const hash = createHash('sha256').update(styles[0][1], 'utf8').digest('base64');
  assert.ok(csp.includes(`style-src 'sha256-${hash}'`));
  for (const directive of ["default-src 'none'", "script-src 'none'", "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"]) {
    assert.ok(csp.includes(directive));
  }
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval|https?:/);
  assert.doesNotMatch(html, /<script\b|<link\b|<iframe\b|<form\b|\sstyle=|\son[a-z]+=|@import|url\(/i);
  assert.equal(csp, renderLoginFailure('https://api.pingxingxian.space/login').csp);
});

test('copy is neutral and the document provides semantic, responsive recovery controls', () => {
  const { html } = renderLoginFailure(MAIN_URL);
  assert.match(html, /<html lang="zh-CN">/);
  assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1">/);
  assert.match(html, /<main class="page">/);
  assert.match(html, /aria-labelledby="login-title" aria-describedby="login-description"/);
  assert.equal((html.match(/<h1\b/g) || []).length, 1);
  assert.equal((html.match(/<a\b/g) || []).length, 2);
  assert.match(html, /本次登录未能完成。你可以重新登录，或返回主站。/);
  assert.doesNotMatch(html, /过期|已失效|invalid_grant|approval_code|interaction_id|client_secret/);
  assert.match(html, /background: #409EFF;/);
  assert.match(html, /color: #ffffff;/);
  assert.match(html, /a:focus-visible/);
  assert.match(html, /prefers-reduced-motion/);
});
