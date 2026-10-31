# Đặc tả MVP 2. Người thật, thiết bị thật

Bản v0.7, chốt ngày 2026-10-31. Đây là phạm vi đã thống nhất, chưa phải báo cáo triển khai. [MVP2_ARCHITECTURE.md](MVP2_ARCHITECTURE.md) định nghĩa dữ liệu và module; [MVP2_PLAN.md](MVP2_PLAN.md) định nghĩa thứ tự làm trong 5 ngày. MVP 1 tại tag `mvp1` và [ACCEPTANCE.md](ACCEPTANCE.md) là bản dự phòng.

## Mục tiêu và phạm vi

Chứng minh một vòng hoàn chỉnh: IMU thật → phát hiện ngã → hỏi người đeo bằng tiếng nói → Telegram thật → người chăm sóc Ack hoặc liên hệ 2 nhận cảnh báo. Kết quả đo trên thiết bị dự án, công bố cả lỗi và giới hạn.

P0 gồm phiên sống một người, G4, giọng nói tiếng Anh, camera thật, SAM, agent giải thích và trấn an, một gói S3 xem lại được và bảng thử nghiệm ít nhất hai người. G4 và thử audio iPhone bắt đầu ngay ngày 1; khi thiếu phần cứng có thể làm phần mềm bằng nguồn giả, nhưng nguồn giả không thay thế gate thiết bị.

| Thiết bị | Vai trò | Nhãn nguồn |
|---|---|---|
| ESP32 + MPU-6050 ở hông | Lấy mẫu mục tiêu 50 Hz; gửi batch tối đa 10 mẫu | `ESP32 waist IMU` |
| iPhone 1 gắn ngang mắt | Camera, loa và micro; màn hình không phải HUD người đeo | `Phone camera at eye height (glasses proxy)` |
| iPhone 2 | Telegram với nút `Acknowledge` | `Caregiver phone` |
| Laptop | Backend, bản sao lời nói và bằng chứng cho khán giả | `Live session` hoặc `Replay session` |

UI, lời nhắc và nghiệm thu giọng nói P0 dùng tiếng Anh. Điện thoại được mô tả là proxy, không gọi là kính thật. Nhà 3D là hình minh họa; phiên sống không có tọa độ đo được thì hiện `Unknown`. Vị trí nhập thủ công phải có nhãn `Manual location`; không di chuyển avatar theo IMU hoặc tự gán phòng từ cảnh minh họa.

## Policy do code quyết định

Detector và điều kiện nằm yên dùng chung với MVP 1. Chỉ một policy; ngưỡng và config được chụp lại khi tạo session. Việc thay cấu hình sau đó không đổi incident đang chạy.

| Sự kiện | Hành vi |
|---|---|
| FALL đạt điều kiện nằm yên | Tạo `awaiting_wearer`, deadline bằng thời điểm mở incident cộng 30 s; yêu cầu loa phát template `ask_ok` |
| OK rõ ràng được backend quyết định trước deadline khi đang awaiting_wearer | Chuyển `cancelled_by_wearer`; không gửi cảnh báo |
| HELP đã được STT nhận dạng và code phân loại khi đang awaiting_wearer | Chuyển `alerted`; lập hành động gửi liên hệ 1 ngay, không chờ agent, SAM hoặc audio tiếp theo |
| UNCLEAR, im lặng, STT lỗi hoặc iPhone mất kết nối khi đang awaiting_wearer | Không hủy; đến deadline chuyển `alerted` |
| HELP hợp lệ theo thời điểm thu và giới hạn xử lý bên dưới, khi đang `cancelled_by_wearer` | Chuyển `alerted` trên cùng incident, kể cả STT trả sau deadline gốc; tạo initial notify một lần |
| Deadline gốc khi vẫn `cancelled_by_wearer` | Kết thúc thu mới; giữ hủy, không tự báo do timeout; tiếp tục xử lý audio trước hạn trong thời gian grace |
| OK được xử lý sau khi đã alert, kể cả từ deadline trở đi | Ghi self-report và cập nhật theo suất late OK dành riêng trong quota bên dưới, không thu hồi cảnh báo |
| Kết quả muộn khi incident đã hủy | HELP chỉ được nâng cảnh báo khi đạt hợp đồng audio muộn; OK/UNCLEAR chỉ ghi log, không tự báo do timeout |
| Liên hệ 1 chưa Ack sau 20 s kể từ lần gửi đầu | Gửi liên hệ 2 một lần |
| Gửi liên hệ 1 trả lỗi rõ ràng | Gửi liên hệ 2 ngay nếu chưa Ack; không chờ đủ 20 s |
| Gửi liên hệ 1 timeout, chưa biết có nhận không | Ghi `unknown`; đồng hồ escalation vẫn chạy, không tự gửi lại cùng tin |
| Ack hợp lệ | Ghi nhận; chặn mọi escalation chưa được quyết định. Tin đã gửi không thể thu hồi |

