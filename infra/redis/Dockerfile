FROM redis:7-alpine@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe
# Install the signed Alpine fixes for zlib and OpenSSL (CVE-2026-124709).
RUN apk add --no-cache --upgrade 'zlib=1.4-r0' 'libcrypto3=3.5.9-r2' 'libssl3=3.5.9-r2'
