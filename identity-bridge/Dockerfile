FROM node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53
WORKDIR /app
COPY package.json package-lock.json ./
# 仅依赖锁变更触发安装；下载缓存跨构建复用，不进入镜像或携带运行时凭据。
RUN --mount=type=cache,id=pl-chat-identity-npm-node24,target=/root/.npm,sharing=locked \
    npm ci --omit=dev --ignore-scripts --prefer-offline --no-audit --no-fund
COPY --chown=node:node config.mjs identity-provider.mjs main-bridge.mjs main-profile.mjs server.mjs sqlite-adapter.mjs error-page.mjs ./
RUN mkdir -p /data && chown node:node /data && chmod 0700 /data
USER node
ENV HOST=0.0.0.0 PORT=3002 OIDC_DATABASE_PATH=/data/oidc.sqlite NODE_ENV=production
EXPOSE 3002
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:3002/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.mjs"]