Quyết định kiểm tra deadline, OK, HELP, Ack và escalation được tuần tự hóa theo session. Luật timeout tự báo chỉ áp dụng `awaiting_wearer`; không áp dụng `cancelled_by_wearer`. Thời điểm thu âm chỉ được dùng cho ngoại lệ HELP làm mạnh cảnh báo theo hợp đồng audio muộn; không cho phép OK đến muộn hủy một cảnh báo. Tiếng nói OK là self-report, không phải nhãn chứng minh model báo nhầm.

Stop ngừng nhận dữ liệu mới và lời nhắc phòng ngừa. Incident đã mở vẫn hoàn tất cửa sổ phản hồi gốc, kể cả lượt chỉ nhận HELP sau hủy và thời gian xử lý audio muộn, rồi chạy escalation nếu đã alert. Chỉ một session được nhận dữ liệu; chưa Start phiên mới khi phiên trước còn cửa sổ phản hồi/grace, lượt gửi hoặc thời hạn escalation. Restart đánh dấu session cũ `Interrupted`; hiển thị incident chưa hoàn tất để người demo xử lý, không tự phát lại thông báo ra ngoài.

## Kênh tiếng nói

Một lần chạm Start trên iPhone xin quyền camera/micro và mở âm thanh. Cần thử trên chính điện thoại và giá gắn sẽ dùng trong demo; không suy ra từ thử trên laptop.

1. Mọi câu loa có thể phát khi còn awaiting_wearer hoặc help_only không chứa cụm OK hoặc HELP trong các danh sách chuẩn bên dưới, xét theo chuẩn hóa và ranh giới token. Câu chỉ được phép phát khi đã alerted như alert_unconfirmed có thể chứa HELP, vì không mở lại lượt nghe hoặc tạo alert thứ hai. Trước Start, màn hình hướng dẫn người demo các câu trả lời được hỗ trợ; không đọc các câu trả lời mẫu trong lượt hỏi sự cố.
2. Không thu để phân loại trong lúc phát loa và trong `post_playback_guard_ms` sau sự kiện kết thúc phát trên iPhone. Khởi đầu 500 ms cho mọi lời hệ thống phát; đo lại trên phòng/điện thoại thật. Khoảng chờ giảm rủi ro tiếng vang, không bảo đảm loại hết tiếng vọng. Client chỉ gửi `played` sau khi guard kết thúc; lưu riêng speech end và listen start. Deadline backend không thay đổi.
3. Thu lượt cố định 4 s, P0 không dùng VAD. Lượt cuối ngắn hơn nếu chạm deadline hoặc có quyết định chuyển trạng thái; luôn stop/finalize để file giải mã được. Mốc giây 10 và 20 chỉ là lúc reminder đủ điều kiện; hoàn tất lượt đang thu rồi xét lời nhắc mới nhất, không cắt câu để phát nhắc, không mở lượt mới trước khi xét reminder. Ngay trước phát kiểm tra incident vẫn `awaiting_wearer` và chưa hết hạn; không gia hạn deadline.
4. Phân loại theo danh sách và thứ tự chuẩn bên dưới; không mở rộng danh sách bằng prompt của agent hoặc bằng substring tùy ý.
5. Khi OK hủy cảnh báo, hoàn tất lượt đang thu, phát `cancel_confirmed`, chờ guard, rồi tiếp tục nghe đến deadline gốc trong chế độ `help_only`. HELP đạt hợp đồng thời điểm bên dưới được nâng lên alerted dù STT trả sau hạn; OK/UNCLEAR/im lặng giữ hủy. Không phát reminder 10/20 trong chế độ này. Không kéo dài cửa sổ vì câu xác nhận hoặc guard.
6. Khi đã alerted thì kết thúc lượt nghe, không tự mở hội thoại mới trong P0. Kết quả muộn vẫn lưu; OK sau alert không thu hồi cảnh báo. Sau deadline gốc không thu yêu cầu mới; chỉ xử lý đoạn đã thu trước hạn theo hợp đồng audio muộn. Sau thời gian grace, không có đường xin giúp bằng giọng nói trong P0; ghi giới hạn này trên laptop và bài nộp.

