# Việc cần làm để đóng MVP 2

Cập nhật ngày 2026-11-02. Phần code của MVP 2 (U0 đến U9) đã xong, kể cả các công cụ C1, C2, C4 và C5 mà bản trước ghi là "việc tôi còn nợ". File này là danh sách đầy đủ những gì còn lại, chia ba loại.

- **Việc của bạn** (phần A và B): cần phần cứng, người thật, tài khoản hoặc hiện trường. Làm xong mới có bằng chứng thật.
- **Việc đóng gói** (phần D): cập nhật bảng, commit, tag, video.
- **Những phần tôi chưa kiểm chứng** (phần E): mỗi mục nói rõ chưa chắc điều gì và bạn làm gì để biết.

Đánh dấu `[x]` khi xong. Trạng thái từng tiêu chí nằm ở [docs/spatial/ACCEPTANCE_MVP2.md](docs/spatial/ACCEPTANCE_MVP2.md). File cũ [docs/spatial/MVP2_TODO_NGUOI_LAM.md](docs/spatial/MVP2_TODO_NGUOI_LAM.md) vẫn còn nhưng file này thay thế nó.

## Máy của bạn hôm nay (tôi vừa kiểm, không in giá trị bí mật)

| Mục | Hiện nay | Cần làm |
|---|---|---|
| `backend/.env` | Chưa có `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID_1`, `TELEGRAM_CHAT_ID_2`, `DEVICE_TOKEN` | Phần A |
| `vesticare-sensor/include/config.h` | `BACKEND_URL` đã trỏ cổng 8010, `DEVICE_ID "esp32-01"`. `DEVICE_TOKEN` còn rỗng. `FIRMWARE_VERSION` đang là `"1.0.0"` | Điền token. Đổi version thành `"2.0.0"` cho khớp mã đang thử (chỉ là nhãn báo lên, không ảnh hưởng chạy) |
| Firmware | Build thành công (7 giây, `pio run -e esp32dev`, đo hôm nay) | Chưa nạp lên mạch |
| Backend | 410 test đạt, node 22/22 (đo hôm nay) | Không cần làm gì |

## Quy tắc khi làm

1. Không dán token, mật khẩu Wi-Fi, link có `#s=…&p=…` hay ảnh QR vào chat, commit hoặc video.
2. Không đổi ngưỡng hay định nghĩa đạt sau khi thấy kết quả. Nếu không đạt, sửa nguyên nhân rồi ghi lại, và công bố cả các lần không đạt.
3. Mỗi người thử phải đồng ý trước khi ghi và trước khi công bố. Chỉ ghi nhãn (ví dụ "person A") trừ khi họ cho phép dùng tên.
4. Dữ liệu từ thiết bị giả (`fake_esp32.py`) luôn mang nhãn "Simulated" và không thay bằng chứng thật.
5. Không gọi cấp cứu thật (115) trong lúc thử. Mọi lượt ngã dùng nệm dày và có người đứng cạnh.
6. Cổng 8000 là dự án khác của bạn. Backend dự án này chạy cổng 8010.

## Trạng thái các tiêu chí

| Tiêu chí | Hiện nay | Để thành bằng chứng thật |
|---|---|---|
| M1 (G4, mạch ESP32) | Chưa chạy | B2 |
| M2 (OK, HELP, hết hạn, Ack, leo thang, lỗi gửi) | Một phần: test và Telegram giả | B1 |
| M3 (cầu thang) | Chưa chạy | B4 |
| M4 (theo người, ít nhất 2 người) | Chưa chạy | B5 |
| M5a, M5b (phát lại S3) | Có bằng chứng giả lập, chưa có gói thật | B6 |
| M6 (nhãn nguồn, ảnh không đồng bộ) | Có bằng chứng | B7 xem lại trên mạch thật |
| M7 (giọng nói, hủy nhầm, tiếng vọng loa) | Chưa chạy | B3 |
| M8 (token, quyền) | Một phần | B8, và quyết định C3 |
| M9 (agent) | Có bằng chứng | Không cần thêm |
| Video demo | Chưa quay | D5 |

## Phần 0. Ba thứ dùng nhiều lần

**Chạy backend.** Chỉ trên máy: `cd backend && ../.verify/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010`. Cho ESP32 nối qua Wi-Fi, đổi `127.0.0.1` thành `0.0.0.0`.

