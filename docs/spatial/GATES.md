# Kết quả gate Ngày 1

Ghi lại kết quả G1, G2, G3 theo [PLAN.md](PLAN.md). Mỗi dòng là kết quả đã chạy thật, kèm ngày và cách chạy.

| Gate | Trạng thái | Ngày |
|---|---|---|
| G1. Model và ý nghĩa nhãn | Load và tốc độ đạt. Nhánh FALL bắt 300/300 file ngã ở mức incident nhưng báo nhầm 78,5% file hoạt động thường ngày (SA). Lớp INSTABILITY không đạt. Chưa xác minh được tập train của weights | 2026-10-29 |
| G2. SAM trên ảnh demo | Đạt trên ảnh nhà khối hộp: box và mask (đã giải mã) khớp cầu thang, không nhận nhầm. Chạy lại trên ảnh có nội thất ở Ngày 5 | 2026-10-29 |
| G3. Tool calling | Đạt phần 1 (một lượt gọi tool) | 2026-10-28 |

## G1. Model và ý nghĩa nhãn

Môi trường: `.verify/venv`, Python 3.12, TensorFlow 2.21.0 (bản `macosx_12_0_arm64`). Config trỏ về `ml/model_3class/`.

- `FallDetectionService` báo `model_source: keras`. File được load là `fall_detection_3class_cnn_bilstm_attn.keras`. Không có file TFLite, nên log in dòng "falling back to heuristic detection" trước khi chuyển sang Keras. Dòng log này gây hiểu nhầm.
- Model nhận input `(None, 100, 7)` và trả output `(None, 3)`, khớp thứ tự `[NORMAL, INSTABILITY, FALL]` trong code train.
- Scaler: mean `[-0.161, -0.557, -0.065, -0.882, 3.249, -0.293, 1.037]`, std `[0.499, 0.596, 0.504, 38.235, 34.465, 24.863, 0.358]`. Std của ba kênh gyro khoảng 25 đến 38, phù hợp đơn vị độ/giây. Firmware (`imu.cpp:27`) và simulator đều gửi gyro theo độ/giây.
- Tốc độ: gọi model trực tiếp như `predict_fall_probability` đang làm mất 111,98 ms mỗi cửa sổ, đo trên 200 lần. Ngân sách ở 50 Hz, chạy model mỗi mẫu, là 20 ms. Bọc model bằng `tf.function` với input signature cố định còn 1,48 ms mỗi cửa sổ. Cần sửa ở Ngày 2.
- Thử cửa sổ đứng yên tổng hợp (không nhiễu, SVM = 1): trọng lực trên +z cho FALL = 0,34, trên -y cho 0,29, trên +y cho 0,70. Cửa sổ không nhiễu khác xa dữ liệu thật, nên đây chỉ là dấu hiệu model nhạy với hướng trục. Chưa đủ để kết luận.

### Ý nghĩa nhãn trên subject của tập TEST (split v2)

Nguồn gốc dữ liệu train của weights chưa xác minh được. Script tạo lại split của `build_subject_independent_dataset.py` (seed 42) và lấy tập TEST. Không có manifest chứng minh weights đang dùng được train theo split này. Nhiều subject trong tập này lại thuộc tập train của split cũ (`preprocess_vestibular.py` và `preprocess.py`): `44_HS`, `02_BV`, `03_BV`, `09_BV`, `14_BV`, `34_UV`, `SA02`, `SA03`, `SA09`, `SA14`, `SE11`. Vì vậy trạng thái đúng là: chưa biết có chồng lấn với dữ liệu train hay không. Các con số dưới đây là của subject tập TEST v2, không phải dữ liệu đã chứng minh model chưa từng thấy.

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
- Ngã SisFall: 300/300 file có ít nhất một cửa sổ FALL thô. Đây là mức cửa sổ; kết quả theo incident ở mục dưới.
- Báo nhầm FALL trên hoạt động thường ngày SisFall ở mức cửa sổ: 258/316 file SA và 52/118 file SE có ít nhất một cửa sổ FALL. Đo theo cửa sổ bước 50, chưa qua xác nhận 0,5 giây.

### Kết quả theo incident

Chạy `PYTHONPATH=. python ml/scripts/gate_g1_incidents.py`. Mỗi bản ghi được phát lại từng mẫu qua `StreamMonitor`, đúng đường chạy của backend: xác nhận 0,5 giây rồi gom incident theo 10 giây. Cùng các subject tập TEST v2 ở trên.

| Bản ghi | File | Có ít nhất 1 incident | Nguồn các incident |
|---|---|---|---|
| SisFall SA, ngã (F*) | 300 | 300 (100%) | keras 222, svm_rule 78 |
| SisFall SA, hoạt động thường ngày (D*) | 316 | 248 (78,5%) | keras 164, svm_rule 89 |
| SisFall SE, hoạt động thường ngày (D*) | 118 | 49 (41,5%) | keras 29, svm_rule 26 |
| Vestibular HS, Stairs | 2 | 2 | svm_rule 2 |
| Vestibular HS, Walk | 2 | 0 | |
| Vestibular BV/UV, Walk | 7 | 0 | |

