# Kết quả gate Ngày 1

Ghi lại kết quả G1, G2, G3 theo [PLAN.md](PLAN.md). Mỗi dòng là kết quả đã chạy thật, kèm ngày và cách chạy.

| Gate | Trạng thái | Ngày |
|---|---|---|
| G1. Model và ý nghĩa nhãn | Load và tốc độ đạt. Lớp INSTABILITY không đạt: không phân biệt được bất ổn với đi lại bình thường | 2026-10-29 |
| G2. SAM trên ảnh demo | Đạt trên ảnh nhà khối hộp; còn chạy lại ở Ngày 5 | 2026-10-28 |
| G3. Tool calling | Đạt phần 1 (một lượt gọi tool) | 2026-10-28 |

## G1. Model và ý nghĩa nhãn

Môi trường: `.verify/venv`, Python 3.12, TensorFlow 2.21.0 (bản `macosx_12_0_arm64`). Config trỏ về `ml/model_3class/`.

- `FallDetectionService` báo `model_source: keras`. File được load là `fall_detection_3class_cnn_bilstm_attn.keras`. Không có file TFLite, nên log in dòng "falling back to heuristic detection" trước khi chuyển sang Keras. Dòng log này gây hiểu nhầm.
- Model nhận input `(None, 100, 7)` và trả output `(None, 3)`, khớp thứ tự `[NORMAL, INSTABILITY, FALL]` trong code train.
- Scaler: mean `[-0.161, -0.557, -0.065, -0.882, 3.249, -0.293, 1.037]`, std `[0.499, 0.596, 0.504, 38.235, 34.465, 24.863, 0.358]`. Std của ba kênh gyro khoảng 25 đến 38, phù hợp đơn vị độ/giây. Firmware (`imu.cpp:27`) và simulator đều gửi gyro theo độ/giây.
- Tốc độ: gọi model trực tiếp như `predict_fall_probability` đang làm mất 111,98 ms mỗi cửa sổ, đo trên 200 lần. Ngân sách ở 50 Hz, chạy model mỗi mẫu, là 20 ms. Bọc model bằng `tf.function` với input signature cố định còn 1,48 ms mỗi cửa sổ. Cần sửa ở Ngày 2.
- Thử cửa sổ đứng yên tổng hợp (không nhiễu, SVM = 1): trọng lực trên +z cho FALL = 0,34, trên -y cho 0,29, trên +y cho 0,70. Cửa sổ không nhiễu khác xa dữ liệu thật, nên đây chỉ là dấu hiệu model nhạy với hướng trục. Chưa đủ để kết luận.

### Ý nghĩa nhãn trên subject held-out

Chạy `PYTHONPATH=.. python ../ml/scripts/gate_g1_labels.py` từ `backend/`. Script dựng cửa sổ giống `build_subject_independent_dataset.py` (cửa sổ 100 mẫu, bước 50), chỉ lấy subject thuộc tập TEST của split seed 42, và gán trạng thái bằng chính `classify_state` của backend (luật SVM ≥ 2,5 g trước, rồi ngưỡng ML). Cột cuối là argmax của model, không qua luật SVM.

Subject test: vestibular `44_HS, 58_HS, 02_BV, 03_BV, 09_BV, 14_BV, 15_BV, 34_UV, 37_UV`; SisFall `SA02, SA03, SA09, SA14, SE11, SE14`.

| Nguồn | Nhóm | Hoạt động | Nhãn train | Cửa sổ | Backend N / I / F | Model N / I / F |
|---|---|---|---|---|---|---|
| vestibular | HS (người khỏe) | Walk | NORMAL | 21 | 24% / 71% / 5% | 24% / 71% / 5% |
| vestibular | HS | Stairs | NORMAL | 19 | 0% / 79% / 21% | 0% / 100% / 0% |
| vestibular | HS | Sorting (ngồi) | NORMAL | 108 | 44% / 56% / 0% | 44% / 56% / 0% |
| vestibular | BV | Walk | NORMAL | 45 | 13% / 87% / 0% | 13% / 87% / 0% |
| vestibular | BV | Stairs | INSTABILITY | 56 | 2% / 95% / 4% | 2% / 98% / 0% |
| vestibular | UV | Walk | NORMAL | 21 | 0% / 100% / 0% | 0% / 100% / 0% |
| SisFall | SA | Hoạt động thường ngày (D*) | NORMAL | 5.664 | 66% / 0% / 33% | 83% / 0% / 17% |
| SisFall | SE (người cao tuổi) | Hoạt động thường ngày (D*) | NORMAL | 2.259 | 76% / 0% / 24% | 94% / 0% / 6% |
| SisFall | SA | Ngã (F*) | FALL | 4.197 | 23% / 1% / 76% | 20% / 1% / 80% |

