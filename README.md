# Workers: private music boundary

This repository contains a music-only execution module for the existing NGS three-candidate workflow. NGS retains request authorization, candidate count, consecutive seeds, durable intent/unknown handling, selection and registration. The module delegates the existing ACE Turbo runner without changing its arguments or returned provenance. It does not implement LM4B/XL routing, SFX serialization, or new queues/services.

Deployment copies the four package leaves into a separately pinned release. An owner-private binding selects exact code/runtime/interpreter hashes and existing lock identities. No operational bindings, models, completed outputs or historical source trees belong in this repository. Importing the module loads no models. Missing or stale pins fail closed; both existing lock descriptors are inherited by the child.

The venv launcher path is preserved, while its resolved interpreter and pyvenv.cfg are pinned. Runtime/model migration is not completed by this package: the selected model runtime, venv and weights remain external dependencies; the byte-identical runner is now included in the pinned Workers release. SFX producers and reciprocal crash behavior of other Audio services are outside this music-only change. The inherited backend final wait after SIGKILL remains unbounded.

Private validation included 20 isolated subprocess/loader tests and 7 existing NGS approval/three-candidate tests. No new real inference was performed after connection. Full integration fixtures and deployment evidence are retained privately outside this minimal source repository. This is not public-release or license clearance, and no remote publication is authorized.
