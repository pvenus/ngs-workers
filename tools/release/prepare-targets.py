#!/usr/bin/env python3
"""Static release proof only. Never starts app code or packages source/data."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REF = "refs/heads/main"
SHA = re.compile(r"[a-f0-9]{40}")
ID = re.compile(r"[1-9][0-9]{0,19}")
SOURCE = {".py": "python", ".mjs": "js", ".cjs": "js", ".js": "js", ".json": "json"}
NODE = "v24.21.0"

# Filled by the owner of this repository; not open-ended manifest-controlled paths.
REPO = "pvenus/ngs-workers"
WORKFLOW = ".github/workflows/prepare-worker-targets.yml"
TARGETS = {
    'worker-document': (('workflow-production/document',),
        ('workflow-production/document/cli.mjs',)),
    'worker-file': (('workflow-production/file',),
        ('workflow-production/file/cli.mjs',)),
    'worker-image': (('workflow-production/image', 'workflow-production/native-image'),
        ('workflow-production/image/image-cli.mjs',)),
    'worker-animation': (('workflow-production/animation', 'workflow-production/native-wan'),
        ('workflow-production/animation/animation-cli.mjs',)),
    'worker-postprocess': (('workflow-production/postprocess', 'workflow-production/native-postprocess'),
        ('workflow-production/postprocess/postprocess-cli.mjs',)),
    'worker-audio': (('workflow-production/candidate-audio-resident', 'workflow-production/candidate-native-audio-resident'),
        ('workflow-production/candidate-audio-resident/audio-cli.mjs',)),
}

def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def require(test, code):
    if not test:
        raise ValueError(code)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def ascii_line(raw):
    return raw.decode("ascii").strip()


def workflow_claim(env):
    head, run, attempt = (env.get(k, "") for k in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    require(env.get("GITHUB_REPOSITORY") == REPO and env.get("GITHUB_EVENT_NAME") == "push"
            and env.get("GITHUB_REF") == REF and env.get("GITHUB_WORKFLOW_REF") == REPO + "/" + WORKFLOW + "@" + REF
            and SHA.fullmatch(head) and ID.fullmatch(run) and ID.fullmatch(attempt), "TARGET_WORKFLOW_NOT_CANONICAL")
    return head, run, attempt


def target_proof(root, head, roots, entrypoints, checked):
    names = {}
    hashes = {}
    counts = dict.fromkeys(("python", "js", "json"), 0)
    for prefix in sorted(roots):
        sha = ascii_line(git(root, "rev-parse", head + ":" + prefix))
        require(SHA.fullmatch(sha) and ascii_line(git(root, "cat-file", "-t", sha)) == "tree", "TARGET_ROOT_INVALID")
        hashes[prefix] = sha
        raw = git(root, "ls-tree", "-r", "-z", head, "--", prefix)
        for row in raw.split(b"\0"):
            if not row:
                continue
            flags, path = row.split(b"\t", 1)
            mode, kind, oid = flags.split(b" ")
            path = path.decode("utf-8", errors="strict")
            require(mode in (b"100644", b"100755") and kind == b"blob" and path.startswith(prefix + "/"),
                    "TARGET_TREE_NONREGULAR")
            require(path not in names, "TARGET_ROOT_OVERLAP")
            names[path] = oid.decode("ascii")
    require(all(name in names and Path(name).suffix in {".mjs", ".py"} for name in entrypoints),
            "TARGET_ENTRYPOINT_MISSING")
    for name, oid in names.items():
        language = SOURCE.get(Path(name).suffix)
        if language:
            counts[language] += 1
            checked.setdefault((language, oid), name)
    return {"roots": hashes, "entrypoints": list(entrypoints),
            "sourceFingerprint": hashlib.sha256(canonical(hashes)).hexdigest(), "checked": counts}


def validate_one(root, key, name):
    language, oid = key
    data = git(root, "cat-file", "blob", oid)
    require(len(data) <= 8 * 1024 * 1024, "TARGET_SOURCE_TOO_LARGE")
    if language == "python":
        compile(data, name, mode="exec", dont_inherit=True)
    elif language == "json":
        json.loads(data)
    else:
        cmd = ["node", "--input-type=module", "--check"] if name.endswith(".mjs") else ["node", "--check"]
        done = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        require(done.returncode == 0, "TARGET_JAVASCRIPT_SYNTAX_ERROR")


def prepare(root, env, require_node=True):
    head, run, attempt = workflow_claim(env)
    require(ascii_line(git(root, "rev-parse", "HEAD")) == head, "TARGET_CHECKOUT_NOT_AT_SHA")
    if require_node:
        require(ascii_line(subprocess.check_output(["node", "--version"], timeout=8)) == NODE, "TARGET_NODE_VERSION_DRIFT")
    checked = {}
    targets = {target: target_proof(root, head, roots, entries, checked)
               for target, (roots, entries) in TARGETS.items()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(validate_one, root, key, name) for key, name in checked.items()]:
            future.result()
    return {"schemaVersion": "ops.target-source-readiness.v1", "repository": REPO, "ref": REF,
            "headSha": head, "runId": run, "runAttempt": attempt, "targets": targets}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare-targets.py PROOF-DIRECTORY")
    try:
        proof = prepare(Path(__file__).resolve().parents[2], os.environ)
    except (ValueError, SyntaxError, UnicodeError, subprocess.SubprocessError, OSError):
        # Never echo parsed source, malformed data, or inherited configuration.
        raise SystemExit("Target source verification failed.") from None
    out = Path(sys.argv[1])
    out.mkdir(mode=0o700)
    (out / "release.json").write_bytes(canonical(proof) + b"\n")
    print("Verified static source for " + str(len(proof["targets"])) + " targets; no deployment.")