Kết luận:

- Lớp INSTABILITY bật cho gần như mọi bản ghi từ bộ vestibular, kể cả người khỏe đi bộ (71%) và người khỏe đi cầu thang (100% theo model). Nó gần như không bật trên SisFall (dưới 1%). Lớp này đang tách nguồn dữ liệu (cảm biến thân của bộ vestibular so với SisFall), không tách bất ổn với chuyển động bình thường. Đây đúng là rủi ro mà Cell 11 của notebook train (domain-origin confound check) cảnh báo. Notebook không lưu output của cell đó.
- Người khỏe đi cầu thang: cả 2 file đều có cửa sổ FALL theo luật backend. Model không dự đoán FALL (0%), nên FALL đến từ luật SVM ≥ 2,5 g.
- Ngã SisFall: 300/300 file có ít nhất một cửa sổ FALL. Nhánh FALL hoạt động.
- Báo nhầm FALL trên hoạt động thường ngày SisFall: 258/316 file SA và 52/118 file SE có ít nhất một cửa sổ FALL. Phần lớn đến từ luật SVM. Đo theo cửa sổ bước 50, chưa qua cơ chế xác nhận 0,5 giây và cooldown của backend.

Lưu ý pipeline: trong `build_subject_independent_dataset.py`, nhãn SisFall giữ nguyên từ `preprocess.get_label_from_filename`, nơi `F* = 1`. Trong không gian 3 lớp, 1 là INSTABILITY. Model hiện tại dự đoán FALL cho 80% cửa sổ ngã SisFall, nên weights đang dùng có lẽ không được train từ pipeline này (suy luận, chưa có manifest). Nếu train lại bằng script này phải sửa ánh xạ nhãn trước.

## G2. SAM trên ảnh demo

Chạy `backend/tools/gate_sam.py --prompt stairs` trên bốn ảnh render từ nhà khối hộp Three.js (tường xám, sàn xanh đậm, ánh sáng phẳng). Ảnh gửi dạng data URL base64 tới `POST /v1/responses` với `model=sam-3.1`, không stream. Ảnh có kích thước 400×300.

| Ảnh | Kết quả | Thời gian | Đánh giá bằng mắt |
|---|---|---|---|
| Cầu thang nhìn từ dưới lên | 1 vật thể, box (124,81)-(275,295) | 3,17 s | Đúng. Box ôm trọn các bậc. |
| Cầu thang nhìn ngang | 1 vật thể, box (0,0)-(399,298) | 2,16 s | Đúng, nhưng box gần bằng cả ảnh vì cầu thang chiếm gần hết khung. |
| Cầu thang nhìn từ trên xuống | 1 vật thể, box (73,175)-(326,299) | 2,47 s | Đúng, dù bậc gần như không thấy cạnh. |
| Phòng khách, không có cầu thang | 0 vật thể, `output_text` rỗng | 2,11 s | Đúng. Không nhận nhầm. |

Định dạng response: mỗi vật thể là một dòng `<0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;x=0;y=0;data=W,H,~...|>` trong phần `output_text`. Không tìm thấy thì `output_text` là chuỗi rỗng. `usage.images_processed` bằng 1 cho mỗi request.

Chưa kiểm tra: giải mã mask thành ảnh nhị phân (cần `@meta-sam/parser` hoặc tự viết bộ giải mã cho payload `~`), độ bám của mask so với mép cầu thang, và ảnh render sau khi có nội thất đầy đủ. Ba việc này làm ở Ngày 5 (AC04).

## G3. Tool calling

Chạy bằng `curl` tới `https://api.meta.ai/v1`, key lấy từ `MODEL_API_KEY` trong `backend/.env`.

- `GET /v1/models` trả HTTP 200. Model khả dụng cho team: `muse-wonder-1.0`, `sam-3.1`, `muse-spark-1.3`, `muse-spark-1.3-contributor`, `muse-spark-1.2`, `muse-spark-1.2-contributor`, `muse-spark-1.1`, `muse-image-1.0`, `muse-voice-transcribe-1.0`.
- `POST /v1/chat/completions` với `model=muse-spark-1.3` và một tool `get_event_context(incident_id: integer)` trả HTTP 200 sau 2,2 giây.
- Response theo định dạng OpenAI chat completions. `finish_reason` là `tool_calls`. Model gọi `get_event_context` với `{"incident_id":17}`, đúng schema.
- Usage: 559 prompt tokens (113 cached), 189 completion tokens, trong đó 119 reasoning tokens. Theo bảng giá Standard, chi phí khoảng 0,0015 USD.

