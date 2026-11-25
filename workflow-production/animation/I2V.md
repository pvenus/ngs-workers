# 단일 이미지 기반 애니메이션 (I2V)

현 승인 공급자는 **Wan2.2 I2V-A14B Q4_K_M만**입니다. 시네마틱과 프랍 모두
같은 상주 애니메이션 워커의 같은 I2V 선택을 사용하고, 별도 옵션으로 활성화된 독립
`animation.i2v` 요청도 동일 모델을 사용합니다. 기존 NPC의 Wan Animate는 별도의
동작 프리셋 경로로 그대로 유지합니다.

## 설정·고정된 자산

- 기본 소유자 전용 바인딩 파일은 `~/Workspaces/Workers/runtime/prop-animation/i2v-a14b-binding.json`입니다 (부모 0700, 파일 0600).
- 운영자가 다른 승인 경로가 필요한 경우에만 `WORKFLOW_I2V_A14B_BINDING`에 절대 경로를 둡니다. 단독 `animation.i2v` 경로는 이 명시적 바인딩이 있고 해당 PDS 계약이 지원되는 경우에만 활성화됩니다. 기존 5B 바인딩은 읽지 않으며 다른 모델로 대체하지 않습니다.
- 구조: `{schemaVersion:"ngs.animation-i2v-a14b-q4km-binding.v1",comfyRoot,python,ggufNodesRoot,modelsRoot}`.
  사용자 요청은 경로·모델·LoRA·프레임 설정을 지정할 수 없습니다.
- 모든 자산은 정규화된 소유자 경로, 고정 파일명 및 정확한 SHA-256으로 검증합니다.
  두 전문가 파일 모두 GGUF v3 헤더가 필요합니다. 한 파일이라도 빠지거나 바이트가 다르면 준비 표시는 발행되지 않으며 모델은 시작하지 않습니다.

