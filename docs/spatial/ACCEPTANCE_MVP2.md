# Nghiệm thu MVP 2

Trạng thái ngày 2026-11-02, sau U9. Bảng này ghi đúng những gì đã chạy. Phần code của MVP 2 đã xong và đã thử bằng thiết bị và dữ liệu giả lập. Phần cần phần cứng, người thật và tài khoản Telegram thật chưa có bằng chứng và được ghi là chưa chạy, không ghi là đạt.

Test backend: `cd backend && MODEL_API_KEY= TELEGRAM_BOT_TOKEN= ../.verify/venv/bin/python -m pytest` (410 passed). Test giao diện: `node --test dashboard/tests/*.test.mjs` (22 passed). Firmware có 17 test native, chạy ở U1. Việc cần người làm nằm ở [MVP2_TODO_NGUOI_LAM.md](MVP2_TODO_NGUOI_LAM.md).

Cách đọc cột trạng thái.

- **Có bằng chứng**: tiêu chí đã chạy đúng cách spec yêu cầu.
- **Có bằng chứng giả lập**: chạy được trên thiết bị hoặc dữ liệu giả, có nhãn. Không thay thế bằng chứng thật.
- **Một phần**: một phần tiêu chí có bằng chứng, phần còn lại ghi ở cột giới hạn.
- **Chưa chạy**: công cụ và test đã có, chưa có dữ liệu thật.

## Tiêu chí M1 đến M9

| ID | Trạng thái | Bằng chứng | Giới hạn |
|---|---|---|---|
| M1 (G4) | Chưa chạy | Firmware 2.0.0 build cho `esp32dev` (RAM 31,5%, flash 73,3%) và 17 test native. `backend/tools/g4_report.py` ra bảng Pass, Fail, Incomplete; `test_g4_report.py`. Quy trình ghi ở `evidence/mvp2/g4/README.md` | Chưa có ESP32 nên chưa có bản ghi thật. Thiết bị giả ra "Simulated: not G4 evidence". Chưa biết hướng trọng lực của thiết bị so với dữ liệu train (công cụ có cảnh báo trục) |
| M2 | Một phần | Bảng nhánh bên dưới. Telegram được thử với API giả (`test_telegram_channel.py`). Công cụ `m2_report.py` (`test_m2_report.py`) gắn nhánh cho từng incident, chia nhỏ thời gian từng chặng và đếm nhánh nào đã thấy bằng Telegram thật. Trên các phiên giả lập đã ghi: từ hết hạn tới lúc lập lịch gửi lớn nhất 9 ms | Chưa gửi Telegram thật vì chưa có bot nên mọi nhánh đang ghi "Seen with a stand-in" hoặc "Not seen". Chưa đo HELP đến lúc lập lịch gửi ≤ 2 s khi chạy đồng thời các model, chưa đo thời điểm điện thoại nhận tin. Restart chỉ được thử bằng test hai event loop, chưa tắt hẳn process |
| M3 | Chưa chạy | Công cụ `spatial/stairs.html` và `stairs_trials.py`, `test_stairs_trials.py`. Lần chạy Chrome với camera giả và SAM thật: mỗi lần gọi 1,0 đến 2,7 s, camera giả không có cầu thang nên ra "không thấy" | Chưa có cầu thang thật. Bảy lần gọi trên ảnh nhà dựng sẵn S2 đều ra Complete (lần thứ tám bị hủy khi dừng phiên) nhưng đó là ảnh dựng, không phải bằng chứng M3 |
| M4 | Chưa chạy | `m4_report.py`, `tools/m4_report.py`, `test_s3.py`. Bảng báo phát hiện, bỏ sót, báo nhầm theo người, không có ngưỡng đạt | Chưa có người nào. Phiên từ thiết bị giả bị ghi "Simulated" và không được tính |
| M5a | Có bằng chứng giả lập | `s3_replay.py` trên phiên giả lập có ghi ảnh: hai lần phát lại cho cùng 1 incident tại 23.940 ms, khớp lần chạy sống; hash mô hình và cấu hình detector khớp với bản ghi. `test_s3.py` | Chưa có gói S3 thật. Báo cáo sinh ra mang nhãn "Simulated: not S3 evidence". Chỉ chứng minh cùng mô hình ra cùng kết quả hai lần |
| M5b | Có bằng chứng giả lập | Cùng lần chạy: 6 sự kiện phản hồi, hai lần cho cùng chuỗi, khớp chuỗi sống, lệch thời gian lớn nhất 14 ms. `test_policy_replay.py` so mô phỏng với run manager thật trên 5 kịch bản | Chưa có phản hồi thật từ người. Chỉ chứng minh mô phỏng khớp trên các kịch bản đã thử |
| M6 | Có bằng chứng | Nhãn nguồn `ESP32 waist IMU`, `Simulated device (recorded scenario)`, `Voice drill (...)`, `Camera trial (...)`. Vị trí live là `Unknown`. Ảnh không ghép được ra `Unavailable` hoặc `Unsynchronized`, không ra `Available`. `test_live_camera.py`, `test_live_session.py`, trang `live.html` | Nhãn thiết bị thật chưa thấy trên phần cứng |
| M7 | Chưa chạy | Bộ phân loại khớp đúng bảng spec (`test_wearer_reply.py`: mọi cụm HELP, rút gọn, phủ định, ranh giới token, câu hỗn hợp). Trang `drill.html` và `drill_report.py`, gồm bảng echo theo mức loa thấp, vừa, cao cho `ask_ok`, `ask_ok_10`, `ask_ok_20` và `cancel_confirmed`, mỗi ô cần 3 lần phát và 0 hủy nhầm hay HELP giả (`test_echo_trials.py`, chạy thử trong Chrome với điện thoại giả). U0 trên iPhone cầm tay: 6 lượt, mọi lượt decode được | Chưa có người nào chạy bộ 10 OK, 10 HELP, 10 mơ hồ, 10 im lặng. Chưa thử iPhone gắn trán và độ to loa qua giá gắn. Chưa có lượt echo nào trên iPhone thật, nên bảng echo đang trống |
| M8 | Một phần | Token sai bị từ chối (401), token người đeo chỉ mở các route `/api/wearer`. Qua tunnel chỉ có `/glasses/`, `/api/wearer/` và `/api/probe/`, mọi đường khác trả 403 (`test_through_the_tunnel_only_the_phone_routes_answer`). Mã ghép nối dùng một lần, hết hạn 10 phút, khóa sau 5 lần sai (`test_pairing.py`). Sau Stop, ảnh bị từ chối (`test_live_camera.py`) | Start, Stop và Ack không cần token nào khi gọi trực tiếp trên máy hoặc mạng nội bộ; chúng chỉ được bảo vệ vì tunnel chặn. Chưa có token vận hành. Chưa có test riêng cho việc thu hồi token khi phiên và incident đã kết thúc |
| M9 | Có bằng chứng | `test_responder.py`: mô hình giả; agent không có tool hủy, báo động, tư vấn; yêu cầu ngoài danh sách bị từ chối và ghi lại; câu trấn an kiểm tra theo trạng thái gửi; mô hình lỗi không đổi cảnh báo. Một lượt API thật ngày 2026-11-02: Complete sau 30 s, 18.954 token, một cập nhật cho liên hệ 1 có dẫn bốn bằng chứng, ba câu trấn an sai trạng thái bị từ chối | Câu giải thích do mô hình viết nên chất lượng từng lần không được cam kết; phần kiểm soát là dẫn nguồn, câu gửi và câu nói. Chỉ một lượt API thật |

