#!/usr/bin/env bash
# 自动化测试时判断后端是否报错。通过 docker logs / docker exec 读取，不依赖宿主文件权限。
#   logs.sh mark            测试前打标记（记录时间点和 access log 行数）
#   logs.sh check [--warn]  标记之后的应用 ERROR/SEVERE/异常堆栈 + access log 5xx；有问题退出码 1
#   logs.sh tail [N]        最近 N 行容器输出（默认 200）
#   logs.sh file <name> [N] 读 /usr/local/tomcat/logs 下的文件末尾 N 行（如 localhost.2026-09-29.log）
set -uo pipefail
. "$(dirname "$0")/common.sh"

MARK=/tmp/offline-training-logmark
LOGDIR=/usr/local/tomcat/logs

access_file() { echo "localhost_access_log$(date +%Y-%m-%d).txt"; }
access_lines() { dk exec "$CONTAINER" sh -c "wc -l < $LOGDIR/$1 2>/dev/null || echo 0" | tr -d ' '; }

# 从日志流中提取错误块：ERROR/SEVERE 行或裸异常行开头，连同后续堆栈（不以时间戳开头的行），每块最多 40 行
extract_errors() {
  local levels='ERROR|SEVERE|严重'
  [ "${1:-}" = --warn ] && levels='ERROR|SEVERE|严重|WARN|WARNING|警告'
  awk -v lv="$levels" -v noise="$NOISE" '
    function is_head(l) { return l ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2} / || l ~ /^[A-Z][a-z]{2} [0-9]{1,2}, [0-9]{4} / || l ~ /^(INFO|WARNING|SEVERE|严重|警告|信息):/ }
    {
      exc = ($0 ~ /^[A-Za-z0-9_.$]+(Exception|Error)(: |$)/)
      # 紧跟在 ERROR/SEVERE 后面的异常行属于同一块，不另起
      if (is_head($0) || (exc && !inblk)) {
        inblk = 0
        if (($0 ~ ("(^| )(" lv ")(:| )") || $0 ~ /^[A-Za-z0-9_.$]+(Exception|Error)(: |$)/) && $0 !~ noise) {
          inblk = 1; n = 0; blocks++; print "----"
        }
      }
      if (inblk && n++ < 40) print
    }
    END { exit blocks > 0 ? 1 : 0 }'
}

case "${1:-}" in
  mark)
    f=$(access_file)
    printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$f" "$(access_lines "$f")" > "$MARK"
    info "已标记：$(cat "$MARK")"
    ;;
  check)
    [ -f "$MARK" ] || die "先执行 logs.sh mark"
    read -r since mfile mcount < "$MARK"
    info "检查 $since 之后的日志"
    status=0
    dk logs --since "$since" "$CONTAINER" 2>&1 | extract_errors "${2:-}" || status=1
    [ "$status" -eq 0 ] && echo "  应用日志：无 ERROR/SEVERE/异常"
    # access log 按天滚动：标记当天的文件从标记行之后读，之后新产生的文件整份读
    f=$(access_file)
    if [ "$f" = "$mfile" ]; then start=$((mcount + 1)); else start=1; fi
    bad=$(dk exec "$CONTAINER" sh -c "tail -n +$start $LOGDIR/$f 2>/dev/null" \
      | awk -F'"' '{ split($3, a, " "); if (a[1] >= 500) print }')
    if [ -n "$bad" ]; then
      echo "---- access log 5xx："; echo "$bad" | tail -30; status=1
    else
      echo "  access log：无 5xx"
    fi
    [ "$status" -eq 0 ] && info "未发现后端错误" || echo "发现后端错误（见上）" >&2
    exit "$status"
    ;;
  tail) dk logs --tail "${2:-200}" "$CONTAINER" 2>&1 ;;
  file)
    [ -n "${2:-}" ] || die "用法：logs.sh file <文件名> [N]"
    case "$2" in /*|*..*) die "只允许 $LOGDIR 下的相对路径" ;; esac
    dk exec "$CONTAINER" tail -n "${3:-200}" "$LOGDIR/$2"
    ;;
  *) sed -n '2,6p' "$0"; exit 2 ;;
esac
