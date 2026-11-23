// 仅显式安装此可选覆盖层后启用；空闲时加载匿名流量采集，不接触聊天内容或登录状态。
(() => {
  if (location.hostname !== 'chat.pingxingxian.space' || globalThis.__plTrafficLoaderV1) return;
  globalThis.__plTrafficLoaderV1 = true;

  const load = () => {
    if (document.getElementById('pl-traffic-collector-v1')) return;
    const script = document.createElement('script');
    script.id = 'pl-traffic-collector-v1';
    script.src = 'https://pingxingxian.space/traffic-collector.v1.js';
    script.async = true;
    script.defer = true;
    script.referrerPolicy = 'no-referrer';
    script.dataset.site = 'chat';
    script.dataset.endpoint = 'https://pingxingxian.space/api/v1/site/traffic';
    // 统计脚本不可用时浏览器停止此次加载，不重试，也不参与业务请求或页面启动。
    document.head.appendChild(script);
  };

  if (typeof globalThis.requestIdleCallback === 'function') {
    globalThis.requestIdleCallback(load, { timeout: 2000 });
  } else {
    globalThis.setTimeout(load, 1000);
  }
})();
