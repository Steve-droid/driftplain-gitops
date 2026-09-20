#!/bin/sh
# Ping the external heartbeat only while no CRITICAL alert fires and every public edge probe
# answers with its keyword. Prometheus is the ground truth for the cluster; the probes cover
# the public path (Cloudflare edge -> tunnel -> ingress). Any failure withholds the heartbeat
# and the outside sees a miss. Prints outcomes only — never the heartbeat URL.
set -eu
: "${PROMETHEUS_URL:?PROMETHEUS_URL is required}"
: "${HEARTBEAT_URL:?HEARTBEAT_URL is required}"
query='count(ALERTS{alertstate="firing",severity="critical"}) or vector(0)'
answer=$(curl -fsS --max-time 10 --get "$PROMETHEUS_URL/api/v1/query" --data-urlencode "query=$query") || {
  echo "heartbeat withheld: Prometheus did not answer"
  exit 1
}
case "$answer" in
  *'"status":"success"'*'"value":['*',"0"]'*) ;;
  *'"status":"success"'*)
    echo "heartbeat withheld: a critical alert is firing"
    exit 1
    ;;
  *)
    echo "heartbeat withheld: Prometheus answer not understood"
    exit 1
    ;;
esac

# EDGE_PROBES: space-separated url|keyword pairs.
for probe in ${EDGE_PROBES:-}; do
  url=${probe%%|*}
  keyword=${probe#*|}
  body=$(curl -fsS --max-time 10 "$url") || {
    echo "heartbeat withheld: edge probe failed: $url"
    exit 1
  }
  case "$body" in
    *"$keyword"*) ;;
    *)
      echo "heartbeat withheld: edge probe keyword missing: $url"
      exit 1
      ;;
  esac
done

curl -fsS --max-time 10 -o /dev/null "$HEARTBEAT_URL"
echo "heartbeat sent: no critical alert firing${EDGE_PROBES:+, edge probes ok}"
