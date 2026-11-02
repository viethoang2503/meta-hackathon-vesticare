# Việc người làm dự án cần làm cho MVP 2

Cập nhật ngày 2026-11-01. Đây là những việc chỉ người làm dự án làm được: cần phần cứng, tài khoản, người khác hoặc ở hiện trường. Phần code đã sẵn sàng cho từng việc. Làm xong việc nào thì báo để chạy tiếp phần kiểm tra.

Không dán token, mật khẩu Wi-Fi hay link có `#t=` vào chat hoặc commit.

## 1. Chuẩn bị một lần (khoảng 20 phút, không cần ESP32)

| # | Việc | Cách làm | Mở khóa |
|---|---|---|---|
| 1.1 | Tạo bot Telegram mới | @BotFather, gửi `/newbot`. Dán `TELEGRAM_BOT_TOKEN=...` vào `backend/.env` | U3 chạy thật, M2 |
| 1.2 | Hai liên hệ nhắn bot | Từ iPhone 2 nhắn "hi" cho bot. Người làm liên hệ 2 cũng nhắn "hi". Sau đó báo để lấy chat id bằng `backend/tools/telegram_chats.py` | Live ready, M2 |
| 1.3 | Hẹn người làm liên hệ 2 | Người đó cần online lúc thử và lúc quay demo | M2 escalation |
| 1.4 | Tạo `DEVICE_TOKEN` | `openssl rand -hex 16`. Đặt cùng giá trị vào `backend/.env` và `vesticare-sensor/include/config.h` | Phiên sống, đồng bộ đồng hồ |
| 1.5 | Điền `config.h` | Wi-Fi 2,4 GHz, `BACKEND_URL "http://<IP laptop>:8010"`, `FIRMWARE_VERSION "2.0.0"` | Nạp firmware |

## 2. Có ESP32 (khoảng 1 giờ)

| # | Việc | Cách làm | Mở khóa |
|---|---|---|---|
| 2.1 | Mốc 1: mạch có chạy | Báo để bật backend `--host 0.0.0.0`. Chạy `cd vesticare-sensor && pio run -e esp32dev -t upload -t monitor`. Gửi vài dòng `acquired …, buffered …, dropped …, late reads …, last HTTP …` và dòng `Clock sync HTTP 200` | G4 |
| 2.2 | Ghi G4 | Theo `docs/spatial/evidence/mvp2/g4/README.md`: đứng yên 30 s, đi 5 phút, ngã lên nệm 3 lần, 10 lượt hoạt động thường ngày. Quay video có đồng hồ, ghi mốc giờ | M1 |

## 3. Có iPhone gắn trán

| # | Việc | Cách làm | Mở khóa |
|---|---|---|---|
| 3.1 | Làm giá gắn iPhone 1 ở trán | Camera hướng ra trước, loa không bị che | U0, M3, M7 |
| 3.2 | Thử lại U0 trên giá | Chạy tunnel, quét QR, 3 lượt "I answer" và 3 lượt "nobody answers". Ghi tiếng loa nghe rõ hay nhỏ | U0 |
| 3.3 | Thử giọng nói M7 | Mở `http://127.0.0.1:8010/spatial/drill.html` trên laptop, nhập tên người thử, bấm "Start drill session". Chạy tunnel, rồi `backend/tools/wearer_link.py --tunnel <URL>` ra QR cho iPhone 1. Mỗi lần bấm một nút là một lượt: máy hỏi "Are you all right?", người thử làm theo nút (nói OK, nói HELP, nói câu mơ hồ, im lặng, hoặc bật TV). Mỗi người cần 10 OK, 10 HELP, 10 câu mơ hồ, 10 lượt im lặng hoặc TV; trang tự ra bảng và đếm số lần hủy nhầm (phải bằng 0). Cửa sổ drill mặc định 15 s, chỉnh được từ 10 đến 60 s | M7 |
| 3.4 | Thử SAM ở cầu thang thật | Công cụ M3 đã xong. Mở `http://127.0.0.1:8010/spatial/stairs.html` trên laptop, bấm "Start camera session". Chạy tunnel rồi `backend/tools/wearer_link.py --tunnel <URL>` ra QR cho iPhone 1 (mã dùng một lần, hết hạn sau 10 phút, quét lại thì chạy lệnh lần nữa), bấm Start. Mỗi lượt là một lần bấm: chọn "stairs, good light", "stairs, dim light" hoặc "no stairs in view", đi về phía cảnh vài giây, bấm "End trial". Cần 10 lượt mỗi loại. Trang tự ra bảng x/N, báo nhầm và độ trễ SAM. Mục tiêu: đủ sáng thấy cầu thang ít nhất 80%, cảnh không có cầu thang báo nhầm tối đa 10%, thiếu sáng chỉ báo cáo | M3 |

## 4. Người và hiện trường

| # | Việc | Ghi chú | Mở khóa |
|---|---|---|---|
| 4.1 | Nệm dày, sàn phẳng, người đứng cạnh | Bắt buộc khi thử ngã | G4, M4, S3 |
| 4.2 | Hẹn ít nhất 2 người thử | Người trưởng thành khỏe mạnh, đồng ý trước khi ghi và công bố | M4, M7 |
| 4.3 | Ghi S3 | Một lượt đủ bộ: ESP32 + iPhone 1 cùng một người, người đó đồng ý trước. Mở `live.html`, tick "Record camera frames", bấm Start, ghép điện thoại, thực hiện lượt (ngã lên nệm, trả lời hoặc im lặng, Ack từ điện thoại liên hệ), bấm Stop. Rồi chạy `backend/tools/s3_export.py <session> --participant "<nhãn>" --publication granted --recorded-on <ngày>` ra `assets/scenarios/S3/`, và `backend/tools/s3_replay.py` ra bảng M5a/M5b. Gói không chứa âm thanh thô và bị từ chối nếu có token | M5 |
| 4.3b | Bảng M4 theo người | Với mỗi người, ghi một phiên ESP32 có ≥3 lượt ngã và ≥10 lượt hoạt động thường ngày, ghi mốc giây nguồn của từng lượt. Lập `trials.json` (mẫu trong chú thích đầu `backend/tools/m4_report.py`) rồi chạy công cụ đó | M4 |
| 4.4 | Gửi thư viện sơ cứu cho chuyên gia | `assets/first_aid/library.json`, cho giai đoạn sau P0 | Sau P0 |
| 4.5 | Quay video demo | Cuối cùng, theo `MVP2_PLAN.md` | Nộp bài |

## Đã xong

- U8 phần code: ghi ảnh khi phiên được đánh dấu, xuất gói S3, phát lại M5a/M5b, bảng M4 (2026-11-02). Đã chạy thử trên phiên giả lập, chưa có dữ liệu thật.
- U7: agent trả lời có dẫn bằng chứng, trang laptop `http://127.0.0.1:8010/spatial/live.html` (bắt đầu phiên, trạng thái thiết bị/điện thoại/camera, lời người đeo nghe và nói, incident, bằng chứng), mã ghép nối dùng một lần cho QR (2026-11-02).
- Công cụ bảng M3: trang `spatial/stairs.html`, phiên `camera_trial` (2026-11-02).
- U6 phần code: ảnh 1 Hz từ iPhone 1, SAM `stairs`, lời nhắc có cooldown, ảnh gần lúc ngã gắn vào incident (2026-11-02).
- U0 phần định dạng, phát loa, thu âm và ảnh trên iPhone cầm tay (2026-10-31).
