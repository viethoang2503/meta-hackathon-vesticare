# Kế hoạch triển khai MVP 1

Bản v0.3 cho một người, 10 ngày. Đây là phân bổ dự kiến. [SPEC.md](SPEC.md) giữ phạm vi, AC và tham số. [DEMO.md](DEMO.md) giữ walkthrough ba phút.

## Checkpoint

Một nhà một tầng, bốn phòng và chiếu nghỉ; một active replay, một tab và một agent. Ngày 1 kiểm tra hai giả thuyết có thể làm đổi câu chuyện. Ngày 8 khóa tính năng. Hai ngày cuối dành cho kiểm chứng, rehearsal và submission. Mọi mở rộng nằm sau nghiệm thu MVP 1.

Ngày 1 đã xong ngày 2026-10-29. Kết quả ở [GATES.md](GATES.md): G2 và G3 đạt; G1 đạt phần load model và nhánh FALL, lớp INSTABILITY không đạt và không được dùng làm nguồn cảnh báo. Mục Ngày 1 bên dưới giữ lại làm hồ sơ.

Ngày 2 đã xong ngày 2026-10-29. Kết quả đo:

- Suy luận qua `tf.function`: 1,87 ms mỗi cửa sổ, output trùng với cách gọi cũ tới 5 chữ số thập phân.
- `ml/scripts/build_scenarios.py` tạo S1 và S2 từ bản ghi thật trong `assets/scenarios/`. Hai ứng viên S1 có người khỏe leo cầu thang đều bị luật SVM ≥ 2,5 g báo ngã, nên S1 chỉ gồm đi bộ. Kết quả mọi ứng viên nằm trong `detector_check.json`.
- Chạy qua API trên instance cô lập: S2 tạo đúng một FALL incident bắt đầu ở 14.720 ms, nguồn quyết định là `keras`, contact 1 nhận một thông báo mô phỏng. S1 không tạo incident. Start lần hai khi S2 đang chạy trả 409.
- Bộ test: 120 passed, gồm 8 test mới trong `backend/tests/test_spatial_runs.py`.
- Còn lại: route `/api/sensor/*` cũ vẫn dùng cooldown và vẫn tạo alert INSTABILITY cho dashboard cũ. Phân tích bằng agent làm ở Ngày 6, escalation và ack ở Ngày 7.

Ngày 3 đã xong ngày 2026-10-29. Kết quả:

- Nhà một tầng trong `assets/homes/home1/manifest.json`: phòng khách, bếp và phòng ăn, phòng ngủ, phòng tắm, hành lang, cầu thang 8 bậc lên chiếu nghỉ có tay vịn. Nội thất dựng từ khối cơ bản trong `dashboard/spatial/scene.js`, không có asset ngoài. Three.js 0.160.0 nằm trong `dashboard/spatial/vendor/`.
- `/spatial/` có góc toàn nhà với cutaway, vùng nguy hiểm, chọn vật thể bằng click và camera kính với bốn preset. `backend/tests/test_home_manifest.py` kiểm tra inventory theo SPEC (AC01).
- SAM chạy lại trên ảnh camera kính của nhà có nội thất: ba preset cầu thang trả một mask bám các bậc, phòng khách trả không vật thể (GATES.md).
- FPS đo được chỉ là headless với đồ họa phần mềm (khoảng 19); FPS trên máy demo đo ở Ngày 8.
- Báo nhầm FALL được tách theo hoạt động (GATES.md): model báo nhầm cả khi đi bộ chậm, đi cầu thang chậm, nằm xuống và cúi người.

## Ngày 1 có gate bắt buộc

### G1. Model và ý nghĩa nhãn

1. Sửa đường dẫn config về `ml/model_3class/`. Xác nhận runtime TensorFlow load Keras/scaler thật, ghi tên file và model source. Folder upload chưa có TFLite.
2. Xác định preprocessing, dataset version, class order và đơn vị của weights hiện tại. Archive Keras hiện chưa có manifest liên kết weights với training data. Nếu thiếu provenance, ghi rõ thiếu.
3. Kiểm tra chức năng gán nhãn thực tế. Source hiện gán HS thành NORMAL, BV/UV trong nhóm hoạt động khó thành INSTABILITY. Đây là proxy cohort/activity, không phải annotation từng episode.
4. Chạy đoạn cầu thang bình thường của HS held-out và đi bộ bình thường. Nếu có, thêm BV/UV đi cầu thang bình thường và các episode bất ổn có annotation độc lập.
5. Báo xác suất, dự đoán theo window và incident/alert sau grouping. Ghi thông tin subject và split để phân biệt dữ liệu train và dữ liệu kiểm chứng.
6. Chốt cách gọi output và câu chuyện. Negative test đạt chưa biến proxy thành nhãn mất thăng bằng. Nếu semantics chưa đủ bằng chứng, dùng Model-predicted motion risk và công bố giới hạn; nếu detector còn tạo nhiều cảnh báo giả thì đổi scenario/policy trước khi dựng demo. Không điều chỉnh ngưỡng chỉ để ép kết quả đẹp.
7. Đo inference theo sample rate, window và batch dự kiến. Nếu đổi inference stride, xác nhận lại confirmation bằng thời gian. Chọn normal_gap bằng fixtures.

