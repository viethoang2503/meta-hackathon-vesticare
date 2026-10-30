# Nghiệm thu MVP 1

Trạng thái ngày 2026-10-30, sau Ngày 8. Mỗi dòng trỏ tới bằng chứng có thể chạy lại. Test backend: `cd backend && MODEL_API_KEY= ../.verify/venv/bin/python -m pytest tests/` (169 passed). Flow trình duyệt chạy bằng skill `verify-vesticare` trên instance cô lập.

## Tiêu chí nghiệm thu

| ID | Trạng thái | Bằng chứng | Giới hạn |
|---|---|---|---|
| AC01 | Đạt | `backend/tests/test_home_manifest.py`; flow `spatial_home.py`; `docs/spatial/evidence/home1/` | |
| AC02 | Đạt | `vc doctor` (Keras model loaded); `GATES.md` G1, thí nghiệm C và D, bộ lọc nằm yên; `test_monitoring.py` | Chưa xác minh được tập train của weights |
| AC03 | Đạt | `test_incident_detail_brings_location_frame_and_imu_for_the_same_moment`; flow `spatial_run.py` (ảnh lệch 60 ms) | |
| AC04 | Đạt | `GATES.md` G2 và các lần chạy lại; `evidence/day5/evidence-drawer-sam.png`; test NoMatch, Failed, Unavailable, TimedOut | Chưa đo IoU so với mask đúng render từ scene |
| AC05 | Đạt | Lần chạy thật Ngày 6: 4 tool, 6 evidence id, 3 fix; `test_incident_agent.py` | |
| AC06 | Đạt về hành vi | `test_s1_walk_produces_no_incident_and_one_shown_advisory`, `test_a_shown_advisory_is_confirmed_by_sam_on_the_entry_frame`; flow `spatial_day7.py` phần A | Độ trễ từ lúc vào zone tới lúc HUD hiện chưa được đo |
| AC07 | Đạt | `test_s2_fall_alerts_contact_1_once...`, `test_im_ok_*`; flow `spatial_day7.py` phần B; `test_monitoring.py` | |
| AC08 | Đạt | `test_caregiver_ack_before_the_deadline...`, `test_no_ack_escalates_to_contact_2_exactly_once`; flow `spatial_day7.py` phần C | |
| AC09 | Đạt | `test_two_runs_keep_separate_detector_state`; flow `spatial_run.py` (reload); flow `spatial_faults.py` (mất kết nối) | |
| AC10 | Đạt phần bắt buộc | `test_nothing_changes_after_stop_returns...`, `test_stop_*`, `test_restart_marks_a_running_session_interrupted` | Commit lỗi (T12) chỉ best-effort và chưa có test bơm lỗi |
| AC11 | Đạt | `test_sam_error_is_stored_as_failed...`, `test_the_analysis_budget_bounds...`, `test_an_unreadable_frame_file...`, `test_an_agent_failure_is_recorded...`, `test_a_slow_analysis_does_not_delay...` | |
| AC12 | Đạt | Số đo ở mục dưới; nhãn nguồn trên mọi khối của ngăn bằng chứng | |

## Số đo đã công bố

| Chỉ số | Giá trị | Cách đo |
|---|---|---|
| FPS góc toàn nhà | 120 khi đứng yên, 120 khi S2 đang chạy | Chrome có giao diện, GPU Apple M3 qua ANGLE Metal, cửa sổ 1400×900, đếm `requestAnimationFrame` trong 10 s (`evidence/day8/fps.json`). 120 có lẽ là tần số quét của màn hình |
| Từ lúc mẫu mở incident tới lúc incident được ghi | 42, 91, 42 ms qua 3 run S2 | `latency_ms`, so với lúc mẫu đó đến theo đồng hồ 1x; chưa gồm thời gian commit SQLite |
| Từ va chạm tới lúc mẫu mở incident | khoảng 3,1 s ở S2 | 0,5 s xác nhận của detector và khoảng 2,6 s tới khi đủ 2 s nằm yên |
| Từ lúc incident mở tới khi báo người chăm sóc | 7 s | Đếm ngược HUD |
| Inference model | 1,87 ms mỗi cửa sổ | `tf.function`, 500 lần |
| SAM một ảnh | 1,6 đến 8,2 s | Các lần chạy thật Ngày 5 đến Ngày 7 |
| Agent một incident | 11,5 đến 14,2 s, khoảng 6.000 đến 6.800 token, khoảng 0,016 USD | Các lần chạy thật Ngày 6 và 7 |
| Bắt ngã, tập TEST SisFall | 298/300 file | Bộ lọc nằm yên mức Trung bình (`GATES.md`) |
| Báo nhầm, hoạt động thường ngày tập TEST | 241/434 file (55,5%) | Cùng nguồn; phần còn lại người đeo hủy được trên HUD |

## Bài thử hành vi

| ID | Trạng thái | Bằng chứng |
|---|---|---|
| T1 | Đạt | Lời nhắc ở 16,34 s trong S1 và 20,18 s trong S2; không thông báo |
| T2 | Đạt | Lần vào lại ở 35,26 s ghi `shown: false`, không gọi SAM |
| T3 | Đạt | Cooldown của detector đã bị xóa; `test_an_earlier_trigger_does_not_swallow_a_later_real_impact` |
| T4 | Đạt | `test_im_ok_inside_the_countdown...`, `test_im_ok_after_the_alert...`; flow `spatial_day7.py` phần B |
| T5 | Đạt | FALL trong 10 s gộp vào incident đang mở; `test_a_merged_fall_updates_the_open_alert_row`, test cooldown cũ của route cảm biến |
| T6 | Đạt | Hai test caregiver ở AC08 |
| T7 | Đạt | Các test ở AC11 |
| T8 | Đạt | `test_stop_cancels_a_running_analysis...`, `test_stop_cancels_a_running_agent`, `test_stop_during_the_countdown...`, `test_stop_during_the_caregiver_window...` |
| T9 | Đạt ở mức unit | `test_restart_marks_a_running_session_interrupted`; chưa thử bằng cách tắt hẳn process |
| T10 | Đạt | Flow `spatial_run.py`, `spatial_faults.py`; `test_two_runs_keep_separate_detector_state` |
| T11 | Đạt | Flow `spatial_day7.py` phần C: liên hệ 2 được báo sau khi bản ghi S2 đã kết thúc |
| T12 | Chưa chạy | Commit lỗi chỉ best-effort; chưa có test bơm lỗi lưu trữ |

## Đã biết, ngoài MVP 1

- Simulator tổng hợp trượt `normal` và `one_fall` với model Keras, cả trước và sau khi chuyển route cảm biến (`GATES.md`). Demo dùng bản ghi thật.
- Lời giải thích của agent phản ánh trạng thái lúc nó chạy; ngăn bằng chứng ghi rõ thời điểm và trạng thái đó.
- HUD là mô phỏng trên web, chưa chạy trên kính Ray-Ban.
