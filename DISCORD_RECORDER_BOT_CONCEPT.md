# Discord Recorder Bot: Product Objective and Ideas

## Objective

Make a Discord bot that turns a completed FTC recording into a shared, reviewable team moment without making Discord a control surface for the robot. The bot should help the team notice a useful recording, jump directly to its replay, preserve the context that mattered, and turn review into a short feedback loop between drivers, programmers, and scouts.

The bot is useful if it saves the team from asking “which run was that?” or re-explaining what happened in a clip. It is not useful if it merely mirrors every telemetry value into a noisy channel.

## First useful version

When an operator uploads a finalized recording, post one compact card to a configured Discord channel:

- robot name, OpMode, duration, and upload time;
- a link to the Oracle replay page;
- incident count and the highlighted sample ranges;
- a video link or a generated thumbnail when a recording has video;
- a small summary of gaps, failures, and debugger-command outcomes.

Use a thread below the card for discussion. The bot can add buttons or slash commands for **Open replay**, **Mark worth reviewing**, and **Create issue**. It should post only completed uploads, never live high-rate telemetry.

## Why it could matter for this recorder

The current recorder already has the hard part: durable raw packets, video, highlighted incident manifests, and a replay link. Discord gives those artifacts a social home. A programmer can see an incident immediately after practice; a driver can say what they intended; and the team has one canonical replay rather than copies of phone video in DMs.

The most valuable handoff is likely this:

```text
Robot run ends → recording uploads → bot posts replay → team discusses in thread
                                        ↓
                              incident ranges provide the starting points
```

## Out-of-the-box ideas

### “Explain this moment” prompts

For each captured incident, the bot opens a thread prompt with three short roles: Driver intent, Robot behavior, and Next experiment. This changes review from “it looked weird” to an actionable test idea without forcing a formal meeting.

### Replay bookmarks as Discord messages

Let a team member run `/replay bookmark <recording> <sample/time> <note>`. The bot posts a permalink to that exact time and stores the note next to the recording. A mentor can leave feedback at the same moment a driver is seeing.

### Practice-round digest, not a firehose

At the end of a practice block, post one digest: total runs, incidents, known signal gaps, and the three recordings most often marked “worth reviewing.” This can be more valuable than individual notifications during a busy drive session.

### Debugger safety receipts

When the telemetry debugger reports that an output was applied, rejected, or blocked by safety state, summarize it in a private technical channel. This makes it easier to distinguish “the command never reached the robot” from “the robot deliberately refused it.” Keep it separate from the drive-team channel.

### Match-prep comparison

Allow two bookmarked replay moments to be compared in one Discord post: for example, autonomous path attempt A versus B. The bot would link both exact timestamps and list selected telemetry deltas, rather than trying to stream charts into Discord.

### A lightweight learning archive

Tag a replay thread with labels such as `intake`, `drivetrain`, `autonomous`, or `vision`. Over a season, `/replay lessons intake` could return the team’s own short list of evidence-backed past fixes—not generic advice.

## Guardrails

- Discord must be read-only with respect to live robot control: no command execution, capture toggling, or motor actions from the bot.
- Default notifications should be opt-in and low-volume; post finalized uploads and explicit incidents, not every snapshot.
- Replay links should use authentication or unguessable access controls if recordings are not meant to be public.
- Use a private technical channel for debugger/safety receipts because they can be noisy and potentially confusing during practice.
- Make failure states explicit: an upload that fails should post a concise retry/help message, not silently disappear.

## Recommendation

Yes—build it, but begin as an **asynchronous replay-sharing and review bot**, not a live telemetry bot. The smallest valuable release is a finalized-recording post with a replay link, incident timestamps, and a discussion thread. Once the team actually uses those threads, add bookmarks, issue creation, and practice digests.