**Ghép iPhone 1.** Chạy tunnel ở cửa sổ riêng: `cloudflared tunnel --url http://127.0.0.1:8010`. Rồi chạy
```
.verify/venv/bin/python backend/tools/wearer_link.py --tunnel https://<địa chỉ tunnel>
```
Quét QR bằng iPhone 1 và bấm Start. Mã dùng một lần và hết hạn sau 10 phút. Hết hạn thì chạy lại lệnh. Địa chỉ tunnel là địa chỉ công khai, không chia sẻ.

**Lấy mã phiên** cho các công cụ báo cáo: `.verify/venv/bin/python backend/tools/list_sessions.py` liệt kê 10 phiên gần nhất kèm giờ bắt đầu, nguồn (`esp32`, `fake_esp32`, `drill`, `camera_trial`), kênh gửi, số incident và có ghi ảnh hay không. Chép mã dài ở cột đầu.

## Phần A. Chuẩn bị một lần (khoảng 20 phút, chưa cần ESP32)

- [x] **A1. Tạo bot Telegram mới.** Mở @BotFather, gửi `/newbot`. Dán `TELEGRAM_BOT_TOKEN=...` vào `backend/.env`.
- [x] **A2. Hai liên hệ nhắn bot.** Từ iPhone 2 nhắn "hi" cho bot. Người làm liên hệ 2 cũng nhắn "hi". Hẹn người đó online lúc thử và lúc quay.
- [x] **A3. Lấy chat id.** Tắt backend trước, vì Telegram chỉ phát tin cho một bên đọc. Chạy `.verify/venv/bin/python backend/tools/telegram_chats.py`. Điền `TELEGRAM_CHAT_ID_1` và `TELEGRAM_CHAT_ID_2` vào `backend/.env`. Công cụ không in token (tôi đã chạy nó khi chưa có token: nó báo "TELEGRAM_BOT_TOKEN is not set").
- [x] **A4. Tạo `DEVICE_TOKEN`.** Chạy `openssl rand -hex 16`. Đặt cùng một giá trị vào `backend/.env` và `vesticare-sensor/include/config.h`.
- [x] **A5. Điền nốt `config.h`.** Wi-Fi 2,4 GHz (SSID và mật khẩu), IP laptop trong `BACKEND_URL` đúng mạng đang dùng, `FIRMWARE_VERSION "2.0.0"`.
- [ ] **A6. Chuẩn bị người và chỗ.** Hẹn ít nhất 2 người trưởng thành khỏe mạnh đồng ý. Chuẩn bị nệm dày, sàn phẳng, một người đứng cạnh khi ngã, một cầu thang thật, và điện thoại quay video có đồng hồ hiện giờ.

## Phần B. Từ "chưa có bằng chứng" thành bằng chứng thật

Làm theo thứ tự này. B1 chỉ cần bot và hai điện thoại. B2 lộ ra rủi ro lớn nhất, nên làm sớm.

### B1. M2 với Telegram thật (cần A1 đến A4, chưa cần ESP32)

Chuẩn bị: `export DEVICE_TOKEN=$(grep ^DEVICE_TOKEN backend/.env | cut -d= -f2)`, rồi chạy
```
.verify/venv/bin/python backend/tools/fake_esp32.py S2 --start --notify telegram
```
Lệnh này mở phiên với Telegram thật và phát kịch bản ngã. Mở `http://127.0.0.1:8010/spatial/live.html` để xem incident, mã incident hiện ở khung "Incident" (ví dụ `#14`). Ghép iPhone 1 theo phần 0 (cầm tay là đủ, chưa cần giá gắn).

Mỗi lượt là một lần chạy lại lệnh trên. Cách tạo từng nhánh:

