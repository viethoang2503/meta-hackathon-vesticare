# Đặc tả MVP 1

Bản v0.4. Phạm vi là demo web một tab cho một người thực hiện trong 10 ngày. Hành vi dưới đây là hợp đồng dự kiến, chưa phải tính năng đã chạy. Các giá trị cấu hình nằm ở bảng tham số cuối tài liệu.

## Thay đổi từ v0.3

Nhánh FALL báo nhầm trên phần lớn bản ghi hoạt động thường ngày, kể cả đi bộ chậm và đi cầu thang chậm (GATES.md). v0.4 thêm hai lớp. Một FALL đã xác nhận chỉ mở incident nếu sau đó người đeo nằm yên. Khi incident mở, HUD hỏi "Are you OK?" và đếm ngược trước khi báo người chăm sóc; người đeo bấm "I'm OK" để hủy báo nhầm. Đánh đổi: ngã thật được báo chậm hơn, gồm thời gian chờ nằm yên và thời gian đếm ngược.

## Thay đổi từ v0.2

Gate G1 và hai thí nghiệm C, D trong [GATES.md](GATES.md) cho thấy lớp INSTABILITY không phân biệt được mất thăng bằng với đi lại bình thường. Người khỏe đi cầu thang bị báo 100% theo model gốc. Khi biết trước hoạt động, IMU vẫn không tách được bệnh nhân với người khỏe trên cầu thang (AUC 0,49). Bộ dữ liệu không có sự kiện suýt ngã.

Vì vậy v0.3 bỏ mức High dựa trên IMU. Cảnh báo phòng ngừa chuyển sang bối cảnh: zone đã khai báo, có camera xác nhận. IMU chỉ dùng cho FALL, nhánh đã qua gate.

## Người dùng và câu chuyện

Người đeo nhận lời nhắc phòng ngừa trên HUD kính mô phỏng khi đi vào khu vực nguy hiểm. Khi ngã, HUD hỏi "Are you OK?". Người chăm sóc chọn incident trong căn nhà 3D để xem IMU, frame, SAM mask, giải thích của agent và lịch sử phản hồi.

Camera và IMU làm hai việc khác nhau. Camera cho biết phía trước có gì nguy hiểm. IMU cho biết người đeo có ngã thật hay không.

Kịch bản S1: người đeo đi trong nhà buổi tối, vào vùng cầu thang. HUD nhắc "Stairs ahead. Use the handrail." Camera chụp khung hình tại thời điểm đó và SAM xác nhận có cầu thang. Không ai bị báo.

Kịch bản S2: người đeo ngã gần cầu thang và nằm yên. HUD hỏi "Are you OK?" và đếm ngược. Không có phản hồi thì báo liên hệ 1. Người chăm sóc chưa xác nhận đúng hạn thì chuyển liên hệ 2. Agent phân tích khung hình và đề xuất sửa môi trường.

Output INSTABILITY của model vẫn được lưu cùng xác suất, nhưng không tạo incident, không hiện trên HUD và không vào câu chuyện demo.

## Phạm vi

| Trong MVP 1 | Sau MVP 1 |
|---|---|
| Nhà nhỏ đủ nội thất, camera/HUD, Start/Stop, incident timeline và evidence drawer. | Nhà nhiều tầng, house editor, preview cải tạo và không gian cộng tác. |
| Replay qua classifier thật cho FALL, SAM thật và một agent dùng tools có giới hạn. | Hỏi lịch sử tự do, text-to-SQL và báo cáo tuần. |
| Lời nhắc phòng ngừa theo zone, có SAM xác nhận khung hình. | Điểm dáng đi theo tuần từ IMU (thí nghiệm D: AUC 0,73 theo người). |
| Người đeo phản hồi khi ngã, caregiver acknowledge và escalation mô phỏng. | Phát hiện suýt ngã thật, cần thu thập dữ liệu có sự kiện suýt ngã. |
| Lưu session, incident, nguồn bằng chứng và trạng thái analysis; phục hồi UI khi refresh. | Gửi Telegram thật, kính hoặc IMU live, Pause/Resume, job queue có lease/retry và đồng bộ nhiều tab. |

