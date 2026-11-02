# Demo MVP 2 trong khoảng ba phút

Bản v0.1, ngày 2026-11-02. Đây là walkthrough dự kiến. Chưa có video và chưa có lần chạy nào trên phần cứng thật, nên các mốc thời gian là budget, không phải số đo. Trạng thái bằng chứng nằm ở [ACCEPTANCE_MVP2.md](ACCEPTANCE_MVP2.md). Việc phải làm trước khi quay nằm ở [MVP2_TODO_NGUOI_LAM.md](MVP2_TODO_NGUOI_LAM.md).

## Chuẩn bị

1. Backend chạy, cổng 8010, `TELEGRAM_BOT_TOKEN`, hai chat id và `DEVICE_TOKEN` đã điền, ESP32 đã nạp firmware và cùng Wi-Fi.
2. Tunnel chạy (`cloudflared tunnel --url http://127.0.0.1:8010`). Không chiếu địa chỉ tunnel lên màn hình.
3. Mở `http://127.0.0.1:8010/spatial/live.html` trên laptop. Chọn ESP32 và Telegram, tick "Record camera frames" nếu người thử đồng ý ghi.
4. Gắn iPhone 1 ở trán, camera hướng ra trước, loa không bị che. Quét QR từ `wearer_link.py` và bấm Start. Mã ghép nối dùng một lần, tạo lại nếu hết hạn.
5. iPhone 2 của người chăm sóc đã nhắn bot trước đó. Người làm liên hệ 2 online.
6. Nệm dày, người đứng cạnh, người thử đã đồng ý.

Nếu thiếu phần cứng, chạy bằng thiết bị giả (`backend/tools/fake_esp32.py`) và nói rõ trên video và trong lời dẫn rằng đó là thiết bị giả. Không dùng cảnh đó để nói về bằng chứng phần cứng.

## Các bước

| Thời gian mục tiêu | Thao tác | Người xem thấy | M |
|---|---|---|---|
| 0:00 đến 0:20 | Giới thiệu hai điện thoại, mạch ở thắt lưng và trang laptop. | Các nhãn Device, Device clock, Phone, Camera đều báo sẵn sàng. Nhãn nguồn đúng. | M6 |
| 0:20 đến 0:45 | Người đeo đi về phía cầu thang. | Điện thoại nói "Stairs visible ahead." Trang laptop có kết quả SAM. | M3 |
| 0:45 đến 1:25 | Người đeo ngã lên nệm và nằm yên. Không ai nói gì. | Điện thoại hỏi "Are you all right? Please respond." Hết cửa sổ 30 giây, iPhone 2 nhận cảnh báo Telegram. | M2 |
| 1:25 đến 2:00 | Xem ngăn bằng chứng trên laptop. | Ảnh ghép theo thời gian với lúc ngã, nhãn camera, giải thích có dẫn nguồn của agent. Câu agent cho điện thoại nói đúng trạng thái. | M6, M9 |
| 2:00 đến 2:25 | Không bấm Ack. Chờ liên hệ 2 nhận tin leo thang, rồi Ack từ iPhone 2. | Nhật ký ghi lần gửi liên hệ 1, liên hệ 2 và Ack. | M2 |
| 2:25 đến 3:00 | Mở gói S3 vừa ghi và chạy `s3_replay.py`. | Hai lần phát lại cho cùng incident và cùng chuỗi hành động. | M5a, M5b |

Một lượt riêng ngoài video cho nhánh trả lời bằng giọng nói, vì ở lượt chính người đeo im lặng. Một lượt người đeo nói "I'm fine" trong cửa sổ để thấy việc hủy. Một lượt nói "I need help" để thấy báo ngay.

Nếu độ trễ thực không vừa ba phút, rút phần giới thiệu. Giữ nguyên policy và ghi thời gian thực.

## Câu dẫn tiếng Anh

- "The waist sensor detects the fall. The phone on my head asks if I am all right, and my answer is read by code, not by a model."
- "Silence is not a cancel. If I say nothing, my caregiver is alerted."
- "The camera shows what was in front of me when I fell. If the clocks cannot support that, the app says so instead of showing the image as proof."
- "The agent explains the evidence. It chooses which fixed sentence to send and cannot cancel or escalate."
- "The same recording replays with the same result, without asking a speech model or the agent again."

## Không được nói

- Không nói đây là kính Ray-Ban hay kính Meta. Đó là một chiếc iPhone gắn ở trán.
- Không nói hệ thống phát hiện mất thăng bằng, và không nói đây là kiểm chứng lâm sàng.
- Không nói đã thử với nhiều người hay tỷ lệ chính xác nếu M4 và M7 chưa có số.
- Không nói tin Telegram đã đến người chăm sóc khi chỉ dùng thông báo giả lập.
- Không hiện địa chỉ tunnel, token, mã ghép nối hay QR trên video.
