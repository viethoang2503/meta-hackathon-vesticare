# Mô tả dự thi theo MVP 1

Bản nháp v0.3, khớp phạm vi trong [SPEC.md](SPEC.md). Đây là mô tả dự định xây dựng, chưa phải xác nhận đã triển khai hoặc đã nộp lên nền tảng.

## What do you want to build?

I want to build Vesti-Care Spatial, an explainable fall-safety assistant that combines a wearable IMU, Meta AI tools, and a furnished 3D home simulation.

The camera and the IMU each do the job the evidence supports. The IMU detects falls. The camera, through SAM 3.1, shows what is around the person. When the wearer walks into a known hazard zone such as the stairs, a simulated glasses HUD shows a prevention tip, and SAM confirms the hazard in the current frame. When the IMU detects a fall, the caregiver is alerted immediately and the HUD asks the wearer "Are you OK?". An unacknowledged alert escalates to a second simulated contact. Code enforces this policy independently of model reasoning.

For every fall, a Muse Spark agent uses tools to retrieve the sensor interval and the matching frame, segment hazards, and recommend environmental changes from a curated library. Caregivers select the incident in the 3D home to inspect the IMU trace, source image, segmentation mask, explanation, and response log.

We also tested whether the IMU could warn before a fall. On participants from our test split, the instability classifier flagged healthy people walking on stairs as often as patients. A model we retrained with participant-level cross-validation, given the activity as input, still could not tell patients from healthy people on stairs (AUC 0.49). We document these results and do not claim to detect loss of balance. Prevention in this version comes from context, and a weekly gait-screening score is future work.

MVP 1 runs on the web without glasses or live sensor hardware. The home is illustrative, and pairing sensor data with scene frames is a reproducible scenario rather than proof of real-world localization or synchronized capture of a person.