Chưa kiểm tra: gửi kết quả tool trở lại cho lượt thứ hai, output JSON theo schema của AnalysisRun, hành vi khi tool trả lỗi, và timeout. Các phần này làm ở Ngày 6 (AC05, AC11).

## Thí nghiệm C. Model bất ổn train chỉ trên bộ vestibular

Chạy `PYTHONPATH=. python ml/scripts/exp_instability_within_vestibular.py`. Model CNN 1D nhỏ, 2 lớp, nhãn proxy từ `get_activity_label`: NORMAL là mọi hoạt động của HS cùng các hoạt động dễ của BV/UV, INSTABILITY là hoạt động khó của BV/UV. Có 26.404 cửa sổ từ 59 người. Đánh giá bằng kiểm tra chéo 5 phần theo người: mỗi người được chấm bởi model chưa từng thấy họ, và mỗi phần đều có HS, BV, UV.

| Nhóm | Tỉ lệ bị báo bất ổn | Điều kiện đạt |
|---|---|---|
| HS, đi bộ và cầu thang | 51,4% (n=387) | Dưới 20%. **Không đạt** |
| HS, mọi hoạt động | 49,8% (n=7.276) | |
| BV/UV, hoạt động khó | 78,6% (n=13.835) | Trên 60%. Đạt |
| BV/UV, hoạt động dễ | 15,9% (n=5.293) | |

So cùng một hoạt động giữa bệnh nhân và người khỏe:

| Hoạt động | BV | UV | HS |
|---|---|---|---|
| Stairs | 70,0% | 66,0% | 77,1% |
| Wood beam | 88,5% | 79,7% | 73,9% |
| Shoes | 94,1% | 92,5% | 93,3% |
| Uneven ground | 78,3% | 44,8% | 38,3% |
| Darkness walk | 71,1% | 40,0% | 32,1% |
| Walk | 48,9% | 29,2% | 22,5% |
| Sorting | 9,1% | 3,4% | 4,0% |

Kết luận: khi cảm biến được giữ cố định, model học **loại hoạt động** (khó hay dễ), không học bệnh nhân hay người khỏe. Trên cầu thang, người khỏe bị báo nhiều hơn bệnh nhân. Chỉ nhóm BV (tiền đình hai bên) tách khỏi HS ở các hoạt động đi bộ (Walk, Darkness walk, Uneven ground), và UV gần như không tách.

Cả model gốc và thí nghiệm C đều thất bại ở cùng một bài thử. Nhãn proxy theo từng cửa sổ của bộ dữ liệu này không mang tín hiệu "mất thăng bằng" có thể dùng cho cảnh báo theo thời gian thực. MVP 1 không dùng lớp INSTABILITY làm nguồn cảnh báo.

## Thí nghiệm D. IMU có điều kiện theo hoạt động

Chạy `PYTHONPATH=. python ml/scripts/exp_context_conditioned.py`. Model nhận IMU cùng tên hoạt động (vai trò của camera hoặc zone) và học một việc: bệnh nhân (BV/UV) hay người khỏe (HS), trong cùng một hoạt động. Cùng dữ liệu và cùng cách kiểm tra chéo 5 phần theo người như thí nghiệm C. AUC 0,5 nghĩa là không tách được.

| Hoạt động | AUC | HS bị báo | BV bị báo | UV bị báo |
|---|---|---|---|---|
| Stairs | 0,49 | 61,5% | 64,4% | 63,7% |
| Walk | 0,68 | 42,3% | 75,0% | 65,6% |
| Inclined plane | 0,68 | 46,0% | 81,1% | 68,1% |
| Uneven ground | 0,67 | 50,6% | 91,2% | 66,2% |
| Darkness walk | 0,66 | 56,1% | 86,2% | 63,7% |
| Sorting | 0,50 | 51,5% | 55,7% | 50,8% |

AUC chung theo cửa sổ là 0,59. AUC theo người (trung bình điểm mọi cửa sổ của một người, n=59) là 0,73.

Kết luận: biết hoạt động không giúp IMU tách bệnh nhân với người khỏe trên cầu thang (AUC 0,49). Các hoạt động đi bộ có tín hiệu vừa phải (0,66 đến 0,68), chưa đủ để cảnh báo theo từng cửa sổ 2 giây. Gộp theo người cho tín hiệu rõ hơn (0,73), phù hợp với điểm sàng lọc dáng đi qua nhiều phiên, không phù hợp cảnh báo tức thời. Bộ dữ liệu không có sự kiện suýt ngã, nên không mô hình nào ở đây học được "sắp ngã".