- [x] **B1.1** Không làm gì. Hết cửa sổ 30 giây thì iPhone 2 (liên hệ 1) nhận cảnh báo.
- [x] **B1.2** OK trước hạn. Nói "I am fine" vào iPhone 1, hoặc chạy `curl -X POST http://127.0.0.1:8010/api/spatial/incidents/<mã incident>/wearer-response -H 'Content-Type: application/json' -d '{"response":"ok"}'`. Không ai nhận cảnh báo.
- [ ] **B1.3** HELP. Nói "I need help" vào iPhone 1 (chỉ nói được bằng giọng). Liên hệ 1 nhận cảnh báo ngay.
- [x] **B1.4** Bấm nút Acknowledge ở iPhone 2. Không có tin leo thang tới liên hệ 2.
- [x] **B1.5** Không bấm Ack. Sau 20 giây liên hệ 2 nhận tin leo thang. Rồi liên hệ 2 bấm Ack.
- [x] **B1.6** Lỗi gửi. Trên iPhone 2, chặn bot (Telegram, Block bot), chạy một lượt, rồi bỏ chặn. Liên hệ 2 phải được báo ngay. Telegram trả lỗi thế nào với bot bị chặn là chỗ tôi chưa chắc (xem E1).
- [x] **B1.7** OK muộn sau khi đã báo (nói "I am fine" sau khi cảnh báo đi). Phải ra tin cập nhật, không thu hồi cảnh báo. Nói OK hai lần để thấy lần lặp chỉ ghi log.
- [x] **B1.8** Bấm Stop trên `live.html` khi đang đếm ngược. Incident vẫn báo ở hạn. Làm thêm một lượt: nói OK, bấm Stop, rồi nói HELP trước hạn: cảnh báo vẫn phải đi.
- [x] **B1.9** Tắt hẳn backend (Ctrl+C) giữa lúc đếm ngược, mở lại. Phiên phải ghi "Interrupted" và không có tin tự gửi.
- [ ] **B1.10** Nói HELP đúng lúc hàng chờ STT đầy (bật nhiều lượt cùng lúc là khó tạo). Nếu không tạo được, bỏ qua và ghi chưa thử.

Sau khi xong: chụp màn hình tin trên iPhone 2 (kèm giờ). Rồi:
```
.verify/venv/bin/python backend/tools/list_sessions.py
.verify/venv/bin/python backend/tools/m2_report.py <mã phiên 1> <mã phiên 2> ...
```
Báo cáo nằm ở `docs/spatial/evidence/mvp2/m2/report.md` và `.json`. Nó cho biết nhánh nào đã thấy bằng Telegram thật, nhánh nào chưa, thời gian từ lúc phân loại tới lúc lập lịch gửi (mục tiêu ≤ 2 giây), và thời gian Telegram trả lời. Nhánh nào còn "Not seen" thì làm bù.

### B2. M1 (G4) với ESP32 (cần A4 đến A6)

Làm đúng theo [docs/spatial/evidence/mvp2/g4/README.md](docs/spatial/evidence/mvp2/g4/README.md). Tóm tắt:

- [ ] **B2.1** Nạp firmware: `cd vesticare-sensor && pio run -e esp32dev -t upload -t monitor`. Chờ dòng `Clock sync HTTP 200`. Gửi tôi vài dòng log dạng `acquired …, buffered …, dropped …, late reads …, last HTTP …`.
- [ ] **B2.2** Gắn mạch ở thắt lưng và nhớ hướng gắn. Mở phiên bằng `live.html` (nguồn "ESP32 waist IMU", device id `esp32-01`, chọn Telegram hoặc Simulated).
- [ ] **B2.3** Quay video có đồng hồ và làm đủ bộ: đứng yên 30 giây, đi bình thường 5 phút, ngã lên nệm 3 lần rồi nằm yên 10 giây, ít nhất 10 lượt hoạt động thường ngày (ngồi xuống, đứng lên, cúi nhặt đồ, nằm xuống chủ ý). Ghi mốc giây của từng lượt.
- [ ] **B2.4** Bấm Stop. Lấy mã phiên bằng `list_sessions.py`. Chỉ dùng phiên ghi sau bản sửa ngày 2026-11-02: G4 giờ trừ số mẫu mạch tự bỏ tính từ batch đầu của phiên, nên phiên cũ không có mốc này sẽ không Pass phần truyền tải. Chạy `.verify/venv/bin/python backend/tools/g4_report.py <mã phiên> --rest 2:32 --fall 340:355 --fall 370:385 --fall 400:415 --adl 430:440 ...` với mốc của bạn.
- [ ] **B2.5** Đọc dòng trục trọng lực trong báo cáo. Nếu trục của mạch khác trục của dữ liệu train, model có thể sai vì nhạy với hướng gắn (xem E2). Sửa hướng gắn rồi ghi lại, không chỉnh ngưỡng.

