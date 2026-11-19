#!/bin/sh
set -eu
# The upstream image's entrypoint assumes an optional sign-in-button plugin
# archive exists. The authentication UI itself is already bundled in the image.
web=/usr/share/nginx/html
find "$web" -type f ! -name 'assessbridge-*' \( -name '*.js' -o -name '*.html' -o -name '*.css' \) -exec sed -i 's|_PUBLIC_URL_||g' {} +
if ! grep -q 'assessbridge-feedback.js' "$web/index.html"; then
  sed -i 's|<head>|<head><link rel="stylesheet" href="/assessbridge-feedback.css"><link rel="stylesheet" href="/assessbridge-demo.css"><script src="/assessbridge-config.js"></script><script src="/assessbridge-feedback.js"></script>|' "$web/index.html"
fi
if ! grep -q 'assessbridge-demo.js' "$web/index.html"; then
  sed -i 's|<head>|<head><script src="/assessbridge-demo.js?v=20261115-feedback"></script>|' "$web/index.html"
fi
sed -i 's|assessbridge-demo.js?v=[^"]*|assessbridge-demo.js?v=20261115-feedback|g' "$web/index.html"
printf 'window._env_ = {\n' > "$web/env-config.js"
awk -F '=' '{ print $1 ": \"" (ENVIRON[$1] ? ENVIRON[$1] : $2) "\"," }' "$web/env.env" >> "$web/env-config.js"
printf '}\n' >> "$web/env-config.js"
exec nginx -g 'daemon off;'
