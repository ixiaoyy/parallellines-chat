import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('./loader.js', import.meta.url), 'utf8');

function harness({ hostname = 'chat.pingxingxian.space', idle = true } = {}) {
  const scripts = [];
  const jobs = [];
  const context = vm.createContext({
    location: { hostname },
    document: {
      getElementById: id => scripts.find(script => script.id === id),
      createElement: name => { assert.equal(name, 'script'); return { dataset: {} }; },
      head: { appendChild: script => scripts.push(script) },
    },
    setTimeout: (callback, delay) => jobs.push({ callback, delay }),
    ...(idle ? { requestIdleCallback: (callback, options) => jobs.push({ callback, options }) } : {}),
  });
  return { scripts, jobs, run: () => vm.runInContext(source, context) };
}

test('waits for idle before loading the fixed anonymous collector entry', () => {
  const browser = harness();
  browser.run();
  assert.equal(browser.scripts.length, 0);
  assert.equal(browser.jobs.length, 1);
  assert.equal(browser.jobs[0].options.timeout, 2000);
  browser.jobs[0].callback();
  const script = browser.scripts[0];
  assert.equal(script.src, 'https://pingxingxian.space/traffic-collector.v1.js');
  assert.equal(script.dataset.site, 'chat');
  assert.equal(script.dataset.endpoint, 'https://pingxingxian.space/api/v1/site/traffic');
  assert.equal(script.async, true);
  assert.equal(script.defer, true);
  assert.equal(script.referrerPolicy, 'no-referrer');
});

test('old browsers use one bounded delayed load and duplicate execution does not retry', () => {
  const browser = harness({ idle: false });
  browser.run();
  browser.run();
  assert.equal(browser.jobs.length, 1);
  assert.equal(browser.jobs[0].delay, 1000);
  browser.jobs[0].callback();
  browser.jobs[0].callback();
  assert.equal(browser.scripts.length, 1);
});

test('unapproved hosts do not load or schedule analytics', () => {
  for (const hostname of ['localhost', 'chat.pingxingxian.space.evil.test', 'api.pingxingxian.space']) {
    const browser = harness({ hostname });
    browser.run();
    assert.equal(browser.jobs.length, 0);
    assert.equal(browser.scripts.length, 0);
  }
});

test('loader does not read private paths, inputs, storage, cookies, or authorization', () => {
  assert.doesNotMatch(source, /location\.(pathname|search|hash)|document\.(cookie|title)|localStorage|sessionStorage|fetch\(|XMLHttpRequest|Authorization|history\./);
});
