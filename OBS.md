> NOBUBBLEWARP variant: this inherited document includes historical sandbox claims. Current host execution and file-tool policy are defined in [README.md](README.md); no command or decoder uses Bubblewrap.

# OBS recording integration

The Bridge always exports `obs_status`, `obs_start_recording` and
`obs_stop_recording`. There is no enable/disable flag. A missing or closed OBS
returns a tool error without affecting filesystem, desktop, browser or memory tools.
Use `app_launch("obs")` to open the configured independent application service.

## Connection

`bridge_config.json` contains only `obs.host`, `obs.port`, and `obs.password_file`.
The default endpoint is `127.0.0.1:4455`; only numeric loopback addresses are accepted
and proxies are disabled. The password file must belong to the Bridge user, be a
regular non-symlink file and have no group/other permissions (use mode 0600).
The WebSocket server must require authentication. Passwords and server error
comments are never returned by the tools. Keep the password file in the already
protected `~/.config/mcp-integration/` directory. Protect OBS's corresponding
`~/.config/obs-studio/plugin_config/obs-websocket/` directory in `filesystem.denied_paths`.
Never commit either credential file.

The client is loopback-only. OBS itself listens on all interfaces, so this host
also runs the system service `obs-websocket-local.service`. Its separate nftables
`inet obs_bridge` table rejects non-loopback TCP traffic to port 4455 only;
it does not flush or replace existing firewall rules. Sources are in
`ops/obs-websocket-local.nft` and `ops/units/obs-websocket-local.service`, installed
as `/etc/obs-websocket-local.nft` and `/etc/systemd/system/obs-websocket-local.service`.
The service is enabled at boot. Changing the OBS port also requires updating
this narrowly scoped firewall rule. Keep OBS authentication enabled.

## Recording

1. Open OBS and select/configure the intended scene and audio sources.
2. Call `obs_status` to see the scene, available scene names, destination and recording state.
3. Call `obs_start_recording`. It checks the destination against Bridge write policy,
   starts the current program scene, and reads recording state back.
4. Call `obs_stop_recording`. It verifies the stopped state and returns OBS's
   `output_path`. Inspect/play that file to verify the actual picture and audio.

These tools do not configure sources, choose monitors, alter audio routing, or
start streaming. On Wayland, screen/window capture requires a PipeWire source and
the desktop's capture consent. An active recording alone does not prove that the
intended game/desktop is visible. The initial profile writes MKV, 1280x720 at 30 fps,
to `/home/bridge-user/Videos/OBS`.

Already recording/stopped returns a no-op. A stop may stop a manually started
recording, so only call it when that recording is intended to end. A later stop
cannot recover a previous output path; preserve the successful stop response.
No generic OBS RPC, scripts, toggle command or arbitrary destination parameter is exposed.

## Failures and verification

Operations have bounded connection/response waits and a per-process lock. Separate
Bridge processes and manual OBS use can still race; StartRecord/StopRecord are used
instead of ToggleRecord. A lost mutation receipt returns `outcome_unknown` and is
never automatically replayed. Inspect state before deciding what to do next.
A successful reply confirms OBS's recording state, not the video contents.

`test_obs.py` uses a separate authenticated localhost WebSocket fixture; it never
records the real desktop or connects to the production OBS. It checks authentication,
path refusal, start/stop/no-op, malformed responses, lost receipts and MCP errors.
Run the full suite with `BRIDGE_HOST_TESTS=1 BRIDGE_SYSTEMD_TESTS=1 ./check.sh`.

Protocol reference: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md

## Validation on 2026-09-11

- Installed Ubuntu OBS Studio 32.1.0 and official `obs-plugins` (PipeWire included),
  without recommended VLC packages. Bundled obs-websocket reports 5.7.2.
- Baseline: 84 tests, 4 expected skips; 30 GI tests. With OBS: 95 tests,
  the same 4 skips, and 30 GI tests passed.
- `check_obs_live.py` exercised status/start/stop via actual MCP stdio against the
  running OBS. Synthetic scene and muted desktop/microphone; original scene and
  mute states restored afterwards. MKV: H.264/AAC, 1280x720, 30 fps, 3.233 seconds.
  A decoded frame was visually inspected. This does not validate game capture,
  microphone audio or a Wayland monitor selection.
