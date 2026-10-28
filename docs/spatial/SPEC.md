# Đặc tả MVP 1

Bản v0.2. Phạm vi là demo web một tab cho một người thực hiện trong 10 ngày. Hành vi dưới đây là hợp đồng dự kiến, chưa phải tính năng đã chạy. Các giá trị cấu hình nằm ở bảng tham số cuối tài liệu.

## Người dùng và câu chuyện

Người đeo nhận cảnh báo ngắn và có thể bấm "I'm OK" trên HUD mô phỏng. Người chăm sóc chọn incident trong căn nhà 3D để xem IMU, frame, SAM mask, giải thích của agent và lịch sử phản hồi.

Một kịch bản đi từ chuyển động bình thường tới model-predicted motion risk ở gần cầu thang. HUD hỏi người đeo trước. Không phản hồi đúng hạn thì cảnh báo liên hệ đầu tiên. Người chăm sóc chưa xác nhận đúng hạn thì chuyển liên hệ thứ hai. Một kịch bản khác có FALL và báo người chăm sóc ngay.

Tên hiển thị mặc định của output INSTABILITY là "Model-predicted motion risk". Chỉ dùng cách gọi mạnh hơn khi nguồn nhãn và bằng chứng độc lập hỗ trợ. Việc vượt qua một negative test không tự xác nhận ý nghĩa lâm sàng của lớp.

## Phạm vi

| Trong MVP 1 | Sau MVP 1 |
|---|---|
| Nhà nhỏ đủ nội thất, camera/HUD, Start/Stop, incident timeline và evidence drawer. | Nhà nhiều tầng, house editor, preview cải tạo và không gian cộng tác. |
| Replay qua classifier thật, SAM thật và một agent dùng tools có giới hạn. | Hỏi lịch sử tự do, text-to-SQL và báo cáo tuần. |
| Người đeo phản hồi, caregiver acknowledge và escalation mô phỏng. | Gửi Telegram thật và tích hợp kính hoặc IMU live vào luồng mới. |
| Lưu session, incident, nguồn bằng chứng và trạng thái analysis; phục hồi UI khi refresh. | Pause/Resume, job queue có lease/retry, khôi phục detector và đồng bộ nhiều tab. |

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

Stop kết thúc run và chặn hành động mới của task còn chờ. Analysis chưa hoàn tất chuyển Cancelled. Session cũng có timeout tự kết thúc, lưu end_reason và đánh dấu công việc còn dở là hết hạn thay vì thành công. Hết file IMU, session tiếp tục Running để xử lý analysis và deadline còn lại, camera giữ frame cuối và UI hiện "Recording ended". Source offset giữ ở cuối recording; đồng hồ của run vẫn tiếp tục. Scenario được kiểm tra để có đủ thời gian cho toàn bộ chuỗi deadline trước timeout.

Sau Stop hoặc timeout, session có trạng thái Stopped, lần lượt với end_reason=user_stop hoặc end_reason=timeout. Người xem vẫn chọn lại incident đã lưu. Refresh tải snapshot của session hiện có. Reconnect SSE cũng tải snapshot. Khi DB hoạt động bình thường, startup sau restart đánh dấu mọi run còn Running là Interrupted và cho phép Start run mới. Trạng thái session chỉ gồm Running, Stopped và Interrupted.

## Policy và hai loại phản hồi

Policy chạy bằng code. Mọi gửi cảnh báo trong P0 dùng simulated sink. Phản hồi người đeo và xác nhận người chăm sóc là dữ liệu riêng.

