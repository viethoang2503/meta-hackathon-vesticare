# Kế hoạch triển khai MVP 2

Bản v0.5, chốt ngày 2026-10-31. [MVP2.md](MVP2.md) là nguồn phạm vi, policy, tham số và tiêu chí M1–M9; [MVP2_ARCHITECTURE.md](MVP2_ARCHITECTURE.md) là hợp đồng dữ liệu và module. Tất cả bước dưới đây là việc cần làm, không phải kết quả đã hoàn thành.

## Thứ tự và checkpoint

Đích ưu tiên là vòng IMU thật → hỏi người đeo → Telegram thật → Ack/escalation. Làm Telegram trước agent. Gate acquisition và audio trên iPhone chạy ngay ngày 1; dữ liệu giả cho phép làm phần mềm khi thiếu thiết bị nhưng không chứng minh phần cứng đạt.

Mỗi bước phải có artifact kiểm tra trước khi chuyển phần phụ thuộc sang bước sau. Khi quá nửa thời gian ngày mà chưa có một vòng chạy được, dừng thêm tính năng, giảm phạm vi theo bảng cắt. Giữ replay MVP 1 và tests liên quan xanh; không thêm framework job/retry/đa người dùng cho demo một process.

| Bước | Việc làm | Bằng chứng khi xong |
|---|---|---|
| U0 | Chụp config, kiểm tra model/API hiện có; tạo bot/hai liên hệ, xác nhận phụ thuộc người ngoài theo bảng dưới. Thử iPhone trên mạng/giá gắn dự kiến, secure origin và media permissions | Điện thoại phát câu, thu file giải mã được, upload một frame. Ghi OS/browser, MIME và latency; chưa coi giọng máy là M7 |
| U1 | Firmware acquisition/upload tách task, buffer giới hạn, sequence/timestamp/boot/counters; chạy G4 | Dữ liệu 5 phút cùng upload, tần số/jitter/gap/clipping/transport, số reset và detector coverage theo SPEC; bảng M1 với giá trị đo và trạng thái pass/fail |
| U2 | Migration DB; nguồn live, journal, clock mapping; fake_esp32; Start/Stop, snapshot/detail/evidence không cần scenario | Tests gap/duplicate/reorder/boot; HTTP chậm nhưng mẫu liên tục không reset; snapshot hiển thị Warming up/Detection unavailable; một incident từ fixture đã kiểm chứng, không báo ở fixture đi bộ; Unknown location; DB MVP 1 migrate và replay được |
| U3 | Policy 30 s, Stop vẫn xử lý incident; Telegram initial/Ack/escalation, gửi lỗi/timeout và quota | Policy tests với clock kiểm soát; template đúng trạng thái, late OK có suất riêng dù agent đã dùng suất cập nhật, OK lặp không gửi trùng; chuyển cancelled→HELP cùng incident; HELP capture trước D/result trong grace được báo, OK muộn không hủy, quá grace/clock sai không áp dụng ngoại lệ; hết hạn sau hủy không tự báo và Stop không đóng help_only/grace sớm; một lượt Telegram thật. Ghi API accepted, phone received và Ack riêng; không chờ agent |
| U4 | WearerChannel, decoder, STT worker giới hạn, classifier Python; frontend speaking/guard/recording/finalizing, lượt cố định không VAD; token/QR trước thử tunnel | Deadline không phụ thuộc played; toàn bộ normalizer/OK/HELP/UNCLEAR trong SPEC, Yes không là OK; kiểm tra trigger trên từng template theo state; capture validation và kết quả muộn; guard theo SPEC; cờ kênh và lời người đeo độc lập, điện thoại lỗi không xóa HELP; token sai/sai session/sai quyền; bỏ classifier JS/voice-test cũ sau khi thay xong |
| U5 | Thử audio iPhone thực tế và failure flows | M7 đủ tập thử; tải đồng thời và ép queue đầy với HELP trong chunk bị loại; đo drop/latency, hiện Speech degraded; thử mốc nhắc 10/20 vắt qua lượt thu, không cắt câu/gia hạn; phát lại chính các prompt theo số lượt/mức âm lượng trong M7, không người đáp; đo vọng sau played/guard, không tự hủy/nâng HELP sau cancel; tiếng loa/TV không tự hủy; mất micro/mạng không kéo dài deadline; restart Interrupted không tự gửi tin |
| U6 | Camera, clock quality, frame index, SAM latest-frame và advisory | M3; ảnh quá cũ/không đồng bộ thành Unavailable; không có marker vị trí thật bịa ra; lời nhắc không chen countdown |
| U7 | Agent giải thích và tools cập nhật/trấn an bằng mẫu cố định | M9 bằng fake model và một lượt API thật; Ack không suy ra on-the-way; quota code+agent; model/SAM lỗi không chặn M2 |
| U8 | Ghi, export và replay S3; bảng theo người | M4, M5a/M5b; hai lần cùng incident/policy với config đã ghi, Telegram simulated khi replay; kiểm tra file không chứa secrets |
| U9 | Đóng phạm vi, cập nhật bài nộp và quay video | M1–M9 có evidence hoặc ghi rõ chưa đạt/cắt; ACCEPTANCE/SUBMISSION/DEMO đúng bản chạy; video đã xuất trong hạn |