| `modelsRoot` 아래 승인 파일 | SHA-256 |
| --- | --- |
| [diffusion_models/Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf](https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF/blob/3410ece6272f4db4fcc2f8fbb38bd50add5b3c5e/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf) | `836250abfaa3411694e2c9cf3a0cc18265329d5156d81aa116d5366b0f8f02e7` |
| [diffusion_models/Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf](https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF/blob/main/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf) | `e2f98d834af009d035c6b0918268f2eba0aa8a63025ce942277e2384d40b0866` |
| text_encoders/umt5_xxl_fp16.safetensors (기존 검증본 재사용) | `7b8850f1961e1cf8a77cca4c964a358d303f490833c6c087d0cff4b2f99db2af` |
| [vae/wan_2.1_vae.safetensors](https://huggingface.co/Comfy-Org/Wan-2.2/blob/main/split_files/vae/wan_2.1_vae.safetensors) | `2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b` |

전문가 2개는 합계 약 19.30GB이며, FP16 텍스트 인코더 및 VAE는 별도입니다.
GGUF 정수 양자화는 가중치 저장 형식이며, 전체 GPU/CPU 메모리 사용량이나
추론 속도를 보장하지 않습니다. Wan 2.1 VAE를 기존 5B용 Wan 2.2 VAE로
대체하면 안 됩니다. 기존 전용 Python/GGUF 노드 환경 및 모델 무결성 검사를
재사용하지만, 워커는 런타임에 의존성이나 모델을 다운로드하지 않습니다.

## 그래프와 독점 슬롯

[Comfy-Org 14B 공식 I2V 템플릿](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_i2v.json)
(검토한 JSON blob `fa67e98001b457769cd00d567c08c6aabe745e86`)의
non-turbo 경로를 따릅니다. `WanImageToVideo`로 입력 PNG를 컨디셔닝하고,
shift 5, CFG 3.5의 Euler/simple 20-step 스케줄을 0–10(HighNoise)과
10–20(LowNoise)으로 잇습니다. 뒤 단계에서 새 노이즈를 만들지 않습니다.
출력 프레임은 32픽셀 단위 128–512 크기의 전체 RGB 장면 45장입니다. 출력을
저장하는 워크플로는 기존 24fps (1.875초)를 유지합니다. 공식 템플릿의 16fps
표시 속도와 같다고 가정하지 말고 실제 영상의 모션 속도를 확인해야 합니다.

기존 Animate 엔진 그룹 부재 및 helper 종료를 확인한 뒤에만 I2V를 시작하고,
I2V 엔진 그룹 종료 확인 뒤에만 다음 모델을 허용합니다. 증명이 불확실하면
어느 모델도 시작하지 않습니다. 모델을 별도로 강제 실행하거나 다른 서비스와
동시에 실험하는 스크립트는 포함하지 않습니다.

프랍과 시네마틱의 준비 파일은 각각 `prop-animation-ready.json`,
`cinematic-animation-ready.json`입니다. 두 파일은
`provider:"wan-i2v-a14b-q4_k_m"`을 쓰고 독립 schema를 유지합니다.
시네마틱은 ffmpeg/ffprobe도 확인합니다. 30초 갱신, PDS 120초 유효 기간입니다.
A14B는 Worker와 PDS/RS/Planner의 동일 계약을 함께 준비한 뒤에만 전환합니다.

## 산출물

- **시네마틱**: `cinematic.sequence.generate` → 단일 `cinematic_sequence_render` →
  모델에서 나온 PNG 원본 프레임과 재생 메타데이터. 배경 제거·재인코딩 없음.
  기존 `cinematic.generate` MP4는 구 요청 호환 경로입니다. [계약과 검증](./CUTSCENE-I2V.md).
- **프랍 모션**: 정식 `prop.animation.generate.raw` → PDS에 원본 RGB →
  해당 프랍의 별도 후처리/패키지 워크플로. 기존 30M decoded-pixel 예산에
  맞춰 필요한 경우 원본 프레임 일부를 균등 선택하여 길이와 첫/끝을 보존합니다.
- **별도 `animation.i2v`**: `ngs.animation-i2v.v1` 요청의 해시/pin을
  PDS `/v1/animations/ownership/workflow-i2v`에서 검증하고 RGB 묶음을
  게시합니다. 파일명이나 사용자 URL만 받아 직접 읽지 않습니다.

워커는 산출물과 접수 claim이 정확히 일치하는 PDS 확인을 받은 뒤에만 DB 완료를
기록합니다. 게시 결과나 모델 프로세스 종료가 불명확할 때 생성 전체를 자동 반복하지
않습니다.

## 프랍 기본 크로마키 입력

정식 프랍 생성·재생성의 원본 이미지 단계는 1536×1024 RGB를 요청합니다.
프랍 바깥은 단색 녹색 `#00FF00`, 프랍 위는 여백, 그림자와 바닥은 없습니다.
RGB는 기존 PDS 단계에 저장하며 투명 정적 결과는 후처리에서 만듭니다.
예전 이미지나 다른 작업 종류는 다시 생성하지 않습니다.

기본 프랍 모션 전처리는 해당 요청이 지정한 정확한 정적 원본을 PDS에서 읽고
별도의 녹색 RGB PNG를 만듭니다. 모델과 같은 512×352에서 원본 비율을 유지하고,
원본 전체를 크롭 없이 78% 안에 맞춘 뒤 가운데로 정렬하고 바닥에서 12px 위에
배치합니다. 빈 캔버스 또는 기존 입력의 투명 영역에만 #00FF00이 들어갑니다.
기존 검정색을 키로 해석하거나 생성 결과를 배경 제거하지 않습니다.

```sh
# 기존 Wan 전용 가상환경의 Pillow가 설치된 Python 사용
python prop-i2v-input-framing.py --preset prop-green-headroom-v1 \
  --source existing-green-still.png --output new-framed-still.png
python -m unittest test_prop_i2v_input_framing.py
```

출력 JSON에 양쪽 SHA-256과 실제 배치 좌표를 기록합니다. 이미 있는 파일은
덮어쓰지 않습니다. `prop.animation.generate`의 요청 형식에는 새 옵션이
없습니다. 기존 촛불 전용 명시 프로필 요청은 기존 그대로 처리합니다.
승인된 원본 바이트 해시와 요청 해시를 확인하고 작업의 임시 경로 안에서만
전처리합니다. 고정 녹색, 고정 카메라와 요청된 모션만 모델에 전달합니다.
A14B가 렌더링한 45프레임은 게시할 상세 캔버스가 1536×1024일 때 기존 30M
디코딩 픽셀 예산에 따라 첫·끝을 포함한 19프레임으로 추려 1.875초를 유지합니다.
색 제거와 픽셀 변환은 별도 후처리 단계이며 시네마틱·NPC는 영향을 받지 않습니다.