Ở mức incident, nhánh FALL bắt mọi file ngã, nhưng báo nhầm trên phần lớn file hoạt động thường ngày của người trẻ. Phần lớn báo nhầm đến từ model, không phải luật SVM. Hoạt động thường ngày của SisFall gồm cả chạy bộ, nhảy và ngồi xuống nhanh; chưa tách theo mã hoạt động. Tỉ lệ này phải được công bố theo AC12 và là việc cần làm tiếp sau MVP 1.

### Báo nhầm theo hoạt động thường ngày

Chạy `PYTHONPATH=. python ml/scripts/gate_g1_daily_by_activity.py`. Cùng đường chạy và subject như trên, tách theo mã hoạt động SisFall. Tên hoạt động lấy từ `SisFall_dataset/Readme.txt`. Người cao tuổi không làm D06, D13, D18, D19.

| Code | Activity | SA files with incident | SE files with incident | Incident sources |
|---|---|---|---|---|
| D01 | Walking slowly | 3/4 | 1/2 | keras 10 |
| D02 | Walking quickly | 3/4 | 1/2 | keras 2, svm_rule 6 |
| D03 | Jogging slowly | 4/4 | 2/2 | svm_rule 6 |
| D04 | Jogging quickly | 4/4 | 2/2 | svm_rule 6 |
| D05 | Walking upstairs and downstairs slowly | 18/20 | 0/10 | keras 19 |
| D06 | Walking upstairs and downstairs quickly | 20/20 | not performed | keras 2, svm_rule 18 |
| D07 | Slowly sit in a half height chair and up slowly | 9/20 | 0/10 | keras 9 |
| D08 | Quickly sit in a half height chair and up quickly | 11/20 | 4/10 | keras 4, svm_rule 11 |
| D09 | Slowly sit in a low height chair and up slowly | 12/20 | 2/10 | keras 11, svm_rule 3 |
| D10 | Quickly sit in a low height chair and up quickly | 11/20 | 6/10 | keras 11, svm_rule 6 |
| D11 | Trying to get up, collapse into a chair | 20/20 | 6/10 | svm_rule 26 |
| D12 | Lying slowly, then sit again | 18/20 | 6/10 | keras 24 |
| D13 | Lying quickly, then sit again | 17/20 | not performed | keras 14, svm_rule 3 |
| D14 | On one's back, change to lateral position and back | 20/20 | 10/10 | keras 30 |
| D15 | Standing, slowly bending at knees, getting up | 7/20 | 5/10 | keras 12 |
| D16 | Standing, slowly bending without bending knees | 19/20 | 2/10 | keras 21 |
| D17 | Get into a car, remain seated, get out | 12/20 | 2/10 | keras 14 |
| D18 | Stumble while walking | 20/20 | not performed | keras 6, svm_rule 14 |
| D19 | Gently jump without falling | 20/20 | not performed | keras 4, svm_rule 16 |

Báo nhầm không chỉ nằm ở hoạt động mạnh như chạy bộ, nhảy hay ngồi phịch xuống, vốn chủ yếu đến từ luật SVM. Model Keras cũng tạo incident khi đi bộ chậm (D01, cả 10 incident), đi cầu thang chậm (D05, 18/20 file SA), nằm xuống (D12, D14) và cúi người (D16). Người cao tuổi đi cầu thang chậm không bị báo (D05, 0/10). Kịch bản S1 (đi bộ) không tạo incident, nhưng điều đó không chứng minh đi bộ nói chung không bị báo nhầm.

## Bộ lọc nằm yên sau FALL (2026-10-29)

Một FALL đã xác nhận chỉ mở incident nếu sau đó người đeo nằm yên (`StillnessRule` trong `backend/app/services/monitoring.py`). Nằm yên được đánh giá trên tín hiệu thô của N giây gần nhất: độ lệch chuẩn SVM và trung bình độ lớn gyro. Hạn chờ tính từ FALL gần nhất. Incident bắt đầu ở đầu đợt FALL cuối cùng.

Tham số được chọn bằng `PYTHONPATH=. python ml/scripts/tune_stillness.py`. Lưới gồm 27 tổ hợp, chạy trên subject TRAIN và VAL của split v2 (3.771 file SisFall), dùng chính `StreamMonitor`. TEST gồm 734 file của subject tập TEST. Dự đoán model được tính theo lô rồi đưa qua `FallDetectionService.add_sample`, nên logic xác nhận giữ nguyên.

| Mức | Quy tắc | TRAIN: bắt ngã / báo nhầm | TEST: bắt ngã / báo nhầm |
|---|---|---|---|
| Không lọc | | 100% / 59,9% | 100% (300/300) / 68,4% (297/434) |
| An toàn | 1 s, std ≤ 0,1 g, gyro ≤ 20 °/s | 100% / 50,6% | 100% (300/300) / 59,9% (260/434) |
| **Trung bình (đang dùng)** | **2 s, std ≤ 0,05 g, gyro ≤ 10 °/s** | **99,8% / 45,0%** | **99,3% (298/300) / 55,5% (241/434)** |
| Chặt | 3 s, std ≤ 0,03 g, gyro ≤ 10 °/s | 99,3% / 37,0% | 97,7% (293/300) / 47,9% (208/434) |