UI và cảnh báo dùng tiếng Anh. Âm thanh là tùy chọn. Clip có thể được thay bằng keyframes đồng bộ. Các bước kiểm thử failure chạy riêng với câu chuyện ba phút trong [DEMO.md](DEMO.md).

## Ngôi nhà và giao diện

Nhà gồm bốn phòng, hành lang ngắn và cầu thang lên chiếu nghỉ. Chiếu nghỉ không mở thành một tầng có nhiều phòng.

| Khu vực | Nội thất bắt buộc |
|---|---|
| Phòng khách | Sofa, bàn trà, TV/kệ, thảm, đèn, cửa sổ và cây. |
| Bếp và phòng ăn chung | Tủ bếp, chậu rửa, bếp, tủ lạnh, bàn và ghế. |
| Phòng ngủ | Giường/chăn gối, tủ, bàn đầu giường và đèn. |
| Phòng tắm | Toilet, chậu rửa, gương và khu tắm. |
| Hành lang và cầu thang | Cửa, đèn, bậc, tay vịn và chiếu nghỉ. |

Nội thất có tỷ lệ và chất liệu nhất quán. Cầu thang, thảm và ghế cần ID để chọn; đồ trang trí chỉ cần render. Dùng cutaway để nhìn rõ các phòng. Nhân vật hoặc marker đi theo tuyến kịch bản, không được mô tả là tái tạo cơ thể từ IMU.

Workspace có góc toàn nhà, camera kính, timeline, evidence drawer và Start/Stop. Chọn marker mở đúng incident. Góc camera bằng chứng tách khỏi camera quan sát toàn nhà.

## Replay và session

Start tạo session mới và chạy 1x. Một replay task trong backend đọc mẫu theo thứ tự. P0 có một session active, một tab điều khiển. Chạy lại tạo ID mới và context detector mới.

Stop kết thúc run và chặn hành động mới của task còn chờ. Analysis chưa hoàn tất chuyển Cancelled. Session cũng có timeout tự kết thúc, lưu end_reason và đánh dấu công việc còn dở là hết hạn thay vì thành công. Hết file IMU, session tiếp tục Running để xử lý analysis và deadline còn lại, camera giữ frame cuối và UI hiện "Recording ended". Scenario được kiểm tra để có đủ thời gian cho toàn bộ chuỗi deadline trước timeout.

Sau Stop hoặc timeout, session có trạng thái Stopped, lần lượt với end_reason=user_stop hoặc end_reason=timeout. Người xem vẫn chọn lại incident đã lưu. Refresh và reconnect SSE tải snapshot của session hiện có. Khi DB hoạt động bình thường, startup sau restart đánh dấu mọi run còn Running là Interrupted và cho phép Start run mới. Trạng thái session chỉ gồm Running, Stopped và Interrupted.

IMU của scenario là bản ghi thật từ bộ dữ liệu, được ghép theo timeline (Scenario-paired). Đoạn ghép được chọn sau khi chạy detector trên toàn bộ file scenario và lưu kết quả đó. Không chọn đoạn bằng cách sửa ngưỡng.

## Policy

Policy chạy bằng code. Mọi gửi cảnh báo trong P0 dùng simulated sink. Phản hồi người đeo và xác nhận người chăm sóc là dữ liệu riêng.

| Sự kiện | Loại | Hành vi |
|---|---|---|
| Người đeo đi vào zone có `fall_exposure` trong scene manifest. | Advisory. | HUD hiện `advisory_text` của zone ngay. Lấy frame camera tại thời điểm vào zone và gọi SAM với `hazard_prompt` của zone. Không báo ai. |
| Vào lại cùng zone trong `advisory_cooldown_seconds`. | Không hiển thị. | Ghi log, không hiện lại HUD. |
| Detector xác nhận FALL. | Ứng viên. | Chưa tạo incident. Chờ người đeo nằm yên trong `fall_stillness_window_seconds`. |
| Ứng viên có khoảng nằm yên `fall_stillness_seconds`. | Critical incident, trạng thái awaiting_wearer. | Hiện "Are you OK?" trên HUD và đếm ngược `wearer_cancel_window_seconds`. Xếp analysis. Chưa báo ai. |
| Ứng viên hết hạn mà không nằm yên. | Bị loại. | Ghi vào danh sách bị loại để báo cáo, không tạo incident. |
| Người đeo bấm "I'm OK" trong lúc đếm ngược. | cancelled_by_wearer. | Không báo ai. Lưu self-report làm nhãn báo nhầm. |
| Hết đếm ngược, không có phản hồi. | alerted. | Báo contact 1 và bắt đầu hạn caregiver. Không chờ SAM hoặc agent. |
| Người đeo bấm "I'm OK" sau khi đã báo. | Thông tin. | Lưu self-report, hiển thị cho người chăm sóc. Không hủy cảnh báo hay escalation. |
| Đã báo contact 1, hết hạn caregiver chưa ack. | Escalation. | Chuyển contact 2 một lần. |
| Caregiver ack trước khi gửi contact 2. | Ack. | Lưu ack và chặn escalation chưa gửi. |