Kết quả nằm ở `docs/spatial/evidence/mvp2/g4/<mã phiên>.md` và `.json`. Giữ cả video.

### B3. M7 giọng nói (cần giá gắn trán và ít nhất 2 người)

- [ ] **B3.1** Làm giá gắn iPhone 1 ở trán: camera hướng ra trước, loa không bị che.
- [ ] **B3.2** Chạy lại U0 trên giá. Chạy backend với `VC_PROBE_TOKEN=$(openssl rand -hex 16)` (đặt biến này trước lệnh uvicorn, không ghi vào file), mở `https://<tunnel>/glasses/probe.html#t=<token>` trên iPhone 1 (chi tiết ở `docs/spatial/evidence/mvp2/u0/README.md`). Làm 3 lượt "I answer" và 3 lượt "nobody answers". Ghi tiếng loa nghe rõ hay nhỏ khi đeo.
- [ ] **B3.3** Mở `http://127.0.0.1:8010/spatial/drill.html`, nhập tên người, để Window là 30 (hoặc lớn hơn, tối đa 60), bấm "Start drill session". Ghép iPhone 1 theo phần 0.
- [ ] **B3.4** **Lượt người trả lời.** Mỗi người làm 10 OK (phủ đủ 5 câu chuẩn trong spec), 10 HELP, 10 câu mơ hồ hoặc phủ định (có "Yes" và "I'm not sure I'm OK"), 10 lượt im lặng hoặc TV hoặc loa hệ thống. Mỗi lượt là một lần bấm nút, chờ điện thoại hỏi, rồi làm theo nút. Mục tiêu: OK ≥ 9/10, HELP ≥ 9/10, hủy nhầm bằng 0.
- [ ] **B3.5** **Lượt tiếng vọng loa (C2, đã làm xong).** Không ai nói trong các lượt này, TV tắt. Với mỗi mức loa `low`, `mid`, `high` (chỉnh âm lượng iPhone 1 bằng nút cứng, chọn đúng mức trong ô "Volume", ghi tên phòng vào ô "Room"):
  - Bấm "Echo: prompts, nobody answers" **ít nhất 3 lần**. Mỗi lần kéo dài hết cửa sổ (30 giây), điện thoại hỏi `ask_ok` ở giây 0, `ask_ok_10` ở giây 10, `ask_ok_20` ở giây 20, và không ai trả lời. Không được tự hủy hay tự nâng thành HELP.
  - Bấm "Echo: after OK, nobody answers" **ít nhất 3 lần**. Laptop tự gửi OK sau 1,5 giây, điện thoại nói `cancel_confirmed`, rồi nghe chế độ chỉ HELP, không ai nói. Không được tự nâng thành HELP. Mỗi lần mất cửa sổ cộng thêm 15 giây grace.
  - Nếu trang báo "busy", chờ lượt trước xong.
  - Bảng "Speaker echo" tự ra. Mỗi ô (mức loa × câu) cần ít nhất 3 lần phát và 0 hủy nhầm, 0 HELP giả mới là Pass. Ước tính 3 mức × 6 lượt × khoảng 30 đến 45 giây là khoảng 10 đến 15 phút.
- [ ] **B3.6** Thử ép hàng chờ STT đầy trong lúc có HELP, và mất micro hoặc mất mạng. Hạn chót không được kéo dài. Phần này khó tạo, nếu không làm được thì ghi chưa thử.

Báo cáo từng người: `GET http://127.0.0.1:8010/api/spatial/sessions/<mã phiên>/drill-report`. Lưu ảnh chụp trang và JSON.

### B4. M3 cầu thang (cần iPhone và cầu thang thật)

- [ ] **B4.1** Mở `http://127.0.0.1:8010/spatial/stairs.html`, bấm "Start camera session", ghép iPhone 1 theo phần 0, bấm Start trên điện thoại.
- [ ] **B4.2** 10 lượt "stairs, good light", 10 lượt "stairs, dim light", 10 lượt "no stairs in view". Mỗi lượt: bấm Start trial, đi về phía cảnh vài giây, bấm End trial. Mục tiêu: đủ sáng thấy ít nhất 80%, không có cầu thang báo nhầm tối đa 10%, thiếu sáng chỉ báo cáo.
- [ ] **B4.3** Lưu ảnh chụp bảng kết quả và `GET /api/spatial/sessions/<mã phiên>/stairs-report`.