### Hợp đồng phân loại P0

Đây là danh sách đầy đủ, không phải ví dụ. Python classifier, onboarding và bộ thử M7 lấy từ đây. Chuẩn hóa theo thứ tự: chữ thường, apostrophe cong về thẳng; mở rộng token `i'm`/`im` thành `i am`, `can't` và `can not` thành `cannot`, `don't` thành `do not`; sau đó đổi dấu câu còn lại thành khoảng trắng và gom khoảng trắng. Không tự bỏ phủ định, từ không chắc chắn hoặc từ thừa. Match cụm theo ranh giới token, không match trong một từ khác.

| Lớp | Danh sách đầy đủ sau chuẩn hóa | Cách match |
|---|---|---|
| OK | `i am ok`; `i am okay`; `i am all right`; `i am alright`; `i am fine` | Khớp toàn bộ câu; không chấp nhận chỉ vì tìm thấy cụm ở bên trong |
| HELP | `help`; `call`; `hurt`; `hurts`; `pain`; `ambulance`; `emergency`; `fell`; `fallen`; `not ok`; `not okay`; `not fine`; `not all right`; `not alright`; `cannot get up`; `cannot stand up`; `cannot move`; `painful`; `hurting`; `injured`; `bleeding`; `dizzy`; `cannot breathe`; `broke`; `broken` | Có ít nhất một token/cụm trong câu, xét trước OK |
| UNCLEAR | Mọi câu còn lại, kể cả `yes`, `yeah`, `sure`, `ok`, `fine` đứng riêng, câu trống và câu không chắc chắn không khớp HELP | Không hủy |

Ví dụ chuẩn: `I'm all right` và `I'm fine` là OK; `Yes` là UNCLEAR; `I'm not sure I'm OK` là UNCLEAR; `I'm OK, call my son` là HELP. Câu chứa HELP cùng phủ định hoặc mâu thuẫn vẫn ưu tiên HELP trong P0; ví dụ `don't call` là HELP. Đây là classifier bảo thủ theo cụm từ, không phải hiểu đầy đủ ngữ nghĩa. Ghi trường hợp báo nhầm đó trong đánh giá; không suy ra không có đau chỉ vì không match từ pain.

Mỗi thay đổi allowlist phải kiểm tra lại toàn bộ template có thể phát trong awaiting_wearer/help_only qua cùng normalizer và phrase matcher: không chứa bất kỳ cụm OK hoặc HELP. Chỉ kiểm tra classifier trả UNCLEAR chưa đủ nếu câu vẫn chứa trigger ở trong. Các template chỉ phát sau alert được kiểm tra điều kiện state và không tự mở lại nghe.

### Hợp đồng audio muộn

Gọi D là deadline gốc, G là `late_help_grace_seconds`. Thu mới dừng tại D, kể cả help_only. Đoạn thuộc lượt nghe hợp lệ, có capture_start ≤ capture_end ≤ D và được backend kiểm tra clock mapping, utterance/incident, listen interval, khoảng loa/guard và độ dài mới được dùng để xét HELP muộn. Không tin timestamp do client gửi mà chưa đối chiếu; clock không đủ dữ kiện là không xác nhận được thời điểm và phải ghi lỗi.

Audio phải được nhận đầy đủ và kết quả HELP được policy xử lý không muộn hơn D+G. HELP đạt các điều kiện này được chuyển cancelled_by_wearer → alerted, trên cùng incident, kể cả đã qua D. Giới hạn G khởi đầu 15 s cho cả upload, hàng chờ và STT, không phải thêm 15 s cho mỗi chặng; cần đo dưới tải. Không trì hoãn timeout báo của awaiting_wearer để chờ grace. OK vẫn chỉ hủy khi policy xử lý trước D; capture time trước D không cho phép OK muộn hủy.

Kết quả sau D+G, audio thu sau D, đoạn bị drop hoặc không xác thực được thời điểm chỉ ghi lý do, không áp dụng ngoại lệ HELP sau hủy. Giữ token/quyền audio của incident, job và khóa tới D+G; không mở mic mới hoặc cho phép frame/ingest mới vì grace. Restart vẫn theo quy tắc Interrupted, không tự khôi phục gửi ngoài. Khi hết grace, hủy/chặn kết quả STT còn lại và giải phóng tài nguyên nếu không có notify/escalation đang xử lý.

