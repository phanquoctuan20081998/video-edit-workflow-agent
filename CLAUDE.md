# CLAUDE.md — Explainer Video Agent System

Tài liệu context cho coding agent và người phát triển. Đọc trước khi viết code.
Mục tiêu: hệ thống agent tự động dựng video giải thích **thuật toán, paper, vật lý/toán học**, với hình minh họa giới hạn ở **toán học, hình học, biểu đồ** (render bằng Manim).

---

## 1. Tổng quan

Pipeline 6 giai đoạn, có human-in-the-loop (HITL) ở 2 checkpoint đầu:

1. **Market search** — tìm chủ đề trending, lọc theo khả năng visualize → người dùng duyệt.
2. **Script** — từ chủ đề đã duyệt, research rồi sinh ra `VideoSpec` (không phải prose) → người dùng duyệt.
3. **Manim codegen** — sinh code Python (Manim) cho từng scene, chạy trong sandbox, tự sửa lỗi, QA bằng vision model.
4. **Voiceover** — TTS từng scene, lấy duration + word-level timestamps. **Chạy TRƯỚC edit.**
5. **Composite + subtitle** — ghép clip Manim + (B-roll stock optional) theo timeline drive bởi duration voiceover, render phụ đề.
6. **Render** — mux video + audio + phụ đề + nhạc nền, encode.

### Nguyên tắc phân chia quan trọng nhất
Chỉ **3 chỗ** là agentic thật sự: market search, script research, và vòng self-repair của Manim. Bốn việc còn lại (fetch stock, ghép timeline, mux, encode) phải **deterministic**. **Không bao giờ để LLM điều khiển ffmpeg/MoviePy từng lệnh** — vừa đắt vừa không reproducible.

---

## 2. Quyết định kiến trúc cốt lõi (KHÔNG vi phạm)

| # | Quyết định | Lý do |
|---|-----------|-------|
| D1 | **Voiceover sinh TRƯỚC edit** | Độ dài narration quyết định mỗi scene hiển thị bao lâu. Không biết duration thì không finalize được cut. Thứ tự sai → lệch tiếng/hình. |
| D2 | **`VideoSpec` (JSON có cấu trúc) là backbone**, không phải script prose | Mọi stage sau đọc/ghi field của mình trên cùng một artifact. Script agent xuất prose thuần → các stage phải đoán lại cấu trúc. |
| D3 | **Manim code chạy trong sandbox Docker, không network**, giới hạn CPU/RAM/timeout | Đang exec code do LLM sinh. Bắt buộc cô lập. |
| D4 | **Edit/composite/render là deterministic** (MoviePy + ffmpeg) | LLM chỉ *chọn* tài nguyên/quyết định cut, code thuần *thực thi* việc dựng. |
| D5 | **Cache theo content-hash** | Sửa 1 scene chỉ render lại scene đó. Manim render rất đắt. |
| D6 | **Trừu tượng hóa LLM/TTS/stock sau interface + config** | Đổi provider không phải sửa pipeline. |
| D7 | **Artifact tham chiếu bằng path, không nhồi binary vào state/JSON** | State (Postgres) nhỏ gọn; binary ở object storage / local fs. |

---

## 3. Tái dùng từ MoneyPrinterTurbo (MPT)

MPT (`github.com/harry0703/MoneyPrinterTurbo`) làm **rất tốt nửa sau** của pipeline nhưng **không phải agent system** và **không có gì cho Manim**. Chiến lược: mượn media layer, tự xây nửa trước agentic, coi clip Manim như "material" đầu vào cho compositor của MPT.

