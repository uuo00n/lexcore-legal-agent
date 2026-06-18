---
name: video-screenshot
description: 视频截图提取工具。从微信聊天录屏、会议录屏、操作录屏或其他证据视频中抽取关键帧、去重并生成可追溯图片证据。Use when the legal agent receives a video, screen recording, chat recording, evidence video, 视频截图, 录屏截图, 聊天记录截图, 抽帧去重, 视频截帧, or 视频关键帧提取 request.
---

# Video Screenshot

## Purpose

Use this skill when the legal agent needs to turn a video or screen recording into reviewable evidence screenshots. The skill supports legal consultation, evidence organization, lawsuit filing, and pleading drafting by producing frame images plus a traceable `_report.json`.

The skill does not decide the authenticity, admissibility, or legal effect of the video. It only prepares evidence material. Legal authorities still must come from legal search tools.

## When to Trigger

Trigger for requests such as:

- "这是微信聊天录屏，帮我提取成截图"
- "把这段视频里的聊天记录做成证据"
- "视频截图 / 录屏截图 / 抽帧去重 / 视频截帧"
- "会议录屏里有关键承诺，帮我截出来"
- "证据视频需要整理成图片和清单"

Do not use this skill for video compression, editing, subtitles, audio extraction, or ordinary media playback.

## Workflow

1. Confirm the input video file and proof purpose.
   - Supported formats: `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`, `.flv`, `.wmv`, `.ts`.
   - Ask the user to preserve the original video file and original carrier.
2. Submit the video to `/api/evidence/video/extract`.
   - Default strategy is scene detection.
   - The backend writes `data/evidence/<evidence_id>/source.<ext>`.
3. Wait for the task result.
   - If dependencies such as `ffmpeg` or `ffprobe` are missing, report the missing dependency clearly.
4. Use the generated report and frames.
   - `frames/frame_001_00m00s.jpg`: retained screenshots.
   - `_report.json`: evidence id, source path, duration, strategy, timestamp, SHA256, and dedup stats.
5. Feed the evidence summary into legal analysis or pleading drafting.
   - Treat extracted frames as factual evidence context.
   - Do not cite video frames as legal authority.

## Output Expectations

When this skill feeds legal work, produce or store:

- `evidence_id`
- original filename and source path
- extraction status
- retained frame count
- frame filenames
- approximate capture timestamps
- SHA256 for every retained frame
- `_report.json` path or API URL
- dependency warnings, if any

## Evidence Rules

- Keep the original video. Screenshots are derived evidence material.
- Keep complete chat context when possible; isolated screenshots may be challenged.
- Preserve account identity, timestamps, transfer records, and related messages.
- If the issue may go to court or arbitration, include screenshots in a formal evidence list and identify the original carrier.
- Do not alter the source video file.

## Script Reference

The product API uses `services.evidence_video`. The local scripts are thin wrappers for manual debugging:

```bash
python agent/skills/video-screenshot/scripts/extract.py input.mp4 -o data/evidence/manual/frames
```