Advisory không phải incident. Nó được ghi vào action log của session với zone, thời điểm, frame ID và kết quả SAM. HUD không chờ SAM. Nếu SAM không tìm thấy vật thể, timeout hoặc lỗi, advisory vẫn đã hiện và log ghi rõ "Not visually confirmed" kèm trạng thái.

Hazard context đến từ zone đã khai báo, ghi là Authored location. SAM cung cấp xác nhận hình ảnh độc lập, ghi là Visual confirmation. Không gộp hai nguồn thành một kết luận. Một mask cầu thang không cho biết khoảng cách hoặc nguyên nhân ngã.

Mỗi FALL đã xác nhận trở thành ứng viên; ứng viên chỉ mở incident khi thấy nằm yên. FALL mới cách incident đang mở dưới `fall_merge_seconds` được gộp vào incident đó. Cooldown của detector không được lọc mất một FALL đã xác nhận.

Caregiver ack ghi việc nhận xử lý, không khẳng định người đeo đã an toàn. Ack lặp lại trả cùng kết quả, không tạo ack mới.

## AI và bằng chứng

Một agent Muse Spark (`muse-spark-1.3`) phân tích mỗi FALL incident. Agent gọi tools lấy context, lấy frame, segment frame và lấy environmental fixes. Wonder chỉ được thêm khi cần và đã đo độ trễ. Advisory chỉ gọi SAM, không gọi agent.

Agent trả summary, observations, evidence IDs, uncertainties và recommendation IDs. Logger ghi tool inputs/results và decision summary. Backend sở hữu severity và notification. Output của agent không thay đổi policy.

Frame bằng chứng có ID, scene/camera version, source offset và nhãn Rendered frame hoặc Recorded video. IMU ghi recording ID, khoảng mẫu, đơn vị và nguồn. Ghép các nguồn theo kịch bản được ghi là Scenario-paired, không gọi là ghi đồng thời người thật.

SAM nhận pixel nguồn và prompt vật thể. Ground truth của scene không được đưa vào prompt như đáp án perception. Mask nằm trên đúng frame đã phân tích. Analysis hỗ trợ Pending, Running, Complete, NoMatch, Failed, TimedOut và Cancelled. Thiếu frame dùng Unavailable. Failure không được thay bằng mask hoặc kết quả bịa.

G2 đã đạt trên ảnh nhà khối hộp. Ngày 5 chạy lại `backend/tools/gate_sam.py` trên ảnh nhà đã có nội thất. Nếu kết quả kém, dùng clip iPhone quay trước và ánh xạ zone thủ công, ghi rõ nguồn.

## Tiêu chí nghiệm thu