### B5. M4 theo người (cần ESP32, 2 người đồng ý)

- [ ] **B5.1** Mỗi người một phiên ESP32: ít nhất 3 lượt ngã có kiểm soát (nệm dày) và ít nhất 10 lượt hoạt động thường ngày. Quay video có đồng hồ.
- [ ] **B5.2** Ghi mốc giây nguồn của từng lượt, rồi lập `trials.json`:
  ```
  {"people": {"person A": {"session": "<mã phiên>", "falls": [[60, 70]], "adl": [[140, 150]]}}}
  ```
- [ ] **B5.3** Chạy `.verify/venv/bin/python backend/tools/m4_report.py trials.json`. Kết quả nằm ở `docs/spatial/evidence/mvp2/m4/`. Bảng chỉ báo phát hiện, bỏ sót và báo nhầm, không có ngưỡng đạt, và không phải kiểm chứng lâm sàng. Phiên từ thiết bị giả bị ghi "Simulated" và không được tính (tôi đã chạy thử, đúng như vậy).

### B6. S3 thật, M5a và M5b (làm cùng B5)

- [ ] **B6.1** Chọn một người đồng ý ghi hình ảnh. Mở `live.html`, tick "Record camera frames", bấm Start.
- [ ] **B6.2** Một lượt đủ bộ với ESP32 và iPhone 1 cùng một người: ngã, trả lời hoặc im lặng, Ack từ iPhone 2. Bấm Stop.
- [ ] **B6.3** Xuất gói: `.verify/venv/bin/python backend/tools/s3_export.py <mã phiên> --participant "<nhãn>" --publication granted --recorded-on <YYYY-MM-DD>`. Gói nằm ở `assets/scenarios/S3/`. Công cụ từ chối nếu thiếu thông tin đồng ý, phát hiện token trong gói, hoặc **incident chưa kết thúc** (còn đếm ngược, còn trong cửa sổ cộng grace, còn lượt gửi dở, hoặc cửa sổ Ack/leo thang chưa đóng). Khi nó báo "The session is not finished", chờ đến giờ nó ghi rồi chạy lại. `--allow-incomplete` vẫn xuất được nhưng gói tự ghi `complete: false`, đừng dùng gói đó làm bằng chứng. Nếu người đó không cho công bố, dùng `--publication not_granted` và đừng đưa gói lên GitHub.
- [ ] **B6.4** Phát lại: `.verify/venv/bin/python backend/tools/s3_replay.py`. Kết quả nằm ở `docs/spatial/evidence/mvp2/s3/`. Đạt khi hai lần phát lại cho cùng kết quả, hash mô hình khớp và cấu hình detector hiện tại (ngưỡng, thời gian nằm yên, cửa sổ, tần số lấy mẫu) đúng bằng cấu hình đã ghi trong gói. Đổi cấu hình sau khi ghi thì báo Fail và nêu khóa nào đổi.

### B7. M6 trên mạch thật

- [ ] **B7.1** Trong các lượt B2 và B6, nhìn trang `live.html` và kiểm tra: nhãn nguồn đúng là "ESP32 waist IMU", vị trí là "Unknown", ảnh nào lệch đồng hồ thì hiện "Unsynchronized" hoặc "Unavailable", không hiện "Available". Chụp màn hình.

### B8. M8 qua tunnel thật

- [ ] **B8.1** Khi tunnel đang chạy, từ iPhone (Safari) hoặc bất kỳ máy nào ngoài mạng mở `https://<tunnel>/glasses/index.html` (phải mở được) rồi `https://<tunnel>/api/spatial/scenarios` và `https://<tunnel>/index.html` (cả hai phải trả 403). Chụp màn hình cả ba. Tôi mới thử việc này bằng cách giả lập tiêu đề của Cloudflare, chưa qua tunnel thật (xem E9).

## Phần C. Việc về phía tôi

