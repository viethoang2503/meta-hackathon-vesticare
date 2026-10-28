# Demo MVP 1 trong khoảng ba phút

Bản v0.2. Đây là walkthrough dự kiến, chưa phải bằng chứng tính năng đã chạy. [SPEC.md](SPEC.md) định nghĩa AC và tham số; [PLAN.md](PLAN.md) mô tả gate phải qua trước khi quay.

## Chuẩn bị

1. Hoàn tất G1/G2/G3 hoặc ghi rõ kết quả Inconclusive và giới hạn claims đã chốt.
2. Mở một tab workspace với nhà một tầng, bốn phòng và cầu thang lên chiếu nghỉ.
3. Chuẩn bị hai scenario ngắn đã chạy qua model. S1 có motion risk ở khu vực cầu thang. S2 có motion risk rồi FALL dưới 10 giây. Mỗi run có pre-roll đủ cho detector.
4. Kiểm tra source badge, simulated contacts, model source và API status. Giữ trạng thái gửi ra ngoài Disabled.
5. Đảm bảo source timeline và run timeout đủ cho analysis và các deadline trong bảng SPEC. Tất cả thời điểm dưới đây là budget trình diễn, điều chỉnh bằng latency thực.

## Các bước và tiêu chí nghiệm thu

| Thời gian mục tiêu | Thao tác | Kết quả người xem thấy | AC |
|---|---|---|---|
| 0:00 đến 0:20 | Giới thiệu nhà, camera/HUD và nguồn IMU/hình ảnh. | Có đủ nội thất; nguồn Rendered/Recorded, Authored location và Scenario-paired rõ ràng. | AC01, AC12 |
| 0:20 đến 0:45 | Start S1. Khi High hiện Are you OK?, bấm I'm OK. | Lưu self-report; contact 1 chưa nhận alert do bước hỏi này. | AC02, AC06 |
| 0:45 đến 1:20 | Chọn marker và mở evidence drawer. | IMU, frame nguồn, mask SAM thật, agent tool log và summary có bằng chứng. Nếu API còn chờ, trạng thái Pending/Running hiển thị thật. | AC03, AC04, AC05 |
| 1:20 đến 1:35 | Stop S1 và Start S2. | Run mới có session/context riêng; history của S1 vẫn xem lại được. | AC09, AC10 |
| 1:35 đến 2:00 | Quan sát FALL sau motion risk. Bấm I'm OK trên FALL để minh họa phản hồi. | Critical và contact 1 ngay; self-report không chặn caregiver alert. | AC07 |
| 2:00 đến 2:30 | Chờ contact 2 nếu chưa caregiver ack, rồi acknowledge. | Action log ghi đúng lần gửi và người chăm sóc nhận xử lý. | AC08 |
| 2:30 đến 3:00 | Stop, mở lại incident và refresh nếu thời gian cho phép. | Bằng chứng và trạng thái đã lưu vẫn truy cập được. | AC09, AC12 |

Nếu độ trễ thực không vừa ba phút, rút ngắn phần giới thiệu hoặc dùng keyframes; giữ nguyên policy và ghi thời gian thực. Cached analysis chỉ dùng ở phần review có nhãn Cached, không được giới thiệu là request live vừa hoàn tất.

## Câu dẫn tiếng Anh

- "This is an authored home simulation. Location comes from the scenario, while the IMU classifier and Meta API calls run for real."
- "The middle class is shown as model-predicted motion risk. Its training labels and validation limits are documented."
- "For a High alert, the wearer can respond first. A detected fall alerts the caregiver immediately."
- "Each explanation links to the exact sensor interval, source image and segmentation result."

Nếu dùng iPhone fallback, đổi phần giới thiệu hình ảnh thành Recorded video with manual zone mapping. Không nói video và IMU là recording đồng thời nếu chỉ được ghép theo kịch bản.

## Kiểm chứng riêng ngoài video ba phút

- T2 kiểm tra High không phản hồi thì contact 1 nhận alert.
- T5 kiểm tra request cũ không ack Critical.
- T6 có một run ack trước hạn để chứng minh chặn contact 2.
- T7 đến T11 kiểm tra timeout, Stop, restart và recording kết thúc khi DB hoạt động bình thường.
- T12 ghi nhận xử lý commit lỗi ở mức best-effort riêng; không coi đó là bảo đảm phục hồi đầy đủ.

Phần trình diễn không cần nhét mọi failure case. Bảng kết quả kiểm chứng đi kèm submission ghi AC nào đã đạt, chưa đạt hoặc chưa chạy.
