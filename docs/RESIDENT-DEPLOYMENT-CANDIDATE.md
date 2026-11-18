# 독립 서비스 배포 후보: 활성화 전 점검

이 문서는 **운영 실행 완료 보고가 아니다**. `configs/resident/*.example.json`은
`activationApproved: false`, 검토 필요 경로 및 해시로 제공한다. 가져오기·`--check`는
모델·DB migration·서비스·정식 요청을 실행하지 않는다. 실제 프로필을 채우고 서비스별
격리 검증을 마친 다음 별도 운영 전환을 수행해야 한다. 인증정보는 생성하지 않았다.

## Worker 프로세스

각 역할은 `python -m workers.resident ROLE --config REVIEWED_CONFIG` 독립 프로세스다.
OPS는 프로세스 감독만 하며 요청 선점이나 workflow tick을 실행하지 않는다.

- config의 `factoryPath/factorySha256`: 배포 후보의 `deployment/operator_factory.py`
  절대 real path와 SHA256. 설치한 전체 release 소스도 같은 검토 Git revision으로 고정한다.
- `RESIDENT_BINDINGS_PATH/SHA256`: 검토된 site bindings 파일. 코드가 해시를 확인한 후
  `build_bindings(role, settings)`를 호출한다. 요청 JSON에서 코드 경로나 실행 명령을 받지 않는다.
- `RESIDENT_REQUEST_URL`, 역할별 `RESIDENT_<ROLE>_TOKEN`: 해당 worker ID/role에 발급된
  Request loopback endpoint/token. `settings.workerId`와 최상위 workerId는 같아야 한다.
- `RESIDENT_PDS_URL`: 검토한 PDS API. NPC 문서/영상/프레임은 원래 부모 Session에 이미
  승인된 guard를 Request `/v1/workers/ownership-guard`로 받아 전달한다. 새 pin/admit 금지.
- 이미지/음악은 `RESIDENT_PDS_TOKEN`; 모델 역할은 `RESIDENT_MODEL_URL`, 역할별
  `RESIDENT_<ROLE>_MODEL_TOKEN`. 토큰은 env에서만 읽고 prompt/receipt에 넣지 않는다.
- 문서 Codex는 `RESIDENT_CODEX_BINARY/SHA256` 검증. 이미지 native runner는 기존
  reviewed runner 타입을 pinned_native_runner로 묶는다. 코드/모델 실행 파일·환경·모델 경로는
  운영자가 검토한 binding이며 요청 데이터로 덮어쓸 수 없다.
- journal/scratch/model residency 경로는 역할별 전용 절대경로. `/REVIEW` 값을 실제 운영
  원본 경로로 무작정 치환하지 말고 별도 관리 디렉터리로 지정한다.
- animation_config는 기존 Wan 설정 계약; full_frame_config는 Python/ffmpeg/ffprobe,
  module_root, matting_model, tracking_model 등의 실제 설치 경로. full 모드 요청에 이 설정이
  없으면 활성화 금지. half/full dispatch는 full만 model authority를 획득한다.
- image source_reader는 기존 PortraitSource, 음악 source_reader는 MusicIntake의 검증된
  binding. cleanup은 해당 claim/attempt 소유 scratch만 제거하고 확인하는 실제 callback.

## Request 서비스 연결

기존 NGS CommonRequests가 요청 상태 권위자다. PDS/Filex가 콘텐츠 권위자다.
Request host는 기존 원본 module들을 **검토된 release 경로에서** 불러오고 다음을 조립한다.

1. 기존 CommonRequests, 직렬 transaction lock, 독립 서비스 전용 SQLite 연결.
   다중 thread는 thread별 연결 또는 기존 DB API가 검증한 thread-safe 접근을 사용한다.
2. `build_parent_bridge`: 원래 executor, parent service factory, CategoryDispatch,
   npc_ownership.Session과 npc_ownership 모듈. ParentSessionResolver가 child/parent membership과
   기존 ownership journal을 확인한다. callback으로 `lambda: True`를 넣는 대체는 금지다.
3. `DomainResolvers.selector(validators, ready, enabled)`: 원본 domain payload 및 전체 envelope
   해시를 확인하고 실제 workflow dependency-ready 검증자를 사용한다. 미완료 선행 단계를
   건너뛰고 다른 실행 가능 요청을 고른다. `enabled`는 전환 완료된 exact route만 명시한다.
4. `CommonAuthorityBridge`: 실제 stopped proof, live publication proof, guard resolver 및
   자원 capacity. video-global은 반드시 1. legacy consumer가 같은 route를 가져가지 못하도록
   전환을 적용한 뒤에만 pull API를 제공한다.
