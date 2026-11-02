# Mô tả dự thi theo MVP 2

Bản nháp v0.1, ngày 2026-11-02. Mô tả những gì đã xây và những gì đã được kiểm chứng, theo [ACCEPTANCE_MVP2.md](ACCEPTANCE_MVP2.md). Chưa phải xác nhận đã nộp lên nền tảng. Phần nào chưa có bằng chứng thật được viết là chưa kiểm chứng. Đừng thêm câu nào mạnh hơn bảng nghiệm thu.

## What do you want to build?

Vesti-Care Spatial is a fall-safety assistant for one wearer, built from a waist IMU, a phone worn at head height as a stand-in for smart glasses, and a caregiver's phone.

When the waist sensor detects a fall and the person stays still, the head-mounted phone asks out loud, "Are you all right? Please respond." The wearer answers by voice. Speech is transcribed on the laptop, and plain code classifies the answer from a fixed list of phrases. A clear "I'm fine" before the deadline cancels the alert. A request for help raises it at once. Silence, or anything unclear, alerts the first caregiver on Telegram, and an alert nobody acknowledges escalates to a second caregiver. An Acknowledge button on the message records that a caregiver has taken it.

The camera runs the whole time. Meta SAM 3.1 looks for stairs in the latest frame, and the phone says "Stairs visible ahead." at most once every 30 seconds and never while it is waiting for the wearer's answer. When a fall happens, the frame nearest to it is matched by time, within a stated clock uncertainty, and attached to the incident. If the clocks cannot support that match, the incident says so instead of showing the image as evidence.

A Muse Spark agent reads the evidence after the first alert has been committed and writes a cited explanation for the caregiver's laptop. It can send one fixed-format evidence update to each contact and have the phone say one of four fixed sentences. It chooses which evidence to cite and which sentence fits. It cannot write the text, cancel, escalate, or give medical advice, and code checks every request again at the moment it acts. If the model fails, the alert is unchanged.

A recorded session can be exported as a package with its IMU, camera frames, response events and policy actions. The detector and the policy replay from that package twice with the same result, and the policy runs on a virtual clock without calling any speech model, agent or messaging service.

## What is tested, and what is not

The backend has 384 automated tests, and the pieces were run together with a stand-in device, a headless browser with a fake microphone and camera, real SAM, a real Muse Spark run and a simulated notification channel. The policy replay matches the live run manager on five scenarios.

We have not yet run any of this with a person, an ESP32 on a waist, an iPhone on a forehead mount, a staircase or a real Telegram bot. The acceptance table lists those as not run: the sensor gate, the voice trials, the stairs trials, the per-person fall table and the real-device recording. We will publish the counts, including every miss and false alarm, once they exist. Nothing here is a clinical validation.

## Limits we state

Speech recognition runs on English only. The phrase list is conservative: it can treat "don't call" as a request for help, and it cannot tell that a person who says nothing is in pain. After a cancel there is no way to ask for help by voice once the response window and a 15-second grace have closed. A full speech queue can drop a turn that contained a request for help; the laptop shows that state and the deadline keeps running.

Location is unknown in live sessions. The camera is a phone, not smart glasses, and the demo does not claim anything about Ray-Ban hardware. The earlier finding stays: the IMU classifier for loss of balance could not tell patients from healthy people on stairs, so we do not claim to detect it.
