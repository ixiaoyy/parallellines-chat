import http from 'node:http';
import { loadConfiguration } from './config.mjs';
import { createSqliteStore } from './sqlite-adapter.mjs';
import { createIdentityProvider } from './identity-provider.mjs';

process.umask(0o077);
let store;
try {
  const config = loadConfiguration();
  store = createSqliteStore({ directory: config.databaseDirectory, filename: config.databaseFilename,
    clientIds: config.clients.map(client => client.clientId) });
  const { provider, handler } = createIdentityProvider({ config, store });
  provider.on('server_error', () => console.error('oidc_provider_request_failed'));
  const server = http.createServer((req, res) => Promise.resolve(handler(req, res)).catch(() => {
    if (!res.headersSent) { res.writeHead(500, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end('{"error":"oidc_request_failed"}'); }
    else res.destroy();
  }));
  server.headersTimeout = 10000;
  server.requestTimeout = 15000;
  server.listen(config.port, config.host, () => console.log('oidc_provider_ready'));
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => {
    server.close(() => { store.close(); process.exit(0); });
    setTimeout(() => { server.closeAllConnections(); store.close(); process.exit(0); }, 5000).unref();
  });
} catch {
  store?.close();
  console.error('oidc_provider_startup_failed');
  process.exitCode = 1;
}
