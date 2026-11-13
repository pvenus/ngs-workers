# BGM 생성 요청 파일 스키마 · v1

GPT는 게임 장면과 기획 근거를 검토해 **프롬프트와 옵션만 확정**합니다. 생성·리뷰 등록·모델 분석은 AudioForge의 파일 워커가 수행합니다. 요청은 `data/bgm-requests/<request_id>.json`에 원자적으로 기록합니다. 파일을 만든 뒤 내용을 수정하지 말고 새 요청을 만드세요.

## 필수 입력

| CLI 인자 | JSON 필드 | 설명 |
| --- | --- | --- |
| `--title` | `title` | 리뷰 화면에 표시할 곡명 |
| `--use-case` | `use_case`, `brief.use` | 사용 장면의 역할 |
| `--intent` | `brief.intent` | 음악이 해야 할 일과 기획 근거 |
| `--scene` | `brief.scene` | 장면·서사 맥락 |
| `--mood` | `brief.mood` | 원하는 정서와 강도 |
| `--prompt` | `prompt` | 모델에 그대로 전달할 최종 영문 프롬프트 |

## 선택 입력과 제약

| CLI 인자 | 기본값 | 범위 |
| --- | --- | --- |
| `--duration` | `30`초 | 0.25~30초 |
| `--seed` | `42` | 0~2147483647 |
| `--model` | `acestep-v15-turbo` | 현재 AudioForge ACE 백엔드가 지원하는 모델 |
| `--bpm` | 생략 | ACE 백엔드 사용 시 정수 30~300 |
| `--prompt-version` | `1` | 프롬프트 개정 식별자 |
| `--tag` | 생략 | 음원 목록 해시태그. 여러 개면 반복 지정 (`--tag 타이틀 --tag 잔잔함`) |

`schema_version`, `request_id`, `created_at`은 CLI가 생성합니다. JSON은 `BgmRequest`로 검증하며 알 수 없는 필드는 거부됩니다. 요청 파일명과 `request_id`는 같아야 합니다. GPT가 실제 소리의 품질이나 사용자 최종 승인을 미리 단정하지 않습니다.

## 요청 작성 예

```sh
# Workers 저장소 루트에서 실행. 승인된 요청에만 사용합니다.
export AUDIOFORGE_DATA_DIR="$HOME/Workspaces/Workers/data/audioforge"
.venvs/audioforge/bin/python -m audioforge.file_workflow create \
  --title 'Stage BGM concept' \
  --use-case 'quiet stage exploration' \
  --intent 'Support long exploration without masking game cues' \
  --scene 'The player crosses the village outskirts after a difficult event' \
  --mood 'restrained, reflective, gently hopeful' \
  --prompt 'Instrumental game background music, restrained and reflective, soft evolving texture, gentle pulse, spacious mix, no vocals, no abrupt climax.' \
  --duration 30 --seed 42 --bpm 80 --tag 스테이지 --tag 탐험
```

명령은 **요청 파일만 작성**합니다. 워커가 30초마다 파일을 읽어 생성 API에 제출합니다. 상태는 `data/bgm-request-state/<request_id>.json`에서 확인합니다. `phase`가 `done`이면 `song_id`, `artifact_id`, `review_status`가 기록됩니다. `review_status=partial`은 모델 분석이 완결되지 않았음을 뜻하며 사람의 청취·최종 승인을 대신하지 않습니다.

## 웹/API 요청

AudioForge의 `/review` 화면에서 곡 이름과 생성 프롬프트를 입력하면 같은 파일 워커 큐에 요청합니다. 사용 장면, 정서, 길이, 시드, BPM, 해시태그는 상세 옵션에서 입력합니다.

- `GET /v1/bgm-requests/schema` — 브라우저 입력용 JSON Schema.
- `POST /v1/bgm-requests` — JSON 요청을 검증하고 요청 파일만 생성. `202` 응답의 `request_id`를 보관합니다.
- `GET /v1/bgm-requests/{request_id}` — 파일 워커 처리 상태.

브라우저의 생성 요청에는 같은 출처의 `Origin`과 `X-Library-Action: 1` 헤더가 필요합니다. 원격 접속은 AudioForge 게이트웨이의 인증을 거칩니다.