Laptop hiện `Processing recorded responses` từ D tới D+G nếu incident đã hủy; không hiện đang nghe. Đo và công bố HELP bị mất vì drop, clock hoặc quá grace. Việc dùng capture time chỉ giúp các đoạn hợp lệ còn được xử lý, không khôi phục âm thanh chưa thu hoặc đã bị bỏ.

### Trạng thái người đeo và trạng thái kênh

`response_status` chỉ mô tả lời đã nhận; tách `wearer_channel_status` với các giá trị `Ready`, `Phone offline`, `Microphone unavailable`, `STT failed`, `Speech degraded`, `Unknown`. Phone offline cần căn cứ mất heartbeat/poll; lỗi micro đã báo thì ghi Microphone unavailable, không suy ra từ việc im lặng. Nếu đồng thời nhiều lỗi, giữ từng cờ lỗi trong evidence và chọn trạng thái hiển thị theo thứ tự Phone offline → Microphone unavailable → STT failed → Speech degraded → Ready; chưa đủ dữ kiện là Unknown.

Lời đã nhận không bị xóa khi kênh lỗi: có thể đồng thời `Help requested` và `Phone offline`. Tin Telegram chứa cả hai trường. Snapshot laptop hiện `Listening for help only until {deadline}` sau hủy, rồi `Processing recorded responses` khi hết D và `Voice response window closed` sau D+G; deadline có múi giờ và không thay đổi.

Whisper `small`, faster-whisper int8 CPU là cấu hình khởi đầu, chưa phải bảo đảm độ chính xác. [Benchmark giọng máy](evidence/mvp2/whisper_bench.json) có câu Việt `Tôi ổn` bị nhận thành `Tôi ồn`. Tiếng Việt và hội thoại nhiều lượt để sau P0. Không dùng BOOT làm đường hủy P0 khi chưa có hợp đồng firmware và kiểm thử riêng.

Hàng chờ STT có giới hạn và ưu tiên dữ liệu mới nhất, không biết trước đoạn nào chứa HELP. Khi đầy, loại đoạn cũ nhất chưa xử lý để nhận đoạn mới; không hủy đoạn đang suy luận. Đây là giới hạn P0 đã chấp nhận: một câu HELP trong đoạn bị loại có thể không được nhận dạng, nên không bảo đảm độ trễ từ lúc nói HELP tới báo. Backend phải ghi chunk bị loại, số lần overflow và trạng thái `Speech degraded` trên laptop; deadline vẫn chạy. M2 đo riêng thời gian thu, đợi queue, STT, phân loại và lập lịch gửi. Benchmark một câu không chứng minh queue không đầy khi chạy đồng thời các model.

## Agent P0

Agent Muse Spark chạy sau khi hành động cảnh báo đầu tiên đã được commit. I/O gửi tin của code được lập lịch độc lập; agent không chặn countdown hoặc escalation.

| Tool | Hành vi cho phép |
|---|---|
| Đọc bằng chứng | IMU, frame, SAM, self-report, trạng thái gửi và Ack; giữ nhãn nguồn và lỗi |
| `send_message(contact, evidence_ids)` | Cập nhật liên hệ đã được policy cho phép; renderer dùng mẫu cố định và bằng chứng được kiểm tra |
| `reassure_wearer(template_id)` | Đọc câu cố định khi trạng thái hiện tại đủ điều kiện |

Agent tạo giải thích có dẫn bằng chứng trên laptop; phần gửi/đọc ra ngoài dùng mẫu cố định. Cấm tool hủy, tự escalation, hướng dẫn y tế, hỏi khả năng vận động và còi trong P0. Ngân sách 8 lượt tool, 120 s; tối đa 3 tin mỗi liên hệ trong một incident, tính cả tin đầu và escalation do code. Mỗi lần gửi đã lập lịch chiếm một suất; lỗi không mở lại suất tự động.

Quota mỗi liên hệ chia ba suất: một cảnh báo đầu tiên do code (initial với liên hệ 1, escalation với liên hệ 2), một cập nhật do agent, một cập nhật late OK do code. Agent không mượn hai suất dành cho code. Late OK có tính một suất; chỉ gửi tối đa một lần mỗi liên hệ có lượt gửi cảnh báo ở trạng thái `sent` hoặc `unknown`. Nếu lần gửi cảnh báo đang chạy, chờ kết quả để xét cập nhật; `failed` không gửi late OK riêng. Cảnh báo được lập lịch sau khi đã biết late OK dùng response mới trong tin đầu và không cần thêm tin late OK. OK lặp chỉ ghi log, không phát thêm tin. Cập nhật late OK vẫn được gửi sau Ack, vì Ack không phải hủy incident.

