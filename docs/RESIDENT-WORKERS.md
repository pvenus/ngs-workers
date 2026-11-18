# 독립 상주 워커 개발 1차: 공통 런타임 및 계약

2026-11-18. **개발 중, 운영 미절체. 실모델·실제 요청 통합 검증 미완료.**

## 저장소와 작업 경계

기존 `Workers/source` Git 저장소를 재사용한다. 초기 기준 commit `b8a392e`.
이번 브랜치는 `dev/resident-services-20261118`이며 별도 worktree에서 작업한다.
기존 AudioForge/legacy VideoForge와 운영 release/binding/DB/model은 수정하지 않는다.
다섯 서비스는 한 저장소의 공통 코드로 개발하되 개별 프로세스와 모델 환경을 사용한다.
새 Git 저장소, 기존 서비스 복제, launchd 등록, 모델 다운로드는 하지 않았다.

## 역할

| 서비스 역할 | 내부 capability 명칭(제안, 공개 DTO 변경 아님) | 처리 | 모델 |
|---|---|---|---|
| document | document.create/edit | 문서·NPC 기획·마스터 컨셉을 Codex CLI 어댑터로 처리 | CLI/API, 상주 모델 없음 |
| image | image.generate/edit | 승인된 이미지 생성 백엔드 어댑터 | 현재 API형 가정, 로컬 도입 시 별도 프로필 |
| animation | animation.generate | Wan 어댑터 | lazy load / idle unload |
| postprocess | image.process / frames.extract | 배경제거·추출·축소·픽셀화 | CPU 기본, 모델 필요 기능은 자원 계약 확장 |
| music | music.generate | 기존 AudioForge/ACE 어댑터 | lazy load / idle unload |

이는 아직 기존 요청 타입을 바꾼 것이 아니다. 기존 공식 `accept(actor,key,request)` DTO,
`requestId`, inputHash/payloadRef, actor, source/incarnation pins, fence 및 publication
계약은 어댑터에서 보존해야 한다. actor/권한 정보는 Request 서버가 검증하고 실행 claim에
필요한 불변 정보만 전달한다. 모델 초기화는 import/startup이 아니라 유효 claim 이후 수행.

## 공통 코드 구현

`workers/resident/contracts.py`는 Request, PDS, Backend 포트를 선언한다.
`runtime.py`는 하나의 독립 프로세스가 수행할 pull→renew→stage→execute→publish→receipt
검증→cleanup→complete 루프다. 작업 중 lease를 갱신하고 소유권 상실 시 cancel을 전달한다.
다른 역할 워커를 동기 호출하거나 Workflow를 실행하지 않는다.

- `Request.claim`은 서버에서 요청과 자원 grant를 원자적으로 선점해야 한다.
- `Claim`에는 request/attempt/fence, input references, expected revision, 자원 grant가 필요하다.
- `PDS.stage`는 입력 다운로드/해시 확인을 담당. 워커는 반환된 임시 디렉터리 안에서만 작업.
- `PDS.publish`는 파일 교체 API를 사용. workflow metadata JSON API와 혼동하지 않는다.
- 게시 receipt는 request/attempt/fence 일치와 verified 상태를 검사한다.
- cleanup 실패는 별도 상태로 서버에 보고. 생성 작업을 다시 실행해 정리하지 않는다.
- 게시 또는 완료 응답 유실은 `uncertain`을 기록하고 해당 상주 루프를 멈춘다.
  기존 게시 조회·정식 복구가 필요하며 무조건 새 콘텐츠를 만들지 않는다.
- `Backend.execute`는 취소를 관찰하고 자식 프로세스가 종료된 이후에만 반환/예외를 내야 한다.
- `Backend.load` 부분 실패 시 어댑터가 부분 초기화를 회수해야 한다.

## 전역 자원 제어: 아직 서버 통합이 필요

프로필의 `capacity=1`만으로 전역 동시성 1을 보장하지 않는다.
실제 Request 서비스는 **모든 animation 워커/수동/워크플로 요청 합계**에 대해
`video-global=1`을 원자적으로 보장해야 한다. lease 만료만으로 살아 있는 영상 프로세스의
슬롯을 재할당하면 안 된다. 종료 확인/fencing 이후 재할당한다.
음악과 영상은 서로 다른 실행 슬롯이지만 같은 Mac의 통합 메모리 예산을 공유한다.
**idle 상태에서 모델이 메모리에 남아 있는 양도 자원 서버에서 추적해야 한다.**
추론 슬롯 반납과 모델 메모리 예약 해제는 별개다. 이 예약/회수 서버 어댑터는 미구현.