U0 và U1 có thể chạy song song với U2 nếu thiết bị sẵn có. Khi U0 chưa qua, vẫn làm được core policy bằng fake audio nhưng không triển khai tiếp dựa trên giả định codec iPhone đã hoạt động. Khi U1 thất bại, sửa acquisition/transport trước khi kết luận model không phù hợp.

## Lịch 5 ngày

| Ngày | Trọng tâm | Điểm chốt cuối ngày |
|---|---|---|
| 1 | U0/U1 gates; U2 schema và live skeleton | Có số đo thiết bị và file audio iPhone; live samples vào session, snapshot không cần scenario. Nếu chưa có phần cứng, đánh dấu G4 Blocked và giữ nguồn giả có nhãn |
| 2 | Hoàn tất U2/U3; U4 phần policy và channel | Vòng live/fake → incident → Telegram thật → Ack/escalation chạy được. Chưa làm agent |
| 3 | Hoàn tất U4/U5, sửa lỗi giọng nói và kết nối | OK/HELP/im lặng chạy trên iPhone gắn đúng vị trí; tập thử âm tính không hủy nhầm; lỗi âm thanh không chặn timeout |
| 4 | U6/U7/U8: SAM, agent P0, thu dữ liệu theo người và S3 | Có bản thu S3 và video nháp của vòng chính. Phần chưa đạt áp dụng bảng cắt, không chờ sang ngày cuối |
| 5 | Hoàn tất U8/U9, kiểm tra lại và quay bản nộp | Video, bằng chứng và bài nộp đã xuất; không phụ thuộc ngày 6 |

Đây là lịch mục tiêu cho một người, không phải cam kết khối lượng. Nếu thực tế chỉ còn 4 ngày, quay video vòng chính từ ngày 3, gộp U8/U9 vào ngày 4 và cắt phần phụ; không lùi quay video ra sau hạn nộp.

## Mẫu báo cáo mỗi gate

Lưu dưới `docs/spatial/evidence/mvp2/`: ngày giờ, người/thiết bị/phiên bản, config/model hash, lệnh/flow đã chạy, dữ liệu thô, expected/actual, latency và pass/fail/blocked. Chỉ ghi đạt khi có evidence; số liệu từ laptop, giọng máy, fixture và thiết bị thật có nhãn riêng.

- G4: ghi tổng unavailable theo định nghĩa và ví dụ số giây trong SPEC, không thay gate bằng đếm reset; ghi warm-up thực tế, reset theo nguyên nhân, unavailable duration và coverage đúng công thức SPEC; mục tiêu coverage phải đạt cùng các điều kiện G4 khác. Tách acquisition loss, transport loss và detector miss; ghi `decision_source` để thấy nhánh model hoặc SVM rule.
- M2: đủ OK, HELP, timeout, late OK, Ack sát hạn, failure, unknown send, Stop và restart; thêm các chuyển sau hủy, HELP/OK dùng hai mốc thời gian khác nhau theo hợp đồng audio muộn, token lifecycle/grace và S3 replay cùng incident theo SPEC.
- M3: đúng N và điều kiện ánh sáng theo SPEC; ghi cả false positive.
- M7: dùng tập câu đã chốt trước lượt đo; công bố mọi lỗi và 0/N, không bỏ lượt xấu rồi chỉ quay lượt tốt.
- M5: response events phải giữ capture/receive/result/validation để thử HELP muộn và OK muộn; chạy lại offline với config/model đã ghi; policy replay dùng response events và clock ảo, không STT/model sinh lại câu trả lời.

## Khi chậm hoặc gate không đạt

Cắt theo thứ tự từ 1 đến 5. Mỗi lần cắt phải cập nhật phạm vi và trạng thái M trong bài nộp, không đổi định nghĩa đạt sau khi thấy kết quả.

