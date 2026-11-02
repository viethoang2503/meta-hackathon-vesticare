# Nguồn và giấy phép

Trạng thái ngày 2026-11-02. Dòng nào ghi "chưa xác minh" thì chưa tìm thấy điều khoản trong repo. Giấy phép của gói Python ở mục MVP 2 lấy từ metadata của bản đã cài trong `.verify/venv`, không đọc từ file giấy phép đi kèm.

## Thư viện đi kèm

| Thành phần | Phiên bản | Giấy phép | Vị trí |
|---|---|---|---|
| three.js và OrbitControls | 0.160.0 | MIT | `dashboard/spatial/vendor/THREE_LICENSE` |
| `@meta-sam/parser` | 0.1.0 | SAM License, cập nhật 2025-11-19 | `dashboard/spatial/vendor/meta-sam-parser/LICENSE` |

## Mô hình và API

| Thành phần | Ghi chú |
|---|---|
| SAM 3.1 và Muse Spark (`muse-spark-1.3`) | Gọi qua Meta Model API bằng key của dự án, theo điều khoản của API đó |
| Model phát hiện ngã CNN-BiLSTM-Attention (`ml/model_3class/`) | Do dự án train. Chưa xác minh tập train của weights đang dùng (`GATES.md`) |

## Dữ liệu

| Bộ dữ liệu | Dùng ở | Giấy phép |
|---|---|---|
| SisFall (Sucerquia và cộng sự, Sensors 2017, 17(1):198) | Đoạn ngã của S2, đánh giá G1 | Chưa xác minh. README trong `ml/data/raw/SisFall/` chỉ trỏ tới trang tải và bài báo |
| Bộ dữ liệu tiền đình (`vestibular_dataset`) | Đoạn đi bộ của S1 và S2, thí nghiệm INSTABILITY | Chưa xác minh nguồn và điều khoản |
| KFall | Tiền xử lý khi train (`ml/training/preprocess_kfall.py`) | Chưa xác minh |

## Nội dung sơ cứu

`assets/first_aid/library.json` rút gọn hướng dẫn trên trang "Falls" của NHS (https://www.nhs.uk/conditions/falls/, xem lại ngày 6/3/2025) để đọc thành lời. Nội dung NHS dùng theo Open Government Licence theo hiểu biết của dự án; chưa xác minh điều khoản cho trang cụ thể này.

## Asset 3D

Ngôi nhà, nội thất và nhân vật dựng bằng khối cơ bản trong `dashboard/spatial/scene.js`. Không dùng model, texture hay âm thanh bên ngoài. Ảnh camera trong `assets/scenarios/*/frames/` là ảnh render từ cảnh này.

## MVP 2

Thư viện chạy trên backend (`backend/requirements.txt`).

| Thành phần | Phiên bản | Giấy phép | Ghi chú |
|---|---|---|---|
| `faster-whisper` | 1.2.1 | MIT | Chạy Whisper `small` int8 trên CPU. Kèm bộ lọc giọng nói Silero VAD |
| `ctranslate2` | 4.8.4 | MIT | Bộ chạy của faster-whisper |
| Trọng số Whisper `small` | Tải từ Hugging Face khi chạy lần đầu | Chưa xác minh trong repo | Bản chuyển đổi từ Whisper của OpenAI. Chưa đọc điều khoản của bản tải về |
| `av` (PyAV) | 15.1.0 | BSD-3-Clause | Giải mã âm thanh từ iPhone. Bản wheel kèm FFmpeg, giấy phép của FFmpeg đi kèm chưa xác minh |
| `onnxruntime`, `tokenizers`, `huggingface-hub` | 1.31.1, 0.23.3, 2.4.0 | MIT, Apache-2.0, Apache-2.0 | Phụ thuộc của faster-whisper |
| `httpx` | 0.28.1 | BSD-3-Clause | Gọi Meta Model API và Telegram Bot API |
| `qrcode` | 8.2 | BSD (metadata cũng ghi một phân loại "Other/Proprietary", chưa rõ) | Công cụ `wearer_link.py`, không nằm trong sản phẩm chạy |

Thư viện của firmware (`vesticare-sensor/platformio.ini`): ArduinoJson, Adafruit MPU6050, Adafruit Unified Sensor, Adafruit BusIO. Giấy phép của bốn thư viện này chưa xác minh trong repo.

Dịch vụ và công cụ bên ngoài.

| Thành phần | Ghi chú |
|---|---|
| Telegram Bot API | Gửi cảnh báo và nhận nút Acknowledge, theo điều khoản của Telegram. Bot do dự án tạo |
| cloudflared (Cloudflare Tunnel) | Công cụ chạy lúc thử để iPhone vào được laptop. Không nằm trong sản phẩm. Địa chỉ tunnel công khai nên không được đưa vào bài nộp |
| Playwright | Chỉ dùng khi kiểm tra bằng trình duyệt (`.verify/`), không nằm trong sản phẩm |

Dữ liệu và nội dung của MVP 2.

| Nội dung | Ghi chú |
|---|---|
| Ảnh camera và đoạn IMU của người thử | Chỉ ghi khi người đó đồng ý. Gói S3 ghi nhãn người tham gia và quyền công bố trong `manifest.json`. Không lưu âm thanh thô |
| Ảnh nhà dựng sẵn dùng để thử SAM | Cùng nguồn với `assets/scenarios/*/frames/` ở trên, không phải ảnh người thật |
