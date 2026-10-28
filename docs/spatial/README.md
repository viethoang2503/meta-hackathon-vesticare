# Vesti-Care Spatial

Thiết kế v0.2. Phạm vi hiện tại là cập nhật tài liệu MVP 1. Ứng dụng chưa được triển khai theo thiết kế này.

Vesti-Care Spatial kết hợp classifier IMU, Meta AI và một căn nhà 3D có nội thất. Người đeo phản hồi qua HUD kính mô phỏng. Người chăm sóc chọn sự kiện trong nhà để xem bằng chứng và xử lý cảnh báo.

## Phạm vi đã chốt

- Một người thực hiện trong 10 ngày, có IMU và iPhone, có quyền Meta Model API.
- Demo web trên một tab, UI và lời cảnh báo tiếng Anh.
- Nhà một tầng với bốn phòng và cầu thang ngắn lên chiếu nghỉ. Nội thất cách điệu, màu tự nhiên, bố cục rõ.
- Start và Stop cho replay 1x. Mỗi lần chạy có session riêng.
- Classifier, SAM và agent chạy thật. Nhà, vị trí, đường đi và HUD được mô phỏng.
- High hỏi người đeo trước qua nút "I'm OK". FALL báo người chăm sóc ngay.
- Có xác nhận của người chăm sóc và escalation mô phỏng tới liên hệ thứ hai.
- Mọi phần mở rộng bắt đầu sau khi MVP 1 hoàn thành và được nghiệm thu.

## Đọc tài liệu

- [DEMO.md](DEMO.md) là trang ngắn hướng dẫn câu chuyện ba phút và bằng chứng cần trình diễn.
- [SPEC.md](SPEC.md) định nghĩa hành vi, policy, bảng tham số và tiêu chí nghiệm thu.
- [ARCHITECTURE.md](ARCHITECTURE.md) giải thích module, dữ liệu và xử lý bất đồng bộ tối thiểu.
- [PLAN.md](PLAN.md) phân bổ 10 ngày và các gate trước khi triển khai tiếp.
- [SUBMISSION.md](SUBMISSION.md) là mô tả dự thi đã thu gọn theo MVP 1.

## Điều gì chạy thật

| Thành phần | Nguồn trong MVP 1 |
|---|---|
| IMU và classifier | Model thật trên replay; ghi rõ recording thực hay tín hiệu tổng hợp. |
| SAM và agent | API thật; trạng thái và bằng chứng của từng lần gọi được lưu. |
| Nhà và camera chính | Render từ căn nhà đã dựng; phải qua gate SAM Ngày 1. |
| Vị trí và đường đi | Metadata kịch bản, được gắn nhãn Authored location. |
| HUD và người liên hệ | UI và kênh thông báo mô phỏng; tắt gửi ra ngoài. |
| Phương án dự phòng hình ảnh | Clip iPhone quay trước, ánh xạ zone thủ công, ghi rõ Recorded video. |

Ghép IMU và hình ảnh khác nguồn theo timeline là scenario pairing, không chứng minh đó là một người được ghi đồng thời. Video thật chỉ cung cấp thêm bằng chứng perception trên ảnh thật, chưa kiểm chứng toàn bộ pipeline thực địa.

## Những phát hiện đã kiểm tra

Model và scaler nằm ở `ml/model_3class/`. Config hiện trỏ tới `ml/models_3class/`, cần sửa khi triển khai. Folder upload có ba model Keras, chưa có TFLite. Archive cho thấy input 100 mẫu với 7 kênh và output ba lớp; runtime TensorFlow và inference chưa được kiểm chứng trong lượt sửa tài liệu.

`preprocess_vestibular.py` gán HS thành NORMAL. Chỉ BV/UV trong một số hoạt động, gồm Stairs, nhận INSTABILITY. Nhãn này là proxy theo cohort và hoạt động, chưa phải annotation từng episode mất thăng bằng. Chưa có manifest chứng minh weights upload dùng chính pipeline đó. Gate Ngày 1 phải xác minh cả provenance và hành vi model.

## Thay đổi từ v0.1

v0.2 thu gọn nhà và lifecycle. Bỏ Pause/Resume, Settling, ack revision framework, durable job queue, batch sequence/hash và đồng bộ nhiều tab khỏi P0. Giữ session isolation, incident grouping, FALL vượt cooldown, status lưu trong DB và snapshot khi mở lại.

Experience First ưu tiên một vòng demo hoàn chỉnh. Laziness Protocol bỏ cơ chế tổng quát chưa cần cho bản một process. Các phần mở rộng như hỏi lịch sử, báo cáo và chỉnh sửa nhà nằm trong backlog.

MVP 1 có hai scenario S1/S2. Session dùng Running, Stopped và Interrupted. Luật nâng mức do nhiều episode lặp lại để sau; mô tả dự thi được sửa tương ứng. Xử lý commit lỗi là best-effort, không phải cam kết transactional recovery.
