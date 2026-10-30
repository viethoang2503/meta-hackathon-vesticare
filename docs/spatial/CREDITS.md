# Nguồn và giấy phép

Trạng thái ngày 2026-10-30. Dòng nào ghi "chưa xác minh" thì chưa tìm thấy điều khoản trong repo.

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

## Asset 3D

Ngôi nhà, nội thất và nhân vật dựng bằng khối cơ bản trong `dashboard/spatial/scene.js`. Không dùng model, texture hay âm thanh bên ngoài. Ảnh camera trong `assets/scenarios/*/frames/` là ảnh render từ cảnh này.