Đây là gate AC02, không phải kế hoạch train lại toàn bộ model trong 10 ngày. Nếu chưa có recording kiểm chứng, ghi gate Inconclusive và giới hạn claims. Chốt vật liệu IMU demo sau khi chạy, không hardcode predicted class.

### G2. SAM trên đúng hình ảnh demo

1. Dựng frame cầu thang thô bằng box geometry với màu, ánh sáng và góc camera dự kiến. Chưa cần nội thất hoàn chỉnh.
2. Gọi SAM thật trên ít nhất ba góc/điều kiện định dùng trong demo. Quan sát mask có bám đúng cầu thang, có bỏ sót/nhận nhầm và đo latency. Chạy thêm frame không có vật thể yêu cầu để kiểm tra NoMatch/false match.
3. Lưu pixel input, prompt, mask, model ID và kết quả kiểm tra bằng mắt. Scene ground truth chỉ dùng đánh giá, không đưa vào prompt làm đáp án.
4. Nếu render không đáp ứng, đổi góc/phong cách và thử lại trong budget Ngày 1. Nếu vẫn kém, thử clip iPhone quay cầu thang thật, ghi Recorded video và manual zone mapping; kiểm tra lại cùng tiêu chí trước khi chốt nguồn.
5. Nếu cả hai đường chưa qua gate, ghi rõ blocker perception. Không đầu tư ngày dựng nội thất rồi mới tìm hiểu API có xử lý được ảnh không.

G2 đáp ứng AC04. Một mask trên ảnh bất kỳ chưa đủ. Quay video cầu thang không đòi hỏi người quay thực hiện ngã hay hành vi nguy hiểm.

### G3. Tool calling và baseline

Chạy một vòng tool calling thật, xác nhận model ID, output schema và quyền API. Giữ key trong backend environment. Chạy baseline ingest/alert hiện có với notification sink được vô hiệu hóa hoặc mô phỏng. Ghi kết quả, không mặc định baseline xanh.

## Phân bổ 10 ngày

| Ngày | Công việc | Gate cuối ngày |
|---|---|---|
| 1 | G1, G2, G3. Chốt model claims, nguồn frame và config. | Có evidence thực hoặc quyết định giới hạn rõ ràng; chưa qua gate thì sửa giả thuyết trước khi làm tiếp phần phụ thuộc. |
| 2 | Model chạy qua `tf.function`; file IMU của S1/S2 và kết quả detector trên chúng; session riêng, replay nội bộ, FALL incident vượt cooldown; status DB và analysis task tách ingest. | Inference dưới 20 ms mỗi cửa sổ; S1 không có FALL, S2 có đúng một FALL incident; phần session isolation của AC09; phần FALL của AC07. Commit lỗi ở mức best-effort của AC10. |
| 3 | Nhà một tầng/bốn phòng, cầu thang chiếu nghỉ, nội thất và camera. | Inventory đủ; style khớp frame G2; AC01. |
| 4 | Timeline, frame lookup, marker và evidence drawer; Start/Stop. | AC03 và phần Stop/refresh của AC09/AC10. |
| 5 | SAM adapter và lưu mask/status; kiểm tra ảnh sau hoàn thiện nội thất. | AC04, các trạng thái thiếu ảnh/NoMatch/lỗi. |
| 6 | Agent tools, summary có evidence IDs và environmental fixes nhỏ. | AC05 và timeout không chặn FALL trong AC11. |
| 7 | HUD advisory theo zone có SAM xác nhận, HUD "Are you OK?" khi ngã, caregiver ack và hai contact mô phỏng. | AC06, AC07, AC08. |
| 8 | Hoàn thiện S1/S2 và các bài thử T1 đến T6, đo FPS/latency và tỉ lệ báo nhầm FALL, kiểm tra Start/Stop/restart. | AC09 đến AC12 theo phạm vi bắt buộc và best-effort đã ghi; khóa tính năng. |
| 9 | Rehearse DEMO, kiểm tra chạy từ hướng dẫn, asset licences và recording. | Một vòng ba phút cùng evidence, lỗi còn lại được phân loại. |
| 10 | Sửa lỗi cản demo, quay video, hoàn thiện submission và giới hạn. | Bản chạy, hướng dẫn, bảng AC và số đo thật. |