## 현재 코드와 이행 장애

현재 NGS frame/animation `host.py`는 `npc_workflow.host.generation_slot()`으로 직렬화한다.
새 pull 워커와 기존 OPS consumer를 동시에 운영하면 안 된다. drain/소유권 이관 검증 이후에만
기존 역할 소비를 중지하고 새 워커를 연결한다. 본 브랜치는 이 전환을 하지 않는다.
기존 frame worker는 NGS 내부 동적 import/cwd/processor_cli에 결합되어 단순 파일 복사 금지.
현재 unified Workers의 VideoForge는 legacy LTX이며 Wan으로 재명명하지 않는다.

현재 프레임 게시 CAS는 전체 snapshot/sourceVersion + NPC characterHash이므로 독립 분기
병렬 게시가 충돌할 수 있다. PDS 게시 어댑터는 최신 상태 재조회 후 대상 슬롯/입력 pin 불변을
확인해 게시만 재시도하거나 자산 단위 CAS로 이행해야 한다. 생성 전체 재실행으로 해결 금지.
프레임 기본 `extract-alpha-pixel-half.v1`은 `full` 요청에도 추출+50% 축소+픽셀화가 필수.
BOX/32 colors/alpha128/no-dither/공통 팔레트/첫끝 프레임 정책을 어댑터 계약 테스트로 보존한다.

## 실행과 검증

Python 3.11+ (검증 3.12), 외부 패키지 설치 필요 없음.

```sh
python3.12 -B -m unittest discover -s tests/resident -v
python3.12 -B -m workers.resident animation --check
```

`document`, `image`, `animation`, `postprocess`, `music` 모두 `--check` 제공.
이 검사는 모델·DB·큐·서비스를 시작하지 않는다. **`--check` 없는 시작은 명시적으로 실패한다.**
실제 Request/PDS/Backend 어댑터가 없는 상태에서 테스트 fake를 운영으로 선택할 수 없게 한다.

## 완료/미완료

- 완료: 기존 Git 중복 확인, 별도 worktree, 5역할 명세, 공통 pull 루프, lazy/idle lifecycle,
  lease 갱신, PDS receipt 확인, 임시 정리 상태, unknown 처리의 격리 테스트.
- 미완료: 실제 Request atomic pull/renew/resource/reaper 통합, PDS 다운로드/파일 교체 어댑터,
  5개 실백엔드 어댑터, memory residency 예산, graceful subprocess cancellation proof,
  각 독립 프로세스 entrypoint 활성화와 launchd/OPS supervision, 운영 drain/절체.
- 미실행: 실모델 콘텐츠 생성, 운영 DB 변경, 업로드/배포, 기존 전체 모듈 테스트.
- 해당 없음: 라야 지식 등록. 최신 사용자 결정으로 코드 기반 분배로 대체되었으므로 복원하지 않음.

## 2차 구현: 실제 공식 계약 연결(격리 검증)

### 요청 DB는 Request 서비스만 접근

- `adapters/common_authority.py`: **Request 서비스 측** 어댑터다. 기존 NGS
  `CommonRequests.accept/claim/heartbeat/transition/block_expired` 계약을 재사용한다.
  워커 프로세스가 이 모듈을 import하거나 DB를 열지 않는다.
- `adapters/request_api.py`: 새 내부 pull API의 서버 앞단. 기존 운영에 이미 있는 API가
  아니라 이번 브랜치에서 개발한 미배포 API다. 서버가 인증된 worker ID→role을 고정한다.
- `adapters/request_http.py`: **워커 측** 클라이언트. SQLite·NGS import 없이 API만 호출.
  endpoint 명시 필수, loopback 전용, proxy/redirect 금지, HTTP timeout 최대 5초.
- 공용 요청 DB의 기존 표·이력·ID는 유지한다. 추가 resource grant 표는 현재 테스트의
  메모리 DB에서만 명시적으로 생성했다. 운영 schema migration은 수행하지 않았다.

새 API 계약(POST `/v1/workers/`):
`claim`, `renew`, `complete`, `fail`, `uncertain`, `release`.
서비스가 worker 토큰과 canonical role, grant owner/fence를 검증한다. HTTP의 worker 입력만으로
영상 자원 종류·용량을 바꾸거나 다른 worker grant를 갱신/반납할 수 없다.
인증 토큰 프로비저닝·rotation·실제 서비스 등록은 미구현이며 테스트에는 fixture token만 사용.

### Request 측 자원·만료 안전성

