# 단일 이미지 기반 애니메이션 (I2V)

이 단계는 동작 프리셋 기반 NPC 영상 경로와 별개입니다. 영상 합성기는 PNG 한 장과 동작 지시를 받아 고정 카메라의 짧은 결과 한 개를 만듭니다. 경로, 실행 명령, 모델 이름, 영상 길이, 해상도, 후보 개수는 요청하지 않습니다.

## 워크플로 연결

- 승인된 요청: `animation.i2v` / `ngs.animation-i2v.v1`
- 본문: `{schemaVersion,projectId:"binari",sourceRef:{assetId,sha256},motionPrompt}`
- 소스: `assetId === sha256 + ".png"`. 해시가 같아도 PDS가 해당 프로젝트에서 읽을 수 있다고 확인한 PNG만 허용합니다.
- 단계: `animation_i2v`, 타입 `animation.i2v`, 워커 `animation`. 단계 ID는 공식 요청과 키에 묶이며 이 버전의 독립 요청에서 선행 단계는 없습니다.
- PDS: `/v1/animations/ownership/workflow-i2v` 활성 기능 확인, `/read` → 정식 작업 클레임에 한정한 자산 연결, `/asset` → 원본 PNG, 같은 루트 POST → 검증한 프레임 묶음 게시. 워커는 PDS를 우회해 Filex를 열지 않습니다.
- 최종 `result_ref`: `binari/animations/i2v/<officialRequestId>/manifest.json`. 정확한 저장 응답을 받은 경우에만 단계가 완료됩니다. POST 이후 응답 또는 DB 확정이 불명확하면 이미 게시됐을 수 있으므로 자동 실패/생성 재실행으로 바꾸지 않습니다.
- 조합하려면 후속 단계에서 이 정확한 단계 ID의 완료 및 해당 결과 레퍼런스를 선행 조건으로, PDS에서 같은 요청의 매니페스트/해시를 확인합니다. 폴더명·임의 URL만 입력받지 않으며, 현재 독립 요청 외 다른 루트 계약은 해당 그래프에 등록할 때 추가합니다. 공식 단계 선택은 이미 운영 중인 코드 기반 Planner를 따릅니다. 라야 등록이나 호출을 다시 넣지 않습니다.

## 모델 전환과 안전