**Tái dùng được (stage 4–6 + cross-cutting):**
- TTS + subtitle timing: hai chế độ — `edge` (timestamp từ Edge TTS, nhanh, không cần GPU) làm mặc định; `whisper` (faster-whisper, timestamp mịn hơn, cần model ~3GB) làm fallback khi câu dài/công thức làm lệch sync.
- Composite stack: **MoviePy 2.x + Pillow** (render phụ đề bằng Pillow, không cần ImageMagick) + ffmpeg.
- Provider abstraction: nhiều LLM provider qua config; TTS tách Edge TTS free / Azure TTS V2 paid.
- Nhạc nền + cấu hình độ dài segment (điều tiết tần suất chuyển cảnh).
- WebUI Streamlit + API FastAPI (MVC) — dùng cho 2 checkpoint HITL.

**KHÔNG tái dùng / phải tự xây:**
- Market/trending agent, vòng research, topic approval loop — MPT không có.
- **Vòng self-repair cho codegen — MPT không có. Đây là engineering thật.**
- Thay stage "stock material = keyword search" của MPT bằng "Manim render". Interface giống (scene → clip mp4) nhưng Manim đắt hơn nhiều, per-scene custom, cần sandbox.

> **Lưu ý:** Stock B-roll phần lớn vô dụng cho video toán/lý. Giữ như nice-to-have cho intro/transition, KHÔNG phải thành phần chính. Giá trị nằm ở animation Manim.

---

## 4. `VideoSpec` — Intermediate Representation (backbone)

Script agent sinh ra. Mọi stage sau điền dần các field `null`.

```json
{
  "project_id": "uuid",
  "topic": "Fast Fourier Transform",
  "source_refs": ["arxiv:1234.5678", "https://..."],
  "language": "vi",
  "aspect_ratio": "16:9",
  "status": "scripted",            // searched|scripted|approved|animated|voiced|composited|rendered
  "scenes": [
    {
      "id": "s01",
      "order": 1,
      "narration": "Hãy hình dung một tín hiệu như tổng của các vector quay...",
      "visual_type": "manim",      // manim | stock | chart | static_image | title_card
      "visual_spec": "các phasor quay cộng lại thành sóng vuông; nhấn mạnh thành phần tần số",
      "manim_code": null,          // điền ở stage 3
      "manim_code_hash": null,     // cache key
      "clip_path": null,           // điền ở stage 3 (output render scene)
      "clip_qa_passed": null,      // bool, từ visual QA
      "audio_path": null,          // điền ở stage 4
      "duration_sec": null,        // điền ở stage 4 — DRIVE timeline
      "word_timestamps": null,     // điền ở stage 4 (cho phụ đề sync)
      "stock_query": null,         // nếu visual_type=stock
      "stock_clip_path": null
    }
  ],
  "bgm": { "path": null, "volume": 0.15 },
  "subtitle_style": { "font": "...", "size": 42, "color": "#FFF", "stroke": true },
  "final_video_path": null
}
```

Quy tắc: stage chỉ được ghi field của mình. `duration_sec` chỉ được set ở stage 4 (voiceover) và là nguồn chân lý cho timeline ở stage 5.

---

## 5. Chi tiết từng stage

### Stage 1 — Market search agent (agentic)
- Nguồn: arXiv (papers mới/trending), Reddit (r/math, r/Physics, r/MachineLearning), Hacker News, YouTube search niche "explained", Google Trends.
- Chấm điểm theo **2 trục độc lập**: (a) độ trending, (b) **khả năng visualize bằng Manim** (toán/hình/biểu đồ). Thiếu trục (b) → agent liên tục đề xuất chủ đề không animate được.
- Output: danh sách topic ranked + lý do + độ khó ước lượng + góc tiếp cận.
- HITL: người dùng chọn. Lưu feedback để refine query.

### Stage 2 — Script agent (agentic, sub-pipeline)
- `research (fetch + parse PDF/nguồn, RAG) → outline → viết script → emit VideoSpec`.
- Output là `VideoSpec` với `scenes[]` đã chia beat, mỗi beat có `narration` + `visual_type` + `visual_spec`.
- HITL: người dùng duyệt/sửa.

