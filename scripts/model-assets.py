#!/usr/bin/env python3
"""Plan, explicitly download, or verify a pinned new-machine model bundle.

Default is offline dry-run. No runtime installation, import, inference or activation.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / 'manifests/model-assets.v1.json'
RECEIPT = 'MODEL-BUNDLE.json'


def load_manifest():
    data = MANIFEST.read_bytes()
    manifest = json.loads(data)
    if manifest.get('schemaVersion') != 'workers-model-assets.v1':
        raise ValueError('MANIFEST_VERSION_UNSUPPORTED')
    return manifest, hashlib.sha256(data).hexdigest()


def relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or path.as_posix() != value:
        raise ValueError('UNSAFE_ARTIFACT_PATH')
    return value


def allowed_url(value, redirect=False):
    url = urllib.parse.urlsplit(value)
    if url.scheme != 'https' or url.username or url.password or url.fragment or url.port not in (None, 443):
        raise ValueError('DOWNLOAD_ORIGIN_DENIED')
    host = url.hostname or ''
    hosts = {'huggingface.co', 'raw.githubusercontent.com'}
    if not (host in hosts or (redirect and (host.endswith('.huggingface.co') or host.endswith('.hf.co')))):
        raise ValueError('DOWNLOAD_ORIGIN_DENIED')
    if not redirect and url.query:
        raise ValueError('PINNED_URL_QUERY_DENIED')
    return value


def select(manifest, profile):
    value = manifest['profiles'].get(profile)
    if not value:
        raise ValueError('UNKNOWN_MODEL_PROFILE')
    if value['status'] != 'explicit-download-supported':
        raise ValueError('PROFILE_NOT_DOWNLOADABLE:' + value['status'])
    seen = set()
    for file in value['files']:
        name = relative(file['path'])
        if name in seen or type(file['size']) is not int or file['size'] <= 0 or not re.fullmatch('[a-f0-9]{64}', file['sha256']):
            raise ValueError('INVALID_ARTIFACT_RECORD')
        seen.add(name)
        if file.get('gitBlobSha1') and not re.fullmatch('[a-f0-9]{40}', file['gitBlobSha1']):
            raise ValueError('INVALID_GIT_BLOB_HASH')
        if file.get('integrityBasis') == 'hf-lfs-sha256' and file.get('gitBlobSha1'):
            raise ValueError('LFS_POINTER_IS_NOT_ARTIFACT_CONTENT_HASH')
        allowed_url(file['url'])
    return value


def new_destination(value):
    path = Path(value)
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise ValueError('NEW_ABSOLUTE_DESTINATION_REQUIRED')
    if path.parent.resolve() != path.parent or not path.parent.is_dir():
        raise ValueError('EXISTING_CANONICAL_PARENT_REQUIRED')
    if path == REPO or REPO in path.parents or path in REPO.parents:
        raise ValueError('DESTINATION_MUST_NOT_OVERLAP_SOURCE')
    return path


def existing_destination(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path or not path.is_dir():
        raise ValueError('CANONICAL_BUNDLE_REQUIRED')
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('OWNER_PRIVATE_BUNDLE_REQUIRED')
    if path == REPO or REPO in path.parents or path in REPO.parents:
        raise ValueError('DESTINATION_MUST_NOT_OVERLAP_SOURCE')
    return path


def checked_file(root, name):
    path = root / relative(name)
    if path.resolve() != path or path.is_symlink():
        raise ValueError('BUNDLE_SYMLINK_DENIED')
    if path.exists():
        st = path.stat()
        if not path.is_file() or st.st_uid != os.getuid() or st.st_nlink != 1:
            raise ValueError('BUNDLE_FILE_IDENTITY_DENIED')
    return path


def verify_file(path, record):
    if not path.is_file() or path.stat().st_size != record['size']:
        raise ValueError('ARTIFACT_SIZE_MISMATCH:' + record['path'])
    sha = hashlib.sha256()
    git = hashlib.sha1(('blob ' + str(record['size']) + '\0').encode())
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(part)
            git.update(part)
    if sha.hexdigest() != record['sha256'] or (record.get('gitBlobSha1') and git.hexdigest() != record['gitBlobSha1']):
        raise ValueError('ARTIFACT_HASH_MISMATCH:' + record['path'])


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def network(request):
    # No proxy credentials, Hugging Face token, cookies, implicit account cache
    # or alternate mirror. A denied/gated response stops this download.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), OfficialRedirects())
    return opener.open(request, timeout=60)


def transfer(root, record, opener=None):
    opener = opener or network
    final = checked_file(root, record['path'])
    if final.exists():
        verify_file(final, record)
        return 'verified-existing'
    final.parent.mkdir(parents=True, exist_ok=True)
    part = checked_file(root, record['path'] + '.part')
    offset = part.stat().st_size if part.exists() else 0
    if offset > record['size']:
        raise ValueError('PARTIAL_TOO_LARGE_INSPECT_BEFORE_RETRY')
    if offset < record['size']:
        headers = {'User-Agent': 'UnifiedWorkers-PinnedModels/1', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = 'bytes=' + str(offset) + '-'
        request = urllib.request.Request(allowed_url(record['url']), headers=headers)
        with opener(request) as response:
            if offset:
                wanted = 'bytes ' + str(offset) + '-' + str(record['size'] - 1) + '/' + str(record['size'])
                if response.status != 206 or response.headers.get('Content-Range') != wanted:
                    raise ValueError('RANGE_RESUME_NOT_CONFIRMED_PARTIAL_PRESERVED')
            elif response.status != 200:
                raise ValueError('DOWNLOAD_STATUS_UNCONFIRMED')
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise ValueError('ENCODED_RESPONSE_DENIED')
            declared = response.headers.get('Content-Length')
            if declared is not None and declared != str(record['size'] - offset):
                raise ValueError('DOWNLOAD_LENGTH_UNCONFIRMED')
            flags = os.O_WRONLY | os.O_NOFOLLOW | (os.O_APPEND if part.exists() else os.O_CREAT | os.O_EXCL)
            with os.fdopen(os.open(part, flags, 0o600), 'ab') as stream:
                size = offset
                reported = offset
                for chunk in iter(lambda: response.read(1024 * 1024), b''):
                    size += len(chunk)
                    if size > record['size']:
                        raise ValueError('DOWNLOAD_EXCEEDS_PINNED_SIZE')
                    stream.write(chunk)
                    if size - reported >= 64 * 1024**2:
                        print(json.dumps({'artifact': record['path'], 'receivedBytes': size, 'totalBytes': record['size']}), file=sys.stderr, flush=True)
                        reported = size
                stream.flush()
                os.fsync(stream.fileno())
    verify_file(part, record)
    if final.exists():
        raise ValueError('FINAL_APPEARED_INSPECT_BEFORE_RETRY')
    part.rename(final)
    return 'downloaded-and-verified'


def receipt(root, profile, manifest_sha, state, files):
    value = {'schemaVersion': 'workers-model-bundle.v1', 'profile': profile,
             'manifestSha256': manifest_sha, 'state': state, 'files': files,
             'runtimeInstalled': False, 'activated': False, 'inferenceTested': False}
    dest = checked_file(root, RECEIPT)
    temp = root / ('.receipt-' + uuid.uuid4().hex)
    with temp.open('x') as stream:
        os.chmod(temp, 0o600)
        stream.write(json.dumps(value, indent=2) + '\n')
    temp.replace(dest)
    return value


def bound_receipt(root, profile, manifest_sha):
    path = checked_file(root, RECEIPT)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('RECOGNIZED_BUNDLE_RECEIPT_REQUIRED')
    value = json.loads(path.read_text())
    if value.get('schemaVersion') != 'workers-model-bundle.v1' or value.get('profile') != profile or value.get('manifestSha256') != manifest_sha:
        raise ValueError('BUNDLE_MANIFEST_OR_PROFILE_MISMATCH')


def download(destination, profile, value, manifest_sha, resume=False):
    root = existing_destination(destination) if resume else new_destination(destination)
    if resume:
        bound_receipt(root, profile, manifest_sha)
    else:
        root.mkdir(mode=0o700)
        receipt(root, profile, manifest_sha, 'incomplete', [])
    lock = checked_file(root, '.download.lock')
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        total = sum(record['size'] for record in value['files'])
        present = sum(checked_file(root, r['path']).stat().st_size if checked_file(root, r['path']).exists()
                      else min(checked_file(root, r['path'] + '.part').stat().st_size, r['size'])
                      if checked_file(root, r['path'] + '.part').exists() else 0 for r in value['files'])
        if shutil.disk_usage(root).free < total - present + 512 * 1024**2:
            raise ValueError('INSUFFICIENT_DOWNLOAD_SPACE')
        done = []
        for record in value['files']:
            print(json.dumps({'artifact': record['path'], 'bytes': record['size']}), file=sys.stderr, flush=True)
            state = transfer(root, record)
            done.append({'path': record['path'], 'sha256': record['sha256'], 'size': record['size'], 'result': state})
            receipt(root, profile, manifest_sha, 'incomplete', done)
        return receipt(root, profile, manifest_sha, 'complete-bundle-only', done)


def main(argv=None):
    manifest, manifest_sha = load_manifest()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--profile', default=manifest.get('defaultProfile', 'acestep-turbo'))
    parser.add_argument('--destination')
    parser.add_argument('--download', action='store_true', help='Explicitly fetch pinned official artifacts into a NEW bundle')
    parser.add_argument('--resume', action='store_true', help='With --download only; resume this exact owned bundle, never truncate partials')
    parser.add_argument('--verify', action='store_true', help='Read/hash an existing bundle, without network or initialization')
    args = parser.parse_args(argv)
    if args.list:
        if args.download or args.resume or args.verify or args.destination:
            parser.error('--list cannot be combined with actions')
        print(json.dumps({k: {field: v[field] for field in ('status', 'name', 'role', 'preferred', 'currentGenerationAdapterSupported', 'sourceGenerationAdapterImplemented', 'adapterStatus', 'reason') if field in v} for k, v in manifest['profiles'].items()}, indent=2))
        return
    if not args.destination or (args.verify and (args.download or args.resume)) or (args.resume and not args.download):
        parser.error('supply --destination; --verify is separate; --resume requires --download')
    value = select(manifest, args.profile)
    if args.download:
        result = download(args.destination, args.profile, value, manifest_sha, args.resume)
    elif args.verify:
        root = existing_destination(args.destination)
        bound_receipt(root, args.profile, manifest_sha)
        for record in value['files']:
            verify_file(checked_file(root, record['path']), record)
        result = {'state': 'verified-bundle-only', 'fileCount': len(value['files']), 'runtimeInstalled': False, 'activated': False}
    else:
        root = new_destination(args.destination)
        result = {'state': 'dry-run', 'profile': args.profile, 'destination': str(root),
                  'fileCount': len(value['files']), 'downloadBytes': sum(r['size'] for r in value['files']),
                  'runtimeRevision': value['runtimeRevision'], 'modelRevision': value['modelRevision'],
                  'plannerRevision': value.get('plannerRevision'), 'components': value['components'],
                  'role': value.get('role'), 'currentGenerationAdapterSupported': value.get('currentGenerationAdapterSupported', False),
                  'sourceGenerationAdapterImplemented': value.get('sourceGenerationAdapterImplemented', False),
                  'adapterStatus': value.get('adapterStatus'), 'liveGenerationValidated': value.get('liveGenerationValidated', False),
                  'prerequisites': value.get('prerequisites', []),
                  'license': value['license'], 'networkUsed': False, 'filesystemChanged': False,
                  'runtimeInstalled': False, 'activated': False}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        sys.exit('MODEL_DOWNLOAD_HTTP_' + str(error.code) + ': no alternate mirror or automatic retry')
    except urllib.error.URLError:
        sys.exit('MODEL_DOWNLOAD_NETWORK_ERROR: partials preserved; inspect before explicit resume')
