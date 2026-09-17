#!/bin/sh
# Ping the external heartbeat only while no CRITICAL alert fires. Prometheus is the ground
# truth: if it cannot answer, the heartbeat is withheld and the outside sees a miss.
# Prints outcomes only — never the heartbeat URL.
set -eu
: "${PROMETHEUS_URL:?PROMETHEUS_URL is required}"
: "${HEARTBEAT_URL:?HEARTBEAT_URL is required}"
query='count(ALERTS{alertstate="firing",severity="critical"}) or vector(0)'
answer=$(curl -fsS --max-time 10 --get "$PROMETHEUS_URL/api/v1/query" --data-urlencode "query=$query") || {
  echo "heartbeat withheld: Prometheus did not answer"
  exit 1
}
case "$answer" in
  *'"status":"success"'*'"value":['*',"0"]'*)
    curl -fsS --max-time 10 -o /dev/null "$HEARTBEAT_URL"
    echo "heartbeat sent: no critical alert firing"
    ;;
  *'"status":"success"'*)
    echo "heartbeat withheld: a critical alert is firing"
    exit 1
    ;;
  *)
    echo "heartbeat withheld: Prometheus answer not understood"
    exit 1
    ;;
esac
