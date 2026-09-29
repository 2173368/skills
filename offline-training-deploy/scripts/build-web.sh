#!/usr/bin/env bash
# 用法：build-web.sh <TASK_DIR> [--clean-deps]
# 用 Node 8.17.0 构建 web。只有 node_modules 由其他 Node 大版本安装（或显式 --clean-deps）
# 时才删除 node_modules，同版本重复构建复用依赖。结束后恢复原 Node 版本。
set -uo pipefail
. "$(dirname "$0")/common.sh"

TASK_DIR=$(resolve_task_dir "${1:-}") || exit 1
CLEAN_DEPS=0
[ "${2:-}" = "--clean-deps" ] && CLEAN_DEPS=1
WEB="$TASK_DIR/web"
LOG=$(log_path "$TASK_DIR" web-build)
# 记录 node_modules 由哪个 Node 大版本安装，用于判断是否需要清理
MARKER="$WEB/node_modules/.offline-training-node-major"
info "构建 web，日志：$LOG"

# 用 ( ) 子 shell：内部 exit 只结束子 shell，外层仍会记录退出码
(
  set -u
  cd "$WEB" || exit 1
  export NVM_DIR="$HOME/.nvm"
  . "$NVM_DIR/nvm.sh"
  ORIG_NODE=$(nvm current)
  restore_node() {
    nvm use "$ORIG_NODE" >/dev/null 2>&1 || true
    echo "restored node: $(node -v 2>/dev/null || echo none)"
  }
  trap restore_node EXIT

  nvm use "$NODE_VERSION" || exit 1
  major=$(node -v | sed -E 's/^v([0-9]+).*/\1/')
  echo "build node: $(node -v), npm: $(npm -v)"

  if [ -d node_modules ]; then
    prev=$(cat "$MARKER" 2>/dev/null || echo unknown)
    # 标记未知（旧依赖）时不清理，交给下面的 binding 检查和 npm rebuild
    if [ "$CLEAN_DEPS" -eq 1 ] || { [ "$prev" != unknown ] && [ "$prev" != "$major" ]; }; then
      echo "清理 node_modules（上次安装 Node 大版本：$prev，--clean-deps=$CLEAN_DEPS）"
      rm -rf node_modules dist
    fi
  fi

  export npm_config_registry=https://registry.npmmirror.com
  export SASS_BINARY_SITE=https://registry.npmmirror.com/-/binary/node-sass
  export npm_config_sass_binary_site="$SASS_BINARY_SITE"
  export npm_config_disturl=https://registry.npmmirror.com/-/binary/node
  export PYTHON=/usr/bin/python3

  npm install --no-package-lock --no-audit --no-fund || exit 1
  echo "$major" > "$MARKER"
  test -f "$NODE_ABI_BINDING" || npm rebuild node-sass || exit 1
  test -f "$NODE_ABI_BINDING" || { echo "缺少 $NODE_ABI_BINDING"; exit 1; }
  npm run build || exit 1
) > "$LOG" 2>&1
status=$?
echo "exit code: $status" >> "$LOG"

grep -nE 'Build complete\.|restored node:|exit code:' "$LOG"
[ "$status" -eq 0 ] || { tail -40 "$LOG"; die "web 构建失败（exit $status），完整日志：$LOG"; }
grep -q 'Build complete\.' "$LOG" || die "日志中没有 Build complete."
[ -f "$WEB/dist/index.html" ] || die "缺少 $WEB/dist/index.html"
info "web 构建成功：$(find "$WEB/dist" -type f | wc -l) 个文件"
