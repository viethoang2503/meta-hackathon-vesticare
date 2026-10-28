# Mô tả dự thi theo MVP 1

Bản nháp thay cho description ban đầu, khớp phạm vi v0.2. Đây là mô tả dự định xây dựng, chưa phải xác nhận đã triển khai hoặc đã nộp lên nền tảng.

## What do you want to build?

I want to build Vesti-Care Spatial, an explainable fall-risk assistant that combines wearable IMU signals, Meta AI tools, and a furnished 3D home simulation.

The demo replays IMU data through a classifier with outputs for normal motion, model-predicted motion risk, and falls. When an event occurs, the system retrieves a matching scene frame. SAM segments relevant objects, while a Muse Spark agent uses tools to retrieve evidence, analyze context, and recommend environmental changes. The classifier's motion-risk label and its validation limits are documented rather than presented as a medical diagnosis.

A simulated glasses HUD asks the wearer "Are you OK?" for a High motion-risk alert. If the wearer does not respond within the configured time, the system notifies a simulated caregiver contact. A detected fall alerts the caregiver immediately. An unacknowledged caregiver alert escalates to a second simulated contact. Code enforces this policy independently of model reasoning.

Caregivers can select an incident in the 3D home to inspect its IMU interval, source image, segmentation mask, explanation, and response log. Authored locations, rendered camera frames, and model observations have separate source labels. The home is illustrative, and pairing sensor data with scene frames is a reproducible scenario rather than proof of real-world localization or synchronized capture of a person.

MVP 1 runs on the web without requiring glasses or live sensor hardware. Its goal is to demonstrate a complete, evidence-linked response workflow and environmental guidance, with real classifier and Meta API execution.
