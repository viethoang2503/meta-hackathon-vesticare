# Vesti-Care Spatial

Bộ tài liệu MVP 1 và MVP 2. Trạng thái và bằng chứng MVP 1 nằm ở [ACCEPTANCE.md](ACCEPTANCE.md). Trạng thái MVP 2 nằm ở [ACCEPTANCE_MVP2.md](ACCEPTANCE_MVP2.md): phần code đã xong và đã thử bằng dữ liệu giả lập, phần cần phần cứng và người thật chưa chạy.

Vesti-Care Spatial kết hợp phát hiện ngã bằng IMU, camera có SAM 3.1, agent Muse Spark và một căn nhà 3D có nội thất. Camera cho biết phía trước có gì nguy hiểm; IMU cho biết người đeo có ngã thật hay không. Người đeo nhận lời nhắc và phản hồi qua HUD kính mô phỏng. Người chăm sóc chọn sự kiện trong nhà để xem bằng chứng và xử lý cảnh báo.

## Phạm vi MVP 1

- Một người thực hiện trong 10 ngày, có IMU và iPhone, có quyền Meta Model API.
- Demo web trên một tab, UI và lời cảnh báo tiếng Anh.
- Nhà một tầng với bốn phòng và cầu thang ngắn lên chiếu nghỉ. Nội thất cách điệu, màu tự nhiên, bố cục rõ.
- Start và Stop cho replay 1x. Mỗi lần chạy có session riêng.
- Classifier, SAM và agent chạy thật. Nhà, vị trí, đường đi và HUD được mô phỏng.
- Vào vùng nguy hiểm thì HUD nhắc phòng ngừa, SAM xác nhận trên khung hình, không báo ai.
- FALL chỉ được tính khi người đeo nằm yên sau đó. HUD hỏi "Are you OK?" và đếm ngược 7 giây; người đeo tự xác nhận ổn để hủy cảnh báo, im lặng thì báo người chăm sóc.
- Không phát hiện mất thăng bằng hay suýt ngã bằng IMU (xem GATES.md).
- Có xác nhận của người chăm sóc và escalation mô phỏng tới liên hệ thứ hai.
- Mọi phần mở rộng bắt đầu sau khi MVP 1 hoàn thành và được nghiệm thu.

## Đọc tài liệu

- [DEMO.md](DEMO.md) là trang ngắn hướng dẫn câu chuyện ba phút và bằng chứng cần trình diễn.
- [SPEC.md](SPEC.md) định nghĩa hành vi, policy, bảng tham số và tiêu chí nghiệm thu.
- [ARCHITECTURE.md](ARCHITECTURE.md) giải thích module, dữ liệu và xử lý bất đồng bộ tối thiểu.
- [PLAN.md](PLAN.md) phân bổ 10 ngày và các gate trước khi triển khai tiếp.
- [SUBMISSION.md](SUBMISSION.md) là mô tả dự thi đã thu gọn theo MVP 1.
- [GATES.md](GATES.md) ghi kết quả đo của Ngày 1 và các thí nghiệm về lớp INSTABILITY.
- [ACCEPTANCE.md](ACCEPTANCE.md) là bảng nghiệm thu AC01 đến AC12, T1 đến T12 và các số đo đã công bố.
- [CREDITS.md](CREDITS.md) ghi nguồn và giấy phép của thư viện, model, dữ liệu và asset.
- [MVP2.md](MVP2.md) là đặc tả MVP 2: người thật, thiết bị thật.
- [ACCEPTANCE_MVP2.md](ACCEPTANCE_MVP2.md), [SUBMISSION_MVP2.md](SUBMISSION_MVP2.md) và [DEMO_MVP2.md](DEMO_MVP2.md) là bảng nghiệm thu, mô tả dự thi và walkthrough của MVP 2.
- [MVP2_TODO_NGUOI_LAM.md](MVP2_TODO_NGUOI_LAM.md) liệt kê việc chỉ người làm dự án làm được.
- [MVP2_ARCHITECTURE.md](MVP2_ARCHITECTURE.md) là kiến trúc MVP 2: phiên sống, kênh người đeo, Telegram, agent giải thích và trấn an.
- [MVP2_PLAN.md](MVP2_PLAN.md) là thứ tự làm, lịch và cách cắt khi chậm của MVP 2.

