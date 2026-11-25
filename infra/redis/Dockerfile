FROM redis:7-alpine@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe
# Pin the signed Alpine security update even when the official Redis base predates it.
RUN apk add --no-cache --upgrade 'zlib=1.4-r0'