동일 요청 DB transaction에서 claim과 grant를 함께 기록한다. 이 격리 어댑터에서는
`video-global=1`을 두 worker의 동시 pull에도 검증했다. 프레임 자원은 별도라 영상과 함께 claim된다.
만료됐다는 이유만으로 grant를 제거하지 않는다. **Request 서비스의 신뢰할 수 있는 프로세스
감독 어댑터가 종료를 확인한 경우에만** block_expired 및 grant 반납을 허용한다.
`verify_stopped`는 worker JSON의 true 값을 받는 구조가 아니며, 실제 OS 프로세스 종료 확인
어댑터가 제공되어야 한다. 현재 이 감독 연결은 미완료이며 실제 절체를 막는 조건이다.

미래 retry_at 요청을 조기에 claim하지 않으며, inputHash는 공식 payload reference 및 전달된
원래 요청 본문의 canonical hash 모두와 대조한다. 검증된 지원 mapping은 현재
`npc.animation.generate`와 `npc.animation.frames.generate` 두 종류다.
나머지 타입은 실제 도메인 어댑터·계약 테스트를 추가하기 전 지원하지 않는다.

### PDS 실제 파일 API 클라이언트

`adapters/pds_category_files.py`는 기존 운영 코드의 다음 계약과 교차검증했다.

1. GET `/v1/npc-category/file` + 원래 NPC ownership guard/stage ID
2. `X-NPC-File-Basis`의 target/revision/request/stage/SHA 검증
3. 편집된 파일 bytes를 POST `/v1/npc-category/file-replace`
4. `Content-Type: application/octet-stream`, `X-NPC-File-Replace` 메타데이터
5. 동일 operation 재전송 시 동일 write receipt, 다른 target/stage/범위 변경 거절

이는 아직 공통 PDS 포트 전체 구현이 아니다. scratch registry/임시 lease 정리, ownership
admit/finish, 최종 publication 검증은 다음 연결 대상이다. **write receipt를 verified publication
receipt로 위장하지 않는다.** 기존 파일 크기 2 MiB 제한을 임의 제거하지 않았다.

### 검증 범위와 명령

아래 경로들은 테스트가 읽을 실제 코드 release를 명시한다. 운영 DB나 API를 가리키지 않는다.

```sh
NGS_RUNTIME_SOURCE=/Users/pvenus/Workspaces/NGS/code/releases/runtime-space-0597802/runtime \
PDS_RELEASE_SOURCE=/Users/pvenus/Workspaces/BackendServices/code/releases/backend-f3e61b1 \
python3.12 -B -m unittest discover -s tests/resident -v
```

검증은 기존 CommonRequests를 `:memory:` SQLite에서 실행하고, 실제 PDS Handler와 Filex
writer를 임시 디렉터리·랜덤 loopback 포트에서 실행한다. 기존 서비스 8030/운영 Filex와
실제 NPC를 호출하거나 변경하지 않는다. 41개 테스트 통과(작성 시점).
실제 FrameRequests의 accept 재접수, 공식 DTO/full 값 보존, HTTP worker 실행 경로,
자원 profile 위조, stale fence·lease, 종료 증거 없는 반납, 게시/정리 실패를 포함한다.

heartbeat의 네트워크 호출은 timeout을 가져야 한다. `HttpRequests`는 최대 5초로 제한한다.
임의의 다른 Requests 어댑터가 무한 대기하면 runtime의 join도 기다릴 수 있으므로 이러한
어댑터는 허용하지 않는다. OS 프로세스 취소/종료 및 모델 unload 증거 검증은 별도로 남아 있다.

## 3차: 첫 실제 CPU 처리 backend

`backends/frame_postprocess.py`는 정식 frame claim의 PDS-materialized 입력을 검증하고
실제 Pillow 후처리를 수행한다. 기존 배포본의 순수 `frame_requests/processing.py`를
`backends/frame_processing.py`로 그대로 이관했다. NGS cwd·동적 local.services import가 없다.
8방향/프레임 수/SHA/path/요청·attempt·revision을 검사하고 항상 50% 축소+32색 공통 팔레트+
alpha128/no dither를 적용한다. `full`은 축소를 생략하는 옵션으로 해석하지 않는다.

이 backend는 **이미 alpha 처리된 PDS 프레임 후처리 단계**다. 영상에서 원본 프레임 추출,
배경 segmentation, 실제 PDS frame 게시/cleanup 어댑터는 아직 연결하지 않았다.
합성 입력의 CPU 처리 검증을 실제 NPC/영상 생성 완료로 간주하지 않는다.

