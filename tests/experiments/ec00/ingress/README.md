# EC-00 ingress fixture

This directory holds a disposable workload for the authenticated candidate
fixture, so the signed-identity cases can run through the cluster's Cilium
Gateway. It also holds a client that reports what the Gateway does to identity
and correlation headers. That second part is the open ER-010 question. Nothing
here is a production server or a production identity model.

| File | Purpose |
| --- | --- |
| `Dockerfile`, `Dockerfile.dockerignore` | Image built from repository source. Dependencies are the hash-locked runtime set from `../candidates/uv.lock`. The build context is an allow-list, and `*.pem`/`*.key` files are excluded from it and refused in the image. |
| `serve_fixture.py` | HTTP v2 on 8080, Python Connect (JSON and binary, HTTP/1.1) on 8081, native gRPC (h2c) on 8082. It starts from a fresh SQLite owner store. It trusts only the public key mounted at `/fixture/public.pem` and refuses to start if given a private key. |
| `remote_checks.py` | Generates a keypair (`keygen`) and runs the remote-mode checks (`run`). See below. |
| `k8s/` | Kustomize resources for namespace `ec00-qualification`: Namespace (Pod Security `restricted`), a ConfigMap generated from the public key, the Deployment (pinned by digest), the Service, two HTTPRoutes and a GRPCRoute, and a CiliumNetworkPolicy. |
| `../ingress_fixture_checks.py` | Loopback test, run in CI: the workload as a subprocess, with `remote_checks.py run` pointed at it. |

## Gateway facts (inspected read-only, 20 October 2026)

- The Gateway is `gateway/internal`, class `cilium` (Cilium v1.20.3), at
  address `192.168.20.219`. Gateway API CRDs are v1.6.3, experimental channel.
  Cilium runs with `enable-gateway-api-alpn` and
  `enable-gateway-api-app-protocol` set to true.
- It has three listeners:
  - `http` (80): used by the existing `*-http-redirect` routes.
  - `https` (443, HTTPS, TLS Terminate, no hostname restriction): certificate
    `clusterissuer/certificate-issuer-domain-0-wildcard`, with dnsNames
    `apps.kotona.app` and `*.apps.kotona.app`.
  - `https-public` (443, only for `auth.kotona.app`).

  All three allow routes from every namespace, and each supports both
  `HTTPRoute` and `GRPCRoute`.
- Hostnames: the plan uses `ec00-qualification.apps.kotona.app` (HTTP v2 and
  readback), `ec00-qualification-connect.apps.kotona.app` and
  `ec00-qualification-grpc.apps.kotona.app`. None of them collides with the
  existing HTTPRoutes. The cluster had no GRPCRoutes and no
  `ec00-qualification` namespace.
- gRPC caveat: Gateway API lets an implementation accept only one of an
  HTTPRoute and a GRPCRoute whose hostnames intersect on the same listener. The
  other route is rejected (`Accepted=False`). So gRPC needs its own hostname,
  but not its own listener: the wildcard `https` listener serves all three. The
  gRPC Service port sets `appProtocol: kubernetes.io/h2c` so that Envoy uses h2c
  upstream. The Connect port has no h2c hint, because uvicorn speaks HTTP/1.1
  only.
- DNS caveat: `*.apps.kotona.app` has no wildcard record. Each existing
  hostname has its own record at the LAN resolver (192.168.10.1), and the three
  new names do not resolve. The client takes `--connect-to 192.168.20.219`: it
  dials that address, while SNI, `Host`/`:authority` and the certificate name
  check keep the real hostname. Adding DNS records is optional and only needed
  for other clients.
- Envoy and `x-request-id`: Envoy can regenerate `x-request-id` on requests it
  classes as external. If the Gateway does that, every signed request fails
  correlation, because the header no longer equals the claim. The run then
  reports `correlation.preserved: false` on `signed-publication`. That is an
  ER-010 finding, not a fixture fault.

## Remote-mode checks

`remote_checks.py run` covers four arms: `http-v2`, `connect-json`,
`connect-binary` and `grpc`. Each arm runs 27 cases, 108 in total:

- signed publication;
- assertion replay;
- fresh-assertion retry of the same idempotency key;
- changed body;
- missing identity and unsigned actor;
- wrong signature, issuer, audience, workspace or repository;
- expired and future assertions;
- actor disagreement and a bad principal epoch;
- correlation header mismatch, and envelope mismatch (header and claim agree,
  the body does not);
- insufficient authority;
- owner refusal of revoked, foreign and observer principals;
- a display-actor link takeover;
- four repeated-header variants (identity or correlation, identical or
  different values);
- 16 concurrent publications from 8 workers, checked against the fixture's
  server-side identity-context records.

How it runs:

- Absence of effect is checked through the read-only `/_ec00/state` readback,
  which returns row counts plus a digest of every owner table.
- Received headers come from `/_ec00/wire/<probe>`. Assertions are redacted to
  a sha256 prefix.
- Latency: client-side p50/p95/min/max over `--samples` signed publications and
  missing-identity refusals, with sample size, on a reused connection. The first
  request is excluded. No comparison is implied.
- Ingress observations: the HTTP version for ALPN-default, forced HTTP/1.1 and
  forced HTTP/2 requests, on the client and as received by the fixture; exact
  HTTP/1.1 header bytes (case, padding, underscore, duplicates, commas) against
  what arrives; Connect header handling over the negotiated version; grpc-web
  over HTTP/1.1 and HTTP/2 against the gRPC hostname.
- Dropped compared with the loopback suite: revocation after commit, which needs
  an owner-store write that the fixture does not expose.
