# 캐릭터 포트레이트·애니메이션 요청 스키마 v1

GPT 작업은 두 지점에서 끝납니다. 캐릭터당 한 번 투명 PNG 포트레이트를 생성하고 포트레이트 등록 요청 파일을 작성합니다. 새 동작마다 영상용 기획·프롬프트·모델 옵션을 정해 애니메이션 요청 파일을 작성합니다. 영상 생성, 상태 갱신, 목록 노출은 VideoForge 파일 워커가 처리합니다. MCP 호출은 필요하지 않습니다.

## 경로와 키

기본 경로는 `<VIDEOFORGE_WORKSPACE>/data/characters`입니다. `VIDEOFORGE_CHARACTER_ROOT`로 변경할 수 있습니다.

- 생성한 PNG는 `intake/<portrait_key>.png`에 둡니다. 실제 투명 영역이 있는 RGBA/LA PNG만 받습니다.
- 포트레이트 요청은 `portrait-requests/<portrait_key>.json`, 등록 결과는 `portraits/<portrait_key>.json`, 관리 자산은 `portrait-assets/<portrait_key>.png`입니다. 같은 포트레이트 키의 재등록은 허용하지 않습니다.
- 애니메이션 요청은 `animation-requests/<animation_key>.json`, 처리 결과는 `animations/<animation_key>.json`입니다. 각 요청에 **기존 `portrait_key`가 필수**입니다. 같은 포트레이트로 동작은 여러 번 요청할 수 있으며 각각 다른 `animation_key`를 사용합니다.
- 키 형식은 소문자 영문자로 시작하는 2~64자의 소문자·숫자·`_`·`-`입니다. 요청 파일을 만든 뒤 수정하지 말고 새 키로 새 요청을 작성합니다.

## 포트레이트 JSON

```json
{
  "schema_version": 1,
  "portrait_key": "hero_arin",
  "title": "아린",
  "planning": "게임 내 대화와 동작 영상에 공통으로 쓰는 정면 전신 포트레이트",
  "prompt": "Full-body character portrait, neutral pose, isolated subject, transparent background...",
  "source_png": "<VIDEOFORGE_WORKSPACE>/data/characters/intake/hero_arin.png",
  "rights_attested": true,
  "created_at": "2026-09-18T00:00:00+00:00"
}
```

`rights_attested`는 해당 이미지의 영상 생성 사용 권한을 확인했다는 뜻입니다. CLI는 `--rights-attested`를 요구합니다.

## 애니메이션 JSON

```json
{
  "schema_version": 1,
  "animation_key": "hero_arin_idle_01",
  "portrait_key": "hero_arin",
  "title": "아린 대기 동작",
  "planning": "대화 장면에서 자연스럽게 호흡하고 눈을 깜박인다. 원본 디자인과 위치를 유지한다.",
  "prompt": "The character breathes gently and blinks once, camera locked, stable composition...",
  "negative_prompt": "camera movement, scene cut, extra limbs, background",
  "model_options": {
    "profile_id": "ltx-video-2b-0.9.8-distilled-bf16-i2v",
    "width": 320, "height": 576, "frames": 33, "fps": 8,
    "steps": 8, "seed": 42, "timeout_seconds": 1800
  },
  "created_at": "2026-09-18T00:00:00+00:00"
}
```

현재 프로필 최대값은 320×576, 33프레임, 8 FPS, 1800초이며 너비·높이는 8의 배수입니다. 모델 파일이 없으면 `waiting_for_model`로 유지하고 설치 후 워커가 자동으로 제출합니다. 큐와 모델 실행기는 기존 VideoForge I2V 경로를 사용합니다.

## CLI

```sh
cd <VIDEOFORGE_WORKSPACE>
.venv/bin/python -B -m videoforge_mcp.character_workflow portrait \
  --portrait-key hero_arin --title '아린' \
  --planning '정면 전신, 모든 동작의 기준 이미지' \
  --prompt 'Full-body character portrait, transparent background...' \
  --source-png data/characters/intake/hero_arin.png --rights-attested

.venv/bin/python -B -m videoforge_mcp.character_workflow animation \
  --animation-key hero_arin_idle_01 --portrait-key hero_arin \
  --title '아린 대기 동작' --planning '호흡과 눈 깜박임, 고정 카메라' \
  --prompt 'Gentle breathing and one blink, locked camera...' \
  --negative-prompt 'camera movement, extra limbs'
```

두 명령 모두 **요청 JSON만 작성**합니다. 서비스는 `.venv/bin/python -B -m videoforge_mcp.character_workflow loop --interval 30`으로 30초마다 요청을 읽고, 기존 I2V 워커는 `.venv/bin/videoforge-i2v-worker`로 실행합니다. 후자는 `VIDEOFORGE_PROFILE_PATH`, `VIDEOFORGE_INPUT_ROOT`, `VIDEOFORGE_I2V_MODEL_DIR`을 설정해야 합니다. `VIDEOFORGE_INPUT_ROOT`는 `data/characters` 경로입니다.

웹 화면은 Forge 작업실의 **캐릭터 애니메이션** 탭입니다. 포트레이트 목록 → 포트레이트 상세 팝업의 동작 목록 → 영상 중심의 동작 팝업 → **상세 정보 보기** 순서로 기획·프롬프트·옵션을 확인합니다. API는 `/vf/api/characters/portraits` 아래에 있습니다.

웹 화면에서 투명 PNG와 생성 프롬프트로 포트레이트를 등록하고, 포트레이트 상세 창에서 영상 생성 프롬프트로 동작을 요청할 수 있습니다. 두 요청 모두 기존 파일 워커 큐에 파일을 등록하며 `202`를 반환합니다.

- `GET /vf/api/characters/schemas` — 포트레이트 업로드와 애니메이션 요청의 입력 스키마 및 모델 옵션 기본값/제한.
- `POST /vf/api/characters/portrait-requests` — `multipart/form-data`: `portrait_key`, `title`, `planning`, `prompt`, `png`, `rights_attested=true`.
- `POST /vf/api/characters/animation-requests` — JSON: `animation_key`, 기존 `portrait_key`, `title`, `planning`, `prompt`, 선택 항목 `negative_prompt`, `model_options`.

포트레이트는 실제 투명 영역이 있는 PNG만 등록됩니다. 동작 요청에는 등록 완료된 포트레이트가 필요합니다. 브라우저 요청은 Forge 작업실의 동일 출처 프록시를 통해 접수됩니다.

macOS 사용자 서비스 정의는 `deploy/com.projectbs.character-file-worker.plist`, `deploy/com.projectbs.videoforge-i2v-worker.plist`, `deploy/com.projectbs.videoforge-http.plist`와 Forge 프론트의 `deploy/com.projectbs.forge-frontend.plist`입니다. 현재 사용자 LaunchAgents에 등록되어 있습니다. 영상 모델 파일 `models/ltx-video-i2v/ltxv-2b-0.9.8-distilled.safetensors`는 아직 없으므로 실제 영상 결과 검증은 모델 설치 이후 가능합니다.