- `WORKFLOW_I2V_BINDING` 미설정이면 기존 Wan/NPC 실행만 작동합니다. 설정을 못 읽거나 PDS/설치 사전 점검이 실패하면 I2V는 클레임 전 대기합니다.
- 같은 resident animation worker에서 순차 실행합니다. Wan이 남아 있으면 즉시 정식 unload 명령을 요청해 **엔진 그룹 부재 + helper 종료**를 확인한 뒤에만 Wan2.2 TI2V-5B Q8_0를 시작합니다. I2V는 작업당 소유 프로세스 그룹으로 켜고 작업을 끝낼 때 해당 그룹을 종료/확인합니다. 프로세스 종료를 증명할 수 없으면 이 워커는 어느 모델도 다시 시작하지 않습니다.
- 인수한 모델이 없는 경우가 명확하거나 모든 소유 프로세스가 내려간 실행 전/중 오류는 일반 태스크 실패로 기록합니다. 종료 미확정은 작업을 `running`으로 보존해 다시 실행되는 것을 막습니다.
- 보장은 이 워커의 생존 세션과 자신이 소유한 프로세스 그룹에 한정됩니다. 외부 수동 모델을 관리하거나 중지하지 않습니다. 런처 전체 강제 종료/재시작으로 소유 증거가 사라지면 기존 프로세스가 없는지 운영자가 확인해야 합니다.
- 로컬 Mac에서 현재 사용하던 Comfy 자체와 UMT5 FP16 **텍스트 인코더**는 확인했습니다. 후보의 영상 생성 백본은 명시적으로 Q8_0 정수 양자화 GGUF 하나만 쓰며, BF16/FP8 영상 모델로 대체하지 않습니다. GGUF은 저장 가중치를 정수 양자화하는 것이며 Apple GPU가 모든 연산에서 INT8 커널을 쓴다는 뜻은 아닙니다.
- [실제 Q8_0 후보](https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF/blob/57437632ddd08bdcbd1508c866aa22e126ed51d2/Wan2.2-TI2V-5B-Q8_0.gguf), [Wan2.2 VAE](https://huggingface.co/Comfy-Org/Wan-2.2/blob/main/split_files/vae/wan2.2_vae.safetensors), [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)가 해당 Mac에 설치되지 않아 실제 추론은 실행하지 않았습니다. 신뢰된 경로/체크포인트 해시·GGUF 헤더 및 필요한 그래프 노드를 확인하기 전에는 렌더할 수 없습니다.
- [공식 Comfy의 TI2V 그래프](https://docs.comfy.org/tutorials/video/wan/wan2_2)를 사용하되, 고정 Q8_0 로더와 실제 PNG 이미지 조건을 연결하고 24 fps × 45프레임(1.875초), 긴 변 512px, RGB PNG 직출로 제한합니다. 원본 비율은 32px 격자/짧은 변 128px 이내에서 근사합니다.
- 예시 내부 설정 형태: `{schemaVersion:"ngs.animation-i2v-int8-binding.v1",comfyRoot,python,ggufNodesRoot,modelsRoot}`. `modelsRoot`는 `diffusion_models`, `text_encoders`, `vae` 하위 폴더를 사용합니다. 모델/로더는 운영자가 공급해야 하며 이 코드가 설치 또는 다운로드하지 않습니다.

## 알파/미리보기

- 원본 PNG의 알파는 영상 모델의 네이티브 출력 채널이 아닙니다. 본 단계는 디코딩한 8비트 RGB PNG들을 시간순으로 게시하고 매니페스트에 `alpha:"none"`으로 기록합니다. PNG라는 이유로 투명 결과로 표시하지 않습니다.
- 투명이 필요하면 `animation_i2v` 결과를 입력으로 받은 독립 후처리 단계에서 관심영역/초기 마스크, 전경 분리, 프레임별 투명 영역 및 시간적 안정성을 검증하고 RGBA 결과를 게시합니다. 광원/불꽃만 들어 있는 검정 배경 효과에는 기존 luma 모드를 검토할 수 있지만, 촛대+불꽃 전체를 luma로 자르면 어두운 금속도 손실되므로 동일 공정으로 취급하지 않습니다.
- 기존 NPC 후처리 API는 NPC·포즈·방향 계약에 묶여 직접 호출할 수 없습니다. 검증된 RGBA 결과가 없으면 원본 투명 정지 이미지를 퍼즐용 대체로 유지합니다.

## 소품 파이프라인 연결 검증

`propI2VRGBStage`는 승인된 `prop_motion` 작업의 RGB 프레임을
`/v1/props/ownership/workflow-stage`용 매니페스트와 원본 바이트로 변환합니다.
24fps × 45프레임은 누적 반올림으로 정확히 1,875ms가 되며, 재인코딩하지
않습니다. 실제 PDS의 순서·해시·RGB 검증을 합성 프레임으로 통과했습니다.
변환기만으로는 소품 Animation 워커를 시작하거나 요청을 실행하지 않습니다.

공급할 독립 런타임의 고정 소스:

- [QuantStack Q8_0](https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF/blob/57437632ddd08bdcbd1508c866aa22e126ed51d2/Wan2.2-TI2V-5B-Q8_0.gguf):
  `57bece983817ab2f957546683bb670f13be7d99022d45674840cd999a050ea8f`,
  5,400,179,040 bytes.
- [Comfy-Org Wan2.2 VAE](https://huggingface.co/Comfy-Org/Wan-2.2/blob/8441d066add15eae8d84f42aa6d9c45417973ce6/split_files/vae/wan2.2_vae.safetensors):
  `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156`.
- [city96 ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF/commit/6ea2651e7df66d7585f6ffee804b20e92fb38b8a):
  고정 커밋 `6ea2651e7df66d7585f6ffee804b20e92fb38b8a`.
- 기존 Comfy 런타임 Python을 바꾸지 않고 별도의 Python 환경에
  [gguf 0.19.0](https://pypi.org/project/gguf/0.19.0/#files),
  [protobuf 6.33.5](https://pypi.org/project/protobuf/6.33.5/#files)와
  기존 설치에서 검증된 `sentencepiece`를 준비합니다.

가중치나 Python 의존성이 공급되지 않으면 I2V 기능을 켜지 않습니다.
소품의 정적 작업은 이 제한과 독립입니다.

## Official prop animation append

The existing animation resident alone claims the official `prop.animation.generate.raw`
stage. This requires both a healthy private Q8_0 runtime and the PDS
`append-animation` admission signal. Nothing starts on a separate service or old
model. An already-resident Animate session must provide joined shutdown before
Wan TI2V input is sent, and the TI2V process group must be absent before Animate
resumes. Only the exact immutable PDS detail PNG and formal prompt enter the
renderer; the raw result has no alpha. The two ordinary Postprocess tasks create
same-timing transparent detail/pixel APNGs before final Filex publication; a
committed raw stage can finish these downstream tasks after the renderer lease
expires, since no generation is needed.

The private installed binding defaults to
`~/Workspaces/Workers/runtime/prop-animation/i2v-binding.json`. The runtime
publishes a short-lived, private readiness status
`prop-animation-ready.json` in the same directory only after a file/header/hash
probe, never as evidence of actual inference. It removes that readiness after an
unknown model stop or ordinary shutdown. No FP8/BF16 diffusion fallback.

The model's native output is 45 RGB frames at 24 fps (1.875 seconds). When the
pinned detail canvas would exceed the existing per-clip 30 million decoded pixel
budget, the stage selects evenly spaced exact rendered frames, including both
endpoints. It maintains the common 1,875 ms duration at both original lane sizes
without modifying the base stills, increasing budgets or simulating frames.
E.g. a 1536×1024 detail uses 19 frames.

Verified by the opt-in scratch backend fixture
`prop-resident-chain.fixture.py WorkerChain.test_actual_worker_append_preserves_original_and_publishes_both_timed_lanes`.
This uses real claim-bound worker ports, PDS, Filex stores and Postprocess APNG
encoding/byte checks, with the actual render and segmentation boundaries mocked.
