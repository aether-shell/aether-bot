#!/bin/bash

URL="https://gmn.chuangzuoli.com/v1/models"
PROXY="http://127.0.0.1:7897"
INTERVAL=30  # 每30秒检测一次
LOG_FILE="monitor_llm.log"

echo "=== LLM API Monitor ==="
echo "Target: $URL"
echo "Interval: ${INTERVAL}s"
echo "Log: $LOG_FILE"
echo "Press Ctrl+C to stop"
echo ""

total=0
direct_ok=0
direct_fail=0
proxy_ok=0
proxy_fail=0

cleanup() {
    echo ""
    echo "=== Summary ==="
    echo "Total checks: $total"
    echo "Direct: OK=$direct_ok  FAIL=$direct_fail"
    echo "Proxy:  OK=$proxy_ok  FAIL=$proxy_fail"
    exit 0
}
trap cleanup INT

while true; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    total=$((total + 1))

    # Direct request (no proxy)
    direct_result=$(curl -s -o /dev/null -w '%{http_code} %{time_total}' --connect-timeout 10 --max-time 15 "$URL" 2>/dev/null)
    direct_code=$(echo "$direct_result" | awk '{print $1}')
    direct_time=$(echo "$direct_result" | awk '{print $2}')

    # Proxy request
    proxy_result=$(curl -s -o /dev/null -w '%{http_code} %{time_total}' --connect-timeout 10 --max-time 15 -x "$PROXY" "$URL" 2>/dev/null)
    proxy_code=$(echo "$proxy_result" | awk '{print $1}')
    proxy_time=$(echo "$proxy_result" | awk '{print $2}')

    # Handle connection failure
    [ "$direct_code" = "000" ] && direct_code="TIMEOUT"
    [ "$proxy_code" = "000" ] && proxy_code="TIMEOUT"

    # Count results
    if [ "$direct_code" = "200" ]; then
        direct_ok=$((direct_ok + 1))
        direct_mark="OK"
    else
        direct_fail=$((direct_fail + 1))
        direct_mark="FAIL"
    fi

    if [ "$proxy_code" = "200" ]; then
        proxy_ok=$((proxy_ok + 1))
        proxy_mark="OK"
    else
        proxy_fail=$((proxy_fail + 1))
        proxy_mark="FAIL"
    fi

    line="[$ts] #$total  Direct: $direct_code (${direct_time}s) [$direct_mark]  |  Proxy: $proxy_code (${proxy_time}s) [$proxy_mark]"

    echo "$line"
    echo "$line" >> "$LOG_FILE"

    sleep "$INTERVAL"
done
