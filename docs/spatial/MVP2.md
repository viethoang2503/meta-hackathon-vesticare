# MVP 2. Người thật, thiết bị thật

Bản v0.2 ngày 2026-10-31. Hạn nộp còn 4 đến 5 ngày. MVP 1 ([ACCEPTANCE.md](ACCEPTANCE.md)) giữ nguyên làm bản dự phòng.

## Mục tiêu

Một người thật đeo thiết bị thật. Hệ thống chạy trực tiếp từ cú ngã tới tin nhắn của người chăm sóc. Kết quả được đo trên chính phần cứng của dự án.

MVP 1 chứng minh logic trên dữ liệu ghi sẵn của nhiều người, ghép với nhà 3D. MVP 2 trả lời câu hỏi còn lại: hệ thống có chạy với một người thật không.

## Thiết bị

| Thiết bị | Vai trò | Nhãn nguồn trên giao diện |
|---|---|---|
| ESP32 + MPU-6050 đeo ở hông | IMU, gửi batch 10 mẫu ở 50 Hz vào `/api/sensor/batch` | `ESP32 waist IMU` |
| iPhone 1 gắn ở trán | Camera ngang mắt, loa và micro. Màn hình úp vào trán nên không dùng làm HUD | `Phone camera at eye height (glasses proxy)` |
| iPhone 2 | Người chăm sóc: trang Ack và Telegram | `Caregiver phone` |
| Laptop | Backend, màn hình toàn cảnh, bản sao HUD cho khán giả | |

Không gọi điện thoại là kính ở bất kỳ đâu.

## Trong phạm vi

1. **G4, gate phần cứng.** Làm trước mọi thứ khác.
2. **Phiên sống.** IMU từ ESP32 và ảnh từ iPhone 1 vào cùng `StreamMonitor`, incident, đếm ngược, liên hệ 1 và liên hệ 2 như MVP 1. Không có bản luật thứ hai.
3. **Trang `/glasses/` trên iPhone 1, HUD bằng âm thanh.** Camera gửi ảnh định kỳ, SAM tìm cầu thang trong ảnh thật thay cho zone soạn sẵn. Loa đọc lời nhắc. Sau khi ngã, hệ thống hỏi và nghe người đeo theo mục "Hội thoại sau khi ngã". Người đeo chỉ chạm một lần lúc bắt đầu phiên để iPhone cho mở loa và micro. Laptop chiếu bản sao HUD để khán giả thấy người đeo nghe gì.
4. **Người chăm sóc trên iPhone 2.** Trang Ack và tin Telegram thật qua tích hợp sẵn có.
5. **Agent** giải thích trên ảnh thật, cùng luật trích bằng chứng như MVP 1.
6. **S3.** Một lượt ghi thật của cùng một người (video ở trán, IMU ở hông, đồng bộ bằng một cái vỗ tay), xem lại được trên repo mà không cần phần cứng.
7. **Bảng thử nghiệm.** Ít nhất 2 người, ngã có kiểm soát lên nệm và các hoạt động dễ gây báo nhầm, tính theo từng người.
8. **Nhà 3D đổi vai** thành bản đồ cho người chăm sóc, không còn là bằng chứng chính.
9. **Nếu còn thời gian.** Đánh giá chéo trên một bộ dữ liệu ngã chưa dùng khi train; đường link cho giám khảo tự chụp cầu thang.

## Hội thoại sau khi ngã

Không chạm vào điện thoại. Code quyết định báo hay không; agent AI không nằm trên đường an toàn này.

```
Ngã + nằm yên 2 s
  → loa: "Are you OK?"            micro mở, tối đa 30 s (chỉnh được tới 60 s)
  → giây 10 và 20, nếu chưa rõ:   "Say I'm OK, or I will call your caregiver."
  → "I'm OK", "Tôi ổn"            → "OK, I won't call anyone."      → cancelled_by_wearer
  → "Help", "Cứu", "call", "ngã"  → "Calling your caregiver now."  → báo ngay
  → im lặng, tiếng ồn, không rõ   → tiếp tục nghe; hết 30 s thì báo
Micro tắt khi có kết quả. Câu nghe được lưu vào bằng chứng của incident.
```

- `wearer_cancel_seconds` là 30 cho phiên sống. MVP 1 giữ 7.
- Nhận dạng giọng nói bằng Whisper `small` trên laptop (faster-whisper, int8, CPU). iPhone mở micro một lần lúc bắt đầu phiên và gửi đoạn âm thanh về backend trong lúc nghe; không gửi cho bên thứ ba. Đo trên Apple M3 với 6 câu đọc bằng giọng máy: đúng cả 6 câu Anh và Việt, khoảng 1,0 đến 1,2 s mỗi câu. Model `base` nhanh hơn (khoảng 0,3 s) nhưng sai tiếng Việt.
- Phân loại câu trả lời bằng `dashboard/glasses/reply.js`: cụm cần giúp được xét trước; chỉ câu "I'm OK" rõ ràng mới hủy. Một câu vừa ổn vừa nhờ gọi người ("I'm OK, call my son") được tính là cần giúp.
- Nút BOOT trên ESP32 là phương án dự phòng.

## Lệnh giọng nói và agent

Người đeo có thể nói tự do, ví dụ "I fell and my hip hurts, call my daughter". Nguyên tắc: **AI chỉ được làm cảnh báo mạnh hơn, không bao giờ làm yếu đi.**