- Exit status 1 means some judged case differed from its expectation. Each
  failure has a `failure_kind`: `security` (a request that should be refused
  was accepted or changed owner state, a repeated identity header that reached
  the fixture was not refused, or identity crossed requests) or `expectation`
  (for example signed publications refused because the ingress rewrote
  `x-request-id`). The report counts both under `failure_kinds`. Probes of
  ingress behaviour are only recorded.
- If the ingress collapses a repeated identity or correlation header into one
  value (one of the two, or a comma join, detected through the redaction tag's
  comma count) and the request is then accepted, the report raises it under the
  top-level `er010_warnings`.
- The Deployment sets `EC00_RESET_STATE=1`: a container restart inside the same
  pod (whose `emptyDir` survives) wipes the disposable store and starts fresh
  instead of crash-looping. Without that variable an existing store is refused.

TLS is always verified. gRPC and the raw HTTP/1.1 probe load the host CA bundle
(`SSL_CERT_FILE`, `NIX_SSL_CERT_FILE`, the interpreter default, then
`/etc/ssl/certs/ca-certificates.crt`); the HTTP v2 and Connect arms use the
pyqwest transport's system-store loader. There is no option to skip verification. Plain `http://` and
insecure gRPC are refused unless `--allow-plaintext-loopback` is given, and even
then only for loopback hosts.

## Operator commands

Run these from the repository root at the commit under test, on the
workstation. That workstation's Docker already lists `192.168.20.221:5000` as a
registry. Cluster access goes through `/projects/dev/appservice`'s direnv
(context `appservice`).

1. Build and push. Note the `digest: sha256:…` line that `docker push` prints:

   ```sh
   docker build -f tests/experiments/ec00/ingress/Dockerfile -t 192.168.20.221:5000/ec00-ingress-fixture:20261020 . && docker push 192.168.20.221:5000/ec00-ingress-fixture:20261020
   ```

2. Generate a disposable keypair outside the repository, stage the public half
   for the ConfigMap, and pin the digest (replace `<DIGEST>` with the hex from
   step 1):

   ```sh
   export EC00_KEYS=$(mktemp -d) && uv run --project tests/experiments/ec00/candidates --locked python tests/experiments/ec00/ingress/remote_checks.py keygen --directory "$EC00_KEYS/keys" && cp "$EC00_KEYS/keys/fixture-public.pem" tests/experiments/ec00/ingress/k8s/fixture-public.pem && sed -i 's/sha256:0\{64\}/sha256:<DIGEST>/' tests/experiments/ec00/ingress/k8s/kustomization.yaml
   ```

3. Apply and wait. Every route must report `Accepted=True` and
   `ResolvedRefs=True` on parent `gateway/internal` section `https`:

   ```sh
   direnv exec /projects/dev/appservice kubectl apply -k tests/experiments/ec00/ingress/k8s && direnv exec /projects/dev/appservice kubectl -n ec00-qualification rollout status deploy/ec00-ingress-fixture --timeout=180s && direnv exec /projects/dev/appservice kubectl -n ec00-qualification get httproute,grpcroute -o jsonpath='{range .items[*]}{.kind}/{.metadata.name}: {range .status.parents[*].conditions[*]}{.type}={.status} {end}{"\n"}{end}'
   ```

4. Run through the Gateway, then delete the private key:

   ```sh
   uv run --project tests/experiments/ec00/candidates --locked python tests/experiments/ec00/ingress/remote_checks.py run --http-url https://ec00-qualification.apps.kotona.app --connect-url https://ec00-qualification-connect.apps.kotona.app --grpc-target ec00-qualification-grpc.apps.kotona.app:443 --connect-to 192.168.20.219 --private-key "$EC00_KEYS/keys/fixture-private.pem" --report "$EC00_KEYS/ingress-report.json"; rm -f "$EC00_KEYS/keys/fixture-private.pem"
   ```

5. Tear down:

   ```sh
   direnv exec /projects/dev/appservice kubectl delete namespace ec00-qualification --wait=true && rm -f tests/experiments/ec00/ingress/k8s/fixture-public.pem && git checkout -- tests/experiments/ec00/ingress/k8s/kustomization.yaml
   ```

What to send back: `$EC00_KEYS/ingress-report.json`, the stdout summary, and
the route conditions from step 3. Expected result:
`"schema_version": "ec00-ingress-observation/v1"`, 108 cases,
`invariant_failures: []`, `target.plaintext_loopback: false`, and an `ingress`
section of observations. If `signed-publication` fails with `identity` and
`correlation.preserved` is false, the Gateway rewrote `x-request-id` (see above).

Local validation (plain loopback, no TLS) uses the same image:

```sh
docker run -d --name ec00-ingress-local --read-only --tmpfs /state:uid=65532,gid=65532,mode=0700 --cap-drop ALL --security-opt no-new-privileges -v "$EC00_KEYS/keys/fixture-public.pem:/fixture/public.pem:ro" -p 127.0.0.1:28080:8080 -p 127.0.0.1:28081:8081 -p 127.0.0.1:28082:8082 ec00-ingress-fixture:<tag>
uv run --project tests/experiments/ec00/candidates --locked python tests/experiments/ec00/ingress/remote_checks.py run --http-url http://127.0.0.1:28080 --connect-url http://127.0.0.1:28081 --grpc-target 127.0.0.1:28082 --allow-plaintext-loopback --private-key "$EC00_KEYS/keys/fixture-private.pem" --report "$EC00_KEYS/local-report.json"
docker rm -f ec00-ingress-local
```

The public key file must be readable by uid 65532.