`Alert sent` chỉ dùng khi Telegram API xác nhận gửi. `Your caregiver has acknowledged the alert` cần Ack. Không dùng `Help is coming` hoặc `on the way` vì P0 chưa có xác nhận người thân đang đến. Thư viện `assets/first_aid/library.json` là tài liệu cho giai đoạn sau, không được nạp vào tool runtime P0.

## Bảng câu chữ P0

Bảng này là nguồn duy nhất của câu phát ra loa và khung nội dung Telegram. Runtime tạo registry từ đúng ID/câu này; ARCH và PLAN chỉ tham chiếu. Agent chọn ID/bằng chứng, không tự viết lại câu. Mọi câu không có biến được phát nguyên văn.

| template_id | Câu tiếng Anh | Kênh và điều kiện |
|---|---|---|
| `ask_ok` | Are you all right? Please respond. | Loa; mở incident |
| `ask_ok_10` | Are you all right? Please respond. | Loa; lời nhắc đủ điều kiện từ giây 10, theo quy tắc lượt thu |
| `ask_ok_20` | I have not received a clear response. Please respond. | Loa; lời nhắc đủ điều kiện từ giây 20, theo quy tắc lượt thu |
| `cancel_confirmed` | The alert is cancelled. I will keep listening until the response window closes. | Loa; cancelled_by_wearer và còn thời gian trước deadline gốc; nếu đã hết hạn thì bỏ câu |
| `alert_pending` | I am sending an alert to your caregiver. | Loa; initial action đã lập lịch, chưa khẳng định gửi thành công |
| `alert_sent` | An alert has been sent to your caregiver. | Loa; initial hoặc escalation có kết quả sent |
| `alert_unconfirmed` | I could not confirm that the alert was sent. If you can, call out for help. | Loa; chưa có sent và lượt gửi gần nhất failed/unknown |
| `caregiver_ack` | Your caregiver has acknowledged the alert. | Loa; có Ack hợp lệ |
| `late_ok_recorded` | You said you are OK. The alert remains active. | Loa; OK sau deadline hoặc sau alert; không khẳng định người thân đã nhận cập nhật |
| `stairs_visible` | Stairs visible ahead. | Loa; mask còn mới, cooldown đạt, không trong countdown |
| `notify_initial` | Fall alert #{incident_id} at {event_time}. Source: waist IMU. Wearer response: {response_status}. Wearer channel: {wearer_channel_status}. Location: {location_label}. Please check on the wearer and acknowledge this alert. | Telegram liên hệ 1; initial action, có nút Acknowledge |
| `notify_escalation` | Escalated fall alert #{incident_id} at {event_time}. Reason: {escalation_reason}. Wearer response: {response_status}. Wearer channel: {wearer_channel_status}. Location: {location_label}. Please check on the wearer and acknowledge this alert. | Telegram liên hệ 2; policy cho phép, có nút Acknowledge |
| `notify_late_ok` | Update for fall alert #{incident_id}: the wearer has now said they are OK. This is a self-report. Wearer channel: {wearer_channel_status}. The earlier alert has not been withdrawn. | Telegram; suất late OK của liên hệ đủ điều kiện |
| `notify_evidence` | Evidence update for fall alert #{incident_id}. Wearer response: {response_status}. Wearer channel: {wearer_channel_status}. Camera: {camera_status}. Scene analysis: {scene_status}. Location: {location_label}. | Telegram; tối đa một cập nhật agent mỗi liên hệ |
| `ack_received` | Acknowledgement recorded for fall alert #{incident_id}. | Telegram answerCallbackQuery; Ack hợp lệ hoặc lặp, không phải tin nhắn mới |

Biến do code lấy từ incident: `incident_id` là ID; `event_time` có múi giờ; `location_label` chỉ là `Unknown` hoặc `Manual: <label>` đã nhập và escape, không do agent suy diễn. `response_status` chỉ nhận `Help requested`, `No clear response`, `OK reported after alert`. `wearer_channel_status` lấy độc lập theo mục Trạng thái người đeo và trạng thái kênh, không do agent tự chọn. `escalation_reason` là `No acknowledgement within the response window` hoặc `The first contact could not be notified`. `camera_status` là `Available`, `Unavailable`, `Unsynchronized`; `scene_status` là `Stairs visible`, `No stairs identified`, `Pending`, `Failed`, `Timed out`, `Unavailable` theo evidence thật. Không có evidence thì dùng Unavailable, không bịa giá trị.