Ba mức được định nghĩa từ số TRAIN. Người dùng chọn mức Trung bình sau khi xem cả bảng TEST, nên TEST không còn hoàn toàn chưa được dùng cho quyết định này. "Báo nhầm" là tỉ lệ file hoạt động thường ngày có ít nhất một incident.

Kết luận: bộ lọc giảm báo nhầm từ 68,4% xuống 55,5% file và bỏ sót 2/300 cú ngã. Phần lớn báo nhầm còn lại được xử lý bằng đếm ngược trên HUD, nơi người đeo bấm "I'm OK" để hủy (SPEC v0.4). Hai ứng viên S1 có người khỏe leo cầu thang vẫn tạo incident vì luật SVM, kể cả khi đã có bộ lọc.

Bộ lọc còn sửa lại S2. Trước đây incident S2 mở ở 14.720 ms. Tín hiệu thô cho thấy đó là model kích hoạt lúc đang đi bộ (đỉnh 1,74 g, vẫn cử động). Cú ngã thật nằm ở giây 24 đến 25 (đỉnh 5,18 g), sau đó nằm yên từ giây 26. Với bộ lọc, incident bắt đầu ở 23.940 ms và mở ở khoảng 27,1 s.

Chạy trên instance cô lập qua API: không phản hồi thì incident đi từ awaiting_wearer sang alerted và contact 1 nhận tin sau 7 s; gọi "I'm OK" trong lúc đếm ngược thì incident là cancelled_by_wearer và không ai được báo. Độ trễ từ va chạm tới lúc báo người chăm sóc trong S2 khoảng 10 s (khoảng 3 s chờ nằm yên và 7 s đếm ngược).

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

Mask đã giải mã bằng `@meta-sam/parser` 0.1.0 (`backend/tools/sam_masks/`). Cả ba ảnh cầu thang trả đúng 1 mask, không có diagnostic; ảnh phòng khách trả 0 mask. Kích thước raster khớp box. Tỉ lệ pixel thuộc mask trong box: 74% (nhìn từ dưới), 25% (nhìn ngang, box gần bằng cả khung), 71% (nhìn từ trên). Ảnh chồng mask lên frame nằm trong `docs/spatial/evidence/g2/*.mask.png`. Kiểm tra bằng mắt: mask bám theo các bậc; ảnh nhìn ngang có gồm chiếu nghỉ; ảnh nhìn từ dưới có mép lởm chởm ở bậc trên cùng.

### Chạy lại trên nhà có nội thất (Ngày 3, 2026-10-29)

Bốn preset camera kính trong `assets/homes/home1/manifest.json`, render bởi `/spatial/` (ảnh 329×246). Cùng script và prompt `stairs`.

| Preset | Kết quả | Thời gian | Tỉ lệ pixel mask trong box |
|---|---|---|---|
| hall_to_stairs | 1 vật thể | 2,31 s | 0,67 |
| stairs_bottom | 1 vật thể | 3,64 s | 0,78 |
| landing_down | 1 vật thể | 2,71 s | 0,46 |
| living_room (sofa, bàn, thảm, đèn; không có cầu thang) | 0 vật thể | 0,56 s | |

Ảnh chồng mask: `docs/spatial/evidence/home1/glasses-sam-masks.png`. Kiểm tra bằng mắt: mask bám các bậc ở cả ba góc; góc từ chiếu nghỉ nhìn xuống có lấn một phần mặt chiếu nghỉ ở mép dưới. Phòng khách có nội thất không bị nhận nhầm.

Chưa kiểm tra: IoU với mask đúng render từ scene. Ngày 5 chạy lại nếu nội thất hoặc ánh sáng còn thay đổi.

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

AUC chung theo cửa sổ là 0,59. AUC theo người (trung bình điểm mọi cửa sổ của một người, n=59) là 0,73. Hàm AUC ban đầu xử lý sai điểm bằng nhau; sau khi đổi sang hạng trung bình (`scipy.stats.rankdata`) và chạy lại, mọi số liệu trong bảng không đổi.

Kết luận: biết hoạt động không giúp IMU tách bệnh nhân với người khỏe trên cầu thang (AUC 0,49). Các hoạt động đi bộ có tín hiệu vừa phải (0,66 đến 0,68), chưa đủ để cảnh báo theo từng cửa sổ 2 giây. Gộp theo người cho tín hiệu rõ hơn (0,73), phù hợp với điểm sàng lọc dáng đi qua nhiều phiên, không phù hợp cảnh báo tức thời. Bộ dữ liệu không có sự kiện suýt ngã, nên không mô hình nào ở đây học được "sắp ngã".
