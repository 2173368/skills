#!/usr/bin/env bash
# 用法：start-verify.sh <TASK_DIR> [--recreate]
# 启动 Tomcat，等待 Server startup，再检查启动错误、网关映射和 HTTP。
# 顺序：先启动 -> 容器运行后才能取到网关 -> 网关不一致则提示改 compose 后 --recreate。
# 修改过挂载或 extra_hosts 时必须带 --recreate（restart 不会生效）。
set -uo pipefail
. "$(dirname "$0")/common.sh"

TASK_DIR=$(resolve_task_dir "${1:-}") || exit 1
RECREATE=0
[ "${2:-}" = "--recreate" ] && RECREATE=1
TIMEOUT=${STARTUP_TIMEOUT:-300}

[ "$(current_docker_task)" = "$TASK_DIR" ] || die "Docker 未指向 $TASK_DIR，先运行 switch-docker.sh"
for d in manager/manager-web/target/manager workspace/tycj-main/target/tycj-main web/dist; do
  [ -d "$TASK_DIR/$d" ] || die "产物目录不存在：$TASK_DIR/$d"
done
# 挂载源不存在时 docker 会以 root 创建，之后 mvn clean 无法删除，所以先由当前用户创建
mkdir -p "$TASK_DIR/manager/manager-web/target/manager/incoming"

cd "$DOCKER_DIR" || exit 1
if [ "$RECREATE" -eq 1 ]; then
  dk compose up -d --force-recreate tomcat7 || die "容器重建失败"
else
  dk compose up -d tomcat7 || die "容器启动失败"
fi

started=$(dk inspect "$CONTAINER" --format '{{.State.StartedAt}}')
info "等待 Server startup（最多 ${TIMEOUT}s，启动时间 $started）"
elapsed=0
until dk logs --since "$started" "$CONTAINER" 2>&1 | grep -q 'Server startup in'; do
  state=$(dk inspect "$CONTAINER" --format '{{.State.Status}} {{.RestartCount}}')
  [ "${state%% *}" = running ] && [ "${state##* }" = 0 ] || die "容器异常：$state，查看 docker compose logs tomcat7"
  [ "$elapsed" -lt "$TIMEOUT" ] || die "${TIMEOUT}s 内未出现 Server startup in"
  sleep 5; elapsed=$((elapsed + 5))
done
dk logs --since "$started" "$CONTAINER" 2>&1 | grep 'Server startup in' | tail -1
state=$(dk inspect "$CONTAINER" --format '{{.State.Status}} {{.RestartCount}}')
[ "$state" = "running 0" ] || die "容器状态应为 'running 0'，实际：$state"

GATEWAY=$(dk inspect "$CONTAINER" --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}')
info "网桥网关：$GATEWAY"
for host in whatypx.cep.webtrn.cn control-training.webtrn.cn; do
  grep -q "\"$host:$GATEWAY\"" "$COMPOSE_YML" \
    || die "compose 中 $host 未映射到 $GATEWAY：修改 extra_hosts 后重跑本脚本并加 --recreate"
done

# Server startup in 不代表每个 Context 都成功：失败的 Context 只打印 SEVERE 并返回 404
errors=$(dk logs --since "$started" "$CONTAINER" 2>&1 \
  | grep -E 'SEVERE|严重|Context \[.*\] startup failed|Error (listenerStart|filterStart)' \
  | grep -vE "$NOISE" | head -10)
[ -z "$errors" ] || { echo "$errors" >&2; die "Tomcat 日志有启动错误，详情：scripts/logs.sh tail 500"; }

# 判定：/np/ 容器内外必须 200，且内容与 TASK_DIR/web/dist 一致（证明确实切到了本任务）；
# / 与 /ws/ 及超管入口允许 2xx/3xx（根路径可能跳转 HTTPS），404/5xx/000 视为 Context 未起来。
code_host() { curl -sS -o /dev/null -m 20 -w '%{http_code}' "$1" 2>/dev/null; }
code_ctr() { dk exec "$CONTAINER" curl -sS -o /dev/null -m 20 -w '%{http_code}' "$1" 2>/dev/null; }
NP=http://whatypx.cep.webtrn.cn/np/
fail=0
for url in "$NP" http://whatypx.cep.webtrn.cn/ws/ \
           http://whatypx.cep.webtrn.cn/ http://control-training.webtrn.cn/; do
  h=$(code_host "$url"); c=$(code_ctr "$url")
  printf '  %-40s host=%s container=%s\n' "$url" "$h" "$c"
  for code in "$h" "$c"; do
    if [ "$url" = "$NP" ]; then
      [ "$code" = 200 ] || fail=1
    else
      case "$code" in 2??|3??) ;; *) fail=1 ;; esac
    fi
  done
done
[ "$fail" -eq 0 ] || die "HTTP 检查未通过：/np/ 需 200，其余需 2xx/3xx"

served=$(curl -sS -m 20 "$NP" | md5sum | cut -d' ' -f1)
local_md5=$(md5sum < "$TASK_DIR/web/dist/index.html" | cut -d' ' -f1)
[ "$served" = "$local_md5" ] || die "/np/ 返回的 index.html 与 $TASK_DIR/web/dist 不一致，Docker 可能未指向本任务"
echo "  /np/ index.html 与 $TASK_DIR/web/dist 一致"
info "部署验证通过：$TASK_DIR"