### 핵심 HTTP 회복·입력 불변식 보완

claim API는 worker-scoped `claimKey`를 요구한다. worker는 외부 runtime 위치의 지정 journal에
key를 fsync하고, Request 서비스는 같은 key에 같은 claim을 반환한다. commit 직후 HTTP 응답을
끊는 테스트와 client 재시작 테스트에서 추가 fence/grant 없이 같은 실행권을 회수했다.
발급 claim 전체를 서비스 ledger에 보관하여 input_refs/parameters/expected_revision 변조를 거절한다.
resource 반납은 검증된 종료 후 tombstone으로 기록해 재전송에 멱등이며, 완료된 claim은 재실행하지 않는다.
현재 46개 테스트 통과(실제 CPU backend vertical 포함). worker 단일 인스턴스/프로세스 감독의
운영 등록과 real PDS frame publication은 여전히 미완료다.

CPU backend vertical 검증 명령은 위 명령의 Python을 기존 Pillow 환경
`Workers/runtime/frame-alpha/.venv/bin/python`으로 지정한다. 이 테스트는 공식 FrameRequests
accept와 HTTP pull, 실제 이관된 Pillow 처리 코드를 사용하지만 PDS stage/publication은 합성
fixture다. 8방향×2프레임에 공통 32색·50%·binary alpha와 실제 임시 디렉터리 제거를 확인했다.
`frame_processing.py` 원본 SHA-256:
`180944ec945bb26d56f3dacd8d68fec4eda42f930605836cf036284ebf2c2ce1`.

## Additional isolated integration (2026-11-18)

- PDS half-frame workspace now downloads pinned source PNGs through PDS, verifies hashes, runs the existing 50% common-palette processor, publishes through the real frame publication contract, checks its receipt, then removes only its claim-owned scratch directory. Full video extraction and alpha-model inference are **not** connected by this adapter.
- Request-side model-residency authority is separate from execution grants. Idle models remain charged against host memory. TTL expiry alone does not release capacity. The optional runtime port acquires before load, renews during idle, cancels on lease loss, and releases only after confirmed unload. Backend process-exit ambiguity retains resources and scratch.
- Service-only publication authorization uses exact ledger-issued Claims. New media publications must prepare a hash-bound reservation before Filex commit, then finish with a durable receipt. An unresolved reservation blocks failure transition and execution-grant release even after lease expiry. Replays may only use the same publication hash and receipt; no regeneration or force release.
- These are development APIs in the isolated worktree. They do not provision service credentials, migrate operating databases, start production daemons, or allow the legacy consumer to run concurrently with the new workers.
- Existing PDS frame/category/animation APIs retain their original contracts; attaching the new media reservation protocol does not imply they have already been migrated.
- Actual operating readiness still requires role-specific formal intake/selector and receipt verifier wiring, trusted supervisor stop proofs, resource budget configuration, provider environment bindings, and one aggregate cutover test. A synthetic fixture test is not a real image/video/music generation test.

### Running all opt-in contract tests without hidden skips

Use `sh scripts/test-resident-all.sh` with explicit `WORKERS_TEST_PYTHON` (Pillow-enabled), `NGS_RUNTIME_SOURCE`, `PDS_RELEASE_SOURCE`, and candidate `MEDIA_PDS_SOURCE`. The script enables native/animation source fixtures and runs both `tests/resident` and the separate image/music `tests` directory. Omitting opt-in variables in direct unittest commands intentionally skips those contracts; that is not evidence they passed. The 2026-11-18 all-enabled run passed 145 resident + 28 media tests, zero skipped. This remains isolated testing, not operational or model-output QA.

### Durable completion and automatic reconciliation

Verified PDS publication is checkpointed in RequestService **before** scratch cleanup. Completion acknowledgments may be retried or lost without losing the original receipt/proof. The Request-owned reconciliation loop scans outstanding execution grants, released-but-unresolved publications, and cleanup-pending requests. It obtains trusted stop evidence, validates original/recovery fences, and uses a new official `finalize` lease to finish an existing publication, never to rerun a generator. Idle expired model leases remain charged until actual unload proof permits release.

Unknown process state, network failure, or an ordinary 404 is not proof of non-publication. Those cases remain preserved with backoff. An existing hash-bound prepared/committed reservation may replay the identical stored publication packet through a trusted service-only callback. Unreserved legacy publication absence is not automatically reposted or regenerated. Existing worker consumers must be disabled before activating this service, and real supervisor/PDS proof callbacks are mandatory.
