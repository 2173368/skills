#!/usr/bin/env bash
# 用法：switch-docker.sh <TASK_DIR> [--apply]
# 把 server.xml 的三个 docBase 和 docker-compose.yml 的 incoming rw 挂载一起切到 TASK_DIR。
# 默认只打印差异；加 --apply 才写入（先备份为 *.bak-<时间>）。写入后需重建容器。
set -euo pipefail
. "$(dirname "$0")/common.sh"

TASK_DIR=$(resolve_task_dir "${1:-}")
APPLY=0
[ "${2:-}" = "--apply" ] && APPLY=1

OLD=$(current_docker_task)
[ -n "$OLD" ] || die "无法从 $SERVER_XML 识别当前任务目录，请手工检查"
info "当前：$OLD"
info "目标：$TASK_DIR"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 只替换 docBase 行和 incoming 挂载行，并按 <OLD>/<仓库>/ 精确匹配，
# 避免 training 与 training-2 这类前缀互相误伤
python3 - "$OLD" "$TASK_DIR" "$SERVER_XML" "$COMPOSE_YML" "$TMP" <<'EOF'
import os, sys
old, new, server_xml, compose, tmp = sys.argv[1:]
def rewrite(path, keep):
    out = []
    for line in open(path, encoding="utf-8"):
        if keep(line):
            for repo in ("manager", "workspace", "web"):
                line = line.replace(f"{old}/{repo}/", f"{new}/{repo}/")
        out.append(line)
    with open(os.path.join(tmp, os.path.basename(path)), "w", encoding="utf-8") as f:
        f.writelines(out)
rewrite(server_xml, lambda l: "docBase=" in l)
rewrite(compose, lambda l: "/incoming:" in l)
EOF

changed=0
for f in "$SERVER_XML" "$COMPOSE_YML"; do
  if ! diff -u "$f" "$TMP/$(basename "$f")"; then changed=1; fi
done

# 校验：两份配置都必须只指向 TASK_DIR
refs=$(cat "$TMP/server.xml" "$TMP/docker-compose.yml" \
  | grep -E 'docBase=|/incoming:' | grep -oE '/usr/local/javaspace/[^/"]+' | sort -u)
[ "$refs" = "$TASK_DIR" ] || die "切换后仍有其他任务目录引用：$refs"

if [ "$changed" -eq 0 ]; then
  info "配置已指向 $TASK_DIR，无需修改"
  exit 0
fi
if [ "$APPLY" -eq 0 ]; then
  info "以上为预览，确认后加 --apply 写入"
  exit 0
fi

ts=$(date +%Y%m%d%H%M%S)
for f in "$SERVER_XML" "$COMPOSE_YML"; do
  cp -p "$f" "$f.bak-$ts"
  cp "$TMP/$(basename "$f")" "$f"
done
info "已写入（备份后缀 .bak-$ts），请执行：start-verify.sh $TASK_DIR --recreate"
