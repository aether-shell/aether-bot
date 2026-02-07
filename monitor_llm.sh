#!/bin/bash

# Config — edit these to match your setup (or export env vars)
API_KEY="${API_KEY:-}"
BASE_URL="${BASE_URL:-https://gmn.chuangzuoli.com/v1}"
PROXY="${PROXY:-http://127.0.0.1:7897}"
INTERVAL="${INTERVAL:-60}"
LOG_FILE="${LOG_FILE:-monitor_llm.log}"
MODEL="${MODEL:-gpt-5.2}"

echo "=== LLM API Monitor (Real POST) ==="
echo "Endpoints:"
echo "  GET  $BASE_URL/models"
echo "  POST $BASE_URL/responses"
echo "Proxy: $PROXY"
echo "Interval: ${INTERVAL}s"
echo "Log: $LOG_FILE"
if [ -z "$API_KEY" ]; then
    echo "ERROR: API_KEY is required."
    echo "Set it via: export API_KEY=sk-***"
    exit 1
fi

echo "Press Ctrl+C to stop"
echo ""

total=0
get_ok=0; get_fail=0
post_ok=0; post_fail=0

cleanup() {
    echo ""
    echo "========================================="
    echo "  Summary (total: $total checks)"
    echo "========================================="
    echo "  GET  /models:    OK=$get_ok   FAIL=$get_fail"
    echo "  POST /responses: OK=$post_ok  FAIL=$post_fail"
    echo "========================================="
    echo ""
    if [ "$get_fail" -eq 0 ] && [ "$post_fail" -gt 0 ]; then
        echo "  -> GET OK but POST fails: large request issue (Cloudflare/origin timeout)"
        echo "     Suggests the server struggles with heavy POST payloads."
    elif [ "$get_fail" -gt 0 ] && [ "$post_fail" -gt 0 ]; then
        echo "  -> Both fail: source server (gmn.chuangzuoli.com) is unstable"
    elif [ "$get_fail" -gt 0 ] && [ "$post_fail" -eq 0 ]; then
        echo "  -> GET fails but POST OK: unusual, possible rate limiting on models endpoint"
    else
        echo "  -> All OK: no issues detected during monitoring"
    fi
    exit 0
}
trap cleanup INT

# Build a realistic POST body (simulates nanobot's actual request)
POST_BODY=$(cat <<ENDJSON
{
  "model": "$MODEL",
  "input": [
    {"role": "user", "content": [{"type": "input_text", "text": "ping"}]}
  ],
  "max_output_tokens": 64,
  "temperature": 0.7,
  "stream": true
}
ENDJSON
)

while true; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    total=$((total + 1))

    # 1) GET /v1/models (lightweight, same as before)
    get_result=$(curl -s -o /dev/null -w '%{http_code} %{time_total}' \
        --noproxy '*' -x "$PROXY" \
        --connect-timeout 10 --max-time 30 \
        -H "Authorization: Bearer $API_KEY" \
        "$BASE_URL/models" 2>/dev/null)
    get_code=$(echo "$get_result" | awk '{print $1}')
    get_time=$(echo "$get_result" | awk '{print $2}')
    [ "$get_code" = "000" ] && get_code="TIMEOUT"

    # 2) POST /v1/responses (simulates real nanobot call)
    post_result=$(curl -s -o /dev/null -w '%{http_code} %{time_total}' \
        --noproxy '*' -x "$PROXY" \
        --connect-timeout 10 --max-time 90 \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -H "Accept: text/event-stream" \
        -H "OpenAI-Beta: responses=v1" \
        -H "User-Agent: curl/8.0" \
        -d "$POST_BODY" \
        "$BASE_URL/responses" 2>/dev/null)
    post_code=$(echo "$post_result" | awk '{print $1}')
    post_time=$(echo "$post_result" | awk '{print $2}')
    [ "$post_code" = "000" ] && post_code="TIMEOUT"

    # Count
    if [ "$get_code" = "200" ]; then get_ok=$((get_ok + 1)); get_mark="OK"; else get_fail=$((get_fail + 1)); get_mark="FAIL"; fi
    if [ "$post_code" = "200" ]; then post_ok=$((post_ok + 1)); post_mark="OK"; else post_fail=$((post_fail + 1)); post_mark="FAIL"; fi

    line="[$ts] #$total  GET /models: $get_code (${get_time}s) [$get_mark]  |  POST /responses: $post_code (${post_time}s) [$post_mark]"

    echo "$line"
    echo "$line" >> "$LOG_FILE"

    sleep "$INTERVAL"
done