### Stage 3 — Manim codegen (agentic, phần khó nhất)
- Input: các scene `visual_type ∈ {manim, chart}`.
- Vòng generate → exec sandbox → repair → visual QA (xem mục 6).
- Output: `clip_path` + `clip_qa_passed` cho mỗi scene.
- **Cân nhắc mạnh: thư viện Manim template parametrized** (vector, hàm số, biến đổi hình học, biểu đồ) để LLM điền tham số thay vì viết code tự do → đổi reliability lấy linh hoạt. Với phạm vi hẹp của dự án, đây là đòn bẩy thực tế nhất.
- **Batch-generate-and-pick** cho scene khó: sinh N biến thể, QA + người chọn (giảm rủi ro "chạy được nhưng nhìn sai").

### Stage 4 — Voiceover (deterministic, MPT)
- TTS từng scene → `audio_path`, `duration_sec`, `word_timestamps`.
- Timestamp: `edge` mặc định, fallback `whisper`.

### Stage 5 — Composite + subtitle (deterministic, MPT)
- Timeline assembler: xếp clip theo `order`, kéo dài/cắt khớp `duration_sec`, ghép B-roll stock nếu có, render phụ đề từ `word_timestamps` bằng Pillow.
- MoviePy 2.x. Không LLM trong vòng dựng.

### Stage 6 — Render (deterministic, MPT)
- Mux video + audio + phụ đề + BGM → encode (H.264/H.265) qua ffmpeg.
- Agent nhỏ (tách riêng) sinh title/description/tags/thumbnail cho YouTube.

---

## 6. Vòng self-repair của Manim (cốt lõi — MPT không có)

```python
def render_scene(scene, max_repairs=4, n_variants=1):
    history = []
    for variant in range(n_variants):
        code = llm_generate_manim(scene.visual_spec, scene.narration)
        for attempt in range(max_repairs + 1):
            result = sandbox_exec(code)          # Docker, no network, cpu/mem/timeout limits
            if result.error:
                if attempt == max_repairs:
                    break                        # bỏ variant này
                code = llm_repair(code, traceback=result.traceback)  # ReAct loop
                continue
            frames = sample_frames(result.clip)  # vài frame đại diện
            qa = vision_qa(frames, intent=scene.visual_spec)         # khớp ý đồ?
            if qa.passed:
                return Clip(path=result.clip, qa_passed=True, code=code)
            code = llm_repair(code, feedback=qa.issues)  # sai về thị giác, không phải lỗi
    return best_effort_or_flag_for_human()
```

Ràng buộc:
- `max_repairs` cap (3–5) để tránh đốt token vô hạn.
- Sandbox bắt buộc: Docker có sẵn Manim, **no network**, timeout cứng, giới hạn CPU/RAM.
- Phân biệt 2 loại fail: **lỗi runtime** (sửa bằng traceback) vs **sai thị giác** (sửa bằng feedback của vision QA). Loại thứ 2 mới là cái khó.
- Cache theo `manim_code_hash`: code không đổi thì không render lại.

---

## 7. Orchestration, state, storage

- **Nửa trước (agentic + HITL):** stateful graph có interrupt — LangGraph. Pause tại checkpoint, resume khi duyệt.
- **Bước media nặng (Manim render, final render):** job queue + worker (Celery/RQ) hoặc durable execution (Temporal). Chạy lâu, dễ fail → không chạy inline trong request, không mất công khi crash.
- **State:** Postgres, entity `Project` với status per-stage. Tham chiếu artifact bằng path.
- **Artifact:** object storage (S3) hoặc local fs cho clip/audio/script.
- **Cache:** content-hash (xem D5).
- **Observability:** trace token LLM + render-minute + API cost theo từng project. Chi phí thật nằm ở stage 3 và 6.
- **HITL UI:** Streamlit (tái dùng từ MPT).

---

## 8. Tech stack