1. Code phân loại câu nói trước. Có ý cần giúp thì báo ngay, không chờ agent.
2. Sau đó Muse Spark đọc câu nói và chỉ được gọi các hành động trong danh sách cho phép: báo liên hệ 1, báo liên hệ 2, gọi cấp cứu mô phỏng. Nó tóm tắt tình trạng ("đau hông", "không đứng dậy được") vào tin nhắn cho người chăm sóc.
3. Agent không có hành động hủy. Chỉ câu "I'm OK" rõ ràng qua code mới hủy được.
4. Agent lỗi hoặc chậm thì cảnh báo vẫn đi như bước 1.
5. Không gọi số cấp cứu thật (115) trong dự án. "Cấp cứu" là một liên hệ mô phỏng có nhãn rõ ràng.

## Ngoài phạm vi

- Kính Meta thật, Meta Wearables Device Access Toolkit, app iOS gốc.
- Train lại model. Dữ liệu tự thu quá ít và dễ rò rỉ giữa train và test.
- Định vị trong nhà.
- Ngã trên cầu thang thật.
- Thử nghiệm với người già hoặc bệnh nhân.

## G4. Gate phần cứng

Firmware đặt dải đo ±16 g và ±2000 °/s (`vesticare-sensor/src/imu.cpp`). Dải mặc định của thư viện có thể cắt cú ngã ở 2 g.

| Kiểm tra | Đạt khi |
|---|---|
| Đứng yên 30 s, đai ở hông | Trung bình SVM trong 1 ± 0,05 g |
| Mất mẫu | Ít hơn 1% mẫu bị thiếu trong 5 phút qua Wi-Fi |
| Ngã lên nệm, 3 lần | Đỉnh SVM vượt 2,5 g và không bị cắt ở một giá trị cố định |
| Model trên bản ghi thiết bị | Ghi lại số cú ngã bắt được và số hoạt động báo nhầm |

Nếu model không bắt được ngã trên thiết bị, tìm nguyên nhân ở hướng trục, vị trí đeo và tần số lấy mẫu. Không chỉnh ngưỡng để ép kết quả. Kết quả xấu vẫn được công bố.

## Tiêu chí nghiệm thu

| ID | Tiêu chí |
|---|---|
| M1 | Tín hiệu thiết bị qua G4 |
| M2 | Ngã thật lên nệm rồi nằm yên: iPhone 1 hỏi "Are you OK?". Nói "I'm OK" thì `cancelled_by_wearer`, không ai nhận tin. Nói "Help" thì báo ngay. Im lặng 30 s thì Telegram tới iPhone 2, không Ack sau 20 s thì liên hệ 2 |
| M3 | SAM nhận ra cầu thang thật từ camera ở trán trong x/N lần tiếp cận, ghi riêng lúc đủ sáng và lúc tối |
| M4 | Bảng thử nghiệm được công bố theo từng người, dù kết quả thế nào |
| M5 | S3 cho cùng incident mỗi lần xem lại |
| M6 | Mọi khối dữ liệu có nhãn nguồn đúng như bảng thiết bị |
| M7 | Thử giọng nói trên iPhone: ghi số lần nhận đúng "I'm OK", "Help" và số lần tiếng ồn bị nhận nhầm thành "I'm OK". Lần nhận nhầm thành "I'm OK" phải bằng 0 |

## An toàn

- Chỉ ngã lên nệm dày trên sàn phẳng, luôn có người đứng cạnh.
- Người tham gia là người trưởng thành khỏe mạnh, đồng ý trước khi ghi.
- Không đưa mặt người khác vào video nộp bài khi chưa được phép.

## Mạng

ESP32, laptop và hai iPhone dùng chung một mạng riêng. Wi-Fi ở hội trường có thể chặn thiết bị nói chuyện với nhau. ESP32 chỉ dùng Wi-Fi 2,4 GHz. Safari chỉ cho camera chạy qua HTTPS, nên `/glasses/` cần một tunnel hoặc chứng chỉ cục bộ.

## Phân công

| Ngày | Người làm dự án | Code và phân tích |
|---|---|---|
| 1 | Cài PlatformIO, nạp firmware, đai đeo, ghi các tư thế G4 | Script ghi và phân tích, so với SisFall, quyết định G4 |
| 2 | Giá gắn iPhone ở trán, nệm, liên hệ chuyên gia | Phiên sống, trang `/glasses/` |
| 3 | Thử `/glasses/` ở nhà | Trang người chăm sóc, Telegram |
| 4 | Thử nghiệm với 2 đến 3 người, ghi S3 | S3 xem lại được, bảng kết quả |
| 5 | Kịch bản pitch, tập demo | Nhà 3D đổi vai, cập nhật nghiệm thu và bài nộp |
| 6 nếu còn | Quay video | Dự phòng |

Code không cần phần cứng (phiên sống, `/glasses/`, người chăm sóc) có thể làm trước Ngày 1 bằng IMU giả lập từ bản ghi SisFall.

## Ghi chú về đánh giá chéo

KFall nằm trong `ml/data/raw/` và được dùng trong `ml/training/preprocess_kfall.py`, nên không dùng làm tập kiểm tra độc lập. Đánh giá chéo cần một bộ khác, ví dụ UMAFall hoặc MobiAct, sau khi kiểm tra điều khoản tải.