Ngôi nhà được dựng sau gate ảnh thô. Nếu hoàn thiện nội thất làm đổi kết quả perception, chạy lại đúng frame demo ở Ngày 5. Không thêm SQL, report hoặc live hardware vào ngày còn dư.

Chỉ có hai scenario như trong [DEMO.md](DEMO.md): S1 đi vào vùng cầu thang và không ngã; S2 có FALL gần cầu thang. IMU của cả hai là bản ghi thật từ bộ dữ liệu, ghép theo timeline. Đoạn ghép được chọn sau khi chạy detector, không chỉnh ngưỡng để ép kết quả.

## Kiểm chứng hành vi

| ID | Bài thử | Kỳ vọng |
|---|---|---|
| T1 | Vào zone cầu thang. | HUD hiện advisory trong mục tiêu latency; SAM chạy trên frame lúc vào zone và kết quả được ghi; không gửi thông báo. |
| T2 | Vào lại zone cầu thang trong cooldown. | Không hiện lại HUD; log ghi lần vào lại. |
| T3 | FALL ngay sau khi detector vừa tiêu thụ cooldown ở một cửa sổ trước. | Critical incident và contact 1 ngay; cooldown không chặn. |
| T4 | Bấm "I'm OK" sau FALL. | Self-report được lưu và hiển thị; contact 1 đã nhận, hạn caregiver vẫn chạy. |
| T5 | Hai FALL cách nhau dưới fall_merge_seconds. | Một incident, không gửi contact 1 lần thứ hai. |
| T6 | Caregiver ack trước hạn, và một run khác không ack. | Run đầu chặn contact 2; run sau gửi contact 2 một lần. |
| T7 | SAM timeout/NoMatch/response lỗi hoặc frame thiếu. | Hiển thị trạng thái đúng, không bịa mask; FALL và advisory vẫn được xử lý. |
| T8 | Stop khi Meta/timer đang chờ. | Không tạo notification mới từ callback muộn; late evidence được ghi nguồn nếu lưu. |
| T9 | Restart giữa run khi DB hoạt động bình thường. | Run bị Interrupted; không tiếp tục detector với buffer sai. |
| T10 | Refresh/reconnect và Start run mới. | Khôi phục snapshot đã commit; run mới không dùng chung buffer/count. |
| T11 | Hết recording khi deadline chưa tới. | Recording ended nhưng run còn hoạt động; timer hoàn tất trước run timeout đã kiểm tra. |
| T12 best-effort | Commit lỗi giữa run. | Cố gắng dừng/cancel task, log lỗi và ghi Interrupted nếu còn ghi DB được. Ghi rõ thao tác nào thành công; không yêu cầu phục hồi nhất quán hoàn toàn để nghiệm thu MVP. |

Đây là kế hoạch kiểm chứng, chưa phải các test đã chạy. Dùng sink mô phỏng khi thực thi.

## Khi bị chậm

| Vấn đề | Điều chỉnh |
|---|---|
| Model/scaler chưa load. | Giải quyết G1 trước. Chưa công bố model thật đã chạy. |
| Detector báo FALL trên đoạn S1. | Chọn đoạn IMU khác bằng cách chạy detector, ghi lại tỉ lệ báo nhầm; không chỉnh ngưỡng. |
| SAM không nhận render. | Dùng quyết định G2 trước khi dựng nhà hoàn chỉnh; nếu dùng iPhone phải ghi nguồn và manual mapping. |
| API reasoning chậm. | Một model reasoning, ít frame và tools; giới hạn theo SPEC. |
| Nhà tốn thời gian. | Giữ bốn phòng đủ inventory, giảm chi tiết geometry và hiệu ứng. |
| Export video khó. | Keyframes cùng timestamp và camera version đủ cho P0. |
| Có thời gian dư. | Sửa lỗi, kiểm chứng và rehearsal; mở rộng vẫn để sau. |

## Bằng chứng giao nộp

Lưu kết quả G1/G2/G3, bảng T1 đến T11, ghi nhận best-effort T12 riêng, tỉ lệ báo nhầm FALL, ảnh/clip demo, model/config refs, loại dữ liệu, asset licences và hướng dẫn chạy. Dùng [SUBMISSION.md](SUBMISSION.md) làm mô tả dự thi theo MVP 1; luật nâng mức do nhiều sự kiện lặp lại và điểm dáng đi theo tuần nằm trong backlog. Báo riêng thời gian detector gom/xác nhận cửa sổ, thời gian từ confirmed detection tới incident và thời gian API analysis.

Đánh dấu AC chưa đạt hoặc chưa kiểm chứng. Chỉ công bố độ chính xác/độ trễ có bằng chứng phù hợp; demo synthetic không thay thế đánh giá thực địa.