- **Ngôn ngữ:** Python 3.11 (theo MPT), quản lý env bằng `uv`.
- **Agent/orchestration:** LangGraph (nửa trước) + Celery/RQ hoặc Temporal (media jobs).
- **Animation:** Manim Community Edition, chạy trong Docker sandbox.
- **Media:** MoviePy 2.x, Pillow (phụ đề), ffmpeg.
- **TTS:** Edge TTS (free, mặc định) / Azure Speech (paid). Transcribe fallback: faster-whisper.
- **Stock (optional):** Pexels API.
- **LLM:** abstraction nhiều provider qua config (OpenAI/Anthropic/Gemini/DeepSeek/Ollama...).
- **Vision QA:** vision-capable LLM.
- **State:** Postgres. **Storage:** S3/local. **UI:** Streamlit + FastAPI.
- **Deploy:** Docker / docker-compose (CPU; GPU tùy chọn cho whisper + render nhanh).

---

## 9. Quy ước & ràng buộc cho coding agent

- KHÔNG để LLM sinh/điều khiển lệnh ffmpeg hoặc MoviePy trực tiếp. LLM quyết định *cái gì*; code deterministic *làm như thế nào*.
- KHÔNG chạy code Manim ngoài sandbox.
- KHÔNG finalize timeline trước khi có `duration_sec` từ stage 4.
- KHÔNG nhồi binary (clip/audio) vào `VideoSpec` hay Postgres — chỉ path.
- KHÔNG đặt voiceover sau edit.
- Mỗi stage chỉ ghi field của mình trong `VideoSpec`.
- Mọi LLM/TTS/stock call đi qua lớp provider abstraction, không hardcode SDK trong pipeline logic.
- Cap số vòng repair; mọi scene fail quá cap phải được flag cho người, không render im lặng ra clip xấu.
- Idempotent theo content-hash ở mọi bước render.

---

## 10. Cấu trúc thư mục đề xuất

```
app/
  agents/        # market_search, script, manim_codegen (LLM-facing)
  pipeline/      # voiceover, composite, render (deterministic)
  sandbox/       # Docker runner cho Manim
  providers/     # llm/, tts/, stock/ — abstraction + config
  models/        # VideoSpec, Project (pydantic + ORM)
  orchestration/ # LangGraph graph + queue tasks
  templates/     # thư viện Manim template parametrized
webui/           # Streamlit (HITL review)
api/             # FastAPI
resource/        # songs/, fonts/
```

---

## 11. Rủi ro đã biết (đọc kỹ)

1. **Manim codegen** — rủi ro *chất lượng*, không chỉ lỗi. Code chạy được nhưng vật thể chồng nhau, công thức tràn khung. → Template parametrized + visual QA + batch-and-pick.
2. **Sync narration ↔ animation** — thứ phân biệt explainer hay với slideshow. 3Blue1Brown làm thủ công. Tự động hóa "animation diễn ra đúng lúc lời thoại nhắc tới" là bài toán lõi khó nhất. Đừng kỳ vọng v1 làm tốt.
3. **Giao của "trending" và "visualize được"** hẹp — market agent phải lọc feasibility, nếu không sẽ đề xuất chủ đề không làm nổi.
4. **Chi phí** — stage 3 và 6 đốt nhiều nhất (render + token repair). Cache + cap repair là bắt buộc.
5. **Stock cho toán/lý** — phần lớn vô dụng, làm video rẻ tiền đi. Giữ tối thiểu.

---

## 12. Thứ tự build đề xuất

1. Định nghĩa `VideoSpec` + Project state (Postgres) trước mọi thứ — đây là backbone.
2. Cắm media layer từ MPT: voiceover → composite → render (đường ống deterministic chạy được với clip giả).
3. Sandbox Manim + render 1 scene từ code viết tay → kiểm tra interface clip cắm vào compositor.
4. Manim codegen + vòng self-repair + visual QA.
5. Thư viện template parametrized.
6. Script agent → emit VideoSpec.
7. Market search agent + HITL UI (Streamlit).
8. Orchestration LangGraph nối toàn bộ + queue cho media jobs.