## M2 theo từng nhánh

| Nhánh | Bằng chứng | Mức |
|---|---|---|
| OK trước hạn hủy | `test_only_a_clear_ok_before_the_deadline_cancels`; `test_ok_cancels_then_help_inside_the_window_raises_the_alert` (qua HTTP và audio) | Test |
| HELP báo ngay | `test_help_inside_the_window_alerts_at_once_and_says_help_requested` | Test |
| Hết hạn tự báo | `test_s2_fall_opens_one_incident_that_alerts_after_the_live_window`; `test_the_deadline_alerts_only_while_waiting` | Test |
| Ack và không Ack | `test_contact_1_tapping_the_alert_acknowledges_and_the_button_goes`; `test_silence_then_escalation`; `test_an_ack_before_the_caregiver_deadline_stops_it` | Test |
| Lỗi gửi, gửi không rõ | `test_a_failed_first_send_escalates_at_once`; `test_an_unknown_send_keeps_the_ack_timer_and_does_not_escalate_early` | Test |
| OK rồi HELP trước hạn | `test_ok_cancels_then_help_inside_the_window_raises_the_alert` | Test |
| Hủy rồi im lặng không tự báo | `test_a_cancelled_incident_holds_the_run_until_the_grace_ends` | Test |
| HELP thu trước D, xử lý trong grace | `test_help_captured_before_the_deadline_after_a_cancel_raises_the_alert`; `test_help_captured_before_d_arrives_after_d_inside_the_grace` | Test |
| OK muộn không hủy | `test_an_ok_processed_at_or_after_the_deadline_alerts_first_then_counts_as_late` | Test |
| Thu sau D, đồng hồ sai, quá grace chỉ ghi log | `test_help_captured_after_the_deadline_does_not_undo_a_cancel`; `test_without_a_phone_clock_ok_does_not_cancel_but_help_still_alerts`; `test_an_ok_from_outside_this_incidents_listening_cannot_cancel`; `test_help_after_a_cancel_raises_the_alert_only_if_captured_in_time_and_decided_within_grace` | Test |
| Late OK và OK lặp | `test_late_ok_before_escalation_is_carried_by_the_escalation_not_a_second_message`; `test_late_ok_then_ack` | Test |
| Hàng chờ STT đầy, HELP trong đoạn bị loại | `test_a_full_queue_drops_the_oldest_waiting_turn_and_keeps_the_newest`; trạng thái `Speech degraded` | Test hành vi. Hệ quả cho HELP là giới hạn đã chấp nhận trong spec, chưa đo tỷ lệ mất |
| Lượt thu vắt qua mốc nhắc | `test_only_the_newest_reminder_waits_and_old_lines_drop_before_they_play`; `glasses_turn.test.mjs`; Chrome mic giả | Test |
| Stop khi đang chờ (replay) | Các test Stop của MVP 1 | Test |
| Stop khi đang chờ hoặc trong help_only (live) | `test_live_stop.py`: sau Stop, incident đang chờ vẫn báo ở hạn; sau hủy vẫn nghe HELP | Test |
| Restart | `test_restart_marks_a_running_session_interrupted`; `test_a_restart_marks_the_session_interrupted_and_sends_nothing_by_itself` (phiên live, hai event loop) | Test, chưa tắt hẳn process thật |
| Telegram thật | Chưa có bot | Chưa chạy |