Tool agent `reassure_wearer` chỉ được chọn `alert_pending`, `alert_sent`, `alert_unconfirmed` và `caregiver_ack`, với điều kiện trong bảng. Code sở hữu prompt, cancel, late OK và stairs; agent không tự mở lượt nghe. Những câu loa trừ hỏi lại chỉ phát tối đa một lần cho cùng chuyển trạng thái, qua hàng lời nói chung và guard chống tiếng vọng. Reminder chỉ dành cho awaiting_wearer; không được mở lại sau hủy. Không phát câu obsolete sau khi state đổi; `cancel_confirmed`/`late_ok_recorded` đợi kết thúc lượt thu trước khi phát. Ack và cập nhật trạng thái không chen vào lượt hỏi khi awaiting_wearer. Xác nhận callback và sửa nút Ack trên tin cũ không chiếm suất tin nhắn mới; sửa nút không được chèn cập nhật nội dung để né quota.

## Camera và SAM

Ảnh JPEG 640×480 mỗi 1 s, có timestamp lúc chụp và lúc nhận. SAM prompt `stairs`, chỉ một request đang chạy, bắt đầu cách nhau ít nhất 3 s. Trong khi bận chỉ giữ ứng viên mới nhất cho lần xử lý kế tiếp, không xếp hàng ảnh cũ. Cooldown lời nhắc 30 s; không chen lời nhắc vào lượt hỏi sau ngã.

Một mask hợp lệ chỉ cho biết cầu thang xuất hiện trong ảnh. Câu nhắc là `Stairs visible ahead`, không khẳng định khoảng cách hoặc phòng. Kết quả lỗi, ảnh cũ hay không có mask phải được hiển thị đúng trạng thái.

Chọn frame theo thời điểm sự kiện IMU, không phải thời điểm xác nhận nằm yên. Live dùng sai lệch tối đa 750 ms gồm cả bất định đồng bộ đã đo. Vượt mức này là `Unavailable`; vẫn cho xem ảnh gần nhất với nhãn chưa ghép được, không dùng làm bằng chứng đồng thời. Kết quả SAM cũ hơn 5 s không được mở lời nhắc mới.

## G4 và bảng thử nghiệm

Lấy mẫu và gửi mạng phải chạy tách biệt bằng buffer có giới hạn. Firmware xuất timestamp lấy mẫu, sequence, mã lần khởi động và số mẫu bị bỏ. G4 đo riêng acquisition, transport và detection.

| Kiểm tra | Điều kiện / bằng chứng cần ghi |
|---|---|
| Đứng yên 30 s ở hông | Trung bình SVM trong 1 ± 0,05 g |
| Acquisition 5 phút đồng thời upload | Mục tiêu 49–51 Hz; p95 khoảng cách mẫu ≤30 ms; công bố max gap và số mẫu mất tại nguồn |
| Detector coverage trong lượt 5 phút | Công bố warm-up, reset theo nguyên nhân và tổng thời gian không đủ cửa sổ. Mục tiêu ≥95% thời gian có cửa sổ hợp lệ theo định nghĩa bên dưới |
| Transport 5 phút | Mất dưới 1% mẫu đã thu; đếm bằng sequence, tách duplicate và reorder |
| Dải đo | ±16 g, ±2000 °/s; kiểm tra clipping. Ghi peak thực tế, không buộc mỗi cú ngã vượt 2,5 g |
| Detector trên thiết bị | Tối thiểu 3 lượt ngã có kiểm soát và 10 lượt hoạt động thường ngày mỗi người; công bố kết quả theo người và `decision_source` |

Coverage đo theo source time của toàn lượt ghi: loại đúng 2 s warm-up danh định đầu tiên, không loại warm-up của những lần reset sau. Mẫu số là phần thời gian còn lại tới cuối lượt; mẫu số vẫn gồm gap mất dữ liệu. Mẫu số lớn hơn 0 nhưng chưa bao giờ có cửa sổ hợp lệ thì coverage bằng 0. Tử số là hợp các khoảng có đủ 100 mẫu liên tục hợp lệ; từ gap/reset tới khi đủ lại là unavailable. Báo riêng thời gian warm-up đầu tiên thực tế, số lần reset theo nguyên nhân và tổng thời gian unavailable. Không ngoại suy một cửa sổ cũ qua khoảng thiếu mẫu. Đây là coverage của dữ liệu detector, không phải cam kết độ trễ live; transport lag được công bố riêng.