| Điều kiện | Mức | Hành vi |
|---|---|---|
| Output motion risk chưa có hazard context. | Caution. | Ghi incident, hiển thị thông tin và xếp analysis. |
| Output motion risk trong authored zone có fall_exposure. | High. | Hiện "Are you OK?" và bắt đầu hạn phản hồi người đeo. |
| Người đeo bấm "I'm OK" đúng hạn ở High. | Giữ High. | Lưu self-report và kết thúc bước hỏi của incident; chưa gửi caregiver alert vì bước đó. |
| High hết hạn người đeo chưa phản hồi. | Giữ High. | Báo contact 1 và bắt đầu hạn caregiver acknowledgement. |
| Detector xác nhận FALL. | Critical. | Báo contact 1 ngay, bắt đầu hạn caregiver; không chờ self-report, SAM hoặc agent. |
| Người đeo bấm "I'm OK" với FALL. | Giữ Critical. | Ghi thông tin kèm incident; caregiver alert và escalation vẫn có hiệu lực. |
| Đã báo contact 1, hết hạn caregiver chưa ack. | Giữ mức. | Chuyển contact 2 một lần. |
| Caregiver ack trước dispatch contact 2. | Giữ mức. | Lưu ack và chặn escalation chưa gửi. |

Hazard context P0 đến từ `stairs_approach` hoặc `stairs_landing` được scene manifest đánh dấu `fall_exposure`. Decision ghi rõ Authored location/context. Một mask cầu thang tự nó chưa cho biết khoảng cách hoặc nguyên nhân mất thăng bằng. SAM cung cấp quan sát độc lập; kết quả thiếu hoặc trái với mapping phải được hiển thị.

Rule High là policy minh họa. Gate ý nghĩa nhãn phải hoàn tất trước khi chốt cách kể câu chuyện. Nếu semantics còn là proxy, giữ tên motion risk và công bố giới hạn; không trình bày cảnh báo High như kết luận y khoa.

Nhiều cửa sổ liên tục được gom thành một incident. Khoảng NORMAL đủ dài đóng episode. FALL trong episode mở nâng mức ngay dù vừa có cảnh báo trước. Khi mức tăng, đặt lại caregiver ack hiện tại, lưu log ack cũ và vô hiệu hóa self-report của mức thấp hơn đối với quyết định mới. Tác vụ gửi mức thấp chưa dispatch được bỏ qua.

Ack và "I'm OK" gửi kèm mức đang hiển thị trong trường `seen_severity`. Request của mức cũ được ghi là stale, không xác nhận mức mới. P0 dùng kiểm tra enum severity, không có revision protocol. Caregiver acknowledgement ghi việc nhận xử lý, không khẳng định người đeo đã an toàn.

## AI và bằng chứng

Một agent Muse Spark gọi các tools lấy context, lấy frame, segment frame và lấy environmental fixes. Reasoning có thể dùng Spark; Wonder chỉ được thêm khi cần và đã kiểm tra quyền/độ trễ. Kiểm tra các incident vừa có để áp dụng policy không đồng nghĩa triển khai giao diện hỏi lịch sử.

Agent trả summary, observations, evidence IDs, uncertainties và recommendation IDs. Logger ghi tool inputs/results và decision summary. Backend sở hữu severity floor và notification. FALL giữ Critical bất kể output model reasoning.

Frame bằng chứng có ID, scene/camera version, source offset và nhãn Rendered frame hoặc Recorded video. IMU ghi recording ID, khoảng mẫu, đơn vị và nguồn. Ghép các nguồn theo kịch bản được ghi là Scenario-paired, không gọi là ghi đồng thời người thật.

SAM nhận pixel nguồn và prompt vật thể. Ground truth của scene không được đưa vào prompt như đáp án perception. Mask nằm trên đúng frame đã phân tích. Analysis hỗ trợ Pending, Running, Complete, NoMatch, Failed, TimedOut và Cancelled. Thiếu frame dùng Unavailable. Failure không được thay bằng mask hoặc kết quả bịa.

Nếu render không qua gate SAM Ngày 1, phương án dự phòng là clip iPhone quay trước và ánh xạ zone thủ công. Camera ảo vẫn có thể dùng để điều hướng nhà; khung evidence/HUD ghi rõ nguồn clip. Cần kiểm tra lại frame alignment và scope claim trước khi dựng phần còn lại.

## Tiêu chí nghiệm thu

