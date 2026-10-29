# Demo MVP 1 trong khoảng ba phút

Bản v0.4. Đây là walkthrough dự kiến, chưa phải bằng chứng tính năng đã chạy. [SPEC.md](SPEC.md) định nghĩa AC và tham số; [PLAN.md](PLAN.md) mô tả các bước trước khi quay.

## Chuẩn bị

1. G1, G2, G3 đã có kết quả trong [GATES.md](GATES.md).
2. Mở một tab workspace với nhà một tầng, bốn phòng và cầu thang lên chiếu nghỉ.
3. Chuẩn bị hai scenario đã chạy qua detector và lưu kết quả. S1 đi vào vùng cầu thang buổi tối, không ngã, detector không báo FALL. S2 có FALL gần cầu thang. Mỗi run có pre-roll đủ cho detector.
4. Kiểm tra source badge, simulated contacts, model source và API status. Giữ trạng thái gửi ra ngoài Disabled.
5. Đảm bảo run timeout đủ cho analysis và các deadline trong SPEC. Các mốc thời gian dưới đây là budget trình diễn, điều chỉnh theo latency thực.

## Các bước và tiêu chí nghiệm thu

| Thời gian mục tiêu | Thao tác | Kết quả người xem thấy | AC |
|---|---|---|---|
| 0:00 đến 0:20 | Giới thiệu nhà, camera/HUD và nguồn IMU/hình ảnh. | Có đủ nội thất; nguồn Rendered, Authored location và Scenario-paired rõ ràng. | AC01, AC12 |
| 0:20 đến 0:50 | Start S1. Người đeo đi vào vùng cầu thang. | HUD hiện "Stairs ahead. Use the handrail." Log advisory có frame và mask SAM xác nhận cầu thang. Không ai bị báo. | AC04, AC06 |
| 0:50 đến 1:05 | Stop S1 và Start S2. | Run mới có session riêng; log của S1 vẫn xem lại được. | AC09, AC10 |
| 1:05 đến 1:30 | Người đeo ngã gần cầu thang và nằm yên. Không bấm gì. | HUD hiện "Are you OK?" và đếm ngược 7 giây. Hết giờ, contact 1 nhận cảnh báo. | AC02, AC07 |
| 1:30 đến 2:10 | Chọn marker ở cầu thang và mở evidence drawer. | IMU quanh cú ngã, frame nguồn, mask SAM thật, tool log của agent và summary có bằng chứng, kèm đề xuất sửa môi trường. Nếu API còn chờ, trạng thái Pending/Running hiển thị thật. | AC03, AC04, AC05 |
| 2:10 đến 2:40 | Chờ hết hạn ack để thấy contact 2, rồi acknowledge. | Action log ghi đúng lần gửi contact 1, contact 2 và ack. | AC08 |
| 2:40 đến 3:00 | Stop, mở lại incident và refresh. | Bằng chứng và trạng thái đã lưu vẫn truy cập được. | AC09, AC12 |

Nếu độ trễ thực không vừa ba phút, rút ngắn phần giới thiệu hoặc dùng keyframes. Giữ nguyên policy và ghi thời gian thực. Kết quả analysis lưu sẵn chỉ dùng ở phần review có nhãn Cached, không giới thiệu là request vừa chạy.

## Câu dẫn tiếng Anh

- "This is an authored home simulation. Location comes from the scenario, while the fall detector and Meta API calls run for real."
- "The camera tells us what is ahead. The IMU tells us whether the person actually fell."
- "The fall detector also fires on ordinary activity, so a fall only counts if the person stays still afterwards, and the glasses give the wearer seven seconds to cancel a false alarm before anyone is called."
- "We tested whether the IMU could predict instability before a fall. It could not separate patients from healthy people on stairs, so prevention here comes from context, not from motion."
- "Each explanation links to the exact sensor interval, source image and segmentation result."

Không nói video và IMU là bản ghi đồng thời của một người. Không nói hệ thống phát hiện mất thăng bằng.

## Kiểm chứng riêng ngoài video ba phút

- T2 kiểm tra advisory không hiện lại trong cooldown.
- T4 có một run bấm "I'm OK" trong lúc đếm ngược để chứng minh không ai bị báo.
- T6 có một run ack trước hạn để chứng minh chặn contact 2.
- T7 đến T11 kiểm tra lỗi Meta, Stop, restart, refresh và recording kết thúc khi DB hoạt động bình thường.
- T12 ghi nhận xử lý commit lỗi ở mức best-effort.
- Tỉ lệ bắt ngã và báo nhầm theo incident, đo bằng `ml/scripts/gate_g1_incidents.py` và `ml/scripts/gate_g1_daily_by_activity.py` với bộ lọc nằm yên đang dùng, được công bố cùng submission.

Bảng kết quả kiểm chứng đi kèm submission ghi AC nào đã đạt, chưa đạt hoặc chưa chạy.