Laptop hiện `Warming up` khi chưa đủ cửa sổ đầu tiên, `Detection unavailable` khi có gap/reset và `Device silent` theo timeout thiết bị. Chỉ hiển thị detector ready khi cửa sổ đầy lại. Batch đến chậm nhưng device timestamp/sequence liên tục không làm reset; thời gian nhận HTTP không phải khoảng lấy mẫu.

Ví dụ G4 dài 300 s: mẫu số 298 s sau khi loại 2 s đầu; coverage 95% cho phép tối đa 14,9 s unavailable. Nếu mỗi reset tốn đúng 2 s và không có tổn thất khác, khoảng 7 reset rời nhau còn có thể đạt, 8 reset không đạt. Đây là ví dụ ước lượng, không phải quota reset: gap kéo dài hoặc warm-up chậm làm giảm số lần có thể chịu được; các khoảng unavailable chồng nhau chỉ tính một lần. Gate dùng thời gian thực đo và công thức coverage, không dùng số lần reset thay thế.

Các mức trên là mục tiêu nghiệm thu mới, chưa phải kết quả đã đo. Nếu G4 không đạt thì sửa acquisition, trục, đơn vị, vị trí đeo hoặc vận chuyển rồi đo lại. Không đổi ngưỡng sau khi xem kết quả để ép gate xanh. Ghi lại toàn bộ thay đổi cấu hình và công bố cả kết quả xấu.

Chỉ ghi thử ngã có kiểm soát lên nệm, trên sàn phẳng, với người trưởng thành khỏe mạnh đồng ý tham gia và có người hỗ trợ. Không thử ngã trên cầu thang, người già hoặc bệnh nhân. Không công bố hình/giọng người khác khi chưa được đồng ý.

## S3 và tiêu chí nghiệm thu

S3 là dữ liệu đồng thời của cùng một người: IMU, chuỗi ảnh camera và response events. P0 không yêu cầu video liên tục hoặc lưu audio thô. Không coi vỗ tay là phương pháp đồng bộ đã được kiểm chứng; giữ bằng chứng đo sai số clock trong manifest.

| ID | Tiêu chí |
|---|---|
| M1 | G4 có dữ liệu thô, config và bảng kết quả; gate đạt mới tuyên bố phần cứng đạt |
| M2 | Chạy đủ nhánh OK, HELP, timeout, Ack, không Ack, lỗi gửi và Stop khi đang chờ. HELP đã được phân loại hoặc timeout tới lập lịch gửi ≤2 s khi backend khỏe; thử thêm HELP trong chunk bị loại, queue quá tải, lượt thu vắt qua mốc nhắc, OK→HELP trước hạn, hủy→im lặng hết hạn không tự báo, HELP thu trước D xử lý trong grace vẫn nâng, OK muộn không hủy, capture sau D/clock sai/quá grace chỉ log, Stop trong help_only, quota late OK và OK lặp; Telegram API và thời điểm điện thoại nhận được đo riêng, không hứa SLA mạng |
| M3 | Thử 10 lượt tiếp cận đủ sáng, 10 lượt thiếu sáng và 10 ảnh/cảnh không có cầu thang; công bố x/N, false positive và latency. Mục tiêu ≥8/10 đủ sáng, ≤1/10 false positive; thiếu sáng báo riêng. Không đạt thì công bố và dùng phương án cắt trong PLAN |
| M4 | Tối thiểu 2 người; mỗi người ≥3 lượt ngã có kiểm soát, ≥10 lượt hoạt động thường ngày. Báo phát hiện, bỏ sót, báo nhầm theo lượt và độ trễ, không coi đây là kiểm chứng lâm sàng |
| M5a | Replay S3 hai lần cho cùng số incident và mốc source time với cùng model/config |
| M5b | Inject response events gồm capture/receive/classification time, validation và grace theo đồng hồ policy ảo; hai lần cho cùng chuỗi hủy/báo/Ack/escalation. Không gọi lại STT hoặc yêu cầu agent sinh cùng văn bản |
| M6 | Nhãn nguồn đúng; live không có định vị thì Unknown; ảnh không đồng bộ không được gọi là bằng chứng đồng thời |
| M7 | Trên iPhone đang gắn: mỗi người 10 OK phủ đủ 5 câu chuẩn trong allowlist, 10 HELP, 10 câu mơ hồ/phủ định có Yes và câu không chắc chắn không khớp HELP, cùng 10 lượt im lặng/TV/loa hệ thống. Thử classifier riêng với tất cả HELP phrases, contractions, phủ định, ranh giới token và câu hỗn hợp; không trộn unit cases vào mẫu số đo iPhone. Mục tiêu ≥9/10 OK, ≥9/10 HELP; hủy nhầm phải 0/20 lượt thử âm tính mỗi người. Thêm ít nhất 3 lần mỗi mức loa thấp/vừa/cao cho từng prompt ask_ok/ask_ok_10/ask_ok_20, không có người trả lời; ghi phòng, guard, âm lượng, audio/transcript được phép lưu và mọi lần HELP giả, không được tự hủy. Thử cancel_confirmed trong help_only cũng 3 lần mỗi mức loa thấp/vừa/cao, không có người đáp và không được tự nâng lên HELP. Báo 0/N riêng cho từng nhóm, không trộn mẫu để làm đẹp tỷ lệ; không suy rộng thành bảo đảm tuyệt đối |
| M8 | Token sai/phiên khác bị từ chối; token người đeo không có quyền Start/Stop/Ack; hết phiên và incident được thu hồi quyền |
| M9 | Agent không hủy/escalate hoặc đọc sơ cứu; câu trấn an đúng trạng thái, cập nhật giữ nguồn; agent lỗi không làm chậm M2 |