## Phạm vi MVP 2 đã chốt

- Tiếng Anh trước; IMU thật → hỏi người đeo → Telegram thật → Ack/escalation.
- Backend giữ deadline 30 s; giọng nói không rõ hoặc mất kết nối không gia hạn. Stop ngừng thu nhưng incident đã mở vẫn được xử lý.
- Agent giải thích, cập nhật bằng chứng và trấn an bằng mẫu cố định. Sơ cứu, hướng dẫn đứng dậy và tiếng Việt nằm ngoài P0.
- Camera thật, SAM, S3 replay được; vị trí live chưa đo được hiện Unknown. Nhà 3D là minh họa.
- Gate phần cứng và iPhone từ ngày 1; lịch 5 ngày bao gồm quay video. Các giá trị nghiệm thu mới là mục tiêu, chưa phải số đo.

## Điều gì chạy thật

| Thành phần | Nguồn trong MVP 1 |
|---|---|
| IMU và classifier | Model thật trên replay bản ghi thật, chỉ dùng nhánh FALL. |
| SAM và agent | API thật; trạng thái và bằng chứng của từng lần gọi được lưu. |
| Nhà và camera chính | Render từ căn nhà đã dựng; phải qua gate SAM Ngày 1. |
| Vị trí và đường đi | Metadata kịch bản, được gắn nhãn Authored location. |
| HUD và người liên hệ | UI và kênh thông báo mô phỏng; tắt gửi ra ngoài. |
| Phương án dự phòng hình ảnh | Clip iPhone quay trước, ánh xạ zone thủ công, ghi rõ Recorded video. |

Ghép IMU và hình ảnh khác nguồn theo timeline là scenario pairing, không chứng minh đó là một người được ghi đồng thời. Video thật chỉ cung cấp thêm bằng chứng perception trên ảnh thật, chưa kiểm chứng toàn bộ pipeline thực địa.

## Những phát hiện đã kiểm tra

Chi tiết và lệnh chạy lại nằm trong [GATES.md](GATES.md).

- Model Keras và scaler load được từ `ml/model_3class/`. Bọc model bằng `tf.function` thì suy luận khoảng 1,5 ms mỗi cửa sổ.
- Nhánh FALL tạo incident trên 300/300 file ngã SisFall của subject tập TEST v2, nhưng cũng trên 78,5% file hoạt động thường ngày của người trẻ, phần lớn do model. Chưa xác minh được weights có được train trên các subject này hay không.
- Lớp INSTABILITY bật 71% với người khỏe đi bộ và 100% với người khỏe đi cầu thang. Train lại chỉ trên bộ vestibular (thí nghiệm C) thì model học loại hoạt động. Thêm thông tin hoạt động (thí nghiệm D) vẫn không tách được bệnh nhân với người khỏe trên cầu thang (AUC 0,49).
- SAM 3.1 nhận đúng cầu thang ở 3 góc và không nhận nhầm ở ảnh không có cầu thang. Muse Spark gọi tool đúng schema.

## Lịch sử thay đổi

v0.3 bỏ cảnh báo dựa trên lớp INSTABILITY sau kết quả G1, C, D. Phòng ngừa chuyển sang advisory theo zone có SAM xác nhận. Chỉ còn một mức incident (Critical khi ngã), nên bỏ mức High, hạn chờ người đeo và cơ chế ack theo mức.

### v0.2

v0.2 thu gọn nhà và lifecycle. Bỏ Pause/Resume, Settling, ack revision framework, durable job queue, batch sequence/hash và đồng bộ nhiều tab khỏi P0. Giữ session isolation, incident grouping, FALL vượt cooldown, status lưu trong DB và snapshot khi mở lại.

Experience First ưu tiên một vòng demo hoàn chỉnh. Laziness Protocol bỏ cơ chế tổng quát chưa cần cho bản một process. Các phần mở rộng như hỏi lịch sử, báo cáo và chỉnh sửa nhà nằm trong backlog.

MVP 1 có hai scenario S1/S2. Session dùng Running, Stopped và Interrupted. Luật nâng mức do nhiều episode lặp lại để sau; mô tả dự thi được sửa tương ứng. Xử lý commit lỗi là best-effort, không phải cam kết transactional recovery.
