# Kế hoạch triển khai MVP 1

Bản v0.2 cho một người, 10 ngày. Đây là phân bổ dự kiến. [SPEC.md](SPEC.md) giữ phạm vi, AC và tham số. [DEMO.md](DEMO.md) giữ walkthrough ba phút.

## Checkpoint

Một nhà một tầng, bốn phòng và chiếu nghỉ; một active replay, một tab và một agent. Ngày 1 kiểm tra hai giả thuyết có thể làm đổi câu chuyện. Ngày 8 khóa tính năng. Hai ngày cuối dành cho kiểm chứng, rehearsal và submission. Mọi mở rộng nằm sau nghiệm thu MVP 1.

Lượt v0.2 chỉ cập nhật tài liệu. Chưa sửa config, cài TensorFlow, chạy inference hoặc gọi SAM.

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
| 2 | Session riêng, replay nội bộ, incident grouping, FALL vượt cooldown; status DB và analysis task tách ingest. | AC07, phần session isolation của AC09. Xử lý commit lỗi ở mức best-effort của AC10. |
| 3 | Nhà một tầng/bốn phòng, cầu thang chiếu nghỉ, nội thất và camera. | Inventory đủ; style khớp frame G2; AC01. |
| 4 | Timeline, frame lookup, marker và evidence drawer; Start/Stop. | AC03 và phần Stop/refresh của AC09/AC10. |
| 5 | SAM adapter và lưu mask/status; kiểm tra ảnh sau hoàn thiện nội thất. | AC04, các trạng thái thiếu ảnh/NoMatch/lỗi. |
| 6 | Agent tools, summary có evidence IDs và environmental fixes nhỏ. | AC05 và timeout không chặn FALL trong AC11. |
| 7 | HUD I'm OK, caregiver ack và hai contact mô phỏng. | AC06, AC07, AC08; phản hồi stale không ảnh hưởng FALL. |
| 8 | Hoàn thiện S1/S2 và các nhánh phản hồi T1/T2/T6, đo FPS/latency, kiểm tra Start/Stop/restart. | AC09 đến AC12 theo phạm vi bắt buộc và best-effort đã ghi; khóa tính năng. |
| 9 | Rehearse DEMO, kiểm tra chạy từ hướng dẫn, asset licences và recording. | Một vòng ba phút cùng evidence, lỗi còn lại được phân loại. |
| 10 | Sửa lỗi cản demo, quay video, hoàn thiện submission và giới hạn. | Bản chạy, hướng dẫn, bảng AC và số đo thật. |

Ngôi nhà được dựng sau gate ảnh thô. Nếu hoàn thiện nội thất làm đổi kết quả perception, chạy lại đúng frame demo ở Ngày 5. Không thêm SQL, report hoặc live hardware vào ngày còn dư.

Chỉ có hai scenario source như trong [DEMO.md](DEMO.md): S1 có motion risk gần cầu thang; S2 có motion risk rồi FALL trong dưới 10 giây. Các lựa chọn phản hồi dùng lại hai scenario này.

## Kiểm chứng hành vi

| ID | Bài thử | Kỳ vọng |
|---|---|---|
| T1 | High và I'm OK trước wearer deadline. | Self-report được lưu, bước hỏi đó không gửi contact 1; caregiver ack vẫn là dữ liệu riêng. |
| T2 | High không phản hồi. | Contact 1 nhận một thông báo mô phỏng sau wearer deadline. |
| T3 | Motion risk rồi FALL dưới 10 giây. | Critical và contact 1 ngay; cooldown cũ không chặn. |
| T4 | FALL sau I'm OK hoặc caregiver ack ở High. | Trạng thái mức thấp không chặn Critical; caregiver ack hiện tại bị reset, log cũ còn. |
| T5 | Request I'm OK/ack của High về sau khi đã Critical. | Applied false/stale; không xác nhận Critical. |
| T6 | Caregiver ack trước hạn, và một run khác không ack. | Run đầu chặn contact 2; run sau gửi contact 2 một lần. |
| T7 | SAM timeout/NoMatch/response lỗi hoặc frame thiếu. | Hiển thị trạng thái đúng, không bịa mask; FALL vẫn được xử lý. |
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
| Không có bằng chứng semantics hoặc thiếu raw recordings. | Ghi Inconclusive, dùng nhãn motion risk và giới hạn claims; chọn lại story/policy. |
| SAM không nhận render. | Dùng quyết định G2 trước khi dựng nhà hoàn chỉnh; nếu dùng iPhone phải ghi nguồn và manual mapping. |
| API reasoning chậm. | Một model reasoning, ít frame và tools; giới hạn theo SPEC. |
| Nhà tốn thời gian. | Giữ bốn phòng đủ inventory, giảm chi tiết geometry và hiệu ứng. |
| Export video khó. | Keyframes cùng timestamp và camera version đủ cho P0. |
| Có thời gian dư. | Sửa lỗi, kiểm chứng và rehearsal; mở rộng vẫn để sau. |

## Bằng chứng giao nộp

Lưu kết quả G1/G2/G3, bảng T1 đến T11 và ghi nhận best-effort T12 riêng, ảnh/clip demo, model/config refs, loại dữ liệu, asset licences và hướng dẫn chạy. Dùng [SUBMISSION.md](SUBMISSION.md) làm mô tả dự thi theo MVP 1; luật lặp lại nhiều episode rồi nâng mức nằm trong backlog. Báo riêng thời gian detector gom/xác nhận cửa sổ, thời gian từ confirmed detection tới incident và thời gian API analysis.

Đánh dấu AC chưa đạt hoặc chưa kiểm chứng. Chỉ công bố độ chính xác/độ trễ có bằng chứng phù hợp; demo synthetic không thay thế đánh giá thực địa.