5. `build_handler(bridge, identities, document_bridge=..., model_authority=...)`: 역할별 token
   identity map. 외부 전체 바인딩 대신 검토한 loopback host. API/서버 thread 역시 DB 연결 정책 준수.
6. `build_request_loops(...)`로 reconciliation/parent-auto-apply 구성. construction은 inert.
   명시 migration 때 bridge/model/supervisor의 schema initializer를 실행하고, Request 서버의
   시작/종료 lifecycle에서 `with loops:`를 사용한다. OPS/worker에서 직접 tick하지 않는다.

`build_request_loops`의 필수 서비스 전용 ports:

| Port | 실제 구현 책임 |
|---|---|
| service_for_parent | 원래 부모 요청별 새 서비스 객체, original executor/CategoryService/CAS finalizer 유지 |
| inspect_publication | 원래 attempt durable intent와 PDS immutable receipt 비교. worker 만료 lease 재사용 금지 |
| verify_recovery_publication | 원래 grant/claim/request/inputHash와 실제 receipt exact match 검증 |
| cleanup | 해당 attempt만 재구성, 프로세스 stopped 확인 후 scratch cleanup 확인 |
| resume_publication (선택) | 기존 prepared/committed reservation+같은 publicationHash에 한해 PDS replay. 생성 호출 금지 |

문서 recovery는 builder가 `ParentDraftBridge.inspect_recovery`와 별도
`verify_recovery_publication`으로 분기한다. 원래 resource grant의 claim_json과 parent receipt를
대조한다. worker HTTP `read`/live verifier의 lease 검증을 느슨하게 하지 않는다.
영상/half/full은 reservation 신규 생성 대신 원래 intent의 GET receipt 복구만 사용한다.
이미지/음악은 기존 reservation이 있고 stopped proof가 있을 때만 기존 publish를 재개한다.
확인 불가(None)는 미게시 확정이 아니므로 자원/자료를 보존하고 backoff한다.

PDS receipt → Request durable checkpoint → scratch cleanup → complete 순서다.
checkpoint 이후 응답 유실은 저장된 proof를 이용한다. checkpoint 이전은 위의 서비스 전용
검증자를 사용한다. 프로세스 종료 불명확은 모델 재생성/자원 반환 없이 격리한다.
cleanup pending은 원본 attempt를 server에서 계속 관리한다. worker의 메모리 포인터만 해제하여
다른 요청을 처리하며 이전 scratch를 지우거나 재사용하지 않는다.

## 지원 범위 및 원본 자동 반영

- NPC 문서: `npc.workflow / ngs.npc-category-candidate.v1 / npc.category-candidate`,
  단일 overview/art/category-add/category-modify. immutable envelope 그대로 해시 검증.
  worker는 candidate만 제출. 부모 자동 적용 루프가 완료 child receipt를 받아 기존
  finalizer/CAS를 실행하고 sourceApplied+publication.verified를 확인한다. 사용자 추가 선택 없음.
- NPC portrait: `npc.workflow.stage / npc-workflow-portrait-single.v1 / npc.workflow.portrait`.
- 영상 `npc.animation.generate`, 프레임 `npc.animation.frames.generate`: 정확한 schema/domain
  tuple은 domain_resolvers 상수와 기존 intake 등록을 대조. full은 8방향 원본+축소 필수.
- 음악 `audio.music.generate / audio-music-hq.v1 / audio.music`.
- capability 목록은 지원 route가 아니다. document.create/image.edit 및 임의 legacy.pipeline,
  비교 다중안은 selector 등록 없으면 실행되지 않는다.
- 마스터 컨셉은 기존 v3 single-auto-apply 경로다. NPC parent 루프에 포함된 것으로 보고하지 않는다.
  전환하려면 별도 실제 intake/envelope/PDS 계약 검증이 필요하다.

## 전환 차단 조건

검토된 source bindings, 실제 stop/receipt/ready 검증자, PDS 권한 전달, role token, 모델 설치,
DB thread 정책 중 하나라도 없으면 활성화하지 않는다. 예제 bindings는 의도적으로 오류를 낸다.
기존 OPS consumer의 중복 route ownership을 먼저 차단하고 활성 claim/부모 workflow를 정리한다.
과거 실행 기록을 새 실행처럼 다시 선점하지 않는다. 전환 rollback 때도 두 consumer를 동시에
켜지 않는다. 원본 서비스 수정·재시작·운영 모델 호출은 이 후보 작성에서 수행하지 않았다.