| ID | Điều kiện đạt |
|---|---|
| AC01 | Nhà có đủ inventory đã chốt; camera/HUD và chọn marker dùng được trên máy demo. |
| AC02 | Model/scaler thực sự load, class/unit đúng; provenance, kiểm thử semantics và giới hạn được ghi lại. |
| AC03 | Chọn incident mở đúng IMU interval, frame và nguồn của cùng session. |
| AC04 | SAM trả mask thật trên chính loại ảnh dùng trong demo; mask gắn đúng ảnh, có trạng thái NoMatch/lỗi. |
| AC05 | Có vòng agent dùng tool thật và summary liên kết evidence IDs. |
| AC06 | High nhận "I'm OK" đúng hạn thì không gửi caregiver alert do bước hỏi đó; không phản hồi thì báo contact 1. |
| AC07 | FALL sau motion risk trong dưới 10 giây vẫn lên Critical và báo ngay; self-report cũ hoặc mới không chặn gửi. |
| AC08 | Ack trước hạn chặn contact 2; không ack thì contact 2 nhận một thông báo mô phỏng. |
| AC09 | Refresh/reconnect phục hồi snapshot; hai lần Start có detector context và incident riêng. |
| AC10 | Với DB hoạt động bình thường, Stop chặn action muộn và startup sau restart đánh dấu run cũ Interrupted, không resume detector. Commit lỗi chỉ yêu cầu best-effort dừng/cancel task, log và ghi Interrupted nếu còn ghi được; không cam kết transactional recovery hoặc chặn mọi side effect trong lỗi storage. |
| AC11 | Thiếu frame, lỗi/schema sai hoặc timeout Meta không chặn nhánh FALL và hiển thị đúng trạng thái. |
| AC12 | Nhật ký và recording demo thể hiện đúng nguồn; FPS và latency công bố đều có số đo thật. |

## Tham số demo tập trung

Các giá trị Default bên dưới là cấu hình khởi đầu để đo. Đây không phải ngưỡng y khoa. Khi triển khai, một config backend là nguồn cho cả policy và metadata trả về UI.

| Tên đề xuất | Default hoặc cách chốt | Ý nghĩa |
|---|---|---|
| sample_rate_hz | 50 | Nhịp dữ liệu IMU hiện tại. |
| window_samples | 100 | Cửa sổ detector hiện tại. |
| fall_confirmation_seconds | 0.5 | Cơ chế hiện tại; nếu đổi inference stride phải xác nhận lại theo thời gian. |
| fall_threshold, instability_threshold, svm_fall_threshold | Theo config/model đã kiểm chứng ở G1 | Ghi cả hard-rule source và model output; pin trong session. |
| normal_gap_seconds | Chốt bằng fixtures ở G1 | Khoảng NORMAL dùng để tách episode. |
| wearer_response_timeout_seconds | 10 | Thời gian High chờ self-report. |
| caregiver_ack_timeout_seconds | 20 | Tính từ lúc contact 1 thực sự nhận thông báo mô phỏng của mức hiện tại. |
| run_timeout_seconds | 180 | Hạn run demo; scenario phải đủ chỗ cho các deadline dự kiến. |
| analysis_timeout_seconds | 45 | Budget toàn bộ analysis, không chỉ một HTTP request. |
| max_tool_calls | 5 | Budget tool calls mỗi incident; failure có output riêng. |
| automatic_analysis_retries | 0 | P0 lưu lỗi và cho review; lần Start mới chạy lại từ đầu. |
| frame_alignment_tolerance_ms | 250 | Đo error, vượt tolerance thì Unavailable. |
| imu_detail_max_seconds | 10 | Khoảng IMU tối đa trả về evidence drawer ở sample rate đã pin. |
| target_overview_fps | 30 | Mục tiêu đo trên máy demo. |
| target_confirmed_detection_to_incident_ms | 1000 | Mục tiêu local sau khi detector xác nhận; báo riêng window/confirmation delay. |

Luật lặp lại nhiều episode rồi nâng mức, thống kê lịch sử, SQL và báo cáo tuần thuộc backlog sau MVP 1. Mô tả dự thi hiện tại nằm trong [SUBMISSION.md](SUBMISSION.md) và chỉ nêu tính năng MVP 1.