- [x] **C1. Công cụ trích bảng M2.** Xong: `backend/tools/m2_report.py`. Nó đã chạy thử trên các phiên giả lập và ra đúng nhãn "Seen with a stand-in". Dùng ở B1.
- [x] **C2. Mức loa cho M7.** Xong: hai nút echo, ô Volume, ô Room và bảng "Speaker echo" trên trang drill. Đã chạy thử trong Chrome với điện thoại giả. Dùng ở B3.5.
- [ ] **C3. Token vận hành cho Start, Stop, Ack (M8).** Chưa làm, cần bạn quyết định. Hiện chúng chỉ được bảo vệ vì tunnel chặn. Trên mạng nội bộ ai biết địa chỉ cũng gọi được. Hai lựa chọn: (a) tôi làm một token vận hành (khoảng nửa buổi), hoặc (b) giữ nguyên và ghi giới hạn này vào bài nộp, đúng như đang ghi trong `ACCEPTANCE_MVP2.md`. Tôi khuyên (b) nếu không có thêm thời gian, vì demo chạy trên một máy.
- [x] **C4. Test Stop và restart trên phiên live.** Xong: `tests/test_live_stop.py`, 3 test đạt, không phát hiện lỗi.
- [x] **C5. `docs/spatial/CREDITS.md`.** Xong cho phần Python (giấy phép lấy từ metadata gói đã cài). Còn ghi "chưa xác minh" cho trọng số Whisper, FFmpeg đi kèm PyAV và bốn thư viện của firmware. Việc của bạn nếu muốn đóng hẳn: mở trang các thư viện đó và đọc giấy phép (phần D3).
- [ ] **C6. Cập nhật `ACCEPTANCE_MVP2.md`** theo từng bằng chứng thật bạn gửi, bỏ nhãn "giả lập" và ghi số thật, kể cả khi không đạt. Tôi làm khi bạn báo xong từng mục B.

## Phần D. Đóng gói để nộp

- [ ] **D1. Commit và push.** Hiện chưa commit: ba tài liệu U9 (`ACCEPTANCE_MVP2.md`, `SUBMISSION_MVP2.md`, `DEMO_MVP2.md`), README, `CREDITS.md`, `vieccanlam.md`, và code của C1, C2, C4. Báo tôi commit dưới tên viethoang2503, không có dòng Co-Authored-By.
- [ ] **D2. Đưa bằng chứng thật vào `docs/spatial/evidence/mvp2/`.** Mỗi M một thư mục hoặc một file, kèm ngày giờ, người, thiết bị, lệnh đã chạy, dữ liệu thô, kết quả kỳ vọng so với thực tế, độ trễ và pass, fail hoặc blocked (mẫu ở `docs/spatial/MVP2_PLAN.md`, mục "Mẫu báo cáo mỗi gate").
- [ ] **D3. Giấy phép còn "chưa xác minh" trong `CREDITS.md`.** Đọc giấy phép của trọng số Whisper small (Hugging Face `Systran/faster-whisper-small`), của FFmpeg trong wheel PyAV, và của ArduinoJson, Adafruit MPU6050, Adafruit Unified Sensor, Adafruit BusIO. Báo tôi kết quả để tôi điền.
- [ ] **D4. Nếu một M không đạt**, áp dụng bảng cắt trong mục "Khi chậm hoặc gate không đạt" của `docs/spatial/MVP2_PLAN.md`. Cập nhật phạm vi và trạng thái M trong bài nộp, không đổi định nghĩa đạt sau khi thấy kết quả.
- [ ] **D5. Rà `SUBMISSION_MVP2.md` và quay video.** Tôi viết bài nộp dựa trên bảng spec và các lần chạy, chưa cùng bạn đọc lại từng dòng. Đảm bảo không câu nào mạnh hơn bảng nghiệm thu. Quay theo `docs/spatial/DEMO_MVP2.md`. Không hiện địa chỉ tunnel, token, mã ghép nối hay QR. Không nói đây là kính Ray-Ban hay kính Meta, không nói phát hiện mất thăng bằng, không nói đã thử nhiều người nếu M4 và M7 chưa có số. Xuất video trong hạn nộp.
- [ ] **D6. Kiểm tra trước khi push.** Chạy `git status` và xác nhận không có `backend/.env`, `vesticare-sensor/include/config.h`, `backend/live_data/`, `backend/probe_data/`, ảnh QR hay link có `#p=`. Gói S3 đã qua công cụ quét bí mật khi xuất.
- [ ] **D7. Chạy lại toàn bộ test** trước khi gắn tag: `cd backend && MODEL_API_KEY= TELEGRAM_BOT_TOKEN= ../.verify/venv/bin/python -m pytest` và `node --test dashboard/tests/*.test.mjs`.
- [ ] **D8. Gắn tag `mvp2`** và push, như đã làm với `mvp1`.
- [ ] **D9. Sau P0, không cần làm bây giờ.** Gửi `assets/first_aid/library.json` cho chuyên gia xem. Tiếng Việt, hội thoại nhiều lượt, hướng dẫn sơ cứu, còi, kính Meta thật và định vị nằm ngoài P0.