| Thứ tự | Cắt | Cách trình bày |
|---|---|---|
| 1 | Trang trí/thay vai nhà 3D thêm | Giữ nhà hiện có làm minh họa; live location Unknown |
| 2 | Tool/cập nhật agent bổ sung | Giữ một giải thích có evidence và câu trấn an cố định; policy vẫn đầy đủ |
| 3 | Đánh giá mở rộng ngoài số lượt tối thiểu | Giữ tối thiểu M4/M7; phần chưa làm ghi chưa đánh giá |
| 4 | Advisory camera liên tục khi chưa ổn định | Giữ ảnh thật tại incident và SAM phân tích sau sự cố. M3 preventive advisory chưa đạt; không thay bằng authored zone của người thật |
| 5 | MVP 2 không qua gate thiết bị/audio/gửi thật kịp hạn | Nộp MVP 1 làm bản dự phòng và báo phần MVP 2 thử nghiệm. Không gọi simulated là Telegram thật hoặc fixture là thử thiết bị |

Sơ cứu, hướng dẫn đứng dậy, tiếng Việt, nhiều lượt ứng cứu và sound alarm đã ngoài P0; không dành thời gian triển khai. G4, phiên sống, policy, giọng nói và Telegram Ack là lõi. Nếu một lõi không đạt, công bố MVP 2 chưa hoàn thành thay vì giấu bằng fallback.

## Phụ thuộc cần xác nhận trước ngày 1

| Phụ thuộc | Người chịu trách nhiệm | Hạn / bằng chứng | Nếu thiếu |
|---|---|---|---|
| Bot, config liên hệ 1 | Người làm dự án phối hợp người giữ iPhone 2 | Trước U0 ngày 1: người nhận Start/nhắn bot, lấy đúng chat/sender; kiểm tra gửi và callback | Đánh dấu contact setup Blocked, không tuyên bố Live ready thật |
| Liên hệ 2 | Người làm dự án hẹn trước; chủ tài khoản liên hệ 2 phải tự khởi tạo chat với bot | Trước U0 ngày 1: xác nhận người đó có mặt/online, chat/sender hợp lệ; có thể nhận và Ack tin thử | Đây là phụ thuộc bên ngoài. Tiếp tục core/fake adapter, nhưng U0 phần contact setup và gửi thật đầy đủ bị chặn |

U0 báo trạng thái riêng cho media, clock và contact setup; thiếu liên hệ không làm mất bằng chứng các gate độc lập đã chạy. Người làm dự án kiểm tra và nhắc phụ thuộc này trước ngày 1, không đợi tới U3 hoặc ngày quay mới tìm liên hệ thứ hai. Simulated không thay điều kiện nghiệm thu Telegram thật.

## Việc người làm dự án cần chuẩn bị

- Nạp firmware, Wi-Fi 2,4 GHz và device token; gửi log/dữ liệu gate, không gửi secrets vào chat hoặc repo.
- Tạo bot qua BotFather; liên hệ 1/2 khởi tạo chat với bot; cấu hình token/chat/sender trong `.env`.
- Chọn tunnel HTTPS hoặc chứng chỉ tin cậy trên iPhone; kiểm tra phân quyền trước khi expose. Chỉ giữ URL bí mật không thay cho token.
- Giá gắn điện thoại, nguồn pin, mạng riêng, nệm và người hỗ trợ; xác nhận đồng ý của người tham gia và quyền công bố dữ liệu.
- Tối thiểu hai người thử theo bộ lượt đã định. Chốt lịch thử sớm, không để tới ngày quay mới tìm người.

## Rủi ro và điều kiện dừng

| Rủi ro | Đo / hành động |
|---|---|
| Firmware gửi HTTP làm hụt mẫu | Gate acquisition cùng lúc upload; buffer/drop counters, sửa trước đánh giá model |
| Model không bắt được dữ liệu thiết bị | Kiểm tra trục/đơn vị/vị trí/sample timing; cố định config trước đo lại; công bố miss, không ép peak >2,5 g |
| iPhone không finalize được audio hoặc tự nghe loa | Gate U0/U5 trên máy thật; chọn MIME đã decode được, lượt cố định và guard theo SPEC; thử chính prompt không người đáp và đo tiếng vọng |
| STT quá chậm hoặc hủy nhầm | Queue giới hạn, deadline độc lập; siết allowlist hoặc sửa classifier rồi chạy lại toàn tập. Chưa qua M7 thì chưa tuyên bố voice cancel đạt |
| Màn hình khóa làm dừng media | Thử đúng phiên bản iOS; giữ foreground và màn hình sáng trong demo, ghi giới hạn. Không mặc định Wake Lock/video câm luôn giải quyết được |
| Telegram lỗi/timeout | failed và unknown riêng; policy escalation vẫn chạy. Simulated chỉ là demo dự phòng |
| Clock lệch / ảnh chậm | Hiện uncertainty/Unavailable; không dùng frame lệch làm bằng chứng đồng thời |
| Deadline gần hết khi còn nhiều hạng mục | Đóng phạm vi ngày 4, có video nháp trước đó; quay vòng chính trước bổ sung tính năng |
