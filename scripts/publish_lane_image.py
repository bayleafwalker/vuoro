"""Qualify a pushed candidate by digest, then expose separate lane aliases."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/publish-lane-service-image.yaml'


def validate_inputs(repository, source_sha, ref):
    if not re.fullmatch(r'[a-z0-9_.-]+/[a-z0-9_.-]+', repository):
        raise ValueError('invalid repository')
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ValueError('invalid source commit')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,122}', ref):
        raise ValueError('ref cannot form a distinct lane tag')
    if ref != 'main' and not ref.startswith('vuoro-service-v'):
        raise ValueError('publish only service tags or manually selected main')


def publish(repository, digest, source_sha, ref, receipt_path, *, run=subprocess.run):
    validate_inputs(repository, source_sha, ref)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('an exact registry output digest is required')
    image = f'ghcr.io/{repository.split("/")[0]}/vuoro-service'
    reference = f'{image}@{digest}'
    receipt = {'schema': 'vuoro-lane-image-publication/v1', 'source_sha': source_sha,
               'image': reference, 'platform': 'linux/amd64', 'checks': [],
               'aliases': [], 'complete': False}
    receipt_path = Path(receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')

    def command(argv, *, stdin=None):
        try:
            result = run(argv, input=stdin, capture_output=True, check=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            receipt['checks'].append({'command': argv, 'passed': False,
                                      'exit_code': getattr(error, 'returncode', None),
                                      'timeout': isinstance(error, subprocess.TimeoutExpired)})
            save()
            raise
        receipt['checks'].append({'command': argv, 'passed': True})
        save()
        return result.stdout

    save()
    try:
        command(['gh', 'attestation', 'verify', 'oci://' + reference,
                 '--repo', repository, '--signer-workflow', repository + '/' + WORKFLOW,
                 '--source-digest', source_sha, '--predicate-type', 'https://slsa.dev/provenance/v1',
                 '--source-ref', 'refs/heads/main' if ref == 'main' else 'refs/tags/' + ref,
                 '--deny-self-hosted-runners'])
        index = json.loads(command(['docker', 'buildx', 'imagetools', 'inspect', reference, '--raw']))
        if index.get('mediaType') not in ('application/vnd.oci.image.index.v1+json',
                                          'application/vnd.docker.distribution.manifest.list.v2+json'):
            raise ValueError('registry digest must identify an image index')
        manifests = index.get('manifests', [])
        runtimes = [m for m in manifests if m.get('annotations', {}).get('vnd.docker.reference.type') != 'attestation-manifest']
        attestations = [m for m in manifests if m not in runtimes]
        if (len(runtimes) != 1 or runtimes[0].get('platform') != {'architecture': 'amd64', 'os': 'linux'}
                or not attestations):
            raise ValueError('index must carry only linux/amd64 plus attached attestations')
        predicates = set()
        for descriptor in attestations:
            if descriptor.get('annotations', {}).get('vnd.docker.reference.digest') != runtimes[0]['digest']:
                raise ValueError('attestation is not bound to the runnable image')
            artifact = json.loads(command(['docker', 'buildx', 'imagetools', 'inspect',
                                          image + '@' + descriptor['digest'], '--raw']))
            predicates.update(layer.get('annotations', {}).get('in-toto.io/predicate-type')
                              for layer in artifact.get('layers', []))
        if ('https://spdx.dev/Document' not in predicates or not predicates.intersection({
                'https://slsa.dev/provenance/v0.2', 'https://slsa.dev/provenance/v1'})):
            raise ValueError('index lacks attached SBOM or build provenance')
        receipt['index'] = {'runtime_digest': runtimes[0]['digest'],
                            'attestation_manifests': len(attestations),
                            'predicate_types': sorted(p for p in predicates if p is not None)}
        command(['docker', 'pull', '--platform', 'linux/amd64', reference])
        manifest_hash = hashlib.sha256((ROOT / 'packages/vuoro-service/composition/lane-adapter-pins.json').read_bytes()).hexdigest()
        receipt['manifest_sha256'] = manifest_hash
        check = command(['docker', 'run', '--rm', '-i', '--platform', 'linux/amd64',
                         '--entrypoint', 'python', reference, '-', manifest_hash],
                        stdin=(ROOT / 'scripts/validate_lane_service_image.py').read_bytes())
        installed = json.loads(check)
        if installed.get('verified') is not True or installed.get('manifest_sha256') != manifest_hash:
            raise ValueError('image verifier did not confirm the selected manifest')
        receipt['installed_composition'] = installed
        names = command(['python3', str(ROOT / 'scripts/validate_service_image.py'), 'workspace-names']).decode().splitlines()
        command(['docker', 'run', '--rm', '-i', '--platform', 'linux/amd64', '--entrypoint', 'python', reference,
                 '-', 'check', *names], stdin=(ROOT / 'scripts/validate_service_image.py').read_bytes())
        command(['docker', 'run', '--rm', '--platform', 'linux/amd64', '--entrypoint', 'python', reference, '-m', 'pip', 'check'])
        command(['docker', 'run', '--rm', '-i', '--platform', 'linux/amd64', '--entrypoint', 'python', reference, '-'],
                stdin=(ROOT / 'scripts/validate_service_image_preview.py').read_bytes())
        command(['docker', 'run', '--rm', '--platform', 'linux/amd64', reference, 'mcp-serve', '--help'])
        aliases = [f'{image}:lane-{ref}', f'{image}:lane-sha-{source_sha}']
        for alias in aliases:
            entry = {'tag': alias, 'updated': None, 'effect': 'unknown', 'verified': False}
            receipt['aliases'].append(entry)
            save()
            command(['docker', 'buildx', 'imagetools', 'create', '--tag', alias, reference])
            entry['updated'] = True
            entry['effect'] = 'updated'
            save()
            actual = command(['docker', 'buildx', 'imagetools', 'inspect', alias,
                              '--format', '{{.Manifest.Digest}}']).decode().strip()
            entry['digest'] = actual
            if actual != digest:
                raise ValueError('alias top-level digest differs from qualified index')
            entry['verified'] = True
            save()
        receipt['complete'] = True
        save()
        return receipt
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as error:
        receipt['failure'] = type(error).__name__
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'digest', 'source-sha', 'ref', 'receipt'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    publish(args.repository, args.digest, args.source_sha, args.ref, args.receipt)


if __name__ == '__main__':
    main()