## Phần E. Những phần tôi chưa kiểm chứng, và bạn làm gì với chúng

Đây là chỗ tôi viết hoặc kiểm bằng giả lập nhưng chưa thấy chạy ngoài đời. Mỗi mục nói rõ tôi chưa chắc điều gì. Phần lớn đã được B1 đến B8 bao phủ, nên bạn không cần việc riêng, chỉ cần để ý kết quả và báo tôi nếu khác dự kiến.

| # | Chưa kiểm chứng | Vì sao chưa chắc | Bạn làm gì | Nếu kết quả khác dự kiến |
|---|---|---|---|---|
| E1 | Telegram thật: gửi, nút Acknowledge, mã lỗi khi chat id sai hoặc bot bị chặn | Tôi chỉ thử với một API Telegram giả. Tôi tin mọi phản hồi không phải 200 đều thành "failed", nhưng chưa thấy Telegram thật trả gì | B1.1 đến B1.6 | Gửi tôi dòng của lượt đó trong `m2_report.md` và nội dung tin lỗi. Tôi sửa cách phân loại |
| E2 | ESP32 trên người: Wi-Fi, cảm biến MPU6050, lấy mẫu 50 Hz, đồng bộ đồng hồ qua Wi-Fi, hướng trục trọng lực | Firmware chỉ build (7 s) và chạy 17 test trên máy. Chưa từng chạy trên mạch. Số ±1 ms đồng bộ đồng hồ đo giữa hai chương trình trên cùng một máy, không phải trên mạch | B2 | Gửi log serial và báo cáo G4. Nếu trục lệch, sửa hướng gắn. Nếu lấy mẫu hụt, tôi sửa firmware |
| E3 | iPhone gắn trán: tiếng loa nghe rõ không, micro có thu tiếng loa của chính nó không | U0 chỉ thử cầm tay, 6 lượt. Chưa gắn lên trán | B3.1, B3.2, B3.5 | Nếu có hủy nhầm hoặc HELP giả ở một ô, báo tôi mức loa và câu hỏi. Có thể phải tăng thời gian guard (hiện 500 ms) và đo lại toàn bộ M7 |
| E4 | Whisper nhận đúng giọng người thật trong phòng thật | Mới có 6 lượt U0 và lượt im lặng giả. Chưa biết tỷ lệ nhận đúng OK và HELP | B3.4 | Nếu OK hoặc HELP dưới 9/10, công bố số thật. Đổi danh sách cụm từ phải đo lại toàn bộ tập |
| E5 | Safari trên iPhone chạy lâu: khóa màn hình, quyền micro, MediaRecorder sau nhiều lượt | Tôi chỉ thử Chrome tự động và iPhone vài phút | Giữ màn hình sáng (trang tự xin giữ). Ghi lại mọi lần "Connection problem" hoặc điện thoại dừng nói | Báo tôi giờ xảy ra và đang làm gì |
| E6 | SAM trên cầu thang thật và thiếu sáng | Mới thử ảnh dựng và camera giả | B4 | Nếu dưới 8/10 đủ sáng hoặc hơn 1/10 báo nhầm, công bố và áp dụng bảng cắt |
| E7 | Ghép ảnh với lúc ngã (≤ 750 ms) bằng đồng hồ thật | Đo 125 ms trong thiết bị giả. Chưa có đồng hồ ESP32 thật | B2 và B6 (xem ảnh khớp hay "Unsynchronized") | Nếu hay hụt, báo tôi sai số đồng hồ ghi trong manifest |
| E8 | Chất lượng câu giải thích của agent | Mới một lượt API thật. Phần kiểm soát (dẫn nguồn, câu gửi, câu nói) có test, nhưng nội dung do mô hình viết | Đọc câu giải thích khi quay S3 thật | Nếu sai thực tế (ví dụ nói đã thấy cầu thang khi không), báo tôi để thắt thêm kiểm tra |
| E9 | Chặn qua tunnel thật | Tôi giả lập tiêu đề `Cf-Connecting-Ip`. Chưa đi qua cloudflared thật | B8 | Nếu `/api/spatial/scenarios` mở được qua tunnel, **dừng ngay tunnel** rồi báo tôi |
| E10 | Chạy ổn định lâu: phiên 1 giờ, nhiều lượt liên tiếp, bộ nhớ | Tôi chưa chạy phiên dài | Nếu thấy chậm dần, ghi giờ và số lượt | Báo tôi kèm `list_sessions.py` |
| E11 | Bài `SUBMISSION_MVP2.md` | Tôi viết, bạn chưa đọc | D5 | Sửa câu nào mạnh hơn bảng nghiệm thu |
| E13 | Khoảng đệm 250 ms trước hạn khi thu lượt cuối (bản sửa review vòng 3) | Dừng sớm là cách chắc để mốc cuối không vượt hạn, nhưng đổi lại có thể mất tối đa khoảng 250 ms cuối của lượt cuối. Chưa đo trên iPhone thật | B3.4, B3.6: thử nói HELP sát cuối cửa sổ | Nếu HELP sát hạn hay bị mất, báo tôi để chỉnh đệm |
| E14 | Máy chủ trả chậm từng byte với SAM, và loa TTS bị treo | Tôi dựng cả hai bằng giả lập và test. Chưa thấy iPhone hay Meta API thật gặp chúng | Không cần làm riêng. Nếu thấy dòng "speech did not end, cut off" trong log trang điện thoại, hoặc SAM báo "an earlier SAM request is still running", báo tôi | Ghi lại giờ và lần thử |
| E12 | Cách tạo lỗi gửi, restart và hàng chờ STT đầy ở B1.6, B1.9, B1.10 | Đây là cách tôi đề xuất, chưa thử với Telegram thật và máy thật | B1 | Nếu một cách không tạo được tình huống, ghi "chưa thử" và báo tôi để tìm cách khác |