## Tham số P0

| Tham số | Giá trị |
|---|---|
| Lấy mẫu / batch | 50 Hz / tối đa 10 mẫu |
| Nằm yên / cửa sổ sau FALL | 2 s / 10 s, cùng luật MVP 1 |
| `wearer_cancel_seconds` live / replay MVP 1 | 30 / 7; replay S3 dùng config đã ghi |
| Hỏi lại | Đủ điều kiện từ giây 10, 20 trong awaiting_wearer; chờ lượt thu hoàn tất, một reminder mới nhất |
| `post_playback_guard_ms` | 500 ms sau kết thúc loa; giá trị khởi đầu cần đo, áp dụng trước mọi lượt nghe tiếp theo |
| Sau hủy | help_only tới D; sau D không thu mới, HELP từ audio trước hạn được xét trong grace, không tự báo do timeout |
| `late_help_grace_seconds` | 15 s sau D cho toàn bộ nhận audio và xử lý HELP muộn; không áp dụng cho hủy bằng OK |
| `caregiver_ack_seconds` | 20 kể từ lần gửi đầu |
| Timeout gửi Telegram | Tổng 5 s mỗi lần gửi |
| Audio / STT | Lượt cố định 4 s, P0 không VAD; cắt ở deadline/quyết định và finalize; Whisper small int8 CPU |
| Hàng chờ STT | 1 worker, tối đa 2 đoạn chờ; loại đoạn cũ khi đầy |
| Ngắt cửa sổ khi gap | Device timestamp cách >40 ms hoặc mất sample sequence; không dùng thời gian nhận batch |
| Detector coverage G4 | ≥95% sau khi loại đúng 2 s đầu, vẫn tính warm-up do reset và gap |
| Clock exchange | Ít nhất 5 lượt lúc kết nối, đồng bộ lại mỗi phút |
| Ảnh / sai lệch ghép live | 1 Hz 640×480 / 750 ms gồm bất định clock |
| SAM / tuổi advisory / cooldown | Một request, khoảng bắt đầu ≥3 s / 5 s / 30 s |
| Thiết bị im lặng | 3 s; hiện Device silent, không tự tạo incident |
| Agent | 8 lượt tool, tổng 120 s, 3 tin mỗi liên hệ gồm tin code |

Chụp config theo session; thay đổi tham số cần ghi lý do và đo lại các M liên quan. SPEC là nguồn tham số duy nhất.

## Ngoài P0 và phương án mạng

Hoãn tiếng Việt, sơ cứu/hướng dẫn đứng dậy, hội thoại ứng cứu nhiều lượt, sound alarm, kính Meta thật, app iOS gốc, định vị, train lại và đánh giá chéo. KFall đã có trong pipeline training nên không được tự coi là tập độc lập.

Thiết bị dùng mạng riêng đã thử hỗ trợ ESP32 2,4 GHz. Trang iPhone cần secure origin; chọn tunnel HTTPS hoặc chứng chỉ cục bộ đã được iPhone tin cậy và thử ngay ngày 1. Start tạo phiên để ghép thiết bị. Live gửi thật chỉ bật nhận mẫu và hiện Live ready sau khi có device token, người đeo/media/clock sẵn sàng và hai liên hệ Telegram đã cấu hình. Chế độ `simulated` phải chọn rõ và không được tính là đạt M2 gửi thật. Mạng/thiết bị lỗi luôn hiện trên laptop; mất audio không gia hạn deadline.
