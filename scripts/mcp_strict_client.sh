#!/usr/bin/env bash
# Run the strict MCP 2026-07-28 client against `vuoro-service mcp-serve`
# from the service image (agentops#2522; the `mcp-strict-client` CI job).
#
#   scripts/mcp_strict_client.sh [IMAGE]      (default vuoro-service:ci)
#
# Starts, on a private docker network:
#   stub-a, stub-b   stand-in runtime shells (scripts/mcp_strict_upstream_stub.py,
#                    run from the same image), one per workspace
#   edge-a, edge-b   `vuoro-service mcp-serve` for workspaces A and B, each
#                    trusting the same ephemeral test signer and reading its
#                    own stub
# then runs scripts/mcp_strict_client.py on the host against them.  Needs
# docker and uv.  Everything is removed on exit; the signer's private key
# lives only in a temporary directory.
set -euo pipefail

IMAGE="${1:-vuoro-service:ci}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="mcpstrict-$$"
NET="${RUN_ID}-net"
STATE="$(mktemp -d "${TMPDIR:-/tmp}/${RUN_ID}.XXXXXX")"
EDGE_PORT_A="${MCP_STRICT_EDGE_PORT_A:-18181}"
EDGE_PORT_B="${MCP_STRICT_EDGE_PORT_B:-18182}"
STUB_PORT_A="${MCP_STRICT_STUB_PORT_A:-18191}"
STUB_PORT_B="${MCP_STRICT_STUB_PORT_B:-18192}"

client() {
  uv run --no-project --quiet \
    --with 'httpx>=0.27,<1' --with 'jsonschema>=4.23,<5' \
    --with 'pyjwt[crypto]>=2.10,<3' \
    python "$ROOT/scripts/mcp_strict_client.py" "$@"
}

containers=()
cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ "${#containers[@]}" -gt 0 ]; then
    for name in "${containers[@]}"; do
      echo "::group::docker logs $name"
      docker logs "$name" 2>&1 | tail -n 80 || true
      echo "::endgroup::"
    done
  fi
  for name in "${containers[@]}"; do docker rm -f "$name" >/dev/null 2>&1 || true; done
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$STATE"
  exit "$status"
}
trap cleanup EXIT

client setup "$STATE"
docker network create "$NET" >/dev/null

for ws in a b; do
  port_var="STUB_PORT_${ws^^}"
  name="${RUN_ID}-stub-${ws}"
  containers+=("$name")
  docker run -d --name "$name" --network "$NET" --network-alias "stub-${ws}" \
    --read-only \
    -p "127.0.0.1:${!port_var}:8080" \
    --env-file "$STATE/stub-${ws}.env" \
    -v "$STATE/etc-vuoro-${ws}:/etc/vuoro:ro" \
    -v "$ROOT/scripts/mcp_strict_upstream_stub.py:/ci/stub.py:ro" \
    --entrypoint python "$IMAGE" /ci/stub.py >/dev/null
done

for ws in a b; do
  port_var="EDGE_PORT_${ws^^}"
  name="${RUN_ID}-edge-${ws}"
  containers+=("$name")
  # --- edge proof / replay protection (vuoro#134, agentops#2519) ---------
  # #134 makes mcp-serve require VUORO_EDGE_PROOF_KEY_FILE at exactly
  # /run/vuoro/edge-proof/key, created by whichever pod process starts
  # first.  A writable tmpfs owned by the image user stands in for the pod's
  # memory-backed emptyDir.  Before #134 the variable is unread and harmless.
  edge_proof=(
    --tmpfs "/run/vuoro/edge-proof:rw,uid=65532,gid=65532,mode=0700"
    -e VUORO_EDGE_PROOF_KEY_FILE=/run/vuoro/edge-proof/key
  )
  # ------------------------------------------------------------------------
  docker run -d --name "$name" --network "$NET" \
    --read-only \
    -p "127.0.0.1:${!port_var}:8081" \
    --env-file "$STATE/edge-${ws}.env" \
    "${edge_proof[@]}" \
    -v "$STATE/etc-vuoro-${ws}:/etc/vuoro:ro" \
    "$IMAGE" mcp-serve --host 0.0.0.0 --port 8081 >/dev/null
done

client check --state "$STATE" \
  --edge-a "http://127.0.0.1:${EDGE_PORT_A}" --edge-b "http://127.0.0.1:${EDGE_PORT_B}" \
  --stub-a "http://127.0.0.1:${STUB_PORT_A}" --stub-b "http://127.0.0.1:${STUB_PORT_B}"