## Số đo đã có

| Chỉ số | Giá trị | Cách đo và giới hạn |
|---|---|---|
| Đồng bộ đồng hồ | ±1 đến ±1,9 ms | 5 vòng, cùng máy (thiết bị giả và Chrome), không đi qua Wi-Fi. Không phải số của ESP32 |
| Ghép ảnh với lúc ngã | 125 ms | Thiết bị giả phát S2 theo thời gian thực cùng Chrome camera giả; giới hạn 750 ms |
| Ảnh gửi lên | 10 ảnh trong 11 s | Chrome, camera giả, 640×480 |
| SAM một ảnh | 1,0 đến 2,7 s | Các lần chạy thật ngày 2026-11-02 |
| Agent một incident | 30 s, 18.954 token | Một lượt API thật |
| STT trên iPhone | 0,9 đến 1,1 s mỗi lượt, một lượt 3,1 s | U0, iPhone cầm tay, 6 lượt |
| Lượt im lặng ở U0 | 3 trên 3 lượt sinh chữ khi chưa dùng VAD và chưa tắt ngữ cảnh trước; hai cài đặt này đã được thêm | Chỉ một lượt im lặng sau khi sửa, cần thêm lượt trong M7 |

## Đã biết

- Phần cứng ESP32 chưa được nạp và thử. Firmware build và test native được, không có số đo trên mạch.
- Giá gắn trán và độ to loa qua giá chưa được thử (U0 còn mở phần này).
- "Live ready" mới chặn Start theo hai liên hệ Telegram. Trang laptop hiện trạng thái thiết bị, đồng hồ, điện thoại và camera nhưng không chặn Start theo chúng.
- Token người đeo nằm trong snapshot phiên. Chỉ laptop đọc được vì tunnel chặn mọi đường `/api/spatial`.
- Bộ phân loại là cụm từ bảo thủ. Câu như "don't call" vẫn được tính là HELP.
- Tiếng Việt, hội thoại nhiều lượt, hướng dẫn sơ cứu, còi, kính Meta thật, định vị nằm ngoài P0.
- Ba vòng review ngoài (26 điểm) đã được sửa, nằm trong lịch sử commit. Vòng ba có mười điểm. Điểm về `/api/sensor/data` được hạ xuống P2: `/batch` chạy luồng cũ khi không có phiên live vẫn được giữ cho tương thích, và chỉ khi có phiên live thì `/data` bị trả 409. Các điểm còn lại: lượt thu cuối dừng sớm hơn hạn một khoảng đệm; kết quả giọng nói quá D cộng grace chỉ ghi lý do; phiên giữ token và khóa tới D cộng grace ở mọi trạng thái; trang laptop tìm lại phiên sau khi làm mới; gói S3 chỉ xuất khi incident đã xong; phát lại M5a kiểm cấu hình detector đã ghi; G4 trừ số mẫu bỏ theo mốc của phiên; loa TTS treo vẫn có đoạn im lặng; SAM live không chồng request sau timeout.