- Bridge health/readiness returned HTTP 200 after restarting only its tunnel.
  Unity, Blender and Browser tunnel PIDs were unchanged; OBS stayed connected.
- Existing source/configuration backup:
  `/home/bridge-user/.local/state/mcp-integration/pre-obs-20260911T194517`.
  Revert only the OBS diff or carefully restore affected files if no later changes
  exist. Do not remove the firewall guard while leaving OBS exposed unintentionally.
- Refresh the Local Dev Bridge action catalogue in ChatGPT to expose the new tools.
  Local catalogue validation does not prove that the hosted catalogue was refreshed.

The active observation scene is now `Bridge Desktop`, using `Bridge Screen`
(PipeWire). The user selected the desktop through the native chooser. The observed
1920x1080 source is fitted into the 1280x720 recording canvas. Global desktop and
microphone audio are muted for visual observation; enable intentionally only when
needed for an audio task. The original `Scene` remains available. A future OBS or
portal restart may require native selection again; never assume a source is live
without inspecting a frame.

## Choosing observation and extracting frames

The MCP initialization instructions and get_session_context now prefer semantic
Browser/application/accessibility state for structured questions, a screenshot for
static appearance, and a short recording only for motion or sequence (including
browser, desktop and other applications, not just games). The user has authorized
short screen recordings when useful for authorized PC work; native consent and any
later restriction still apply. No continuous recording is started automatically.
Check the current scene/destination first, do not take over another recording,
start immediately before the relevant action, and stop promptly even on failure.

`obs_extract_frames(path, start_seconds=0, interval_seconds=1, count=3)` returns
native JPEG images and timing metadata. Use the completed output_path from stop.
Limits: 1-6 frames, 0.1-60 second spacing, maximum 960x540 per image, 8 GiB input,
20-second extraction budget. All requested times must be inside the video.
The source is read-only and changes during sampling are rejected. The decoder
runs inside Bubblewrap with only the opened input and system libraries visible,
no host home/network, and bounded memory/CPU/output. No extracted files are saved
to the workspace by the tool. It does not analyze audio or delete the video.
Samples can miss short events between frames; inspect denser samples near the
relevant transition. Historical frames must never be used as live click targets.

Measured on this host on 2026-09-11, with OBS already open, through MCP stdio:

- Three sample-extraction calls: 416.3, 462.2 and 462.3 ms.
- Real desktop cycle: status 11.7 ms, start 155.9 ms, stop 1059.0 ms,
  three-frame extraction 463.8 ms; total 4.693 seconds including 3 seconds waiting.
- Video duration 3.2 seconds. Decoded desktop frame visually inspected.
- These are local wall times, not guarantees, hosted-chat latency or model image
  analysis time. Opening OBS and granting capture permission were not timed.
- Full suite: 103 tests with 4 expected skips, plus 30 GI tests passed.
  Added native-image/MCP, malformed input, limits, changed-file and real decoder
  filesystem/network isolation checks. Local catalogue includes 37 tools.

## General animation validation rule

The general animation/timing validation rule lives in `/home/bridge-user/AGENTS.md`,
with its restore copy in `ops/config/global-agent-instructions.md`. OBS procedures
live in `BRIDGE_INSTRUCTIONS.md`. `effective_instructions()` composes both for MCP
initialization and `get_session_context`; project rules remain separate.

Time-based sampling retains its 0.1-second minimum. For consecutive recorded
frames use start_frame as described below; recording FPS still limits precision.

## Consecutive recorded frames

`obs_extract_frames(path, start_frame=60, count=6)` selects source frames 60–65
by decoded frame index, without time seeking. It ignores time-sampling arguments.
Returns `sampling_mode=consecutive_frames` and `source_frame_index` for each image;
requested timestamps are null rather than fabricated. This is video frame precision,
not game simulation precision. Limits of six images, decoder isolation, 8 GiB and
time budgets remain. Late indices in long recordings can exceed the decode budget.

Validated using distinct consecutive frames from a 60 FPS fixture and a real
4.166-second game recording at `/home/bridge-user/Videos/OBS/2026-09-12 03-17-13.mkv`.
Visual inspection confirmed the game was captured; this does not validate landing timing.
