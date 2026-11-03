# M2 branches

9 incident(s). All branches seen with real Telegram: **False**.

| Branch | What it covers | Seen | With real Telegram | Status |
|---|---|---|---|---|
| `ok_cancel` | OK before the deadline cancels | 1 | 1 | Seen with real Telegram |
| `help_alert` | HELP raises the alert at once | 0 | 0 | Not seen |
| `timeout_alert` | Silence alerts at the deadline | 7 | 7 | Seen with real Telegram |
| `ack` | A caregiver acknowledges | 5 | 5 | Seen with real Telegram |
| `escalation_no_ack` | No acknowledgement escalates to contact 2 | 5 | 5 | Seen with real Telegram |
| `send_failed` | The first alert fails to send and contact 2 is alerted at once | 1 | 1 | Seen with real Telegram |
| `send_unknown` | A send with no answer keeps the acknowledgement timer | 1 | 1 | Seen with real Telegram |
| `stop_during_window` | Stop pressed while the wearer is still being asked | 1 | 1 | Seen with real Telegram |
| `stop_during_help_only` | Stop pressed after a cancel, while only HELP is heard | 0 | 0 | Not seen |
| `ok_then_help_before_deadline` | OK, then HELP before the deadline | 0 | 0 | Not seen |
| `cancel_then_silence` | Cancelled, then silence to the end: no alert | 1 | 1 | Seen with real Telegram |
| `help_in_grace` | HELP captured before D, processed after D within the grace | 0 | 0 | Not seen |
| `help_rejected_late` | HELP captured after D, too late, or with an unconfirmed clock is only logged | 0 | 0 | Not seen |
| `ok_late` | OK after the alert is a late update, not a cancel | 1 | 1 | Seen with real Telegram |
| `ok_repeated` | A repeated OK is logged and sends nothing | 1 | 1 | Seen with real Telegram |
| `stt_dropped` | A turn dropped from a full speech queue | 0 | 0 | Not seen |
| `stt_failed` | Speech recognition failed on a turn | 0 | 0 | Not seen |
| `interrupted` | The backend restarted during the session | 1 | 1 | Seen with real Telegram |

## Timing

From classification (or the deadline, for silence) to the send being scheduled: 7 sample(s), largest 4 ms, target 2000 ms, within target: True.
Telegram send, from scheduled to answered: 10 real send(s), 889 to 1836 ms.
Not measured: phone receipt time of a Telegram message; behaviour under load from other models.

## Incidents

- Incident 23 (telegram, fake_esp32): alerted, alert timeout. Tags: ack, send_failed, timeout_alert.
  - send to contact_1: failed via telegram, scheduled 30003 ms, done 31000 ms
  - send to contact_2: sent via telegram, scheduled 31001 ms, done 31914 ms
- Incident 22 (telegram, fake_esp32): alerted, alert timeout. Tags: escalation_no_ack, timeout_alert.
  - send to contact_1: sent via telegram, scheduled 30004 ms, done 30990 ms
  - send to contact_2: failed via telegram, scheduled 50011 ms, done 51001 ms
- Incident 21 (telegram, fake_esp32): alerted, alert timeout. Tags: ack, escalation_no_ack, timeout_alert.
  - send to contact_1: sent via telegram, scheduled 30004 ms, done 30950 ms
  - send to contact_2: sent via telegram, scheduled 50016 ms, done 50998 ms
- Incident 20 (telegram, fake_esp32): alerted, alert timeout. Tags: ack, timeout_alert.
  - send to contact_1: sent via telegram, scheduled 30003 ms, done 30960 ms
- Incident 19 (telegram, fake_esp32): awaiting_wearer, alert None. Tags: interrupted.
- Incident 18 (telegram, fake_esp32): alerted, alert timeout. Tags: escalation_no_ack, stop_during_window, timeout_alert.
  - send to contact_1: sent via telegram, scheduled 30002 ms, done 31032 ms
  - send to contact_2: sent via telegram, scheduled 50010 ms, done 50946 ms
- Incident 17 (telegram, fake_esp32): alerted, alert timeout. Tags: ack, escalation_no_ack, ok_late, ok_repeated, timeout_alert.
  - heard “None” as ok (applied True, None); received None ms, queue None ms, STT None ms, classified 33804 ms
  - heard “None” as ok (applied False, already_recorded); received None ms, queue None ms, STT None ms, classified 35838 ms
  - send to contact_1: sent via telegram, scheduled 30004 ms, done 30893 ms
  - send to contact_2: sent via telegram, scheduled 50010 ms, done 51846 ms
- Incident 16 (telegram, fake_esp32): cancelled_by_wearer, alert None. Tags: cancel_then_silence, ok_cancel.
  - heard “None” as ok (applied True, None); received None ms, queue None ms, STT None ms, classified 2075 ms
- Incident 15 (telegram, fake_esp32): alerted, alert timeout. Tags: ack, escalation_no_ack, send_unknown, timeout_alert.
  - send to contact_1: sent via telegram, scheduled 30003 ms, done 31028 ms
  - send to contact_2: unknown via telegram, scheduled 50021 ms, done 55031 ms
