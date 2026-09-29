#!/usr/bin/env bash
# 用法：sync-nginx.sh
# 普通 Java/web 构建不需要运行。只有首次部署或 nginx-training.conf 变化时才同步。
# 目标文件不可写或 sudo 需要密码时，不重试，打印命令交给有权限的人执行（退出码 2）。
set -uo pipefail
. "$(dirname "$0")/common.sh"

if cmp -s "$NGINX_SRC" "$NGINX_DST"; then
  info "nginx 配置未变化，无需 reload"
  exit 0
fi

diff -u "$NGINX_DST" "$NGINX_SRC" 2>/dev/null | head -60
# 必须保留的代理设置
for pat in 'proxy_set_header +Host +\$host' 'client_max_body_size +1024m' 'Upgrade'; do
  grep -qE "$pat" "$NGINX_SRC" || die "源配置缺少：$pat"
done

handoff() {
  echo "需要有权限的操作者执行：" >&2
  echo "  sudo cp $NGINX_SRC $NGINX_DST && sudo nginx -t && sudo systemctl reload nginx" >&2
  exit 2
}

BACKUP=$(mktemp)
cp "$NGINX_DST" "$BACKUP" 2>/dev/null || true
if [ -w "$NGINX_DST" ]; then
  cp "$NGINX_SRC" "$NGINX_DST" || handoff
else
  sudo -n cp "$NGINX_SRC" "$NGINX_DST" 2>/dev/null || handoff
fi
if ! sudo -n nginx -t; then
  # 测试失败时还原生效配置，避免下次 nginx 重启起不来
  [ -s "$BACKUP" ] && { cp "$BACKUP" "$NGINX_DST" 2>/dev/null || sudo -n cp "$BACKUP" "$NGINX_DST"; }
  rm -f "$BACKUP"
  die "nginx -t 失败，已还原 $NGINX_DST"
fi
rm -f "$BACKUP"
sudo -n systemctl reload nginx || handoff
info "nginx 已同步并 reload"