## Thứ tự nên làm

1. Phần A, rồi B1. Chỉ cần bot và hai điện thoại.
2. B2. Rủi ro lớn nhất (hướng trục của mạch) lộ ra ở đây.
3. Làm giá gắn trán, rồi B3.
4. B4 khi có cầu thang.
5. B5 và B6 làm cùng lúc, mỗi người một phiên, một phiên bật ghi ảnh.
6. B7, B8, rồi phần D.

Thời gian ước tính (đoán, chưa đo): A khoảng 20 phút, B1 khoảng 1 giờ, B2 khoảng 1 đến 2 giờ, B3 khoảng nửa ngày cho hai người (riêng B3.5 khoảng 10 đến 15 phút mỗi lần), B4 khoảng 1 giờ, B5 và B6 khoảng nửa ngày, D khoảng nửa ngày.

## Khi gặp vấn đề

- Mạch không gửi dữ liệu: gửi tôi vài dòng log serial và dòng `Clock sync HTTP …`.
- Điện thoại báo "Link not usable": mã ghép nối đã dùng hoặc hết hạn, chạy lại `wearer_link.py`.
- Backend báo "Blocked: contact setup": thiếu bot hoặc chat id (làm lại A1 đến A3), hoặc chọn chế độ Simulated cho lượt không cần gửi thật.
- Trang drill báo "busy": lượt trước chưa xong. Báo "window_too_short": mở phiên drill mới với cửa sổ từ 30 giây.
- Một phiên khác còn đang chạy: bấm Stop ở phiên cũ (mỗi lúc chỉ một phiên). Sau Stop, phiên mới còn bị chặn cho tới khi incident cũ hết cửa sổ phản hồi cộng grace (mặc định 30 + 15 giây) và xong việc gửi, đó là chủ ý.
- Làm mới trang laptop (`live.html`, `drill.html`, `stairs.html`) không còn làm mất phiên đang chạy: trang tự tìm lại phiên đó.
- `fake_esp32.py` hoặc thiết bị cũ gửi vào `/api/sensor/data` trong lúc có phiên live sẽ bị trả 409. Hãy gửi vào `/api/sensor/batch`.
- Không nhớ mã phiên: `list_sessions.py`.