| ID | Điều kiện đạt |
|---|---|
| AC01 | Nhà có đủ inventory đã chốt; camera/HUD và chọn marker dùng được trên máy demo. |
| AC02 | Model/scaler thực sự load; kết quả G1, C, D và giới hạn được ghi trong GATES.md. INSTABILITY không tạo incident hay HUD. |
| AC03 | Chọn incident mở đúng IMU interval, frame và nguồn của cùng session. |
| AC04 | SAM trả mask thật trên chính loại ảnh dùng trong demo; mask gắn đúng ảnh, có trạng thái NoMatch/lỗi. |
| AC05 | Có vòng agent dùng tool thật và summary liên kết evidence IDs. |
| AC06 | Vào zone `fall_exposure` hiện advisory trên HUD trong `advisory_hud_latency_ms`, không gửi thông báo cho ai; kết quả SAM được ghi kèm advisory; vào lại trong cooldown không hiện lại. |
| AC07 | FALL có nằm yên sau đó tạo Critical incident và hiện "Are you OK?" với đếm ngược. "I'm OK" trong lúc đếm hủy và không báo ai; hết đếm ngược thì báo contact 1; "I'm OK" sau đó chỉ được ghi. Cooldown detector không chặn FALL. Tỉ lệ bắt ngã và báo nhầm có và không có bộ lọc nằm yên được công bố. |
| AC08 | Ack trước hạn chặn contact 2; không ack thì contact 2 nhận một thông báo mô phỏng. |
| AC09 | Refresh/reconnect phục hồi snapshot; hai lần Start có detector context và incident riêng. |
| AC10 | Với DB hoạt động bình thường, Stop chặn action muộn và startup sau restart đánh dấu run cũ Interrupted, không resume detector. Commit lỗi chỉ yêu cầu best-effort dừng/cancel task, log và ghi Interrupted nếu còn ghi được. |
| AC11 | Thiếu frame, lỗi/schema sai hoặc timeout Meta không chặn nhánh FALL hay advisory, và hiển thị đúng trạng thái. |
| AC12 | Nhật ký và recording demo thể hiện đúng nguồn; FPS, latency và tỉ lệ báo nhầm FALL công bố đều có số đo thật. |

## Tham số demo tập trung

Các giá trị Default bên dưới là cấu hình khởi đầu để đo. Đây không phải ngưỡng y khoa. Khi triển khai, một config backend là nguồn cho cả policy và metadata trả về UI.

| Tên đề xuất | Default hoặc cách chốt | Ý nghĩa |
|---|---|---|
| sample_rate_hz | 50 | Nhịp dữ liệu IMU hiện tại. |
| window_samples | 100 | Cửa sổ detector hiện tại. |
| fall_confirmation_seconds | 0.5 | Cơ chế hiện tại; nếu đổi inference stride phải xác nhận lại theo thời gian. |
| fall_threshold, svm_fall_threshold | 0.55 và 2.5 g, theo config hiện tại | Ghi nguồn quyết định (model hoặc luật SVM); pin trong session. |
| fall_merge_seconds | 10 | FALL cách nhau dưới khoảng này được gộp vào một incident. |
| fall_stillness_seconds, fall_stillness_window_seconds, fall_stillness_svm_std_max, fall_stillness_gyro_mean_max | 2 s, 10 s, 0,05 g, 10 °/s (mức Trung bình, GATES.md) | Bộ lọc nằm yên sau FALL. |
| wearer_cancel_window_seconds | 7 | Thời gian HUD đếm ngược trước khi báo contact 1. Người dùng chọn trong khoảng 5 đến 7 giây. |
| advisory_cooldown_seconds | 60 | Không hiện lại advisory của cùng zone trong khoảng này. |
| advisory_hud_latency_ms | 1000 | Mục tiêu từ lúc vào zone tới lúc HUD hiện. |
| caregiver_ack_timeout_seconds | 20 | Tính từ lúc contact 1 nhận thông báo mô phỏng. |
| run_timeout_seconds | 180 | Hạn run demo; scenario phải đủ chỗ cho các deadline dự kiến. |
| analysis_timeout_seconds | 45 | Budget toàn bộ analysis, không chỉ một HTTP request. |
| max_tool_calls | 5 | Budget tool calls mỗi incident; failure có output riêng. |
| automatic_analysis_retries | 0 | P0 lưu lỗi và cho review; lần Start mới chạy lại từ đầu. |
| frame_alignment_tolerance_ms | 250 | Đo error, vượt tolerance thì Unavailable. |
| imu_detail_max_seconds | 10 | Khoảng IMU tối đa trả về evidence drawer ở sample rate đã pin. |
| target_overview_fps | 30 | Mục tiêu đo trên máy demo. |
| target_confirmed_detection_to_incident_ms | 1000 | Mục tiêu local sau khi detector xác nhận; báo riêng window/confirmation delay. |

Luật nâng mức do nhiều sự kiện lặp lại, thống kê lịch sử, SQL và báo cáo tuần thuộc backlog. Mô tả dự thi nằm trong [SUBMISSION.md](SUBMISSION.md) và chỉ nêu tính năng MVP 1.
